"""工具注册表。

抄自 smolagents：工具就是普通 Python 函数，模型看到的是一行真实函数签名
（不是 JSON schema），调用时直接传参。
抄自 OpenHands：工具和 agent 解耦，统一注册、统一执行、统一报错。

2026-11-24 补的三件事：
  1. 统一审计：每次调用前后各记一条事件，进 ledger；参数里的 key/token 打码。
  2. 安全模式：只留只读工具，写文件、跑代码、插件工具一律拒绝并说明原因。
  3. 代码执行仍然走 Runtime 的超时/资源限制，不在注册表里裸跑。
"""

import inspect
import json
import os
import time
from dataclasses import dataclass

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# 安全模式只允许这组只读能力。要加新工具，先想清楚它会不会写盘/起进程/出网。
SAFE_MODE_ALLOW = {
    "read_file", "list_files", "search_text", "search_memory",
    "get_current_time", "code_map",
}

_SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "authorization", "cookie")


def _bind_params(func, args, kwargs):
    """把位置参数 + 关键字参数还原成 call() 用的 dict。"""
    out = dict(kwargs or {})
    if not args:
        return out
    try:
        names = [p.name for p in inspect.signature(func).parameters.values()
                 if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except Exception:
        return out
    for i, value in enumerate(args):
        if i < len(names):
            out[names[i]] = value
    return out

# 需要人点「允许一次」才执行的高危动作。原则：不可逆、有外部副作用、可能执行代码。
# 新建文件和追加不在此列——那是加东西，不是删/改/跑。
CONFIRM_TOOLS = {
    "delete_file": "删除文件（进回收区，能捞回来，但仍然算删除）",
    "move_file": "移动或重命名文件",
    "open_path": "打开文件或文件夹（等同双击，可能触发执行）",
    "run_python": "执行一段 Python 代码",
    "run_command": "在本机执行一条命令（git/npm/pip/测试等）",
    "edit_file": "局部改写文件内容（改前会备份）",
}


def confirm_label(name, params):
    """返回 (要不要人工确认, 一句人话)。纯函数，方便单测。"""
    if name in CONFIRM_TOOLS:
        return True, CONFIRM_TOOLS[name]
    if name == "write_file":
        path = str((params or {}).get("path") or "")
        append = bool((params or {}).get("append"))
        if path and not append:
            full = os.path.abspath(os.path.normpath(path))
            if os.path.exists(full):
                return True, "覆盖已有文件（改前会备份）"
    return False, ""


def build_json_schema(func):
    """从函数签名自动生成 OpenAI function schema（给 JSON 通道用）。"""
    sig = inspect.signature(func)
    props, required = {}, []
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        ann = p.annotation
        props[name] = {"type": _TYPE_MAP.get(ann, "string")}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def build_code_signature(name, func, description):
    """生成 CodeAgent 能看到的那行签名 + 文档。"""
    sig = inspect.signature(func)
    params = []
    for pname, p in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        ann = p.annotation
        tn = getattr(ann, "__name__", "str") if ann is not inspect.Parameter.empty else "str"
        if p.default is inspect.Parameter.empty:
            params.append(f"{pname}: {tn}")
        else:
            params.append(f"{pname}: {tn} = {p.default!r}")
    one_line = (description or "").strip().replace("\n", " ")
    return f"def {name}({', '.join(params)}) -> str:\n    \"\"\"{one_line}\"\"\""


def _safe_preview(params):
    """工具参数摘要：打码密钥，截断到 120 字符，只进账本，不进 prompt。"""
    if not isinstance(params, dict):
        return str(params)[:120]
    redacted = {}
    for k, v in params.items():
        if any(h in str(k).lower() for h in _SECRET_HINTS):
            redacted[k] = "***"
        else:
            redacted[k] = v
    try:
        return json.dumps(redacted, ensure_ascii=False)[:120]
    except Exception:
        return str(redacted)[:120]


@dataclass
class ToolSpec:
    name: str
    description: str
    func: callable
    # 三态，别再往 True/False 里塞更多含义：
    #   True  + tags=()  → 永远挂在 JSON 通道（基线工具，每轮都发）
    #   False + tags=()  → 永不出现在 JSON 通道（如 get_current_time，靠 system 注时间）
    #   False + tags=(…) → 按用户这句话的关键词动态挂载（说了才给看）
    json_visible: bool = True
    tags: tuple = ()
    # 插件工具的独立开关：enabled=False 时三条通道都看不见、也调不动。
    # 基线工具默认永远 True，插件按「管理插件」里的手动开关设。
    enabled: bool = True

    def json_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": build_json_schema(self.func),
            },
        }

    def code_signature(self):
        return build_code_signature(self.name, self.func, self.description)


class ToolRegistry:
    def __init__(self, safe_mode=False):
        self._tools = {}
        self.safe_mode = bool(safe_mode)
        # audit：agent 注入。签名是 (event: dict) -> None，失败也不能影响工具执行。
        self.audit = None
        # confirm：界面注入。签名是 (name, params, label) -> (allowed: bool, reason: str)。
        # 没注入就按「自动放行」跑——命令行自测、无界面场景不该被卡死。
        self.confirm = None

    def register(self, name, description, func, json_visible=True, tags=(), enabled=True):
        spec = ToolSpec(name=name, description=description, func=func,
                        json_visible=json_visible, tags=tuple(tags or ()),
                        enabled=enabled)
        self._tools[name] = spec
        return spec

    def get(self, name):
        return self._tools.get(name)

    def set_enabled(self, name, enabled):
        """插件开关：开/关某个工具。不存在就返回 False。"""
        spec = self._tools.get(name)
        if not spec:
            return False
        spec.enabled = bool(enabled)
        return True

    def names(self):
        return list(self._tools.keys())

    def all(self):
        return list(self._tools.values())

    def enabled_all(self):
        return [t for t in self._tools.values() if t.enabled]

    def allowed(self, spec):
        """当前模式是否允许这个工具。安全模式按 allowlist，不按黑名单。"""
        if not spec or not spec.enabled:
            return False
        if self.safe_mode and spec.name not in SAFE_MODE_ALLOW:
            return False
        return True

    def json_schemas(self, query=None, force_names=()):
        """只给 JSON 通道可见的工具。schema 每轮都要重发，少一个是一个。

        query 不传 → 老行为（只看 json_visible，全部挂上），保持向后兼容。
        query 传了 → 基线工具全挂，带 tags 的按关键词决定挂不挂。
        语义上等于：说到了才给看。看不见的工具模型不会去调，也就不会占 token。
        force_names → 用户手动「带入对话」的插件工具：无视开关和 tags，本轮直接挂上。
        插件的 enabled=False 时，无论 json_visible 还是 tags 命中都不给。
        安全模式 → 只挂 allowlist，force_names 也不能把它撬开。
        """
        forced = set(force_names or ())
        if query is None:
            return [t.json_schema() for t in self._tools.values()
                    if self.allowed(t) and (t.json_visible or t.name in forced)]

        low = (query or "").lower()
        out = []
        for t in self._tools.values():
            if not self.allowed(t):
                continue
            if t.name in forced:
                out.append(t.json_schema())
                continue
            if t.json_visible:
                out.append(t.json_schema())
            elif t.tags and any(k in low for k in t.tags):
                out.append(t.json_schema())
        return out

    def code_prompt(self):
        """CodeAgent 的提示词里那一块「可用函数」。"""
        return "\n".join(t.code_signature() for t in self._tools.values() if self.allowed(t))

    def _emit_audit(self, payload):
        if not self.audit:
            return
        try:
            self.audit(payload)
        except Exception:
            pass

    def call(self, name, params):
        spec = self._tools.get(name)
        if not spec:
            return f"❌ 未知工具：{name}"
        if not spec.enabled:
            self._emit_audit({
                "phase": "blocked", "name": name, "reason": "disabled",
                "safe_mode": self.safe_mode,
            })
            return f"❌ 工具未启用：{name}（对应插件已在管理插件里关闭，先开启再试）"
        if self.safe_mode and name not in SAFE_MODE_ALLOW:
            self._emit_audit({
                "phase": "blocked", "name": name, "reason": "safe_mode",
                "args": _safe_preview(params or {}), "safe_mode": True,
            })
            return (f"🔒 安全模式已拦截工具「{name}」。"
                    f"安全模式只允许只读工具；要执行/写入，正常启动阿鲤再试。")

        params = params or {}

        need, label = confirm_label(name, params)
        if need and self.confirm:
            allowed, why = self.confirm(name, params, label)
            if not allowed:
                self._emit_audit({
                    "phase": "denied",
                    "name": name,
                    "reason": why or "user_denied",
                    "args": _safe_preview(params),
                    "safe_mode": self.safe_mode,
                })
                tail = ("（原因：" + why + "）") if why else ""
                return f"❌ 用户没有允许这个操作：{label}。{tail}不要绕过，直接说明卡在哪。"

        result = ""
        ok = False
        error = ""
        t0 = time.monotonic()
        self._emit_audit({
            "phase": "call",
            "name": name,
            "args": _safe_preview(params),
            "safe_mode": self.safe_mode,
        })
        try:
            result = str(spec.func(**params))
            ok = not (isinstance(result, str) and result.startswith("❌"))
            error = "" if ok else result[:160]
        except TypeError as e:
            result = f"❌ 参数不对（{name}）：{e}"
            ok, error = False, result[:160]
        except Exception as e:
            result = f"❌ 执行出错（{name}）：{type(e).__name__}: {e}"
            ok, error = False, result[:160]
        finally:
            ms = round((time.monotonic() - t0) * 1000, 1)
            self._emit_audit({
                "phase": "result",
                "name": name,
                "ok": ok,
                "ms": ms,
                "chars": len(result),
                "error": error,
                "safe_mode": self.safe_mode,
            })
        return result

    def func_map(self):
        """给 CodeAgent 的 exec 命名空间：名字 → 可调用对象。

        不能直接给裸函数：代码通道会绕开 call() 里的人工确认。
        普通工具不加壳（保持原行为），只有高危的那几个包一层。
        """
        out = {}
        for name, spec in self._tools.items():
            if not self.allowed(spec):
                continue
            out[name] = self._confirm_guarded(name, spec.func)
        return out

    def _confirm_guarded(self, name, func):
        def proxy(*args, **kwargs):
            params = _bind_params(func, args, kwargs)
            need, label = confirm_label(name, params)
            if need and self.confirm:
                allowed, why = self.confirm(name, params, label)
                if not allowed:
                    tail = ("（原因：" + why + "）") if why else ""
                    return f"❌ 用户没有允许这个操作：{label}。{tail}不要绕过，直接说明卡在哪。"
            return func(*args, **kwargs)

        proxy.__name__ = name
        proxy.__doc__ = getattr(func, "__doc__", None)
        return proxy
