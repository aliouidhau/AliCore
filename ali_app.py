"""基站入口：只做三件事——开窗口、暴露 API、读写历史。

执行逻辑全在 alicore_agent/ 包里。这里是薄壳，规则是：
  · 不改前端契约（chat / save_config / clear_history 名字和参数都不变）
  · 凭证只在后端（get_config 不下发任何 key）
  · 出任何异常都变成一句人话返回，不让窗口卡死
"""

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
import zipfile

import requests

# PyInstaller --noconsole 下 sys.stdout/stderr 为 None；pythonnet(CLR) 初始化对 None 敏感会静默崩
# （旧版壳实测踩过）。必须在 import webview / pythonnet 之前兜底到 devnull。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import webview

from alicore_agent import build_agent, LLMError
from alicore_agent.llm import models_url
from alicore_agent.engine import CANCELLED_MARK
from alicore_agent.session_store import SessionStore
from alicore_agent.paths import resolve_paths, ensure_dirs, default_data_dir
from alicore_agent.fsutil import atomic_write_json
# 记忆的类型常量 / ID 方案 / 写锁统一由内核提供（单一来源，避免两套 hash 漂移）
from alicore_agent.memory import (
    MEMORY_KINDS, MEMORY_PIN_KINDS, MEMORY_LOCK, parse_memory_entries,
)
from alicore_agent.redact import redact
from alicore_agent import progress as learn_progress
from alicore_agent import quiz as lesson_quiz
from alicore_agent import lessons as lesson_mod

# AliCore：代码目录跟着程序走（源码模式看 __file__，打包后看 exe 所在目录）。
# 用户数据默认落到 %APPDATA%\AliCore，不写进代码仓库，也不和本机原有的阿鲤共用配置和密钥。
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = default_data_dir()
RUNTIME_DIR = os.environ.get("ALICORE_RUNTIME_DIR") or os.path.join(DATA_DIR, "_runtime")
BASE_DIR = DATA_DIR  # 旧名兼容；新代码优先用 DATA_DIR
HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
UI_PREFS_FILE = os.path.join(DATA_DIR, "ui_preferences.json")
# 模型不再写死：用户接入哪家服务商，就用哪家 /v1/models 返回的列表。
# MEMORY_KINDS / MEMORY_PIN_KINDS 已从 alicore_agent.memory 导入（内核单一来源）
HTML_FILE = os.path.join(APP_DIR, "ui_v2.html")
START_LOG = os.path.join(DATA_DIR, "_startup.log")
ARCHIVE_DIR = os.path.join(DATA_DIR, "sessions")
# C 练习场用的 TinyCC：首次点 C 教程时按需下载，解压到 runtime 目录，不碰系统目录。
TCC_EXE = os.path.join(RUNTIME_DIR, "tcc", "tcc", "tcc.exe")
# 官方 Windows 64 位包，主站 + GNU 镜像，前一个失败自动试下一个。
TCC_URLS = (
    "https://download.savannah.gnu.org/releases/tinycc/tcc-0.9.27-win64-bin.zip",
    "https://download-mirror.savannah.gnu.org/releases/tinycc/tcc-0.9.27-win64-bin.zip",
)
PRACTICE_FILE = os.path.join(DATA_DIR, "workspace", "practice_cases.json")
# 实战语音讲解：稿子在 workspace/practice_voice.json，音频在 assets/voice/practice/<id>.wav
PRACTICE_VOICE_FILE = os.path.join(DATA_DIR, "workspace", "practice_voice.json")
PRACTICE_VOICE_REL = "assets/voice/practice"
# 每道实战题写过的「我的判断」：跟着题走，重启后还能接着改
PRACTICE_ANSWERS_FILE = os.path.join(DATA_DIR, "workspace", "practice_answers.json")
# 教程语音讲课：稿子在 workspace/tutorial_voice.json，音频在 assets/voice/tutorial/<key>/<节>.wav
TUTORIAL_VOICE_FILE = os.path.join(DATA_DIR, "workspace", "tutorial_voice.json")
TUTORIAL_VOICE_REL = "assets/voice/tutorial"
# 仓库不带音频。没有自带 wav 时用 Windows 内置语音（SAPI）现场合成，免费、离线、不联网；
# 合成结果缓存在 runtime 下，同一段第二次点开是秒播。想换自己的音色见 README。
TTS_CACHE_DIR = os.path.join(RUNTIME_DIR, "tts_cache")
TTS_BUILTIN_VOICE = "Microsoft Huihui Desktop"
# 学习进度 / 错题本 / 复习排期：纯本地 JSON，零 token
PROGRESS_FILE = os.path.join(DATA_DIR, "workspace", "_progress.json")
# 分步导学：把每课拆成 6 个可完成的小步（数据在手写 JSON 里，不调模型）
LESSON_STEPS_FILE = os.path.join(DATA_DIR, "workspace", "lesson_steps.json")


def _seed_content():
    """首次启动把仓库里的靶场内容播种到数据目录。

    APP_DIR/workspace 是只读内容源（教程、题库、分步导学、语音稿、练习模板）；
    DATA_DIR/workspace 是用户工作区。只补缺失的文件，用户改过的、自己加的都不覆盖。
    代码目录和数据目录相同时（老的单目录模式）直接返回。
    """
    try:
        if os.path.normcase(os.path.abspath(APP_DIR)) == os.path.normcase(os.path.abspath(DATA_DIR)):
            return
        src_root = os.path.join(APP_DIR, "workspace")
        if not os.path.isdir(src_root):
            return
        dst_root = os.path.join(DATA_DIR, "workspace")
        copied = 0
        for dirpath, _dirs, files in os.walk(src_root):
            rel = os.path.relpath(dirpath, src_root)
            dst_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
            os.makedirs(dst_dir, exist_ok=True)
            for fn in files:
                dst = os.path.join(dst_dir, fn)
                if os.path.exists(dst):
                    continue
                try:
                    shutil.copy2(os.path.join(dirpath, fn), dst)
                    copied += 1
                except Exception:
                    pass
        if copied:
            _log(f"内容播种   {copied} 个文件 → {dst_root}")
    except Exception as e:
        _log(f"内容播种失败：{e}")


def _apply_paths(app_dir=None, data_dir=None, runtime_dir=None):
    """启动时重设 app/data/runtime 三块；默认值保持旧路径，绝不搬用户数据。"""
    global APP_DIR, DATA_DIR, RUNTIME_DIR, BASE_DIR
    global HISTORY_FILE, UI_PREFS_FILE, HTML_FILE, START_LOG, ARCHIVE_DIR, TCC_EXE, PRACTICE_FILE
    global PRACTICE_VOICE_FILE, PRACTICE_VOICE_REL, PRACTICE_ANSWERS_FILE
    global TUTORIAL_VOICE_FILE, TUTORIAL_VOICE_REL, TTS_CACHE_DIR
    global PROGRESS_FILE, LESSON_STEPS_FILE

    paths = ensure_dirs(resolve_paths(app_dir or APP_DIR, data_dir, runtime_dir))
    APP_DIR, DATA_DIR, RUNTIME_DIR = paths.app_dir, paths.data_dir, paths.runtime_dir
    BASE_DIR = DATA_DIR
    HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
    UI_PREFS_FILE = os.path.join(DATA_DIR, "ui_preferences.json")
    HTML_FILE = os.path.join(APP_DIR, "ui_v2.html")
    START_LOG = os.path.join(DATA_DIR, "_startup.log")
    ARCHIVE_DIR = os.path.join(DATA_DIR, "sessions")
    TCC_EXE = os.path.join(RUNTIME_DIR, "tcc", "tcc", "tcc.exe")
    PRACTICE_FILE = os.path.join(DATA_DIR, "workspace", "practice_cases.json")
    PRACTICE_VOICE_FILE = os.path.join(DATA_DIR, "workspace", "practice_voice.json")
    PRACTICE_VOICE_REL = "assets/voice/practice"
    PRACTICE_ANSWERS_FILE = os.path.join(DATA_DIR, "workspace", "practice_answers.json")
    TUTORIAL_VOICE_FILE = os.path.join(DATA_DIR, "workspace", "tutorial_voice.json")
    TUTORIAL_VOICE_REL = "assets/voice/tutorial"
    TTS_CACHE_DIR = os.path.join(RUNTIME_DIR, "tts_cache")
    PROGRESS_FILE = os.path.join(DATA_DIR, "workspace", "_progress.json")
    LESSON_STEPS_FILE = os.path.join(DATA_DIR, "workspace", "lesson_steps.json")
    return paths


def _log(msg):
    """启动计时。pythonw 没有 stdout，只能写文件——不然永远只能靠秒表猜。

    带日期是必须的：engine._now() 要读最后一行算「这台机器多久没开机了」，
    只有 HH:MM:SS 的话跨天就没法算。
    """
    try:
        with open(START_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _powershell_synthesize(text, out_path):
    """零依赖兜底：借 Windows PowerShell 的 System.Speech 合成。

    不要求装 pywin32，也不要求用户懂 pip。代价是每次起一个 powershell 进程，
    一段约 1 秒；有缓存，同一段只听一次。
    """
    txt = out_path + ".txt"
    ps1 = out_path + ".ps1"
    try:
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        # 脚本必须是纯 ASCII：Windows PowerShell 5.1 按 ANSI 读 .ps1，中文会解析失败
        script = (
            "Add-Type -AssemblyName System.Speech\n"
            "$t = Get-Content -LiteralPath $env:ALICORE_TTS_TEXT -Raw -Encoding UTF8\n"
            "$sp = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            "try { $sp.SelectVoice('Microsoft Huihui Desktop') } catch {}\n"
            "$sp.SetOutputToWaveFile($env:ALICORE_TTS_OUT)\n"
            "$sp.Speak($t)\n"
            "$sp.Dispose()\n"
        )
        with open(ps1, "w", encoding="ascii") as f:
            f.write(script)
        env = dict(os.environ)
        env["ALICORE_TTS_TEXT"] = txt
        env["ALICORE_TTS_OUT"] = out_path
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(exe):
            exe = "powershell.exe"
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
            env=env, capture_output=True, timeout=120,
        )
        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
            err = (p.stderr or b"").decode("utf-8", "ignore").strip()[:200]
            raise RuntimeError(err or "这台机器没有可用的中文语音")
    finally:
        for tmp in (txt, ps1):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _win32_synthesize(text, out_path):
    """装了 pywin32 就走这条：直接调 SAPI，快，不用起外部进程。"""
    import pythoncom
    import win32com.client as win32

    pythoncom.CoInitialize()
    try:
        voice = win32.Dispatch("SAPI.SpVoice")
        picked = None
        for v in voice.GetVoices():
            try:
                desc = v.GetDescription() or ""
            except Exception:
                continue
            if "Chinese" in desc or "Huihui" in desc or "中文" in desc:
                picked = v
                break
        if picked is None:
            raise RuntimeError("系统里没找到中文语音")
        voice.Voice = picked
        stream = win32.Dispatch("SAPI.SpFileStream")
        stream.Open(out_path, 3, False)  # SSFMCreateForWrite
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
    finally:
        pythoncom.CoUninitialize()


def _sapi_synthesize(text, out_path):
    """把文字压成 wav：优先 pywin32，没装就退回 PowerShell，两条路都免费、离线。"""
    try:
        return _win32_synthesize(text, out_path)
    except Exception as e:
        _log(f"[语音] pywin32 不可用（{e}），改用 PowerShell 合成")
    return _powershell_synthesize(text, out_path)


class Api:
    def __init__(self, safe_mode=False):
        self.safe_mode = bool(safe_mode)
        self._agent = build_agent(
            DATA_DIR,
            data_dir=DATA_DIR,
            runtime_dir=RUNTIME_DIR,
            app_dir=APP_DIR,
            safe_mode=self.safe_mode,
        )
        # 会话存储：一个对话 = 一个目录 + 追加的事件流（照 DeepSeek Harness 的做法）。
        # 会话从第一句话起就落盘，切历史只换指针，不再「先存当前再载入」。
        self.sessions = SessionStore(ARCHIVE_DIR)
        self.history = self._restore_sessions()
        # 交互式运行会话：sid -> 进程状态。管道读写在后台线程里跑，
        # 前端轮询拿输出、回传 input() 的输入。
        self._pyruns = {}
        self._pyrun_seq = 0
        self._pyruns_lock = threading.Lock()
        # 高危操作审批：工具执行前挂起，等界面点「允许一次」
        self._approvals = {}
        self._approval_lock = threading.Lock()
        try:
            self._agent.registry.confirm = self._confirm_tool
        except Exception as e:
            _log(f"[审批] 钩子没挂上，高危操作不会弹确认: {e}")

    # ---------------- 历史 ----------------
    def _restore_sessions(self):
        """启动：迁移老存档 → 打开最近那段会话（一段都没有就新开）。"""
        try:
            moved = self.sessions.migrate_legacy(
                ARCHIVE_DIR, os.path.join(ARCHIVE_DIR, ".legacy-migrated"))
            if moved:
                _log(f"[会话] 迁移老存档 {moved} 段")
        except Exception as e:
            _log(f"[会话] 迁移老存档失败: {e}")
        try:
            self._import_plain_history()
        except Exception as e:
            _log(f"[会话] 导入 history.json 失败: {e}")
        try:
            return self.sessions.open_last_or_create()
        except Exception as e:
            _log(f"[会话] 打开最近会话失败，新建一段: {e}")
            try:
                self.sessions.create()
            except Exception:
                pass
            return []

    def _import_plain_history(self):
        """老版 history.json 也是真对话：首次启动搬成会话，别丢。

        内容与已有会话完全相同就不重复建（用户刚被重复条目坑过）。
        """
        marker = os.path.join(ARCHIVE_DIR, ".history-json-imported")
        if os.path.exists(marker):
            return
        data = self._load_history()
        body = [(m.get("role"), m.get("content") or "") for m in data
                if m.get("role") in ("user", "assistant")]
        if body:
            for sid in os.listdir(ARCHIVE_DIR):
                if not os.path.isdir(os.path.join(ARCHIVE_DIR, sid)):
                    continue
                msgs = self.sessions.load_messages(sid)
                if [(m["role"], m["content"]) for m in msgs] == body:
                    _log("[会话] history.json 与已有会话相同，跳过导入")
                    break
            else:
                self.sessions.import_messages(data)
                _log(f"[会话] history.json 已导入为新会话（{len(body)} 条）")
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

    def _load_history(self):
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            _log(f"[历史] 读取失败，用空历史: {e}")
            return []

    def _save_history(self):
        """历史已经在 sessions/ 的事件流里实时落盘，这里不再全量重写。

        老版每轮把整段 history（几百 KB）重写一遍 history.json，切会话
        还要再写一次——「点一下卡一下」有一半来自这里。保留空函数只是
        为了不改调用点。
        """

    def _ledger(self, kind, **data):
        """API 层的手工事件：代码运行、实战提交、文件保存。失败不阻断功能。"""
        try:
            self._agent.ledger.append({"kind": kind, **data})
        except Exception:
            pass

    # ---------------- 高危操作审批 ----------------
    APPROVAL_TIMEOUT = 180

    def _confirm_tool(self, name, params, label):
        """ToolRegistry 的确认钩子，返回 (是否允许, 拒绝原因)。

        没有界面时默认拒绝——宁可她卡住，也不要在没人看着的时候把文件删了。
        无人值守的自测用 ALICORE_AUTO_ALLOW=1 显式放行。
        """
        if os.environ.get("ALICORE_AUTO_ALLOW") == "1":
            return True, ""
        window = self._main_window()
        if window is None:
            return False, "审批界面还没就绪，已按拒绝处理"

        action_id = uuid.uuid4().hex[:10]
        event = threading.Event()
        with self._approval_lock:
            self._approvals[action_id] = {
                "event": event, "ok": False, "reason": "", "tool": name,
            }
        payload = {
            "id": action_id,
            "tool": name,
            "label": label,
            "detail": self._approval_detail(params),
            "timeout": self.APPROVAL_TIMEOUT,
        }
        try:
            window.evaluate_js(
                "window.showToolApproval && window.showToolApproval(%s); 'ok'"
                % json.dumps(payload, ensure_ascii=False)
            )
        except Exception as e:
            self._drop_approval(action_id)
            return False, f"审批卡没能弹出来：{e}"
        self._ledger("approval/ask", action_id=action_id, tool=name, label=label)

        if not event.wait(timeout=self.APPROVAL_TIMEOUT):
            self._drop_approval(action_id)
            self._ledger("approval/timeout", action_id=action_id, tool=name)
            return False, "等太久没人确认，已按拒绝处理"
        with self._approval_lock:
            rec = self._approvals.pop(action_id, None)
        if not rec:
            return False, "审批记录丢了，已按拒绝处理"
        self._ledger("approval/result", action_id=action_id, tool=name,
                     allowed=rec["ok"], reason=rec["reason"])
        return rec["ok"], rec["reason"]

    def _drop_approval(self, action_id):
        with self._approval_lock:
            self._approvals.pop(action_id, None)

    def _main_window(self):
        try:
            import webview
            return webview.windows[0] if webview.windows else None
        except Exception:
            return None

    def _approval_detail(self, params):
        """审批卡展示的完整参数：密钥打码，单段过长就截断。"""
        params = params or {}
        lines = []
        for k, v in params.items():
            if any(h in str(k).lower() for h in
                   ("key", "token", "secret", "password", "authorization", "cookie")):
                text = "***（已隐藏）"
            elif isinstance(v, str):
                text = v
            else:
                text = json.dumps(v, ensure_ascii=False)
            if len(text) > 1500:
                text = text[:1500] + "\n…（后面还很长，已截断）"
            lines.append(f"{k}:\n{text}" if "\n" in text else f"{k}: {text}")
        return "\n\n".join(lines) or "（没有参数）"

    def approve_tool(self, action_id, decision):
        """前端点按钮后回传。decision 只认 allow / deny，别的都按拒绝。"""
        aid = str(action_id or "")
        with self._approval_lock:
            rec = self._approvals.get(aid)
            if not rec:
                return {"ok": False, "msg": "这次审批已经结束了"}
            rec["ok"] = str(decision) == "allow"
            rec["reason"] = "" if rec["ok"] else "用户点了拒绝"
        rec["event"].set()
        return {"ok": True, "allowed": rec["ok"]}

    # ---------------- 前端现有接口（名字和参数保持不变） ----------------
    def get_config(self):
        # 不下发 key，只告诉前端有没有配
        return self._agent.config.public_view()

    def get_ui_prefs(self):
        """软件界面设置。与人设无关，只保存显示和交互偏好。"""
        try:
            with open(UI_PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_ui_prefs(self, prefs=None):
        """白名单保存，前端传什么都不会顺手改写别的配置。"""
        if not isinstance(prefs, dict):
            return False
        current = self.get_ui_prefs()
        for key in ("character", "motion", "traces"):
            if key in prefs:
                current[key] = bool(prefs[key])
        if str(prefs.get("fontSize", "")) in ("12", "13", "14", "15"):
            current["fontSize"] = str(prefs["fontSize"])
        if prefs.get("sendMode") in ("enter", "ctrl"):
            current["sendMode"] = prefs["sendMode"]
        model = str(prefs.get("model") or "").strip()
        if model:
            current["model"] = model
        try:
            atomic_write_json(UI_PREFS_FILE, current, mode=0o644)
            return True
        except Exception as e:
            _log(f"[界面设置] 保存失败: {e}")
            return False

    def save_config(self, key="", prompt="", glm_key=""):
        """只保存人设。Key 的入场在 add_provider，这里不再收。"""
        if getattr(self, "safe_mode", False):
            return "🔒 安全模式已禁用配置写入。正常启动阿鲤再保存。"
        cfg = self._agent.config
        cfg.set_many(system_prompt=prompt or "")
        cfg.save()
        return "配置已保存"

    # ---------------- 模型接入 ----------------
    def _models_of(self, base_url, key):
        """读服务商的模型列表。读不到就报错，不猜。"""
        url = models_url(base_url)
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=25)
        except requests.Timeout:
            raise RuntimeError("连不上这个地址（超时）。检查 Base URL 和网络")
        except Exception as e:
            raise RuntimeError(f"连不上这个地址：{e}")
        if resp.status_code == 401:
            raise RuntimeError("Key 不对（401）。复制完整，别带空格")
        if resp.status_code == 404:
            raise RuntimeError("这个地址没有 /v1/models 接口。Base URL 可能填错了（一般填到 /v1 为止）")
        if resp.status_code >= 400:
            raise RuntimeError(f"读取模型列表失败（{resp.status_code}）：{resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError("返回的不是 JSON，这个地址多半不是 OpenAI 兼容接口")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("返回里没有模型列表（data），这个地址可能不是 OpenAI 兼容接口")
        models = []
        for it in items:
            mid = (it.get("id") or "").strip() if isinstance(it, dict) else ""
            if mid:
                models.append(mid)
        return models

    def add_provider(self, name="", base_url="", key=""):
        """接入：先真的拉到模型，再落盘。拉不到就不算接入成功。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "error": "🔒 安全模式已禁用密钥写入"}
        base = (base_url or "").strip()
        if not base:
            return {"ok": False, "error": "Base URL 不能为空"}
        if not base.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "Base URL 要以 http:// 或 https:// 开头"}
        k = (key or "").strip()
        if not k:
            return {"ok": False, "error": "API Key 不能为空"}
        try:
            models = self._models_of(base, k)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        if not models:
            return {"ok": False, "error": "这个服务商返回了空的模型列表"}
        cfg = self._agent.config
        entry = cfg.set_provider({"name": name, "base_url": base, "models": models}, k)
        return {"ok": True, "provider": entry, "models": models,
                "message": f"已接入 {entry['name']}，{len(models)} 个模型"}

    def remove_provider(self, provider_id=""):
        pid = (provider_id or "").strip()
        if not pid:
            return {"ok": False, "error": "缺少服务商 id"}
        self._agent.config.remove_provider(pid)
        return {"ok": True}

    def select_model(self, provider_id="", model=""):
        pid = (provider_id or "").strip()
        m = (model or "").strip()
        prov = self._agent.config.provider(pid)
        if prov is None:
            return {"ok": False, "error": "这个服务商不存在了"}
        if m not in (prov.get("models") or []):
            return {"ok": False, "error": "这个模型不在该服务商的列表里"}
        self._agent.config.set_current_model(pid, m)
        return {"ok": True, "provider": pid, "model": m}

    # ---------------- 插件管理（手动开关，默认全关） ----------------
    def get_plugins(self):
        """返回插件列表给「管理插件」面板。不含密钥。"""
        try:
            return self._agent.plugins.view()
        except Exception as e:
            _log(f"[插件] 读取列表失败: {e}")
            return []

    def set_plugin(self, plugin_id, enabled):
        """开/关一个插件。只有 ready 的插件才允许开启。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用插件状态写入。"}
        try:
            ok, msg = self._agent.plugins.set_enabled(plugin_id, bool(enabled))
            return {"ok": ok, "msg": msg}
        except Exception as e:
            _log(f"[插件] 切换失败 {plugin_id}: {e}")
            return {"ok": False, "msg": f"❌ 切换失败：{type(e).__name__}: {e}"}

    # ---------------- 长期记忆的读写 API ----------------
    def _memory_path(self):
        return os.path.join(DATA_DIR, "knowledge", "memory.md")

    def _memory_snapshot(self):
        """返回 (正文, (mtime_ns, size))，用于检测后台抽取是否在我写之前插了条目。"""
        path = self._memory_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            st = os.stat(path)
            return text, (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return "", None
        except Exception as e:
            _log(f"[记忆] 读取失败: {e}")
            return "", None

    def _memory_parse_entries(self, text):
        """解析 memory.md → 条目（ID 方案与内核完全一致，单一来源）。

        以前这里自己算一遍 sha1；内核后来又加了一份——两套实现迟早漂移，
        编辑/删除就会指错条目。现在只剩内核那一份。
        """
        return parse_memory_entries(text)

    def _memory_backup(self, path):
        """写前留一份备份；失败不影响主流程，但会在日志里留痕。"""
        if not os.path.exists(path):
            return
        try:
            backup_dir = os.path.join(DATA_DIR, "knowledge", "backups")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"
            shutil.copy2(path, os.path.join(backup_dir, f"memory-{stamp}.md"))
        except Exception as e:
            _log(f"[记忆] 备份失败（继续写）: {e}")

    def _memory_commit(self, text, expected):
        """带并发检测的原子写：后台抽取若在我读完后插了条目，直接拒绝本次写。

        2026-11-25：整段放进 memory.MEMORY_LOCK —— 内核后台抽取用的是同一把锁。
        只有「校验 + 落盘」都在锁内，两边才算真互斥，而不只是检测到冲突让你重试。
        另外写入前过一遍脱敏：防止有人把 key 粘进记忆面板。
        """
        path = self._memory_path()
        with MEMORY_LOCK:
            if expected is not None:
                try:
                    st = os.stat(path)
                    if (st.st_mtime_ns, st.st_size) != expected:
                        return False, "记忆正在后台更新，请刷新后重试。"
                except OSError:
                    return False, "记忆文件状态已变化，请刷新后重试。"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._memory_backup(path)
                fd, tmp = tempfile.mkstemp(
                    prefix=".memory-", suffix=".tmp", dir=os.path.dirname(path)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(redact(text))
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                return True, ""
            except Exception as e:
                _log(f"[记忆] 写入失败: {e}")
                return False, f"写入失败：{type(e).__name__}: {e}"

    @staticmethod
    def _memory_clean_text(text):
        text = str(text or "").strip()
        if not text:
            return None, "内容不能为空。"
        if len(text) > 500:
            return None, "单条记忆最多 500 字。"
        if "\n" in text or "\r" in text:
            return None, "一条记忆只能写一行。"
        return text, ""

    @staticmethod
    def _memory_clean_kind(kind):
        kind = str(kind or "").strip()
        if kind not in MEMORY_KINDS:
            return None, "类型必须是：身份 / 红线 / 偏好 / 项目 / 决定 / 待办 / 事实 / 愿望。"
        return kind, ""

    def get_memory(self):
        """返回长期记忆正文，只读。保留旧接口，UI 的原始文本折叠区用。"""
        path = self._memory_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        return {"path": path, "text": text}

    def list_memories(self):
        """给记忆面板：结构化条目，不发模型、不花 token。"""
        text, _ = self._memory_snapshot()
        entries = self._memory_parse_entries(text)
        return {
            "path": self._memory_path(),
            "text": text,
            "count": len(entries),
            "entries": entries,
        }

    def add_memory(self, kind, text, date=None):
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用记忆写入。"}
        kind, msg = self._memory_clean_kind(kind)
        if not kind:
            return {"ok": False, "msg": msg}
        text, msg = self._memory_clean_text(text)
        if not text:
            return {"ok": False, "msg": msg}
        date = str(date or time.strftime("%m-%d")).strip()
        if not (re.fullmatch(r"\d{1,2}-\d{1,2}", date)
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
            return {"ok": False, "msg": "日期格式必须是 MM-DD 或 YYYY-MM-DD。"}

        snapshot = self._memory_snapshot()
        current, state = snapshot
        entries = self._memory_parse_entries(current)
        if any(e["kind"] == kind and e["text"] == text for e in entries):
            return {"ok": False, "msg": "这条记忆已经存在。"}

        raw = f"- [{date}][{kind}] {text}"
        new_text = (current.rstrip("\n") + "\n" if current.strip() else "") + raw + "\n"
        ok, msg = self._memory_commit(new_text, state)
        if not ok:
            return {"ok": False, "msg": msg}
        entry = next((e for e in self._memory_parse_entries(new_text) if e["raw"] == raw), None)
        self._ledger("memory/add", memory_kind=kind, chars=len(text),
                     entry_id=(entry or {}).get("id", ""), safe_mode=self.safe_mode)
        return {"ok": True, "entry": entry, "msg": "已记下。"}

    def update_memory(self, memory_id, text, kind=None):
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用记忆写入。"}
        text, msg = self._memory_clean_text(text)
        if not text:
            return {"ok": False, "msg": msg}
        if kind is not None:
            kind, msg = self._memory_clean_kind(kind)
            if not kind:
                return {"ok": False, "msg": msg}

        snapshot = self._memory_snapshot()
        current, state = snapshot
        lines = current.splitlines()
        entry = next((e for e in self._memory_parse_entries(current) if e["id"] == memory_id), None)
        if entry is None:
            return {"ok": False, "msg": "这条记忆已经不存在，刷新后再试。"}
        kind = kind or entry["kind"]
        if not kind:
            return {"ok": False, "msg": "这条记忆缺少类型，请选择类型后再保存。"}
        date = entry["date"] or time.strftime("%m-%d")
        raw = f"- [{date}][{kind}] {text}"
        lines[entry["line"]] = raw
        new_text = "\n".join(lines) + "\n"
        ok, msg = self._memory_commit(new_text, state)
        if not ok:
            return {"ok": False, "msg": msg}
        new_entry = next((e for e in self._memory_parse_entries(new_text) if e["raw"] == raw), None)
        self._ledger("memory/update", memory_kind=kind, chars=len(text),
                     entry_id=(new_entry or {}).get("id", ""), safe_mode=self.safe_mode)
        return {"ok": True, "entry": new_entry, "msg": "已更新。"}

    def delete_memory(self, memory_id):
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用记忆写入。"}
        snapshot = self._memory_snapshot()
        current, state = snapshot
        lines = current.splitlines()
        entry = next((e for e in self._memory_parse_entries(current) if e["id"] == memory_id), None)
        if entry is None:
            return {"ok": False, "msg": "这条记忆已经不存在，刷新后再试。"}
        lines.pop(entry["line"])
        new_text = "\n".join(lines) + ("\n" if lines else "")
        ok, msg = self._memory_commit(new_text, state)
        if not ok:
            return {"ok": False, "msg": msg}
        self._ledger("memory/delete", memory_kind=entry.get("kind", ""), chars=len(entry.get("text", "")),
                     entry_id=memory_id, safe_mode=self.safe_mode)
        return {"ok": True, "msg": "已删除。"}

    def export_memories(self):
        text, _ = self._memory_snapshot()
        return {"ok": True, "path": self._memory_path(), "text": text}

    def get_tools(self):
        """返回工具注册表全量（名称/说明/是否启用），UI 的「工具」入口用。"""
        out = []
        for t in self._agent.registry.all():
            out.append({
                "name": t.name,
                "description": t.description,
                "enabled": bool(t.enabled and self._agent.registry.allowed(t)),
                "safe_mode_blocked": bool(self.safe_mode and not self._agent.registry.allowed(t)),
            })
        return out

    def open_workspace(self):
        """在资源管理器里打开练习场目录。"""
        try:
            d = os.path.join(BASE_DIR, "workspace")
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606  (Windows 本机壳)
            return "已打开练习场目录"
        except Exception as e:
            return f"❌ 打开失败：{e}"

    def open_output(self):
        """在资源管理器里打开输出目录。"""
        d = os.path.join(BASE_DIR, "output")
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606
            return "已打开输出目录"
        except Exception as e:
            return f"❌ 打开失败：{e}"

    # ---------- Python 练习场 ----------
    def _workspace_path(self, name):
        """把前端传来的相对文件名限制在 workspace/ 里，挡住 ../ 乱窜。"""
        root = os.path.join(BASE_DIR, "workspace")
        rel = (name or "").strip().replace("\\", "/")
        parts = rel.split("/")
        if not rel or rel.startswith("/") or ":" in rel or ".." in parts:
            raise ValueError("文件名不合法")
        if not rel.endswith((".py", ".c")):
            rel += ".py"
        path = os.path.normpath(os.path.join(root, rel))
        if os.path.commonpath([root, path]) != root:
            raise ValueError("文件名不合法")
        return path

    @staticmethod
    def _is_tutorial(name):
        """教程目录判断：去掉 ./ 前缀，Windows 下按大小写不敏感比。

        Windows 上 Tutorials/ 和 tutorials/ 是同一个目录，原来用
        startswith("tutorials/") 比大小写，把首字母改成大写、或前面加个
        ./, 就能写坏真教程。os.path.normcase 在 Windows 上还会把 / 换成 \\，
        所以这里自己 lower，不直接拿它比较。
        """
        rel = (name or "").strip().replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if os.name == "nt":
            rel = rel.lower()
        return rel.startswith("tutorials/")

    def tcc_status(self):
        """C 语言工具链是否就绪。前端据此决定要不要弹下载窗。"""
        ready = bool(TCC_EXE and os.path.isfile(TCC_EXE))
        return {"ok": True, "ready": ready, "path": TCC_EXE}

    def install_tcc(self):
        """按需下载 TinyCC 到 runtime 目录。

        只写 runtime（可丢弃目录），不动系统目录、不装服务；失败原样返回原因。
        """
        global TCC_EXE
        if TCC_EXE and os.path.isfile(TCC_EXE):
            return {"ok": True, "ready": True, "path": TCC_EXE}
        root = os.path.join(RUNTIME_DIR, "tcc")
        tmp = os.path.join(root, "_download")
        try:
            os.makedirs(tmp, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": "无法创建下载目录：%s" % e}
        zip_path = os.path.join(tmp, "tcc-win64.zip")
        last_error = ""
        for url in TCC_URLS:
            try:
                with requests.get(url, timeout=60, stream=True) as r:
                    r.raise_for_status()
                    with open(zip_path, "wb") as f:
                        for chunk in r.iter_content(64 * 1024):
                            if chunk:
                                f.write(chunk)
                last_error = ""
                break
            except Exception as e:
                last_error = str(e)
        if last_error:
            return {"ok": False, "error": "下载失败：%s" % last_error}
        unpack = os.path.join(tmp, "unpack")
        shutil.rmtree(unpack, ignore_errors=True)
        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(unpack)
        except Exception as e:
            return {"ok": False, "error": "解压失败：%s" % e}
        src = ""
        for dirpath, _dirs, files in os.walk(unpack):
            if "tcc.exe" in files:
                src = dirpath
                break
        if not src:
            return {"ok": False, "error": "压缩包里没找到 tcc.exe"}
        dst = os.path.join(root, "tcc")
        try:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.move(src, dst)
        except Exception as e:
            return {"ok": False, "error": "安装失败：%s" % e}
        shutil.rmtree(tmp, ignore_errors=True)
        _apply_paths(APP_DIR, DATA_DIR, RUNTIME_DIR)
        ready = bool(TCC_EXE and os.path.isfile(TCC_EXE))
        return {"ok": ready, "ready": ready, "path": TCC_EXE,
                "error": "" if ready else "安装后仍未找到 tcc.exe"}

    def workspace_list(self):
        """列出我的代码和教程（.py / .c，教程本身就是带注释的示例）。"""
        root = os.path.join(BASE_DIR, "workspace")
        tut = os.path.join(root, "tutorials")
        os.makedirs(tut, exist_ok=True)
        files = sorted(n for n in os.listdir(root) if n.endswith((".py", ".c")))
        tutorials = sorted(n for n in os.listdir(tut) if n.endswith((".py", ".c")))
        return {"root": root, "files": files, "tutorials": tutorials}

    def workspace_read(self, name):
        try:
            with open(self._workspace_path(name), "r", encoding="utf-8") as f:
                content = f.read()
            rel = (name or "").strip().replace("\\", "/")
            if self._is_tutorial(name):
                # 翻开教程就算"读过这课"；记不上不能影响看教程
                try:
                    learn_progress.record_lesson_read(PROGRESS_FILE, rel)
                except Exception:
                    pass
            return {"ok": True, "content": content}
        except Exception as e:
            return {"ok": False, "content": f"❌ 读取失败：{e}"}

    def workspace_save(self, name, content):
        rel = (name or "").strip().replace("\\", "/")
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用代码工作区写入。正常启动阿鲤再保存。"}
        if self._is_tutorial(name):
            return {"ok": False, "msg": "🔒 教程已锁定，不能修改。可以点「复制练习」另存一份再改。"}
        try:
            path = self._workspace_path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content or "")
            self._ledger("file/save", name=rel, chars=len(content or ""), safe_mode=self.safe_mode)
            return {"ok": True, "msg": f"已保存 {os.path.basename(path)}"}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 保存失败：{e}"}

    def workspace_run(self, name, content):
        """运行代码。自己的代码先保存；教程直接跑磁盘原版，忽略编辑器内容。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "stdout": "", "stderr": "🔒 安全模式已禁用代码执行。正常启动阿鲤再运行。",
                    "code": -1, "seconds": 0.0}
        self._ledger("code/run/start", name=name, chars=len(content or ""), kind_detail="one-shot")
        is_tutorial = self._is_tutorial(name)
        if not is_tutorial:
            saved = self.workspace_save(name, content)
            if not saved["ok"]:
                self._ledger("code/run/end", name=name, ok=False, code=-1,
                             error=str(saved["msg"])[:160])
                return {"ok": False, "stdout": "", "stderr": saved["msg"], "code": -1}
        try:
            path = self._workspace_path(name)
        except Exception as e:
            self._ledger("code/run/end", name=name, ok=False, code=-1, error=str(e)[:160])
            return {"ok": False, "stdout": "", "stderr": str(e), "code": -1}
        # Python 用带控制台的 python.exe；C 用 TinyCC 直接编译并运行（-run）。
        try:
            cmd = self._workspace_command(path)
        except Exception as e:
            self._ledger("code/run/end", name=name, ok=False, code=-1, error=str(e)[:160])
            return {"ok": False, "code": -1, "stdout": "", "stderr": f"❌ {e}", "seconds": 0.0}
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=os.path.dirname(path),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, env=env,
            )
            out = (proc.stdout or "")[:12000]
            err = (proc.stderr or "")[:12000]
            result = {"ok": True, "code": proc.returncode, "stdout": out, "stderr": err,
                      "seconds": round(time.time() - started, 2)}
            self._ledger("code/run/end", name=name, ok=True, code=result["code"],
                         seconds=result["seconds"], stdout_chars=len(out), stderr_chars=len(err))
            return result
        except subprocess.TimeoutExpired:
            result = {"ok": True, "code": -1, "stdout": "",
                      "stderr": "⏱ 运行超过 15 秒被强制停了。检查是不是死循环；练习场不支持 input() 交互输入。",
                      "seconds": round(time.time() - started, 2)}
            self._ledger("code/run/end", name=name, ok=False, code=-1,
                         seconds=result["seconds"], timeout=True)
            return result
        except Exception as e:
            result = {"ok": False, "code": -1, "stdout": "", "stderr": f"❌ 运行失败：{e}",
                      "seconds": round(time.time() - started, 2)}
            self._ledger("code/run/end", name=name, ok=False, code=-1,
                         seconds=result["seconds"], error=str(e)[:160])
            return result

    # ---------- 交互式运行：支持 input() ----------
    def _workspace_python(self):
        """阿鲤自己是 pythonw 启动的，跑代码必须换回带控制台的 python.exe。"""
        py = os.path.join(os.path.dirname(sys.executable), "python.exe")
        return py if os.path.exists(py) else sys.executable

    def _workspace_command(self, path):
        """按扩展名决定运行方式：.py 走解释器，.c 走 TinyCC 的 -run（编译后立即执行）。

        tcc -run 会继承 stdin/stdout 管道，所以 scanf() 也能走练习场的交互输入；
        编译错误原样进 stderr，退出码非 0，「阿鲤讲解」可以直接接着分析。
        """
        if path.lower().endswith(".c"):
            if not os.path.exists(TCC_EXE):
                raise RuntimeError("运行 C 需要 TinyCC，但没找到：" + TCC_EXE)
            return [TCC_EXE, "-run", path]
        return [self._workspace_python(), "-u", path]

    def workspace_run_interactive(self, name, content):
        """启动交互式运行，立即返回 session_id。

        前端拿到 id 后每 200ms 调 workspace_poll 取输出，遇到 input() 就调
        workspace_input 回传一行。管道模式对普通 input() 够用，不用上 PTY。
        """
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用代码执行。正常启动阿鲤再运行。"}
        self._ledger("code/run/start", name=name, chars=len(content or ""), kind_detail="interactive")
        is_tutorial = self._is_tutorial(name)
        if not is_tutorial:
            saved = self.workspace_save(name, content)
            if not saved["ok"]:
                return {"ok": False, "msg": saved["msg"]}
        try:
            path = self._workspace_path(name)
        except Exception as e:
            return {"ok": False, "msg": str(e)}
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.Popen(
                self._workspace_command(path),
                cwd=os.path.dirname(path),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=env,
            )
        except Exception as e:
            return {"ok": False, "msg": f"❌ 启动失败：{e}"}

        with self._pyruns_lock:
            self._pyrun_seq += 1
            sid = f"{int(time.time() * 1000)}-{self._pyrun_seq}"
            state = {
                "proc": proc, "name": name,
                "out_buf": [], "err_buf": [], "out_offset": 0, "err_offset": 0,
                "out_chars": 0, "err_chars": 0, "cap_hit": False,
                "started": time.time(), "done": False, "code": None,
                "pumps": [], "ledger_end": False,
            }
            self._pyruns[sid] = state

        def _pump(pipe, tag):
            try:
                while True:
                    ch = pipe.read(1)
                    if not ch:
                        break
                    state[tag + "_buf"].append(ch)
                    state[tag + "_chars"] += 1
                    # 无限 print 死循环会在 60 秒看门狗杀掉前把内存灌爆，这里先截断
                    if state[tag + "_chars"] > 60000 and not state["cap_hit"]:
                        state["cap_hit"] = True
                        state[tag + "_buf"].append("\n…输出过长，已截断并停止。\n")
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
            except Exception:
                pass

        def _watch():
            while True:
                if state["done"] or proc.poll() is not None:
                    break
                if time.time() - state["started"] > 60:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    state["err_buf"].append("\n⏱ 运行超过 60 秒被强制停了。检查是不是死循环，或 input() 一直没等到输入。\n")
                    break
                time.sleep(0.3)

        t_out = threading.Thread(target=_pump, args=(proc.stdout, "out"), daemon=True)
        t_err = threading.Thread(target=_pump, args=(proc.stderr, "err"), daemon=True)
        t_watch = threading.Thread(target=_watch, daemon=True)
        state["pumps"] = [t_out, t_err]
        t_out.start()
        t_err.start()
        t_watch.start()
        return {"ok": True, "session_id": sid, "seconds": 0.0}

    def workspace_poll(self, session_id):
        """拿一次新输出。会话结束后把状态清掉，前端最后一拍能拿到完整输出。"""
        sid = str(session_id)
        with self._pyruns_lock:
            state = self._pyruns.get(sid)
        if not state:
            return {"ok": False, "msg": "会话不存在或已结束", "done": True}
        proc = state["proc"]
        rc = proc.poll()
        if rc is None:
            new_out = "".join(state["out_buf"][state["out_offset"]:])
            new_err = "".join(state["err_buf"][state["err_offset"]:])
            state["out_offset"] = len(state["out_buf"])
            state["err_offset"] = len(state["err_buf"])
            return {"ok": True, "running": True, "done": False, "code": None,
                    "stdout": new_out, "stderr": new_err,
                    "seconds": round(time.time() - state["started"], 2),
                    "name": state["name"], "input_open": True}
        # 进程已退出：等两个泵线程把剩余输出读完，再一次性交还
        for t in state["pumps"]:
            t.join(timeout=1.0)
        new_out = "".join(state["out_buf"][state["out_offset"]:])
        new_err = "".join(state["err_buf"][state["err_offset"]:])
        state["out_offset"] = len(state["out_buf"])
        state["err_offset"] = len(state["err_buf"])
        result = {"ok": True, "running": False, "done": True, "code": rc,
                  "stdout": new_out, "stderr": new_err,
                  "seconds": round(time.time() - state["started"], 2),
                  "name": state["name"], "input_open": False}
        if not state.get("ledger_end"):
            state["ledger_end"] = True
            self._ledger("code/run/end", name=state["name"], ok=(rc == 0), code=rc,
                         seconds=result["seconds"],
                         stdout_chars=state.get("out_chars", 0),
                         stderr_chars=state.get("err_chars", 0),
                         cap_hit=state.get("cap_hit", False))
        with self._pyruns_lock:
            self._pyruns.pop(sid, None)
        return result

    def workspace_input(self, session_id, text):
        """给正在等 input() 的程序回一行输入。"""
        sid = str(session_id)
        with self._pyruns_lock:
            state = self._pyruns.get(sid)
        if not state:
            return {"ok": False, "msg": "会话不存在或已结束"}
        proc = state["proc"]
        if proc.poll() is not None:
            return {"ok": False, "msg": "程序已经结束了"}
        if not isinstance(text, str):
            text = str(text or "")
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            # input() 不会回显，把用户输入补到输出里，方便对照
            state["out_buf"].append(text + "\n")
            state["out_chars"] += len(text) + 1
            # 每次输入都重置空闲计时，别让 60 秒看门狗在用户想题时误杀
            state["started"] = time.time()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 输入失败：{e}"}

    def workspace_kill(self, session_id):
        """前端关窗/重跑时，主动停掉还在跑的进程。"""
        sid = str(session_id)
        with self._pyruns_lock:
            state = self._pyruns.get(sid)
        if not state:
            return {"ok": False, "msg": "会话不存在"}
        try:
            if state["proc"].poll() is None:
                state["proc"].kill()
        except Exception:
            pass
        with self._pyruns_lock:
            self._pyruns.pop(sid, None)
        return {"ok": True}

    # ---------- 错误讲解：本地先定位，再把报错丢给阿鲤 ----------
    def _py_error_hint(self, stderr):
        """从 traceback 里本地扒出行号和异常类型，零 token，先给个硬线索。"""
        hint = {"line": None, "error": "", "quick": ""}
        err = (stderr or "")
        m = re.search(r'File "([^"]+)", line (\d+)', err)
        if m:
            try:
                hint["line"] = int(m.group(2))
            except Exception:
                pass
        m2 = re.search(r'([A-Za-z_][A-Za-z0-9_]*Error|'
                       r'[A-Za-z_][A-Za-z0-9_]*Exception|'
                       r'KeyboardInterrupt|TimeoutError)', err)
        if m2:
            hint["error"] = m2.group(1)
        # 新手最常踩的坑，能本地说清就不必花一次模型钱
        table = {
            "NameError": "这个名字还没定义：要么拼错，要么在使用之前没赋值。",
            "SyntaxError": "语法写错了：通常是括号没配对、冒号漏了、或缩进乱了。",
            "IndentationError": "缩进不一致：同一段代码的空白开头不统一。",
            "TabError": "混用了 Tab 和空格缩进，Python 会直接报错。",
            "TypeError": "类型对不上：比如字符串和数字相加，或函数参数个数/类型不对。",
            "IndexError": "下标越界：列表里没有这个位置，注意索引从 0 开始。",
            "KeyError": "字典里没有这个键：先看键名是否写对、有没有先赋值。",
            "ValueError": "值不合适：比如 int() 转不了非数字字符串。",
            "ZeroDivisionError": "除以 0 了：分母为 0 时没有先判断。",
            "AttributeError": "对象没有这个属性或方法：先确认类型对不对、名字是否拼错。",
            "FileNotFoundError": "找不到文件：路径不对，或文件根本不在那个目录。",
            "ModuleNotFoundError": "模块没安装或名字写错：常见 import 写错。",
        }
        for k, v in table.items():
            if hint["error"] and k in hint["error"]:
                hint["quick"] = v
                break
        return hint

    def workspace_coach(self, name, content, result=None):
        """让阿鲤讲解这次报错：先说错在哪、为什么错、最小改法、自测题。

        不写主聊天历史，只在靶场面板里显示；跑成功时不触发，报错才花钱。
        走当前接入的模型。没接入模型时直接返回「先接入 API」。
        """
        result = result or {}
        code = (content or "").strip()
        stderr = (result.get("stderr") or "").strip()
        stdout = (result.get("stdout") or "").strip()
        code_num = result.get("code")
        if code_num == 0 and not stderr:
            return {"ok": False, "msg": "这次没有报错，不需要讲解。"}
        # 交互式/普通运行超时：看门狗写进 stderr 的人话，本地就能答，不用模型
        if "运行超过" in stderr and "秒" in stderr:
            return {"ok": True, "line": None, "error": "运行超时",
                    "quick": "代码跑太久被强制停了：检查是不是死循环，或 input() 一直没等到输入。",
                    "text": "", "provider": "", "tokens": 0}
        hint = self._py_error_hint(stderr)
        # 超时被强停：没有报错也没有输出，但退出码不是 0
        if not stderr and not stdout and code_num != 0:
            return {"ok": True, "line": None, "error": "运行超时",
                    "quick": "代码被 15 秒强停了：检查是不是死循环，或 while 条件一直为真。",
                    "text": "", "provider": "", "tokens": 0}

        numbered = [f"{i:>4}| {line}" for i, line in enumerate((code or "").splitlines(), 1)]
        snippet = "\n".join(numbered)[:6000]
        err_snippet = stderr[:2500]
        out_snippet = stdout[:800]

        lang = "c" if (name or "").lower().endswith(".c") else "python"
        lang_name = "C" if lang == "c" else "Python"
        sys_msg = (
            f"你是「阿鲤」，一个毒舌但真心教人的 {lang_name} 私教。"
            "学生把代码和报错发给你，你要直接指出：1) 错在哪一行、2) 为什么会这样、"
            "3) 给出最小的改法（只贴改后的关键代码，不整段重写）、"
            "4) 出一个只用一个词或一行就能答的自测题。"
            "别绕弯子，别长篇大论，用学生看得懂的中文。"
            "如果代码其实能跑、只是结果不对，就按结果分析。"
        )
        user_msg = f"我的 {lang_name} 代码（带行号）如下：\n```{lang}\n{snippet}\n```\n\n"
        if err_snippet:
            user_msg += f"运行报错（stderr）：\n{err_snippet}\n\n"
        if out_snippet:
            user_msg += f"运行输出（stdout）：\n{out_snippet}\n\n"
        user_msg += "请按上面四步讲解。"

        messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
        text, provider, tokens = "", "", 0
        try:
            resp = self._agent.llm.chat(messages, temperature=0.25, timeout=120)
        except LLMError as e:
            return {"ok": True, "line": hint["line"], "error": hint["error"],
                    "quick": hint["quick"], "text": f"（模型没连上，先看这条）{e}",
                    "provider": "", "tokens": 0}
        except Exception as e:
            return {"ok": True, "line": hint["line"], "error": hint["error"],
                    "quick": hint["quick"], "text": f"讲解暂时没生成：{e}",
                    "provider": "", "tokens": 0}
        text = resp.get("content") or ""
        provider = resp.get("provider") or ""
        usage = resp.get("usage") or {}
        tokens = int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
        return {"ok": True, "line": hint["line"], "error": hint["error"],
                "quick": hint["quick"], "text": text, "provider": provider, "tokens": tokens}

    # ---------- 实战模式：题目 + 代码 + 验收 ----------
    def _practice_cases(self):
        """读取实战题库。返回给前端的只有题目和起始代码，不包含答案和验收规则。"""
        try:
            with open(PRACTICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cases = data.get("cases") if isinstance(data, dict) else None
            if not isinstance(cases, list):
                return []
            return [c for c in cases if isinstance(c, dict) and c.get("id")]
        except FileNotFoundError:
            # 空数据目录启动时题库本来就没有，不算故障，别刷日志
            return []
        except Exception as e:
            _log(f"[实战] 题库读取失败: {e}")
            return []

    def _practice_case(self, case_id):
        for case in self._practice_cases():
            if str(case.get("id")) == str(case_id):
                return case
        return None

    def _practice_voices(self):
        """语音讲解清单：id → {text, audio, exists}。缺文件不影响做题，只是按钮置灰。"""
        try:
            with open(PRACTICE_VOICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return {}
            out = {}
            voice_name = (data.get("voice") or "").strip() if isinstance(data, dict) else ""
            for it in items:
                if not isinstance(it, dict):
                    continue
                cid = str(it.get("id") or "").strip()
                if not cid:
                    continue
                rel = "%s/%s.wav" % (PRACTICE_VOICE_REL, cid)
                abs_path = os.path.join(APP_DIR, rel.replace("/", os.sep))
                out[cid] = {
                    "name": voice_name,
                    "text": it.get("text") or "",
                    "audio": rel,
                    # 前端页面由本机 HTTP 服务加载，相对路径才通得过 URL 安全检查
                    "url": rel if os.path.exists(abs_path) else "",
                    "exists": os.path.exists(abs_path),
                }
            return out
        except Exception as e:
            _log(f"[实战] 语音稿读取失败: {e}")
            return {}

    def practice_voice_data(self, case_id):
        """兜底：把讲解音频以 data URL 给前端。data_dir 与页面目录不一致时用。"""
        name = str(case_id or "").strip()
        if not re.match(r"^[A-Za-z0-9_\-]{1,40}$", name):
            return {"ok": False, "error": "题号不合法"}
        path = os.path.join(APP_DIR, PRACTICE_VOICE_REL.replace("/", os.sep), name + ".wav")
        if not os.path.isfile(path):
            return {"ok": False, "error": "这题还没有语音"}
        if os.path.getsize(path) > 12 * 1024 * 1024:
            return {"ok": False, "error": "音频过大"}
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return {"ok": True, "data": "data:audio/wav;base64," + b64}

    def tutorial_lecture(self, name):
        """某一课的语音小节清单。前端按 name 找课，找不到就返回 ok=False，静默隐藏讲课条。"""
        want = str(name or "").replace("\\", "/").strip()
        try:
            with open(TUTORIAL_VOICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"ok": False, "sections": []}
        for les in (data.get("lessons") or []):
            if str(les.get("file") or "").replace("\\", "/") != want:
                continue
            key = str(les.get("key") or "")
            sections = []
            for sec in (les.get("sections") or []):
                sid = str(sec.get("id") or "")
                rel = "%s/%s/%s.wav" % (TUTORIAL_VOICE_REL, key, sid)
                exists = os.path.isfile(os.path.join(APP_DIR, rel.replace("/", os.sep)))
                sections.append({
                    "id": sid,
                    "line": int(sec.get("line") or 1),
                    "end": int(sec.get("end") or sec.get("line") or 1),
                    "title": sec.get("title") or "",
                    "text": sec.get("text") or "",
                    "url": rel if exists else "",
                    "exists": exists,
                })
            return {"ok": bool(sections), "voice": data.get("voice") or "", "title": les.get("title") or "", "sections": sections}
        return {"ok": False, "sections": []}

    def tts_speak(self, text=None, key=""):
        """没有自带音频时，用系统内置语音现场合成，返回 data URL。

        仓库不带 241 MB 的语音包，第一次点讲解会走这里；同一段文字按内容哈希
        缓存成 wav，第二次点开直接读文件。完全离线，不往任何服务器发字。
        """
        body = (text or "").strip()
        if not body:
            return {"ok": False, "error": "这段没有讲解稿"}
        body = body[:6000]
        tag = hashlib.md5((TTS_BUILTIN_VOICE + "|" + body).encode("utf-8")).hexdigest()[:16]
        try:
            os.makedirs(TTS_CACHE_DIR, exist_ok=True)
        except Exception:
            pass
        out = os.path.join(TTS_CACHE_DIR, tag + ".wav")
        if not os.path.isfile(out) or os.path.getsize(out) < 1024:
            tmp = out + ".part"
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
                _sapi_synthesize(body, tmp)
                os.replace(tmp, out)
            except Exception as e:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                _log(f"[语音] 合成失败：{e}")
                return {"ok": False, "error": str(e)}
        try:
            if os.path.getsize(out) > 40 * 1024 * 1024:
                return {"ok": False, "error": "合成音频过大"}
            with open(out, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            return {"ok": False, "error": f"读取合成音频失败：{e}"}
        return {"ok": True, "data": "data:audio/wav;base64," + b64, "engine": "sapi"}

    def tts_info(self):
        """语音面板用：当前引擎、有没有自带音频、想自定义该放哪。"""
        custom = 0
        for rel in (TUTORIAL_VOICE_REL, PRACTICE_VOICE_REL):
            root = os.path.join(APP_DIR, rel.replace("/", os.sep))
            for _dp, _dn, fns in os.walk(root):
                custom += sum(1 for fn in fns if fn.lower().endswith(".wav"))
        return {
            "ok": True,
            "engine": "Windows 内置语音（SAPI5）",
            "voice": TTS_BUILTIN_VOICE,
            "free": True,
            "offline": True,
            "custom_wav": custom,
            "voice_dir": os.path.join(APP_DIR, TUTORIAL_VOICE_REL.replace("/", os.sep)),
            "practice_dir": os.path.join(APP_DIR, PRACTICE_VOICE_REL.replace("/", os.sep)),
            "cache_dir": TTS_CACHE_DIR,
        }

    def practice_state(self):
        """实战题的历史状态：通过没通过、错了几次、下次复习、写过的判断。

        前端进实战时拿它点亮列表。之前这些只存在内存里，重启后题目全都变回
        「没做过」，跟顶部「通过 20/74」自相矛盾。
        """
        state = {"cases": {}, "answers": {}}
        try:
            data = learn_progress.load(PROGRESS_FILE)
            for cid, item in (data.get("cases") or {}).items():
                if isinstance(item, dict):
                    state["cases"][str(cid)] = {
                        "passed": bool(item.get("pass_at")),
                        "attempts": int(item.get("attempts") or 0),
                        "fails": int(item.get("fails") or 0),
                        "next_review": str(item.get("next_review") or ""),
                    }
        except Exception:
            pass
        try:
            with open(PRACTICE_ANSWERS_FILE, "r", encoding="utf-8") as f:
                answers = json.load(f)
            if isinstance(answers, dict):
                state["answers"] = {str(k): str(v) for k, v in answers.items()}
        except Exception:
            pass
        return {"ok": True, **state}

    def practice_answer_save(self, case_id, answer):
        """记住这道题写过的判断。写盘失败不阻断做题，下次还能重写。"""
        cid = str(case_id or "").strip()
        if not re.match(r"^[A-Za-z0-9_\-]{1,40}$", cid):
            return {"ok": False, "msg": "题号不合法"}
        try:
            data = {}
            try:
                with open(PRACTICE_ANSWERS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = {str(k): str(v) for k, v in loaded.items()}
            except Exception:
                data = {}
            text = str(answer or "")[:2000]
            if text.strip():
                data[cid] = text
            else:
                data.pop(cid, None)
            os.makedirs(os.path.dirname(PRACTICE_ANSWERS_FILE), exist_ok=True)
            tmp = PRACTICE_ANSWERS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, PRACTICE_ANSWERS_FILE)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": f"判断没存上：{e}"}

    def practice_list(self):
        voices = self._practice_voices()
        cases = []
        for case in self._practice_cases():
            cid = str(case.get("id"))
            cases.append({
                "id": case.get("id"),
                "level": case.get("level") or 0,
                "lang": case.get("lang") or "python",
                "topic": case.get("topic") or "",
                "title": case.get("title") or "未命名实战",
                "question": case.get("question") or "",
                "description": case.get("description") or "",
                "starter": case.get("starter") or "",
                "voice": voices.get(cid) or {},
            })
        return {"ok": True, "cases": cases}

    def practice_submit(self, case_id, answer, code):
        """验收一次实战提交。只返回通过/不通过，不把预期答案回传给前端。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用实战代码执行。正常启动阿鲤再提交。"}
        self._ledger("practice/submit/start", case_id=case_id, chars=len(code or ""))
        answer = (answer or "").strip()
        if not answer:
            return {"ok": False, "msg": "先在左边写下你的判断，再提交验收。"}

        case = self._practice_case(case_id)
        if not case:
            return {"ok": False, "msg": "找不到这道实战题。"}

        code = code or ""
        lang = "c" if (case.get("lang") or "").lower() == "c" else "python"
        ext = ".c" if lang == "c" else ".py"
        # 保存一份尝试，切题或重开靶场时还能找回来；保存失败不阻断验收。
        try:
            self.workspace_save(f"practice/{case_id}{ext}", code)
        except Exception:
            pass

        check = case.get("check") or {}
        # 验收失败时给学生看的「哪条没过」，只放断言消息，不放断言表达式
        reason = {}
        started = time.time()
        try:
            with tempfile.TemporaryDirectory(prefix="ali_practice_") as tmp:
                code_path = os.path.join(tmp, f"{case_id}{ext}")
                with open(code_path, "w", encoding="utf-8") as f:
                    f.write(code)
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"

                if lang == "c":
                    if not os.path.exists(TCC_EXE):
                        return {"ok": False, "msg": "运行 C 需要 TinyCC，但没找到：" + TCC_EXE}
                    if check.get("kind") == "c_function":
                        # 不按 main 的打印结果验收，直接把学生的源文件 include 进来，
                        # 用隐藏测试调用函数；这样改输出文本骗不过去。
                        test_code = str(check.get("test") or "").strip()
                        if not test_code:
                            return {"ok": False, "msg": "这道题没有配置 C 验收规则。"}
                        harness = (
                            "#include <stdio.h>\n"
                            "#include <stdlib.h>\n"
                            "#include <string.h>\n"
                            "#define main student_main\n"
                            f'#include "{os.path.basename(code_path)}"\n'
                            "#undef main\n"
                            "int main(void) {\n"
                            "    setbuf(stdout, NULL);\n"
                            f"{textwrap.indent(test_code, '    ')}\n"
                            # 必须显式 return 0：main 落空时 tcc 的退出码是垃圾值（实测 3），
                            # 会把所有做对的 C 题判成失败。
                            "    return 0;\n"
                            "}\n"
                        )
                        check_path = os.path.join(tmp, "_practice_check.c")
                        with open(check_path, "w", encoding="utf-8") as f:
                            f.write(harness)
                        proc = subprocess.run(
                            [TCC_EXE, "-run", check_path],
                            cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=15, env=env,
                        )
                        out = (proc.stdout or "")[:12000]
                        err = (proc.stderr or "")[:12000]
                        passed = proc.returncode == 0
                    else:
                        proc = subprocess.run(
                            [TCC_EXE, "-run", code_path],
                            cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=15, env=env,
                        )
                        out = (proc.stdout or "")[:12000]
                        err = (proc.stderr or "")[:12000]
                        expected = str(check.get("expected") or "").strip()
                        passed = proc.returncode == 0 and out.strip() == expected
                else:
                    test_code = str(check.get("test") or "").strip()
                    if not test_code:
                        return {"ok": False, "msg": "这道题没有配置验收规则。"}
                    # 用 __practice__ 而不是 __main__ 执行：学生代码里的
                    # `if __name__ == "__main__": unittest.main()` 不会启动，
                    # 否则 unittest 收尾时会 sys.exit，把验收进程一起带走。
                    # 用 exec 而不是 runpy，是为了 SystemExit 时命名空间仍然保留。
                    wrapper = (
                        "import sys\n"
                        f"ns = {{'__name__': '__practice__', '__file__': {code_path!r}}}\n"
                        f"__code = compile(open({code_path!r}, encoding='utf-8').read(), {code_path!r}, 'exec')\n"
                        "try:\n"
                        "    exec(__code, ns)\n"
                        "except SystemExit as __exit:\n"
                        "    if __exit.code not in (0, None):\n"
                        "        raise\n"
                        "globals().update(ns)\n"
                        "try:\n"
                        f"{textwrap.indent(test_code, '    ')}\n"
                        "except Exception as __err:\n"
                        "    import json as __json\n"
                        "    __info = {'type': type(__err).__name__, 'msg': str(__err)[:300]}\n"
                        "    print('__PRACTICE_CHECK__:FAIL:' + __json.dumps(__info, ensure_ascii=True))\n"
                        "    sys.exit(3)\n"
                        "print('__PRACTICE_CHECK__:PASS')\n"
                    )
                    check_path = os.path.join(tmp, "_practice_check.py")
                    with open(check_path, "w", encoding="utf-8") as f:
                        f.write(wrapper)
                    proc = subprocess.run(
                        [self._workspace_python(), "-u", check_path],
                        cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=15, env=env,
                    )
                    out = (proc.stdout or "")[:12000]
                    err = (proc.stderr or "")[:12000]
                    if "__PRACTICE_CHECK__:FAIL:" in out:
                        payload = out.split("__PRACTICE_CHECK__:FAIL:", 1)[1].split("\n", 1)[0].strip()
                        try:
                            reason = json.loads(payload)
                        except Exception:
                            reason = {"msg": payload[:200]} if payload else {}
                    passed = "__PRACTICE_CHECK__:PASS" in out
                    out = "\n".join(
                        line for line in out.splitlines()
                        if "__PRACTICE_CHECK__" not in line
                    )

                result = {
                    "ok": True, "passed": bool(passed),
                    "stdout": out, "stderr": err,
                    "code": proc.returncode,
                    "reason": reason,
                    "seconds": round(time.time() - started, 2),
                }
                self._ledger("practice/submit/end", case_id=case_id,
                             passed=bool(passed), code=proc.returncode,
                             seconds=result["seconds"])
                # 记一次实战结果 → 排下次复习（错了明天，错过又过 3 天后）
                try:
                    learn_progress.record_practice(PROGRESS_FILE, case_id, bool(passed))
                except Exception:
                    pass
                return result
        except subprocess.TimeoutExpired:
            self._ledger("practice/submit/end", case_id=case_id, passed=False,
                         code=-1, timeout=True)
            try:
                learn_progress.record_practice(PROGRESS_FILE, case_id, False)
            except Exception:
                pass
            return {"ok": True, "passed": False, "stdout": "",
                    "stderr": "⏱ 提交运行超过 15 秒，检查是不是死循环。",
                    "code": -1, "seconds": round(time.time() - started, 2)}
        except Exception as e:
            self._ledger("practice/submit/end", case_id=case_id, passed=False,
                         error=str(e)[:160])
            return {"ok": False, "msg": f"验收失败：{e}"}

    def _run_lesson_file(self, name):
        """跑一遍教程文件（自检用）。返回 (path, 标准化结果)。"""
        path = self._workspace_path(name)
        cmd = self._workspace_command(path)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return path, lesson_quiz.run(cmd, cwd=os.path.dirname(path), timeout=12, env=env)

    def tutorial_quiz(self, name):
        """每课自检的题目（不含答案）。答案来自真实运行，零 token。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用代码执行。"}
        if not self._is_tutorial(name):
            return {"ok": False, "msg": "自检只对教程开放。"}
        try:
            _, res = self._run_lesson_file(name)
        except Exception as e:
            return {"ok": False, "msg": f"自检失败：{e}"}
        ok, why = lesson_quiz.usable(res)
        if not ok:
            return {"ok": False, "msg": why}
        questions = lesson_quiz.build_questions(res["stdout"])
        if not questions:
            return {"ok": False, "msg": "这一课没有可预测的输出。"}
        self._ledger("quiz/start", name=name, questions=len(questions))
        return {"ok": True, "file": name, "questions": lesson_quiz.public(questions)}

    def tutorial_quiz_check(self, name, answers):
        """对照答案：再跑一遍，逐题判，并记成绩。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用代码执行。"}
        try:
            _, res = self._run_lesson_file(name)
        except Exception as e:
            return {"ok": False, "msg": f"自检失败：{e}"}
        ok, why = lesson_quiz.usable(res)
        if not ok:
            return {"ok": False, "msg": why}
        out = lesson_quiz.check(res["stdout"], answers or {})
        try:
            learn_progress.record_quiz(PROGRESS_FILE, name, out["score"], out["total"])
        except Exception:
            pass
        self._ledger("quiz/end", name=name, score=out["score"], total=out["total"])
        return {"ok": True, **out}

    def _lesson_catalog(self):
        """课程表：扫教程目录，课名优先用语音稿里的标题。"""
        return learn_progress.catalog(
            os.path.join(BASE_DIR, "workspace", "tutorials"),
            TUTORIAL_VOICE_FILE,
        )

    def _lesson_steps_data(self):
        return lesson_mod.load(LESSON_STEPS_FILE)

    def lesson_guide(self, key):
        """分步导学：下发某课的所有步骤（含提示，隐藏答案）。"""
        data = self._lesson_steps_data()
        pub = lesson_mod.get(data, key)
        if not pub:
            return {"ok": False, "msg": "这一课还没有分步导学，先看教程正文。"}
        try:
            pub["done"] = learn_progress.get_steps(PROGRESS_FILE, key)
        except Exception:
            pub["done"] = {}
        pub["ok"] = True
        return pub

    def lesson_step_check(self, key, step_id, payload):
        """验收一步。通过后记进度；全部步骤通过时这课算学完。"""
        data = self._lesson_steps_data()
        les = (data.get("lessons") or {}).get(str(key))
        if not les:
            return {"ok": False, "msg": "找不到这一课的分步导学。"}
        step = None
        for s in les.get("steps") or []:
            if str(s.get("id")) == str(step_id):
                step = s
                break
        if step is None:
            return {"ok": False, "msg": "找不到这一步。"}
        if not os.path.exists(TCC_EXE):
            return {"ok": False, "msg": "运行 C 需要 TinyCC，但没找到：" + TCC_EXE}

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        workspace_dir = os.path.join(DATA_DIR, "workspace")

        def run_c(code, test=None):
            return lesson_mod.run_c(str(code), TCC_EXE, workspace_dir, test, env=env)

        def run_py(code, test=None):
            return lesson_mod.run_py(str(code), self._workspace_python(), workspace_dir,
                                     test, env=env)

        out = lesson_mod.judge(step, payload or {}, run_c, run_py)
        if out.get("passed"):
            try:
                guide = learn_progress.record_step(PROGRESS_FILE, key, step_id, True)
                total = len(les.get("steps") or [])
                if total and len([1 for v in guide.values() if v]) >= total:
                    learn_progress.mark_lesson_done(PROGRESS_FILE, str(key), True)
            except Exception:
                pass
        self._ledger("lesson/step", key=key, step=step_id, passed=bool(out.get("passed")))
        return {"ok": True, **out}

    def progress_info(self):
        """靶场顶部的学习状态：继续哪课、下一课、到期复习、统计。"""
        try:
            data = learn_progress.summary(
                PROGRESS_FILE, self._lesson_catalog(), self._practice_cases()
            )
            data["ok"] = True
            return data
        except Exception as e:
            return {"ok": False, "msg": f"进度读取失败：{e}"}

    def mark_lesson_done(self, key, done=True):
        """手动标记/取消「这课学完了」。

        先确认这一课真的存在：以前随便传个不存在的课号也返回成功，
        进度就变成「完成 1 / 总数 0」这种鬼数字。
        """
        key = str(key or "").strip()
        valid = set()
        try:
            tut = os.path.join(BASE_DIR, "workspace", "tutorials")
            for n in os.listdir(tut):
                k = learn_progress.lesson_key(n)
                if k:
                    valid.add(k)
        except Exception:
            pass
        if not valid:
            return {"ok": False, "msg": "教程目录里还没有课，没法标记。"}
        if key not in valid:
            return {"ok": False, "msg": f"没有这一课：{key}"}
        try:
            learn_progress.mark_lesson_done(PROGRESS_FILE, key, bool(done))
            return self.progress_info()
        except Exception as e:
            return {"ok": False, "msg": f"标记失败：{e}"}

    def practice_review(self, case_id, answer, code, passed=False, stdout="", stderr=""):
        """学生提交后，讲解判断和代码。未通过时只给下一步提示，不直接贴完整答案。"""
        case = self._practice_case(case_id)
        if not case:
            return {"ok": False, "msg": "找不到这道实战题。"}
        answer = (answer or "").strip()
        if not answer:
            return {"ok": False, "msg": "先在左边写下你的判断，再让我讲。"}

        status = "通过" if passed else "未通过"
        lang = "c" if (case.get("lang") or "").lower() == "c" else "python"
        lang_name = "C" if lang == "c" else "Python"
        numbered = [f"{i:>4}| {line}" for i, line in enumerate((code or "").splitlines(), 1)]
        snippet = "\n".join(numbered)[:6000]

        sys_msg = (
            "你是「阿鲤」，一个毒舌但真心教人的编程私教。"
            "这是实战题，学生已经先写下判断，再提交代码。你要按顺序回答："
            "1) 他的判断有没有说到关键；2) 代码为什么对/为什么不对；"
            "3) 如果未通过，只给最关键的下一步提示，不要直接贴完整答案；"
            "4) 如果通过，追问一个边界情况。用短句，不超过 8 行，中文。"
        )
        user_msg = (
            f"实战题：{case.get('title')}\n"
            f"问题：{case.get('question')}\n"
            f"题目描述：{case.get('description')}\n"
            f"验收结果：{status}\n"
            f"学生的判断：{answer}\n\n"
            f"他的 {lang_name} 代码（带行号）：\n```{lang}\n{snippet}\n```\n"
            f"运行输出：\n{(stdout or '')[:1200]}\n"
            f"运行报错：\n{(stderr or '')[:1200]}\n"
        )
        messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
        text, provider, tokens = "", "", 0
        try:
            resp = self._agent.llm.chat(messages, temperature=0.25, timeout=120)
        except LLMError as e:
            text = f"（模型没连上，先看本地验收结果）{e}"
            return {"ok": True, "text": text, "provider": "", "tokens": 0, "passed": bool(passed)}
        except Exception as e:
            return {"ok": True, "text": f"讲解暂时没生成：{e}", "provider": "", "tokens": 0,
                    "passed": bool(passed)}
        text = resp.get("content") or ""
        provider = resp.get("provider") or ""
        usage = resp.get("usage") or {}
        tokens = int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
        return {"ok": True, "text": text, "provider": provider, "tokens": tokens, "passed": bool(passed)}

    def save_drawing(self, data_url, image_format="png"):
        """把画板合成后的 PNG/JPEG data URL 存到 output/drawings。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用画板导出。"}
        try:
            header, encoded = (data_url or "").split(",", 1)
            image_format = "jpg" if image_format == "jpg" else "png"
            expected = "data:image/jpeg;base64" if image_format == "jpg" else "data:image/png;base64"
            if not header.startswith(expected):
                return {"ok": False, "msg": "❌ 画板数据格式不对"}
            raw = base64.b64decode(encoded)
            if not raw or len(raw) > 30 * 1024 * 1024:
                return {"ok": False, "msg": "❌ 图片为空或超过 30MB"}
            folder = os.path.join(BASE_DIR, "output", "drawings")
            os.makedirs(folder, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            name = f"画板_{ts}.{image_format}"
            n = 1
            while os.path.exists(os.path.join(folder, name)):
                name = f"画板_{ts}_{n}.{image_format}"
                n += 1
            path = os.path.join(folder, name)
            with open(path, "wb") as f:
                f.write(raw)
            return {"ok": True, "msg": f"已导出：{path}", "path": path}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 导出失败：{e}"}

    def save_paint_project(self, project_json):
        """保存分层工程。图层仍是独立 PNG 数据，下次可继续编辑。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用画板工程写入。"}
        try:
            data = json.loads(project_json)
            if data.get("app") != "ali-paint" or not isinstance(data.get("layers"), list):
                return {"ok": False, "msg": "❌ 工程数据格式不对"}
            folder = os.path.join(BASE_DIR, "output", "drawings")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "画板分层工程.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return {"ok": True, "msg": f"分层工程已保存：{path}", "path": path}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 保存工程失败：{e}"}

    def load_paint_project(self):
        """读取最近一次保存的分层工程。"""
        path = os.path.join(BASE_DIR, "output", "drawings", "画板分层工程.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"ok": True, "data": f.read(), "path": path}
        except FileNotFoundError:
            return {"ok": False, "msg": "还没有保存过分层工程"}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 读取工程失败：{e}"}

    def save_paint_layers(self, layers_json):
        """Live2D 拆图导出：透明 PNG 支持原画布或紧凑裁切，并写入坐标清单。"""
        if getattr(self, "safe_mode", False):
            return {"ok": False, "msg": "🔒 安全模式已禁用图层导出。"}
        try:
            layers = json.loads(layers_json)
            if not isinstance(layers, list) or not layers:
                return {"ok": False, "msg": "没有可导出的图层"}
            folder = os.path.join(BASE_DIR, "output", "drawings", "Live2D拆分_" + time.strftime("%Y%m%d_%H%M%S"))
            os.makedirs(folder, exist_ok=True)
            manifest = []
            for index, item in enumerate(layers, start=1):
                header, encoded = (item.get("dataUrl") or "").split(",", 1)
                if not header.startswith("data:image/png;base64"):
                    continue
                raw = base64.b64decode(encoded)
                if not raw:
                    continue
                name = str(item.get("name") or f"图层{index}")
                safe = "".join(c if c.isalnum() or c in "-_()（） " else "_" for c in name).strip()[:50]
                if not safe:
                    safe = f"图层{index}"
                filename = f"{index:02d}_{safe}.png"
                with open(os.path.join(folder, filename), "wb") as f:
                    f.write(raw)
                manifest.append({
                    "file": filename,
                    "name": name,
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "bounds": item.get("bounds"),
                    "blendMode": item.get("blendMode", "source-over"),
                    "opacity": item.get("opacity", 1),
                    "group": item.get("group"),
                    "clipped": bool(item.get("clipped")),
                })
            if not manifest:
                return {"ok": False, "msg": "❌ 没有有效的图层图片"}
            canvas_width = layers[0].get("canvasWidth", 1200)
            canvas_height = layers[0].get("canvasHeight", 700)
            manifest_path = os.path.join(folder, "layers_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({
                    "canvas": {"width": canvas_width, "height": canvas_height},
                    "coordinateNote": "bounds.x/y 是该部件在原始画布中的左上角位置，单位为像素。",
                    "layers": manifest,
                }, f, ensure_ascii=False, indent=2)
            return {"ok": True, "msg": f"已导出 {len(manifest)} 张透明图层及坐标清单：{folder}", "path": folder, "manifest": manifest_path}
        except Exception as e:
            return {"ok": False, "msg": f"❌ 批量导出失败：{e}"}

    def get_history(self):
        return self.history

    def clear_history(self):
        """新开一段对话。

        不再「先存档当前再清空」：每段会话从第一句话起就在 sessions/ 里
        持续落盘，新开只是把指针挪到一段空会话上——没有搬运，也不会
        留下重复档。
        """
        if getattr(self, "safe_mode", False):
            return "🔒 安全模式已禁用新建对话。正常启动阿鲤再试。"
        try:
            self.sessions.create()
        except Exception as e:
            return f"❌ 新建会话失败：{e}"
        self.history = []
        self._save_history()
        try:
            self._agent.reset_session()
        except Exception:
            pass
        return "已开启新对话"

    def list_archives(self):
        """列出会话（前端沿用老名字，字段兼容 name / first / count）。"""
        try:
            return self.sessions.list()
        except Exception:
            return []

    def load_archive(self, name):
        """打开一段会话：只换指针 + 读事件流，不写任何东西。"""
        if not name or "/" in name or "\\" in name:
            return "❌ 会话名不合法"
        try:
            self.history = self.sessions.open(name)
        except FileNotFoundError:
            return f"❌ 找不到会话：{name}"
        except Exception as e:
            return f"❌ 打开会话失败：{e}"
        self._save_history()
        try:
            self._agent.reset_session()  # 换一段对话，滚动摘要从头算，别串味
        except Exception:
            pass
        return f"✅ 已打开（{len(self.history)} 条）"

    def delete_session(self, name):
        """删掉一段对话。删的是当前这段，就自动换到最近的一段。"""
        if not name or "/" in name or "\\" in name:
            return "❌ 会话名不合法"
        try:
            was_current = self.sessions.delete(name)
        except FileNotFoundError:
            return f"❌ 找不到会话：{name}"
        except Exception as e:
            return f"❌ 删除失败：{e}"
        if was_current:
            self.history = []
            try:
                items = self.sessions.list()
                if items:
                    self.history = self.sessions.open(items[0]["id"])
                else:
                    self.sessions.create()
            except Exception:
                pass
            self._save_history()
            try:
                self._agent.reset_session()
            except Exception:
                pass
        return "已删除这段对话"

    def get_events(self, since=0):
        """前端轮询用：拿 seq > since 的事件。

        chat() 是阻塞的，前端在等它返回的时候可以每 300ms 拉一次，
        这样就能看见「正在读文件 / 读到了 / 正在改」这种过程，
        而不是发完消息一片静默。
        """
        try:
            events = self._agent.events.as_list()
        except Exception:
            return {"events": [], "last": since}
        try:
            since = int(since or 0)
        except Exception:
            since = 0
        new = [e for e in events if int(e.get("seq", 0)) > since]
        last = max((int(e.get("seq", 0)) for e in events), default=since)
        return {"events": new, "last": last}

    def chat(self, user_text, model_name="", provider_id="", plugin_id=""):
        """前端唯一的聊天入口，返回字符串（契约不变）。

        第二个参数是前端菜单传下来的模型名，第三个是服务商 id；两个都来自
        「接入 API」时拉到的模型列表，后端不再写死任何模型名。
        第四个参数是前端「带入对话」的插件 id：只挂这一轮的工具，发完即卸，不落盘。
        """
        user_ts = time.strftime("%m-%d %H:%M")
        user_epoch = time.time()
        # 用户这句话先落盘：模型报错、进程崩了，它也不会凭空消失
        self._session_append("user", user_text, user_ts, user_epoch)
        try:
            result = self._agent.chat(user_text, self.history, model_name, plugin_id=plugin_id or None,
                                      provider_id=provider_id or None)
        except LLMError as e:
            self.history.append({"role": "user", "content": user_text, "ts": user_ts, "timestamp": user_epoch})
            return f"❌ {e}"
        except Exception as e:
            self.history.append({"role": "user", "content": user_text, "ts": user_ts, "timestamp": user_epoch})
            return f"❌ 运行出错：{type(e).__name__}: {e}"

        reply = result.get("reply") or "（没说话）"
        if reply == CANCELLED_MARK:
            # 用户按了暂停：留着他说的那句，别编一条假回复塞进上下文
            self.history.append({"role": "user", "content": user_text, "ts": user_ts, "timestamp": user_epoch})
            return CANCELLED_MARK
        self.history.append({"role": "user", "content": user_text, "ts": user_ts, "timestamp": user_epoch})
        assistant_msg = {"role": "assistant", "content": reply,
                         "ts": time.strftime("%m-%d %H:%M"), "timestamp": time.time()}
        self.history.append(assistant_msg)
        self._session_append("assistant", reply, assistant_msg["ts"], assistant_msg["timestamp"])
        self._append_receipt(result, user_text)
        return reply

    def _session_append(self, role, content, ts, timestamp):
        """往当前会话的事件流追加一条消息。写不进去也不能中断聊天。"""
        try:
            self.sessions.append_message(role, content, ts=ts, timestamp=timestamp)
        except Exception as e:
            _log(f"[会话] 写入失败: {e}")

    def cancel_chat(self):
        """前端把发送按钮切成「暂停」时调这个。

        只打标志，不杀线程：正在发的 HTTP 请求收不回来，但引擎会在下一个
        检查点（下一轮模型调用前、工具执行前）抛 TurnCancelled 收手。
        最坏情况是等当前这次模型调用返回，通常几秒。
        """
        try:
            self._agent.request_cancel()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        # 审批卡还挂着的话一并按拒绝收掉，不然要干等满 180 秒
        with self._approval_lock:
            ids = list(self._approvals.keys())
        for aid in ids:
            self.approve_tool(aid, "deny")
        return {"ok": True, "pending_approvals": len(ids)}

    # ---------------- 执行凭据 ----------------
    def _append_receipt(self, result, user_text):
        """把这一轮的消耗与工具链追加到 output/report_YYYY-MM-DD.md。

        为什么要有这个：聊天里只能说「我查过了」，事后没法证明这一轮到底
        调了什么、花了多少。events/*.jsonl 是给机器看的事实流水，这里是给
        人看的一页纸。写失败绝不影响聊天——凭据是附加品，不是功能。
        """
        try:
            stats = result.get("stats") or {}
            trace = result.get("trace") or []
            tools = [e.get("label") or "?" for e in trace if e.get("kind") == "tool/call"]
            prompt = stats.get("prompt_tokens") or 0
            hit = stats.get("cache_hit") or 0
            hit_pct = ("%.0f%%" % (hit / prompt * 100)) if prompt else "-"
            day = time.strftime("%Y-%m-%d")
            path = os.path.join(DATA_DIR, "output", "report_%s.md" % day)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            chain = " → ".join(tools) if tools else "（纯对话）"
            if len(chain) > 80:
                chain = chain[:79] + "…"
            said = (user_text or "").replace("\n", " ").replace("|", "/")[:24]
            with open(path, "a", encoding="utf-8") as f:
                if os.path.getsize(path) == 0:
                    f.write("# 阿鲤执行凭据 %s\n\n" % day)
                    f.write("| 时间 | 模式 | 调用 | prompt | 命中 | 输出 | 工具链 | 结果 | 你说 |\n")
                    f.write("|---|---|---|---|---|---|---|---|---|\n")
                f.write("| %s | %s | %s | %s | %s | %s | %s | %s | %s |\n" % (
                    time.strftime("%H:%M:%S"), result.get("mode", ""),
                    stats.get("calls", 0), "{:,}".format(prompt), hit_pct,
                    stats.get("completion_tokens", 0), chain,
                    "✅" if result.get("ok") else "❌", said))
        except Exception:
            pass

    # ---------------- 新增：给前端以后用 ----------------
    def vision_chat(self, text, imgs=None):
        """识图入口。imgs 是前端 FileReader 出来的 dataURL 数组。

        链路：当前接入的模型看图 -> 产出文字描述 -> 描述作为上下文交给同一个模型，
        由她基于描述回答。看图要求模型支持 OpenAI 兼容的 image_url 多模态格式。
        旧实现把描述原样返回，阿鲤全程没参与，等于她「看不到图」。
        """
        imgs = imgs or []
        if not isinstance(imgs, list):
            imgs = [imgs]
        if not imgs:
            return self.chat(text)

        user_ts = time.strftime("%m-%d %H:%M")
        user_epoch = time.time()
        # 1) 模型读图，拿到文字描述
        try:
            caption = self._agent.llm.vision(text, imgs)
        except LLMError as e:
            return f"❌ {e}"
        except Exception as e:
            return f"❌ 识图出错：{type(e).__name__}: {e}"

        caption = (caption or "（没看清）").strip()
        # 别让长篇描述把下一轮 token 吃光：截到 1600 字，够她把图说清
        if len(caption) > 1600:
            caption = caption[:1600] + "\n（描述过长，已截断）"

        # 2) 把描述交给阿鲤，让她当自己亲眼看过来说
        question = text or "帮我看看这几张图"
        user_msg = (
            f"{question}\n\n"
            f"[你看见的画面，已由视觉通道转成文字——就当是你亲眼所见，直接回应，"
            f"不要再声明「我看不到图」。]\n{caption}"
        )

        try:
            result = self._agent.chat(user_msg, self.history, "")
        except LLMError as e:
            return f"❌ {e}"
        except Exception as e:
            return f"❌ 运行出错：{type(e).__name__}: {e}"

        reply = result.get("reply") or "（没说话）"
        # 历史只存用户原话的精简版，不存中间的长描述，省 token
        short_user = f"[图×{len(imgs)}] {question}"
        self.history.append({"role": "user", "content": short_user, "ts": user_ts, "timestamp": user_epoch})
        assistant_msg = {"role": "assistant", "content": reply,
                         "ts": time.strftime("%m-%d %H:%M"), "timestamp": time.time()}
        self.history.append(assistant_msg)
        self._session_append("user", short_user, user_ts, user_epoch)
        self._session_append("assistant", reply, assistant_msg["ts"], assistant_msg["timestamp"])
        return reply

    def doc_ocr_vision(self, text, imgs=None):
        """文档 OCR 入口：本地 DeepSeek-OCR 转 Markdown -> 阿鲤（当前模型）用人设回答。

        和 vision_chat 的区别：这一步 OCR 完全在本地 Ollama 上跑，零 token；
        识别完把 Markdown 交给阿鲤回答。插件默认关闭，需在「管理插件」手动开。
        """
        imgs = imgs or []
        if not isinstance(imgs, list):
            imgs = [imgs]
        if not imgs:
            return self.chat(text)

        # 开关与就绪检查，没开/没下好模型就明说，不偷偷降级
        try:
            pm = self._agent.plugins
            if not pm.is_enabled("doc_ocr"):
                return "🔒 文档 OCR 插件还没开。去「管理插件」里打开「文档 OCR」，我就能本地识别文档截图，零 token。"
            spec = pm.specs.get("doc_ocr")
            if spec and not spec.ready():
                return f"❌ 文档 OCR 尚未就绪：{spec.status()}。模型可能还在下载，等它完成后再试。"
        except Exception:
            pass

        # 把第一张 dataURL 落成临时图片，识别完即删
        import base64 as b64
        import tempfile
        tmp = None
        try:
            head, encoded = (imgs[0] or "").split(",", 1)
            raw = b64.b64decode(encoded)
            suffix = ".png" if "png" in head else ".jpg"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="ali_ocr_")
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
        except Exception as e:
            return f"❌ 图片处理失败：{e}"

        try:
            ocr_text = self._agent.registry.call("plugin_doc_ocr", {"path": tmp})
        except Exception as e:
            return f"❌ 调用文档 OCR 失败：{type(e).__name__}: {e}"
        finally:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
        if not isinstance(ocr_text, str):
            ocr_text = str(ocr_text)
        if ocr_text.startswith("❌"):
            return ocr_text

        user_ts = time.strftime("%m-%d %H:%M")
        user_epoch = time.time()
        question = text or "帮我识别这份文档"
        user_msg = (
            f"{question}\n\n"
            f"[这是从你上传的文档截图里本地 OCR 出来的 Markdown 文本，"
            f"把它当作你亲眼读到的内容，直接回应。]\n{ocr_text[:8000]}"
        )
        try:
            result = self._agent.chat(user_msg, self.history, "")
        except LLMError as e:
            return f"❌ {e}"
        except Exception as e:
            return f"❌ 运行出错：{type(e).__name__}: {e}"

        reply = result.get("reply") or "（没说话）"
        # 历史只存精简版，不存整段 OCR 文本，省 token
        short_user = f"[文档OCR×{len(imgs)}] {question}"
        self.history.append({"role": "user", "content": short_user, "ts": user_ts, "timestamp": user_epoch})
        assistant_msg = {"role": "assistant", "content": reply,
                         "ts": time.strftime("%m-%d %H:%M"), "timestamp": time.time()}
        self.history.append(assistant_msg)
        self._session_append("user", short_user, user_ts, user_epoch)
        self._session_append("assistant", reply, assistant_msg["ts"], assistant_msg["timestamp"])
        return reply

    def get_trace(self):
        """上一轮的过程轨迹：走了哪个通道、用了哪些工具、花了多少 token。"""
        stats = dict(self._agent.stats)
        prompt = int(stats.get("last_prompt_tokens") or 0)
        cache = int(stats.get("last_cache_hit") or 0)
        window = int(self._agent.config.get("context_window") or 0)
        stats.update({
            "cache_rate": round(cache / prompt, 3) if prompt else 0.0,
            "context_window": window,
            "context_pct": round(prompt / window * 100, 1) if (window and prompt) else None,
            "safe_mode": self.safe_mode,
        })
        return {
            "trace": self._agent.events.as_list(),
            "summary": self._agent.events.summary(),
            "stats": stats,
            "runtime": self._agent.runtime.name,
            "session_id": self._agent.events.session_id,
            "safe_mode": bool(self.safe_mode),
        }

    def get_event_log(self, limit=200, session_id=None):
        """读持久账本。limit 默认 200，最多 2000；可按 session_id 过滤。"""
        try:
            ledger = self._agent.ledger
            return {
                "ok": True,
                "events": ledger.read_recent(limit=limit, session_id=session_id),
                "session_id": ledger.session_id,
                "path": ledger.path,
            }
        except Exception as e:
            return {"ok": False, "events": [], "msg": f"读取事件账本失败：{e}"}

    def get_turn_outline(self, limit=50):
        """轮次大纲：每轮一条（时间/耗时/你说的/她回的/调用/token）。

        数据全部来自 events/*.jsonl 的 fold，不另存一份状态——日志是唯一真相源。
        前端拿它做侧栏跳转；命令行用 _selftest/_report_session.py 看同一份。
        """
        try:
            from alicore_agent.events import turn_outline
            ledger = self._agent.ledger
            events = ledger.read_recent(limit=2000)
            turns = turn_outline(events, limit=max(1, min(int(limit or 50), 200)))
            totals = {
                "turns": len(turns),
                "calls": sum(t.get("calls") or 0 for t in turns),
                "prompt_tokens": sum(t.get("prompt_tokens") or 0 for t in turns),
                "completion_tokens": sum(t.get("completion_tokens") or 0 for t in turns),
                "cache_hit": sum(t.get("cache_hit") or 0 for t in turns),
            }
            return {"ok": True, "turns": turns, "totals": totals}
        except Exception as e:
            return {"ok": False, "turns": [], "msg": f"读取轮次大纲失败：{e}"}

    # ---------------- 工具接口（保留，前端可能直接调） ----------------
    def read_file(self, path):
        return self._agent.registry.call("read_file", {"path": path})

    def list_files(self, path=""):
        return self._agent.registry.call("list_files", {"path": path})

    def write_file(self, filepath, content):
        return self._agent.registry.call("write_file", {"path": filepath, "content": content})

    def get_current_time(self):
        return self._agent.registry.call("get_current_time", {})

    def run_python(self, code):
        return self._agent.registry.call("run_python", {"code": code})


LOCK_PORT = 51998
WINDOW_TITLE = "阿鲤 · AliCore"
DEFAULT_DATA_DIR = default_data_dir()


def _resolve_window_identity():
    """窗口标题和实例锁跟着数据目录走。

    AliCore 用自己的固定标题和端口（51998），和本机原来的阿鲤
    （"阿鲤" / 51999）不是同一个实例，两个可以同时开、不互相抢前台。
    """
    global WINDOW_TITLE, LOCK_PORT
    base = os.path.abspath(DATA_DIR)
    if os.path.normcase(base) == os.path.normcase(os.path.abspath(DEFAULT_DATA_DIR)):
        WINDOW_TITLE, LOCK_PORT = "阿鲤 · AliCore", 51998
        return
    tag = os.path.basename(base.rstrip("\\/")) or "自定义"
    WINDOW_TITLE = "阿鲤 · " + tag
    digest = hashlib.md5(os.path.normcase(base).encode("utf-8")).hexdigest()[:6]
    LOCK_PORT = 51000 + int(digest, 16) % 900


def _single_instance_guard():
    """只允许开一个基站。

    连点两次 bat 会开两个 WebView2 实例，两个抢同一个 storage_profile 目录，
    后起的那个会在没有输出的情况下静默退出（pythonw 下连报错都看不到）。

    锁用一个本地端口：只 bind 不 listen，所以进程结束后端口立刻释放、
    不会留下 TIME_WAIT 把下一次启动误判成「已开着」。
    （试过命名互斥体，ctypes 下 GetLastError 没拿到 183，判断不住，弃了。）
    已有实例时不弹窗打扰，直接把那个窗口提到前面。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        sock.close()
        _bring_to_front()
        return None
    return sock


def _bring_to_front():
    """把已有窗口拉到前面。

    用 SW_SHOWNORMAL 而不是 SW_RESTORE：实测窗口以最小化状态创建时，
    SW_RESTORE 拉不起来，SW_SHOWNORMAL 才行。
    """
    hwnd = ctypes.windll.user32.FindWindowW(None, ctypes.c_wchar_p(WINDOW_TITLE))
    if not hwnd:
        return
    ctypes.windll.user32.ShowWindow(hwnd, 1)                       # SW_SHOWNORMAL
    flags = 0x0003                                                 # NOMOVE | NOSIZE
    ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # TOPMOST
    ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # NOTOPMOST


def _ensure_visible(t0=0.0):
    """兜底：窗口万一被系统以最小化状态创建，就一直盯着，最小化了就拉起来。

    不能只拉一次——实测 webview 会在窗口建出来之后又把它设成最小化，
    那时单次 ShowWindow 早就已经 return 了，于是窗口就一直躺在任务栏里。
    启动期 90 秒内持续巡检，发现状态是 minimized 就 SW_SHOWNORMAL。
    顺手把「窗口第一次出现」的时刻记下来，用来量首启到底卡在哪一段。
    """
    deadline = time.time() + 90
    seen = False
    while time.time() < deadline:
        time.sleep(1)
        hwnd = ctypes.windll.user32.FindWindowW(None, ctypes.c_wchar_p(WINDOW_TITLE))
        if hwnd:
            if not seen:
                seen = True
                _log(f"窗口出现   {time.time() - t0:.1f}s")
            if ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 1)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="阿鲤桌面端")
    p.add_argument("--safe-mode", action="store_true", help="只读安全模式：禁用代码执行、写文件和插件工具")
    p.add_argument("--data-dir", default="", help="用户数据目录（默认 %%APPDATA%%\\AliCore）")
    p.add_argument("--runtime-dir", default="", help="临时/日志/TCC 目录（默认 <data>\\_runtime）")
    return p.parse_args(argv)


def start_app(argv=None):
    args = _parse_args(argv)
    _apply_paths(APP_DIR, args.data_dir or None, args.runtime_dir or None)
    _seed_content()
    _resolve_window_identity()

    _t0 = time.time()
    _log("-" * 40)
    _log("开始启动")
    if args.safe_mode:
        _log("安全模式：只读工具，代码执行与写入已禁用")
    lock = _single_instance_guard()
    if lock is None:
        _log("已有实例在跑，本次退出")
        raise SystemExit(0)
    _log(f"拿到锁     {time.time() - _t0:.1f}s")

    storage_dir = os.path.join(RUNTIME_DIR, "webview_storage")
    os.makedirs(storage_dir, exist_ok=True)

    api = Api(safe_mode=args.safe_mode)
    _log(f"Api 就绪   {time.time() - _t0:.1f}s（build_agent 完成）")
    webview.create_window(
        title=WINDOW_TITLE,
        url=HTML_FILE,
        width=1280,
        height=800,
        resizable=True,
        js_api=api,
    )
    _log(f"窗口已创建 {time.time() - _t0:.1f}s")
    threading.Thread(target=_ensure_visible, args=(_t0,), daemon=True).start()
    webview.start(storage_path=storage_dir)


if __name__ == "__main__":
    start_app()
