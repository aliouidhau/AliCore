"""规则式偏好捕获：零 LLM、零 token。

抄自 dsh-engram 的 autoCapturePreferences。阿鲤原来靠本地 Ollama 每 12 轮抽事实，
慢、而且抽得不准（memory.md 里出现过「女方正在测试插件」——把助手自己记成了用户的
第三人称事实）。可「用户纠正她」这种话，其实按句式就能认出来，而且立刻就该记住。

三种方向（对应 dsh-engram 的 avoid / switch-to / prefer）：
  avoid   ——「不要用 emoji」「别用官方腔」
  switch  ——「改用大白话」「以后统一用正斜杠」
  prefer  ——「我喜欢简短的回答」「最好先问我」

三道过滤器压误报：
  · 疑问句（「你能用中文吗？」不是偏好）
  · 一次性指令（「这次先这样」不是长期规则）
  · 元话语（「记住我叫学生甲」是让她记，不是偏好句）
"""

import datetime
import re

_X = r"(?P<x>[^\n，。；！？,;!?]{1,24})"

_AVOID = [
    re.compile(r"不(?:要|许|准)(?:再)?(?:用|说|提|写|叫|发)\s*" + _X),
    re.compile(r"别(?:再)?(?:用|说|提|写|叫)\s*" + _X),
]
_SWITCH = [
    re.compile(r"改(?:用|成)\s*" + _X),
    re.compile(r"换(?:成|用)\s*" + _X),
    re.compile(r"(?:以后|今后)(?:统一)?(?:用|说|叫)\s*" + _X),
    re.compile(r"统一(?:用|说|叫)\s*" + _X),
]
_PREFER = [
    re.compile(r"我(?:更|最)?喜欢\s*" + _X),
    re.compile(r"最好(?:能|是)?\s*" + _X),
    re.compile(r"尽量\s*" + _X),
    re.compile(r"优先(?:用|选)?\s*" + _X),
]

_QUESTION = ("吗", "？", "?", "呢", "怎么", "为什么", "是否", "能不能", "可不可以")
_ONEOFF = ("这次", "这一回", "暂时", "临时", "就先", "仅此一次", "只这一次")
_META = ("记住", "记一下", "记下", "记忆", "别忘了", "你记得")

_LABEL = {"avoid": "用户要求别用", "switch": "用户要求改用", "prefer": "用户偏好"}


def _clean(x):
    x = (x or "").strip().strip("，。；！？,;!?、 ")
    return x[:24]


def capture_preferences(text):
    """返回 [(方向, 陈述)]；没有就返回 []。纯函数，不碰磁盘。"""
    t = (text or "").strip()
    if len(t) < 3 or len(t) > 300:
        return []
    if any(k in t for k in _META):
        return []
    if t.endswith(_QUESTION[:4]) or any(k in t for k in _QUESTION[4:]):
        return []
    if any(k in t for k in _ONEOFF):
        return []
    out = []
    seen = set()
    for direction, pats in (("avoid", _AVOID), ("switch", _SWITCH), ("prefer", _PREFER)):
        for pat in pats:
            for m in pat.finditer(t):
                x = _clean(m.group("x"))
                if not x or x in seen:
                    continue
                seen.add(x)
                out.append((direction, x))
    return out[:3]


def format_memory_line(direction, statement):
    """拼成 memory.md 的一行：- [MM-DD][偏好] 用户要求别用：xxx"""
    today = datetime.datetime.now().strftime("%m-%d")
    label = _LABEL.get(direction, "用户偏好")
    return "- [%s][偏好] %s：%s" % (today, label, statement)
