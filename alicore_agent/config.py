"""配置层：凭证和运行参数只在后端，不下发到前端。

模型接入改成「用户自己填 Base URL + Key」：
  · config.json 只存服务商名字、地址、模型列表、当前选中的模型
  · 真 Key 全部放在 %APPDATA%\\AliCore\\secrets.json 的 provider_keys 里
  · 不再内置任何免费通道，也不再靠环境变量偷偷塞 key
"""

import json
import os
import sys
import threading

from .fsutil import atomic_write_json

# secrets.json 里只有一个顶层键，值形如 {"<provider_id>": "<key>"}
SECRET_KEYS = ("provider_keys",)

DEFAULT_CONFIG = {
    "providers": [],            # [{id, name, base_url, models: [], vision_model: ""}]
    "current_provider": "",     # 当前用的服务商 id
    "current_model": "",        # 当前用的模型名
    "system_prompt": "",
    "agent_mode": "json",
    "max_rounds": 6,
    "max_steps": 4,
    "sandbox": True,
    "temperature": 0.85,
    "history_limit": 10,
    "context_window": 131072,
    "max_output_tokens": 8192,
}


def _say(msg):
    """pythonw 下 stdout 是 None，日志走文件或直接跳过。"""
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:
        pass


def secrets_path():
    """%APPDATA%\\AliCore\\secrets.json（也在 tools_fs 的拒绝目录里）。

    测试或多实例可以用 ALICORE_SECRETS 指到别处，别再碰真实凭证。
    """
    override = os.environ.get("ALICORE_SECRETS")
    if override:
        return os.path.abspath(override)
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

        for k, v in raw.items():
            self.data[k] = v

        keys = secrets.get("provider_keys")
        self.data["provider_keys"] = keys if isinstance(keys, dict) else {}

        # 老版本 config.json 里的明文 key / 单家配置：迁成第一个 provider，再清掉明文
        legacy = raw.get("openai_base_url") or raw.get("glm_key") or raw.get("api_key")
        if legacy and not self.data.get("providers"):
            self._migrate_legacy(raw, secrets)

        # 老版本 config.json 里可能还留着明文 key，搬走
        if any((raw.get(k) or "").strip() for k in ("api_key", "glm_key", "openai_key", "anthropic_key")):
            self._scrub_legacy(raw)

    def _migrate_legacy(self, raw, secrets):
        """老配置升级：把单家 key + 中转地址变成一个 provider。"""
        try:
            base = (raw.get("openai_base_url") or "").strip()
            key = (secrets.get("openai_key") or secrets.get("api_key")
                   or raw.get("openai_key") or raw.get("api_key") or "").strip()
            name = "OpenAI 兼容"
            if "deepseek" in base:
                name, base = "DeepSeek", base or "https://api.deepseek.com"
            elif "bigmodel" in base:
                name, base = "智谱 GLM", base or "https://open.bigmodel.cn/api/paas/v4"
            elif "anthropic" in base:
                name = "Anthropic"
            # 只存了 key、没存地址的，不猜；让用户自己填一次中转站地址
            if not (base and key):
                return
            pid = "p1"
            self.data["providers"] = [{"id": pid, "name": name, "base_url": base,
                                       "models": [], "vision_model": ""}]
            self.data["provider_keys"] = {pid: key}
            self.data["current_provider"] = pid
            self.save()
            _say(f"[配置] 已把老配置迁移成服务商：{name}")
        except Exception as e:
            _say(f"[配置] 迁移老配置失败：{e}")

    def _scrub_legacy(self, raw):
        try:
            out = {k: v for k, v in self.data.items() if k not in
                   ("api_key", "glm_key", "openai_key", "anthropic_key", "openai_base_url", "anthropic_max_tokens")}
            atomic_write_json(self.path, out, mode=0o644)
            _say("[配置] config.json 里的明文 key 已清掉")
        except Exception as e:
            _say(f"[配置] 清理明文 key 失败：{e}")

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
        atomic_write_json(self.secrets_file, existing, mode=0o600)
        try:
            os.chmod(self.secrets_file, 0o600)
        except Exception:
            pass

    def save(self):
        """provider_keys 进 secrets.json，config.json 只留空位。"""
        keys = self.data.get("provider_keys") or {}
        self._write_secrets({"provider_keys": keys})
        out = {k: v for k, v in self.data.items() if k != "provider_keys"}
        for k, v in DEFAULT_CONFIG.items():
            out.setdefault(k, v)
        atomic_write_json(self.path, out, mode=0o644)

    # ---------------- 服务商 ----------------
    def providers(self):
        out = []
        for p in (self.get("providers") or []):
            if isinstance(p, dict) and p.get("id"):
                out.append(p)
        return out

    def provider(self, pid):
        for p in self.providers():
            if p.get("id") == pid:
                return p
        return None

    def provider_key(self, pid):
        keys = self.data.get("provider_keys") or {}
        if not isinstance(keys, dict):
            return ""
        return (keys.get(pid) or "").strip()

    def set_provider(self, provider, key):
        """新增或更新一个服务商；key 走 secrets，不进 config.json。"""
        provs = self.providers()
        pid = (provider or {}).get("id") or ""
        if not pid:
            pid = "p" + str(len(provs) + 1)
            while self.provider(pid):
                pid += "x"
        entry = {"id": pid, "name": (provider.get("name") or "").strip() or "未命名服务商",
                 "base_url": (provider.get("base_url") or "").strip().rstrip("/"),
                 "models": list(provider.get("models") or []),
                 "vision_model": (provider.get("vision_model") or "").strip()}
        replaced = False
        for i, p in enumerate(provs):
            if p.get("id") == pid:
                provs[i] = entry
                replaced = True
                break
        if not replaced:
            provs.append(entry)
        self.data["providers"] = provs
        if key:
            keys = dict(self.data.get("provider_keys") or {})
            keys[pid] = key.strip()
            self.data["provider_keys"] = keys
        if not self.data.get("current_provider"):
            self.data["current_provider"] = pid
        self.save()
        return entry

    def remove_provider(self, pid):
        provs = [p for p in self.providers() if p.get("id") != pid]
        self.data["providers"] = provs
        keys = dict(self.data.get("provider_keys") or {})
        keys.pop(pid, None)
        self.data["provider_keys"] = keys
        if self.data.get("current_provider") == pid:
            self.data["current_provider"] = provs[0]["id"] if provs else ""
            self.data["current_model"] = (provs[0].get("models") or [""])[0] if provs else ""
        self.save()

    def set_current_model(self, pid, model):
        self.data["current_provider"] = pid
        self.data["current_model"] = model
        self.save()

    # ---------------- 前端视图 ----------------
    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set_many(self, **kw):
        for k, v in kw.items():
            self.data[k] = v

    def public_view(self):
        """给前端看的配置：永远不含 key 真值，只说配没配。"""
        provs = []
        for p in self.providers():
            q = dict(p)
            q["has_key"] = bool(self.provider_key(p["id"]))
            provs.append(q)
        return {
            "system_prompt": self.get("system_prompt"),
            "agent_mode": self.get("agent_mode"),
            "providers": provs,
            "current_provider": self.get("current_provider") or "",
            "current_model": self.get("current_model") or "",
        }

    def describe_secrets(self):
        out = {}
        for p in self.providers():
            v = self.provider_key(p["id"])
            out[p["id"]] = {"configured": bool(v), "masked": (v[:5] + "…" + v[-4:]) if len(v) >= 12 else ("…" if v else "")}
        return out
