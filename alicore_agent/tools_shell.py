"""干活类工具：跑命令、抓网页、维护任务清单。

这三类以前都没有，结果就是模型只能读写文件，真到「跑个测试」「查个文档」
「把十步任务排一下」的时候使不上劲。开源版要给人干活用，得补上。
"""

import ipaddress
import json
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

import requests

# 明令拒绝的命令片段。黑名单挡不住所有变形，真正兜底的是界面审批。
_BANNED = (
    "format ", "rm -rf /", "del /f /s", "del /q /f", "shutdown", "reboot",
    "reg delete", "diskpart", "mkfs", "dd if=", "> /dev/sda",
)

_MAX_OUT = 6000


def register_shell_tools(registry, data_dir, cfg=None):
    todo_file = os.path.join(data_dir, "todos.json")

    def run_command(command: str, cwd: str = "", timeout: int = 60):
        """在本机执行一条命令，返回 exit code + stdout + stderr。"""
        cmd = (command or "").strip()
        if not cmd:
            return "❌ 命令为空"
        low = cmd.lower()
        for b in _BANNED:
            if b in low:
                return f"❌ 拒绝执行：包含危险片段「{b}」"
        work = (cwd or "").strip() or os.getcwd()
        if not os.path.isdir(work):
            return f"❌ 工作目录不存在：{work}"
        limit = max(5, min(int(timeout or 60), 300))
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=work, capture_output=True, timeout=limit,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            return f"⏱ 命令超过 {limit} 秒被停了。要么加 timeout，要么检查是不是卡在交互输入。"
        except Exception as e:
            return f"❌ 启动命令失败：{type(e).__name__}: {e}"
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        body = []
        if out:
            body.append(out[-_MAX_OUT:])
        if err:
            body.append("[stderr]\n" + err[-_MAX_OUT:])
        text = "\n".join(body) if body else "(无输出)"
        return f"exit={proc.returncode}  {time.time() - t0:.1f}s\n{text}"

    def http_get(url: str, max_chars: int = 6000):
        """抓一个公网网页/接口，返回文本。"""
        u = (url or "").strip()
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return "❌ 只支持 http/https"
        host = p.hostname or ""
        if not host:
            return "❌ URL 里没有主机名"
        # 内网/本机地址不抓：防止被诱导去读本地服务
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return "❌ 只允许公网地址（本机/内网地址不抓）"
        except socket.gaierror:
            return f"❌ 解析不了这个域名：{host}"
        except ValueError:
            return "❌ 地址不合法"
        try:
            r = requests.get(u, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AliCore/1.0)",
                "Accept": "text/html,application/json,text/plain,*/*",
            })
        except requests.Timeout:
            return "⏱ 请求超时（20 秒）"
        except Exception as e:
            return f"❌ 请求失败：{type(e).__name__}: {e}"
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            try:
                text = json.dumps(r.json(), ensure_ascii=False, indent=2)
            except Exception:
                text = r.text
        else:
            text = r.text
        cap = max(500, min(int(max_chars or 6000), 20000))
        head = f"HTTP {r.status_code}  {ctype or 'unknown'}  {len(text)} chars\n"
        return head + text[:cap] + ("\n…（截断了）" if len(text) > cap else "")

    def todo_write(items: str):
        """写任务清单。items 每行一条，前缀 [ ] / [x] 表示未完/已完成。"""
        raw = (items or "").strip()
        if not raw:
            return "❌ 清单为空"
        rows = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            done = s.lower().startswith("[x]")
            s = s.replace("[x]", "", 1).replace("[X]", "", 1).replace("[ ]", "", 1).strip()
            s = s.lstrip("-*0123456789. ").strip()
            if s:
                rows.append({"text": s, "done": done})
        if not rows:
            return "❌ 没解析出任务"
        try:
            os.makedirs(os.path.dirname(todo_file), exist_ok=True)
            with open(todo_file, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"❌ 写入失败：{e}"
        return f"已记下 {len(rows)} 条任务（完成 {sum(1 for r in rows if r['done'])} 条）"

    def todo_read():
        """读回任务清单。"""
        try:
            with open(todo_file, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            return "（还没有任务清单）"
        if not rows:
            return "（还没有任务清单）"
        lines = []
        for i, r in enumerate(rows, 1):
            mark = "✔" if r.get("done") else "○"
            lines.append(f"{mark} {i}. {r.get('text') or ''}")
        return "\n".join(lines)

    def web_search(query: str, count: int = 5):
        """联网搜索。key 由用户在设置里自己填，仓库里不带任何内置 key。"""
        q = (query or "").strip()
        if not q:
            return "❌ 搜索词为空"
        provider = (cfg.get("search_provider") if cfg else "") or ""
        key = ((cfg.data.get("search_key") or "") if cfg else "").strip()
        if not provider or not key:
            return ("❌ 还没配联网搜索。打开「设置 → 联网搜索」，选服务商（tavily / bocha / serper）"
                    "并填上你自己的 key——这个仓库不内置任何搜索 key。")
        n = max(1, min(int(count or 5), 10))
        try:
            if provider == "tavily":
                r = requests.post("https://api.tavily.com/search", json={
                    "api_key": key, "query": q, "max_results": n,
                    "search_depth": "basic", "include_answer": False,
                }, timeout=25)
                r.raise_for_status()
                items = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")}
                         for x in (r.json().get("results") or [])]
            elif provider == "bocha":
                r = requests.post("https://api.bochaai.com/v1/web-search",
                                  headers={"Authorization": "Bearer " + key},
                                  json={"query": q, "count": n, "summary": True, "page": 1},
                                  timeout=25)
                r.raise_for_status()
                data = (r.json().get("data") or {}).get("webPages") or {}
                items = [{"title": x.get("name"), "url": x.get("url"),
                          "snippet": x.get("snippet") or x.get("summary")}
                         for x in (data.get("value") or [])]
            elif provider == "serper":
                r = requests.post("https://google.serper.dev/search",
                                  headers={"X-API-KEY": key, "Content-Type": "application/json"},
                                  json={"q": q, "num": n}, timeout=25)
                r.raise_for_status()
                items = [{"title": x.get("title"), "url": x.get("link"), "snippet": x.get("snippet")}
                         for x in (r.json().get("organic") or [])]
            else:
                return f"❌ 不认识的搜索服务商：{provider}（支持 tavily / bocha / serper）"
        except requests.Timeout:
            return "⏱ 搜索超时（25 秒）"
        except requests.HTTPError as e:
            body = (e.response.text[:200] if getattr(e, "response", None) is not None else str(e))
            return f"❌ 搜索接口报错：{body}\n（key 不对或额度用完，去服务商后台看看）"
        except Exception as e:
            return f"❌ 搜索失败：{type(e).__name__}: {e}"
        if not items:
            return f"（没有搜到结果：{q}）"
        out = []
        for i, it in enumerate(items, 1):
            snip = (it.get("snippet") or "").strip().replace("\n", " ")[:400]
            out.append(f"{i}. {it.get('title') or '(无标题)'}\n   {it.get('url') or ''}\n   {snip}")
        return f"搜索「{q}」前 {len(items)} 条（{provider}）：\n" + "\n".join(out)

    # ---- 常驻：干活的主力，每轮都该看得见 ----
    registry.register(
        "run_command",
        "在本机跑一条命令（git / npm / pip / pytest / 构建）。cwd 传工作目录，默认当前目录",
        run_command,
        json_visible=True,
    )
    registry.register(
        "http_get",
        "抓公网网页或接口，返回状态码 + 文本（JSON 会格式化）",
        http_get,
        json_visible=True,
    )
    registry.register(
        "web_search",
        "联网搜索（用户自备 key）。返回标题 + 链接 + 摘要",
        web_search,
        json_visible=True,
    )
    registry.register(
        "todo_write",
        "写任务清单。items 每行一条，[x] 表示已完成",
        todo_write,
        json_visible=True,
    )
    registry.register(
        "todo_read",
        "读回当前任务清单",
        todo_read,
        json_visible=True,
    )
    return registry
