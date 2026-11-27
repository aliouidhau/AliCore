"""模型调用层：和 agent 逻辑解耦。

默认走 GLM（免费额度），只有显式指定才走 DeepSeek——基站烧钱的根子就在拿 DeepSeek
跑多轮工具调用，这条默认不能破。
统一返回 {content, tool_calls, usage, provider}，engine 不关心是哪家。
"""

import json
import os

import requests

from .net import post_with_retry

from .budget import DailyBudget

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

GLM_MODEL = "glm-4-flash"
DEEPSEEK_MODEL = "deepseek-chat"
OLLAMA_MODEL = "qwen2.5:7b"
OPENAI_MODEL = "gpt-5.6"
ANTHROPIC_MODEL = "claude-opus-5"


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, config):
        self.config = config
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        # 路由提示：只填了中转 key 没填地址这类情况，出错时拼进消息里
        self._route_note = ""
        # 额度锁跟配置文件放在一起，跨进程重启也认账
        self.budget = DailyBudget(getattr(config, "base_dir", "."), config.get("daily_limit"))

    def _pick(self, provider=None, model_name=None):
        """按模型名选通道。

        前端菜单传的就是模型名（deepseek-chat / deepseek-v4-pro / deepseek-flash …），
        名字以 deepseek 开头就走 DeepSeek，否则回落 GLM。provider 可以显式指定来覆盖。

        OpenRouter 那种 openai/gpt-4o、anthropic/claude-3.5 的写法也认：
        判断通道用去掉前缀的名字，真正发出去的模型名看有没有配中转（网关要原名，
        官方不认前缀）。
        """
        raw_name = (model_name or "").strip()
        name = raw_name.lower()
        prov = (provider or "").strip().lower()
        self._route_note = ""
        bare, gateway_prefix = name, ""
        for prefix in ("openrouter/", "openai/", "anthropic/", "deepseek/", "google/", "glm/"):
            if bare.startswith(prefix):
                bare = bare[len(prefix):]
                gateway_prefix = prefix
                break

        if prov == "ollama" or bare.startswith("qwen") or bare.startswith("ollama"):
            model = raw_name or self.config.get("ollama_model") or OLLAMA_MODEL
            return OLLAMA_URL, "", model, "ollama"
        if prov in ("openai", "gpt") or bare.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
            key = (self.config.get("openai_key") or "").strip()
            if not key:
                raise LLMError("没配 GPT Key（密钥文件里没有 openai_key）")
            base = (self.config.get("openai_base_url") or "").strip()
            send = raw_name
            if gateway_prefix and not base:
                send = bare
            if not base:
                self._route_note = "（注意：openai_base_url 是空的，这次是直连 api.openai.com）"
            return self._openai_url(), key, (send or self.config.get("openai_model") or OPENAI_MODEL), "openai"
        if prov in ("anthropic", "claude") or bare.startswith("claude"):
            akey = (self.config.get("anthropic_key") or "").strip()
            if akey:
                # 官方 Messages API 不认 anthropic/ 前缀
                return ANTHROPIC_URL, akey, (bare or ANTHROPIC_MODEL), "anthropic"
            # 没填官方 Claude Key、但配了 OpenAI 兼容中转：Claude 从中转走，工具调用也能用
            okey = (self.config.get("openai_key") or "").strip()
            if okey:
                return self._openai_url(), okey, (raw_name or "claude-sonnet-5"), "openai"
            raise LLMError("没配 Claude Key（anthropic_key），也没有可用中转（openai_key + openai_base_url）")
        if bare.startswith("deepseek") or prov == "deepseek":
            key = self.config.get("api_key")
            if not key:
                raise LLMError("没配 DeepSeek Key（密钥文件里没有 api_key）")
            return DEEPSEEK_URL, key, (bare or DEEPSEEK_MODEL), "deepseek"
        key = self.config.get("glm_key")
        if not key:
            raise LLMError("没配 GLM Key（密钥文件里没有 glm_key），点右下角齿轮填一下")
        model = bare if bare.startswith("glm") else GLM_MODEL
        return GLM_URL, key, model, "glm"

    def chat(self, messages, tools=None, provider=None, model_name=None, temperature=None, timeout=90):
        url, key, model, prov = self._pick(provider, model_name)
        if prov == "ollama":
            return self._chat_ollama(url, model, messages, temperature, timeout)
        if prov == "anthropic":
            return self._chat_anthropic(url, key, model, messages, temperature, timeout)

        # 额度锁：DeepSeek 今天烧超了，就强制回落 GLM，不让它无声地继续烧。
        # 只拦 deepseek 分支，GLM 免费档不受影响。
        if prov == "deepseek" and self.budget.over_limit():
            if self.config.get("glm_key"):
                url, key, model, prov = self._pick(provider="glm")
            else:
                raise LLMError(f"今日 DeepSeek 额度已用完（{self.budget.describe()}），"
                               f"GLM Key 也没配，没法回落")

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
            raise LLMError("触发了免费额度/并发限制（429），等几秒再发")
        if resp.status_code == 401:
            label = {"deepseek": "DeepSeek", "glm": "GLM", "openai": "OpenAI"}.get(prov, prov or "模型")
            raise LLMError(f"{label} Key 无效（401），检查密钥文件")
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
            # DeepSeek 的前缀缓存：命中部分按缓存价计费，人设+工具说明这种固定前缀能吃到
            "cache_hit": usage.get("prompt_cache_hit_tokens", 0) or 0,
        }

        content = (msg.get("content") or "").strip()
        if not content:
            # 推理模型的答案偶尔落在 reasoning_content（content 为空只说明预算被思考吃光了）
            content = (msg.get("reasoning_content") or "").strip()

        # 输出撞到上限被截断时，原样贴上去会让人以为她说完了
        if choice.get("finish_reason") == "length" and content:
            content += "\n\n（这条被截断了：输出到了上限。可以让我接着说完，或把 max_output_tokens 调大）"

        # 只给 DeepSeek 记账，GLM 免费档不记（记了也没意义，还会干扰额度判断）
        if prov == "deepseek":
            self.budget.record(self.last_usage["prompt_tokens"],
                               self.last_usage["completion_tokens"],
                               self.last_usage.get("cache_hit", 0))

        return {
            "content": content,
            "tool_calls": msg.get("tool_calls") or [],
            "usage": self.last_usage,
            "provider": prov,
            "model": model,
        }

    def _chat_ollama(self, url, model, messages, temperature, timeout):
        """本地 Ollama 通道：零 API 费，响应格式与 OpenAI 不同，单独解析。"""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature if temperature is not None else 0.2},
        }
        try:
            resp = post_with_retry(url, payload=payload, timeout=timeout)
        except requests.Timeout:
            raise LLMError("本地 Ollama 超时了（模型可能在加载），等一会儿再试")
        except Exception as e:
            raise LLMError(f"本地 Ollama 请求失败：{e}")
        if resp.status_code >= 400:
            raise LLMError(f"Ollama 返回 {resp.status_code}：{resp.text[:300]}")
        try:
            data = resp.json()
        except Exception:
            raise LLMError(f"Ollama 响应不是合法 JSON：{resp.text[:200]}")
        msg = data.get("message") or {}
        content = (msg.get("content") or "").strip()
        self.last_usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0) or 0,
            "completion_tokens": data.get("eval_count", 0) or 0,
        }
        return {
            "content": content,
            "tool_calls": [],
            "usage": self.last_usage,
            "provider": "ollama",
            "model": model,
        }

    def _openai_url(self):
        """OpenAI 官方 / 中转站地址归一化：base、/v1、完整 endpoint 三种写法都认。"""
        base = (self.config.get("openai_base_url") or "").strip().rstrip("/")
        if not base:
            return OPENAI_URL
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def _chat_anthropic(self, url, key, model, messages, temperature, timeout):
        """Claude 官方通道。Messages API 格式与 OpenAI 不同：
        system 是顶层字段、返回是 content 块数组、没有 tool_calls 字段。
        这里先只走纯对话（不带工具）；要工具的话用中转站的 OpenAI 兼容模式。
        """
        system_parts, conv = [], []
        for m in messages or []:
            role = m.get("role")
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                conv.append({"role": role, "content": content})
        if not conv:
            conv = [{"role": "user", "content": "hi"}]

        payload = {
            "model": model,
            "max_tokens": int(self.config.get("anthropic_max_tokens") or 4096),
            "messages": conv,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if temperature is not None:
            payload["temperature"] = temperature

        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        try:
            resp = post_with_retry(url, headers=headers, payload=payload, timeout=timeout)
        except requests.Timeout:
            raise LLMError("Claude 接口超时了，等一会儿再发")
        except Exception as e:
            raise LLMError(f"Claude 请求失败：{e}")

        if resp.status_code == 401:
            raise LLMError("Claude Key 无效（401），检查密钥文件里的 anthropic_key")
        if resp.status_code == 429:
            raise LLMError("Claude 触发了限流（429），等几秒再发")
        if resp.status_code >= 400:
            raise LLMError(f"Claude 接口返回 {resp.status_code}：{resp.text[:300]}")

        try:
            data = resp.json()
        except Exception:
            raise LLMError(f"Claude 响应不是合法 JSON：{resp.text[:200]}")

        blocks = data.get("content") or []
        content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        usage = data.get("usage") or {}
        self.last_usage = {
            "prompt_tokens": usage.get("input_tokens", 0) or 0,
            "completion_tokens": usage.get("output_tokens", 0) or 0,
        }
        return {
            "content": content,
            "tool_calls": [],
            "usage": self.last_usage,
            "provider": "anthropic",
            "model": model,
        }

    # ---------------- 识图（固定走 GLM-4V，不占 DeepSeek 的钱） ----------------
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

    def vision(self, text, images, model="glm-4v-flash", timeout=90):
        """识图。images 是 dataURL 列表（前端 FileReader 出来的就是这种）。"""
        key = self.config.get("glm_key")
        if not key:
            raise LLMError("没配 GLM Key（密钥文件里没有 glm_key），点右下角齿轮填一下")

        parts = [
            {"type": "image_url", "image_url": {"url": self._fit_image(u)}}
            for u in (images or [])[:4]
        ]
        parts.append({"type": "text", "text": text or "描述这些图片"})

        payload = {"model": model, "messages": [{"role": "user", "content": parts}]}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = post_with_retry(GLM_URL, headers=headers, payload=payload, timeout=timeout)
        except requests.Timeout:
            raise LLMError("识图超时了，图太大或网慢，等一会儿再发")
        except Exception as e:
            raise LLMError(f"识图请求失败：{e}")

        if resp.status_code == 429:
            raise LLMError("识图触发了免费额度限制（429），等几秒再发")
        if resp.status_code == 401:
            raise LLMError("GLM Key 无效（401），检查密钥文件里的 glm_key")
        if resp.status_code >= 400:
            raise LLMError(f"识图接口返回 {resp.status_code}：{resp.text[:300]}")

        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        self.last_usage = {
            "prompt_tokens": (data.get("usage") or {}).get("prompt_tokens", 0) or 0,
            "completion_tokens": (data.get("usage") or {}).get("completion_tokens", 0) or 0,
        }
        return (msg.get("content") or "").strip()
