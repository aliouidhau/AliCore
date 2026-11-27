"""带退避重试的 HTTP POST。

抄自 DSH 的 dsh-llm-retry：只重试「等一会儿可能就好」的错误——
429 / 5xx / 超时 / 连接断开。401、400、403 这类重试只会白烧配额，一律不重试。
退避从 0.5s 起翻倍、8s 封顶、加 10% 抖动；服务端给了 Retry-After 且不超上限时优先听它的。
"""

import random
import time

import requests

RETRY_MAX = 3          # 额外重试次数（总共最多 4 次请求）
RETRY_BASE = 0.5       # 首次退避
RETRY_CAP = 8.0        # 单次退避上限


def _sleep(delay):
    time.sleep(delay + delay * 0.1 * random.random())


def _retry_after(resp):
    try:
        raw = (resp.headers or {}).get("Retry-After")
        if not raw:
            return None
        sec = float(str(raw).strip())
        if 0.1 <= sec <= RETRY_CAP:
            return sec
    except Exception:
        pass
    return None


def post_with_retry(url, headers=None, payload=None, timeout=90, max_retries=RETRY_MAX):
    """POST，瞬时错误重试；返回最后一次 response，不替调用方判定业务错误。"""
    delay = RETRY_BASE
    attempt = 0
    while True:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError):
            if attempt >= max_retries:
                raise
            _sleep(delay)
            delay = min(delay * 2, RETRY_CAP)
            attempt += 1
            continue
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries:
            wait = _retry_after(resp)
            if wait is None:
                _sleep(delay)
            else:
                time.sleep(wait)
            delay = min(delay * 2, RETRY_CAP)
            attempt += 1
            continue
        return resp
