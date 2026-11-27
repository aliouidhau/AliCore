"""配置层。

抄自 OpenHands 的一条边界原则：凭证和运行参数只在后端，不下发到前端。

凭证不外露这一条，光「不下发给前端」还不够——agent 自己就有 read_file，
config.json 就摆在它工作目录里，它随时能把自己的钥匙读出来打印给你看。
所以真值不进 config.json，改存到 %APPDATA%\\AliCore\\secrets.json：

  为什么是 APPDATA——tools_fs.py 的 DENY_DIRS 把当前账户的 AppData 整段拒了，
  agent 的 read_file / search_text / list_files 全都碰不到那里；而 config.json
  在它嘴边。把钥匙从嘴边移到它伸不到的地方，才是真的不外露。

  读取优先级：环境变量 > secrets.json > config.json 内联（兼容老版本）。
  老版本 config.json 里如果还留着明文 key，首次加载会自动搬去 secrets.json，
  并把 config.json 里的那份清空——搬一次就够，之后只剩空字符串。
"""

import json
import os
import sys
import threading

from .fsutil import atomic_write_json


def _say(msg):
    """pythonw.exe 下 sys.stdout 是 None，print() 会抛 AttributeError。

    而这个函数只用在 except 分支里——一 print，错误处理本身就成了新的崩溃点，
    原本「读配置失败但继续用默认值」会变成「读配置失败且程序打不开」。
    """
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:
        pass

SECRET_KEYS = ("api_key", "glm_key", "openai_key", "anthropic_key")

# 环境变量优先级最高：适合临时换 key、或者在 CI / 别人的机器上跑而不落地
ENV_MAP = {
    "api_key": "ALICORE_DEEPSEEK_KEY",
    "glm_key": "ALICORE_GLM_KEY",
    "openai_key": "ALICORE_OPENAI_KEY",
    "anthropic_key": "ALICORE_ANTHROPIC_KEY",
}

DEFAULT_CONFIG = {
    "api_key": "",
    "glm_key": "",
    "openai_key": "",
    "anthropic_key": "",
    # 中转站地址（OpenAI 兼容协议）。官方直连留空；填中转后 GPT 和 Claude 都能从中转走，工具调用也可用
    "openai_base_url": "",
    "anthropic_max_tokens": 4096,
    "system_prompt": "",
    "agent_mode": "json",      # json | code | auto
                                # 实测：glm-4-flash 下 code 通道更贵（2.4 倍）且会编答案；
                                # 换成强模型（如 DeepSeek）再试 "code" / "auto"
    "max_rounds": 6,           # 单个任务的工具轮次上限
    "max_steps": 4,            # CodeAgent 最多写几次代码（每轮都要重发上下文，越多越贵）
    "sandbox": True,           # run_python 走 Docker；False 则走本地受限 exec
    "temperature": 0.85,
    "history_limit": 10,       # 带多少条历史（旧版就是 10）
    # 上下文窗口只用于“显示压力”和以后的压缩触发；0 = 不估算。
    # 多数 DeepSeek/GLM 对话模型是 128K，按自己实际模型改，不要拿它当计费。
    "context_window": 131072,
    "max_output_tokens": 8192,
}


def secrets_path():
    """%APPDATA%\\AliCore\\secrets.json（也在 tools_fs 的拒绝目录里）。"""
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "AliCore", "secrets.json")


class Config:
    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        self.path = os.path.join(self.base_dir, "config.json")
        self.secrets_file = secrets_path()
        self.data = dict(DEFAULT_CONFIG)
        # 界面保存和聊天线程可能同时写凭证：读-改-写必须串行，否则后写的会吞掉先写的
        self._secrets_lock = threading.RLock()
        self.load()

    # ---------------- 读取 ----------------
    def load(self):
        raw = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception as e:
                _say(f"[配置] 读取失败，用默认值: {e}")

        secrets = {}
        if os.path.exists(self.secrets_file):
            try:
                with open(self.secrets_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    secrets = loaded
            except Exception as e:
                _say(f"[配置] 密钥文件读取失败: {e}")

        # 环境变量最高，其次是密钥文件
        for k, env in ENV_MAP.items():
            v = (os.environ.get(env) or "").strip()
            if v:
                secrets[k] = v

        for k, v in raw.items():
            self.data[k] = v

        for k in SECRET_KEYS:
            self.data[k] = (secrets.get(k) or "").strip() or (raw.get(k) or "").strip()

        # 老版本迁移：config.json 里还有明文就搬走并清空那一份
        if any((raw.get(k) or "").strip() for k in SECRET_KEYS):
            self._migrate(raw)

    def _migrate(self, raw):
        """把 config.json 里的明文 key 搬到 secrets.json，再清空原处。"""
        try:
            self._write_secrets({k: raw.get(k) for k in SECRET_KEYS if (raw.get(k) or "").strip()})
            out = {k: ("" if k in SECRET_KEYS else self.data.get(k, v)) for k, v in DEFAULT_CONFIG.items()}
            atomic_write_json(self.path, out, mode=0o644)
            _say(f"[配置] 已把明文 key 迁到 {self.secrets_file}，config.json 里的已清空")
        except Exception as e:
            _say(f"[配置] 迁移密钥失败（config.json 仍保留明文）: {e}")

    # ---------------- 写入 ----------------
    def _write_secrets(self, mapping):
        if not mapping:
            return
        with self._secrets_lock:
            self._write_secrets_locked(mapping)

    def _write_secrets_locked(self, mapping):
        os.makedirs(os.path.dirname(self.secrets_file), exist_ok=True)
        existing = {}
        if os.path.exists(self.secrets_file):
            try:
                with open(self.secrets_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                pass
        existing.update(mapping)
        # 原子写 + 0o600：写到一半崩了也不会把密钥文件变成空文件
        atomic_write_json(self.secrets_file, existing, mode=0o600)
        try:
            # Windows 上 chmod 只动只读位，不改 ACL——这句话不构成「别人账户看不到」的保证。
            # 真正挡住 agent 的是 tools_fs 的 DENY_DIRS（现在按 %APPDATA% 动态拼，不再写死用户名）。
            os.chmod(self.secrets_file, 0o600)
        except Exception:
            pass

    def describe_secrets(self):
        """凭证的「配没配」，永远不回真值。给日志、自检、界面文案用。

        前 5 后 4 可见、其余打码——够认出来是哪把，不够拿去用。
        少于 12 位的直接全员打码，避免短 key 被扒光。
        """
        out = {}
        for k in SECRET_KEYS:
            v = (self.data.get(k) or "").strip()
            if not v:
                out[k] = {"configured": False, "length": 0, "masked": ""}
            elif len(v) >= 12:
                out[k] = {"configured": True, "length": len(v),
                          "masked": v[:5] + "…" + v[-4:]}
            else:
                out[k] = {"configured": True, "length": len(v), "masked": "…"}
        return out

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set_many(self, **kw):
        for k, v in kw.items():
            self.data[k] = v

    def save(self):
        """key 进 secrets.json，config.json 只留空位。"""
        self._write_secrets({k: self.data.get(k) for k in SECRET_KEYS if (self.data.get(k) or "").strip()})
        # 保留 data 里所有键，不只 DEFAULT_CONFIG 白名单：以前手改的
        # daily_limit / ollama_model / openai_model 会在下次保存设置时被悄悄丢掉
        out = {k: ("" if k in SECRET_KEYS else v) for k, v in self.data.items()}
        for k, v in DEFAULT_CONFIG.items():
            out.setdefault(k, "" if k in SECRET_KEYS else v)
        atomic_write_json(self.path, out, mode=0o644)

    def public_view(self):
        """给前端看的配置：不含任何 key。"""
        return {
            "system_prompt": self.get("system_prompt"),
            "agent_mode": self.get("agent_mode"),
            "has_glm_key": bool(self.get("glm_key")),
            "has_deepseek_key": bool(self.get("api_key")),
            "has_openai_key": bool(self.get("openai_key")),
            "has_anthropic_key": bool(self.get("anthropic_key")),
        }
