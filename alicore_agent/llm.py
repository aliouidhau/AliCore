"""模型调用层：只认 OpenAI 兼容协议。

用户自己填 Base URL + Key，接入后才能选模型；
没有内置的 GLM 免费通道、本地 Ollama 兜底、额度回落这些省 token 的旁路。
统一返回 {content, tool_calls, usage, provider, model}，engine 不关心是哪家。
"""

import json

import requests

from .net import post_with_retry


class LLMError(Exception):
    pass


def chat_url(base_url):
    """把用户填的地址归一化成 /chat/completions。三种写法都认：
    https://api.deepseek.com  /  .../v1  /  .../v1/chat/completions
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def models_url(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


class LLMClient:
    def __init__(self, config):
        self.config = config
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self._route_note = ""

    # ---------------- 选通道 ----------------
    def _provider(self, provider=None):
        provs = self.config.providers()
        pid = (provider or "").strip() or (self.config.get("current_provider") or "")
        if pid:
            for p in provs:
                if p.get("id") == pid:
                    return p
        if len(provs) == 1:
            return provs[0]
        if not provs:
            raise LLMError("还没有接入模型：点输入框下面的模型按钮 → 接入 API")
        raise LLMError("当前选的服务商不在了，重新选一个模型")

    def _pick(self, provider=None, model_name=None):
        p = self._provider(provider)
        pid = p.get("id") or ""
        key = (self.config.provider_key(pid) or "").strip()
        if not key:
            raise LLMError(f"{p.get('name') or '这个服务商'} 还没有 Key，点模型按钮重新接入")
        model = (model_name or "").strip()
        if not model:
            model = (self.config.get("current_model") or "").strip()
        if not model and pid == (self.config.get("current_provider") or ""):
            model = (p.get("models") or [""])[0]
        if not model:
            raise LLMError("还没有选模型")
        url = chat_url(p.get("base_url"))
        if not url:
            raise LLMError(f"{p.get('name') or '这个服务商'} 没有填 Base URL")
        return url, key, model, pid, p.get("name") or pid

    # ---------------- 对话 ----------------
    def chat(self, messages, tools=None, provider=None, model_name=None, temperature=None, timeout=90):
        url, key, model, pid, pname = self._pick(provider, model_name)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.get("temperature", 0.85),
        }
        if tools:
            payload["tools"] = tools

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            resp = post_with_retry(url, headers=headers, payload=payload, timeout=timeout)
        except requests.Timeout:
            raise LLMError("模型接口超时了，等一会儿再发")
        except Exception as e:
            raise LLMError(f"请求失败：{e}{getattr(self, '_route_note', '')}")

        if resp.status_code == 429:
            raise LLMError("触发了限流（429），等几秒再发")
        if resp.status_code == 401:
            raise LLMError(f"{pname} 的 Key 无效（401），重新接入一下")
        if resp.status_code >= 400:
            raise LLMError(f"接口返回 {resp.status_code}：{resp.text[:300]}")

        try:
            data = resp.json()
        except Exception:
            raise LLMError(f"响应不是合法 JSON：{resp.text[:200]}")

        choice = (data.get("choices") or [{}])[0] or {}
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
            "completion_tokens": usage.get("completion_tokens", 0) or 0,
            "cache_hit": usage.get("prompt_cache_hit_tokens", 0) or 0,
        }

        content = (msg.get("content") or "").strip()
        if not content:
            content = (msg.get("reasoning_content") or "").strip()

        if choice.get("finish_reason") == "length" and content:
            content += "\n\n（这条被截断了：输出到了上限。可以让我接着说完，或把 max_output_tokens 调大）"

        return {
            "content": content,
            "tool_calls": msg.get("tool_calls") or [],
            "usage": self.last_usage,
            "provider": pid,
            "model": model,
        }

    # ---------------- 识图 ----------------
    def vision(self, text, images, model=None, timeout=90):
        """识图。images 是 dataURL 列表；没单独配 vision_model 就用当前模型。"""
        p = self._provider()
        pid = p.get("id") or ""
        key = (self.config.provider_key(pid) or "").strip()
        if not key:
            raise LLMError(f"{p.get('name') or '这个服务商'} 还没有 Key")
        want = (model or p.get("vision_model") or "").strip()
        if not want:
            want = (self.config.get("current_model") or "").strip()
        url = chat_url(p.get("base_url"))
        if not url:
            raise LLMError("服务商没有填 Base URL")

        parts = [{"type": "image_url", "image_url": {"url": self._fit_image(u)}} for u in (images or [])[:4]]
        parts.append({"type": "text", "text": text or "描述这些图片"})
        payload = {"model": want, "messages": [{"role": "user", "content": parts}]}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            resp = post_with_retry(url, headers=headers, payload=payload, timeout=timeout)
        except requests.Timeout:
            raise LLMError("识图超时了，图太大或网慢，等一会儿再发")
        except Exception as e:
            raise LLMError(f"识图请求失败：{e}")

        if resp.status_code == 401:
            raise LLMError(f"{p.get('name') or '服务商'} 的 Key 无效（401）")
        if resp.status_code >= 400:
            raise LLMError(f"识图接口返回 {resp.status_code}：{resp.text[:300]}")

        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        usage = data.get("usage") or {}
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
            "completion_tokens": usage.get("completion_tokens", 0) or 0,
        }
        return (msg.get("content") or "").strip()

    def _fit_image(self, dataurl, max_side=1024):
        """前端传的是 dataURL，原图可能几 MB，模型会拒。超过就缩一下。"""
        if not isinstance(dataurl, str) or not dataurl.startswith("data:image"):
            return dataurl
        if "," not in dataurl:
            return dataurl
        try:
            import base64
            import io

            from PIL import Image

            head, body = dataurl.split(",", 1)
            raw = base64.b64decode(body)
            im = Image.open(io.BytesIO(raw))
            if max(im.size) <= max_side:
                return dataurl
            im = im.convert("RGB")
            ratio = max_side / max(im.size)
            im = im.resize((int(im.width * ratio), int(im.height * ratio)))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return dataurl
