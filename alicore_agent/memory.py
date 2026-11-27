"""本地记忆检索：jieba 分词 + BM25，不调任何 API。

BM25 的打分公式出自信息检索的公开文献（Robertson & Zaragoza, 2009），
代码是按公式自己写的，没有拷第三方实现；分词用 jieba（MIT）。

为什么不用 2-gram：中文 2-gram 把「哪些」切成碎片，IDF 反而给它高分，
真实 348 条历史上四个测试问题全部误匹配。jieba 有词典，知道「哪些」是虚词、
「smolagents」是专名；配上停用词之后，同样的历史同样的问法全部命中。

不是 mem0 那种「让 LLM 抽事实」的路子——那要额外调一次模型，token 直接翻倍。
这里只做「把过去的问题找回来」，索引在本地内存里算，一分钱不花。
"""

import datetime
import math
import os
import re
from collections import Counter

# 虚词和套话。jieba 会切出这些，但它们对「问的是哪件事」没有信息量。
_STOP = set(
    "的 了 是 我 你 他 她 它 这 那 有 没 在 和 与 就 都 也 很 会 要 能 可 "
    "什么 哪些 怎么 怎样 多少 吗 呢 吧 啊 嗯 哦 这个 那个 我们 你们 他们 "
    "一个 一下 可以 然后 已经 现在 时候 如果 因为 所以 但是 而且 或者 "
    "还有 就是 不是 还是 一样 上面 下面 上面 这样 那样 觉得 知道 觉得".split()
)


def tokenize(text):
    """jieba 分词 + 停用词过滤。jieba 没装就退回英文词 + 中文 4-gram。

    退路是必要的：非要 jieba 才能跑，等于给基站加了个硬依赖，
    装不上就整个历史模块一起挂。
    """
    text = str(text or "")
    # 2 位以内的纯数字在中文里必然歧义（11 = 11点 / 11月 / 11号 / 第11轮），实测会串场：
    # 问「11月3号算的那个数」会捞回「我11点半吃的」。4 位以上保留（4060、2024 是型号/年份）。
    # 丢掉这个 token 后，「11点半吃的什么」仍靠「点半」命中，原有检索不受影响。
    out = [
        w.lower() for w in re.findall(r"[A-Za-z0-9_./\\-]{2,}", text)
        if not (len(w) <= 2 and w.isdigit())
    ]
    try:
        import jieba

        jieba.setLogLevel(20)
        for w in jieba.lcut_for_search(text):
            w = w.strip().lower()
            if not w or w in _STOP:
                continue
            if re.fullmatch(r"[a-z0-9_./\\-]+", w):
                continue  # 英文已被上面抓过
            if len(w) == 1 and w in "的了是我你他她它这那有没在和与就都很会要能可吗呢吧啊嗯哦对":
                continue
            out.append(w)
    except ImportError:
        # 短片段整词收。原来的 range(len(han) - 3) 在「吃啥」「改词」这种
        # 两三个字的问句上是空 range——一个 token 都不产出，整个中文检索等于失效。
        for han in re.findall(r"[\u4e00-\u9fff]+", text):
            if len(han) <= 4:
                out.append(han)
            else:
                for i in range(len(han) - 3):
                    out.append(han[i:i + 4])
    return out


class BM25Index:
    """只读索引。历史变了就重建，348 条大约 0.3 秒，比调一次模型便宜太多。"""

    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self._tf = []
        self._len = []
        self.total = len(docs) or 1
        for d in docs:
            toks = tokenize(d)
            self._tf.append(Counter(toks))
            self._len.append(len(toks))
        self.avgdl = (sum(self._len) / self.total) or 1

        df = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self.idf = {w: math.log(1 + (self.total - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self._unknown_idf = math.log(1 + (self.total + 0.5) / 0.5)

    def scores(self, query):
        """对每条文档打分，返回按分数降到低的 (score, index) 列表。"""
        qt = set(tokenize(query))
        out = []
        for i, tf in enumerate(self._tf):
            s = 0.0
            for w in qt:
                f = tf.get(w, 0)
                if not f:
                    continue
                idf = self.idf.get(w, self._unknown_idf)
                k = self.k1 * ((1 - self.b) + self.b * self._len[i] / self.avgdl)
                s += idf * (f * (self.k1 + 1)) / (f + k)
            if s > 0:
                out.append((s, i))
        out.sort(reverse=True)
        return out


def load_memory_entries(path):
    """读 memory.md，按行切条目，只留非空行。

    remember 工具写的是「- [日期] 内容」一行一条，所以按行切就是按条切。
    多行条目（换行续写）暂时不认——现在约定是「一条记一件完整的事」单行写。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip()]


def search_memory_entries(query, entries, k=3):
    """对记忆条目做 BM25 检索，返回 [(score, index), ...] 按相关度降序。

    复用同一个 BM25Index：不调模型、本地算。条目少时（<10 条）BM25 的
    IDF 区分度一般，分数只能当相对参考，别当绝对阈值。
    """
    if not entries:
        return []
    index = BM25Index(entries)
    return index.scores(query)[:k]


# ---------------------------------------------------------------- 重要度重排
#
# 抄 pet-mochi 的记忆排序：0.4×重要度 + 0.3×新鲜度 + 0.3×相关度。
# 它那边的三个量都存在 SQLite 里；阿鲤的记忆本只有「- [日期] 内容」这一行，
# 所以这里做两件事：
#   1) 重要度用行首标记（!!=红线/身份/约定，!=项目/决定，~=临时偏好，空=普通）；
#   2) 新鲜度从 [日期] 算天数衰减；
#   3) 相关度沿用 BM25，先按 RECALL_MIN_SCORE 过滤（宁缺毋滥），再归一化参与排序。
# 这样「旧但重要」能压过「新但琐碎」，同时不会为了排序把不相关的东西硬塞进来。

_IMPORTANCE_WEIGHT = {"!!": 1.0, "!": 0.8, "": 0.6, "~": 0.35}
_MARKER_RE = re.compile(r"^(!!|!|~)\s+")
_DATE_RE = re.compile(r"^\[([^\]]*)\]")


def _days_old(date_str):
    """把「MM-DD」或「YYYY-MM-DD」算成距今多少天；解析不了按 365 天。"""
    d = (date_str or "").strip()
    now = datetime.date.today()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            dt = datetime.datetime.strptime(d, "%Y-%m-%d").date()
        elif re.fullmatch(r"\d{1,2}-\d{1,2}", d):
            dt = datetime.datetime.strptime(f"{now.year}-{d}", "%Y-%m-%d").date()
            if dt > now:  # 日期在明年 → 是去年的
                dt = datetime.datetime.strptime(f"{now.year - 1}-{d}", "%Y-%m-%d").date()
        else:
            return 365.0
        return max(0.0, float((now - dt).days))
    except Exception:
        return 365.0


def _recency_weight(days):
    """新鲜度：刚记的 1.0，30 天后 0.5，90 天后 0.25。"""
    return 1.0 / (1.0 + days / 30.0)


def _entry_meta(entry):
    """从一行记忆里拆出 (重要度标记, 距今多少天)。"""
    s = re.sub(r"^-\s*", "", (entry or "").strip())
    days = 365.0
    m = _DATE_RE.match(s)
    if m:
        days = _days_old(m.group(1))
        s = s[m.end():].strip()
    marker = ""
    mm = _MARKER_RE.match(s)
    if mm:
        marker = mm.group(1)
    return marker, days


def rank_memory_entries(query, entries, k=3, min_rel=3.5):
    """相关度 + 重要度 + 新鲜度 三合一重排，返回 [(combined, index), ...]。

    先按 BM25 相关度过门槛（min_rel，沿用 RECALL_MIN_SCORE），
    再从通过的那批里按 pet-mochi 的 0.4/0.3/0.3 合成排序。
    """
    if not entries:
        return []
    hits = BM25Index(entries).scores(query or "")
    relevant = [(s, i) for s, i in hits if s >= min_rel]
    if not relevant:
        return []
    max_s = max(s for s, _ in relevant) or 1.0
    out = []
    for s, i in relevant:
        marker, days = _entry_meta(entries[i])
        imp = _IMPORTANCE_WEIGHT.get(marker, 0.6)
        rec = _recency_weight(days)
        rel_n = s / max_s
        combined = 0.4 * imp + 0.3 * rec + 0.3 * rel_n
        out.append((round(combined, 4), i))
    out.sort(key=lambda x: x[0], reverse=True)
    return out[:k]

# ==================== 记忆条的单一来源 ====================
# 为什么放在内核：桌面端（ali_app.py）和引擎（engine.py）都要认同一套
# 类型、ID 和写原语。两边各写一份 hash 方案，编辑/删除就会指错条目。
# 11-24 与桌面端对齐：ID = sha1(raw + 序号)[:12]，同一条重复行也能区分。

import hashlib
import threading

from .fsutil import atomic_write_text
from .redact import redact

MEMORY_KINDS = ("身份", "红线", "偏好", "项目", "决定", "待办", "事实", "愿望")
# 「永远该在」的类型：不走检索、不受相关度门槛影响，每轮必注入
MEMORY_PIN_KINDS = ("身份", "红线", "偏好", "决定", "待办")

# 跨模块写锁：引擎的后台抽取和桌面端的手工编辑共用它，避免互相覆盖
MEMORY_LOCK = threading.RLock()


def memory_entry_id(raw, occurrence=1):
    """条目稳定 ID：同一行文本得同一个 ID；重复行靠出现序号区分。"""
    return hashlib.sha1(
        f"{raw}\x00{occurrence}".encode("utf-8")
    ).hexdigest()[:12]


def parse_memory_entries(text):
    """把 memory.md 正文解析成条目对象（UI 和接口共用）。

    返回每项：id / date / kind / text / raw / pinned / source / line。
    """
    import re as _re
    out = []
    seen = {}
    for idx, line in enumerate(str(text or "").splitlines()):
        raw = line.strip()
        if not raw.startswith("-"):
            continue
        rest = _re.sub(r"^-\s*", "", raw)
        tags = []
        while True:
            m = _re.match(r"^\[([^\]]*)\]\s*", rest)
            if not m:
                break
            tags.append(m.group(1))
            rest = rest[m.end():]
        date = next((t for t in tags if _re.fullmatch(r"\d{1,2}-\d{1,2}", t)
                     or _re.fullmatch(r"\d{4}-\d{2}-\d{2}", t)), "")
        kind = next((t for t in tags if t in MEMORY_KINDS), "")
        body = rest.strip()
        if not body:
            continue
        seen[raw] = seen.get(raw, 0) + 1
        out.append({
            "id": memory_entry_id(raw, seen[raw]),
            "date": date,
            "kind": kind,
            "text": body,
            "raw": raw,
            "pinned": kind in MEMORY_PIN_KINDS,
            "source": "memory.md",
            "line": idx,
        })
    return out


def parse_memory_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_memory_entries(f.read())
    except OSError:
        return []


def read_memory_text(path):
    with MEMORY_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""


def write_memory_text(path, text):
    """整篇原子替换（锁内）。失败时目标文件保持原样。

    落盘前先脱敏：记忆本和桌面端编辑都可能被顺手写进凭证，
    这是最后一道闸（抄 dsh-engram 的 deterministic redactor）。
    """
    with MEMORY_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return atomic_write_text(path, redact(text), mode=0o644)


def append_memory_lines(path, lines, max_entries=None):
    """在锁内读-改-写地追加若干行，原子落盘。

    为什么不是 open("a")：桌面端可能正在做「快照 → 改 → os.replace」，
    纯追加会写进已被替换掉的旧 inode，那几行就悄悄丢了。
    """
    lines = [ln for ln in (lines or []) if str(ln).strip()]
    if not lines:
        return False
    with MEMORY_LOCK:
        old = read_memory_text(path)
        body = old.rstrip("\n")
        new = (body + "\n" if body else "") + "\n".join(lines) + "\n"
        if max_entries:
            keep = [ln for ln in new.splitlines() if ln.strip().startswith("-")]
            if len(keep) > int(max_entries):
                new = "\n".join(keep[-int(max_entries):]) + "\n"
        write_memory_text(path, new)
        return True
