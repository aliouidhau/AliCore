"""凭证脱敏：写进记忆/日志之前先把密钥抹掉。

抄自 dsh-engram 的确定性脱敏器：API key、JWT、Bearer、私钥、
AWS/GitHub/Stripe/Slack token、以及 key=value 形式的密钥，
一律替换成 <REDACTED:类型>，而且**在去重和落盘之前**完成。

为什么必须做：11-24 那次事故里，明文 key 在磁盘上躺过好几个备份。
记忆本和事件账本都可能被模型顺手写进凭证——落盘前抹掉是最后一道闸。
"""

import re

_PATTERNS = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("stripe-key", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{8,}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}")),
]

_KV = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{12,}['\"]?"
)


def redact(text):
    """把文本里的凭证替换成 <REDACTED:类型>。非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for name, pat in _PATTERNS:
        out = pat.sub("<REDACTED:%s>" % name, out)
    out = _KV.sub(lambda m: "%s=<REDACTED:secret>" % m.group(1), out)
    return out


def has_secret(text):
    """这段文本里有没有凭证（脱敏前后不一致就是有）。"""
    return isinstance(text, str) and redact(text) != text
