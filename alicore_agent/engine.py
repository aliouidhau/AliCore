"""Agent 引擎：两条通道。

1) JSON 通道：原生 tool_calls，多轮循环（旧版基站只跑一轮工具就收工，这是最硬的一个坑，这里修掉）。
2) CodeAgent 通道：模型一次写一段 Python 跑多步。
   这段根据 HuggingFace 开源的 smolagents（Apache-2.0）进行二次创作：
   保留了「一轮一个代码块、执行结果回灌、done() 收口」的循环骨架，
   改成中文提示词 + 自建受限命名空间 + 跨轮变量快照。

变量策略——这里是练手项目要验证的那个问题：
  参考 smolagents（Apache-2.0）的做法，命名空间跨轮持久（上轮算出的变量这轮还在）；
  但每步开头拍一次快照，这步报错就回滚到快照，只留干净的。
  能回滚的：不可变值（整数/字符串/元组…）被改，以及变量被换成另一个对象。
  回滚不了的：list/dict 被 in-place 改（append/pop/元素赋值）——只有深拷贝才能救，
  成本太高，这里不做，也不假装做了。
"""

import ast
import datetime
import io
import json
import math
import os
import re
import sys
import threading
import time
import traceback
import uuid
from types import SimpleNamespace

from . import prompts
from .memory import (BM25Index, load_memory_entries, rank_memory_entries,
                     MEMORY_KINDS, MEMORY_PIN_KINDS, append_memory_lines)
from .runtime import _SAFE_BUILTINS
from .events import EventStream

RESULT_LIMIT = 1500      # 工具结果回写进上下文的字符上限
PRINT_LIMIT = 400        # 单步 print 保留的字符上限
MAX_TOOL_ROUNDS = 8
ARMED_MAX_ROUNDS = 20    # 带入插件时临时放宽的轮次上限（普通聊天仍 6）

# 用户点「暂停」时这一轮的返回标记。前端认这个串，不当作正文显示。
CANCELLED_MARK = "__ALI_CANCELLED__"


class TurnCancelled(Exception):
    """用户中断了这一轮。只在检查点抛，不会打断正在发的 HTTP 请求。"""


# 执行护栏。数值参照 HuggingFace 开源的 smolagents（Apache-2.0）
# local_python_executor.py 第 57-60 行
# （MAX_EXECUTION_TIME_SECONDS = 30 / MAX_OPERATIONS = 10000000 / MAX_WHILE_ITERATIONS = 1000000），
# 但实现不同：它是 AST walker，能在循环体里逐节点检查；我们是 exec，
# 只能在 sys.settrace 的 line 事件上查。所以这几个值按「exec 查得到的粒度」重定。
CODE_TIME_LIMIT = 20          # 单步代码的挂钟上限（秒）
CODE_MAX_OPS = 20_000_000     # 单步累计执行行数上限（防「跑得不慢但就是算不完」）
CODE_PRINT_LIMIT = 200_000    # 单步 print 累计字符上限（防 for + print 把内存吃爆）
CODE_TRACE_EVERY = 2000       # 每 N 行查一次时间，摊薄 settrace 的开销

# AST 静态闸禁调用的函数名：运行时本来就没有（_SAFE_BUILTINS 不含），
# 提前拦是为了报错更清楚，也防将来往 _SAFE_BUILTINS 加东西时忘了这块。
# 这份清单参考了 HuggingFace 开源的 smolagents（Apache-2.0）local_python_executor.py
# 里「禁 dunder / 禁危险内建」的思路，具体名单按本项目的工具集重写。
_FORBIDDEN_CALLS = {
    "exec", "eval", "compile", "open", "__import__", "input",
    "getattr", "setattr", "delattr", "hasattr",
    "vars", "globals", "locals",
}

# 历史预算：条数只是上限，真正管住的是字符。
# 实测基站历史里一条模型回答就能到 2113 字符（约 1500 token），每轮重发一次，是最大的黑洞。
HISTORY_ITEM_LIMIT = 0         # 0 = 单条不截断（老版写死 400，长回答会被腰斩）
HISTORY_CHAR_BUDGET = 3600   # 历史总量最多这么多字符（1200→3600，让跨轮记忆多装几轮）
HISTORY_RECENT_N = 4         # 最近几轮保底（2 问 2 答）
HISTORY_RECENT_SHARE = 0.7   # 最近这几轮最多吃掉预算的多少，剩下的留给更早的问题

# BM25 的最低分门槛。实测真实语料：相关条目落在 4.2~12，不相关落在 0.7~3。
# 最初定 2.0，问题是不相关的最高能摸到 3——那批会被当成「相关记忆」塞进去，
# 她只能硬解释，这是最阴的一种幻觉（不是凭空编，是被不相关的料带偏）。
# 提到 3.5：卡在不相关上限 3 之上、相关下限 4.2 之下。代价是边缘相关会被漏掉，
# 但宁可少记不可错记——漏了会明说「没找到」，记错了会一本正经地胡说。
RECALL_MIN_SCORE = 3.5

# 长期记忆注入：条目少时全量塞（少的时候 BM25 的 IDF 区分度差，全塞也就百来字），
# 多了再按当前问题检索，token 恒定为检索条数，不随记忆本长大而涨。
MEMORY_INJECT_ALL = 16   # 记忆条数 <= 这个值时全量注入（16 条约 400 token，可接受）
MEMORY_INJECT_K = 3      # 超过后每次检索注入的最多条数

# 「永远该在」的类型：不参与 BM25 检索、不受相关度门槛影响，每轮必注入。
# 原因：11-24 实测，记忆条目的 BM25 分数只有 0.6~0.8，而门槛是 3.5——
# 导致「你还记得我的红线吗」也注入 0 条。身份/红线这种东西不能靠检索碰运气。
# 类型常量与 ID/写原语都在 memory.py 里定义（桌面端和引擎共用一套）

# 会话滚动摘要：L1 层。每聊满 N 轮，用当前接入的模型把这段压成几条要点，
# 追加进 session.md；每轮只注入最新一段（上限 600 字），总量不随聊多久涨 token。
SUMMARY_EVERY = 6          # 每 N 轮（1 问 1 答）做一次摘要
SUMMARY_MAX_ITEMS = 5      # 每次摘要最多保留的要点数
SUMMARY_DIALOG_LIMIT = 400 # 摘要输入里每条消息最多保留的字符数
SESSION_INJECT_CHARS = 900 # 注入上下文的摘要字符上限（原 600，只够一段）
SESSION_INJECT_SEGMENTS = 3 # 注入最近几段摘要（原来只注入最新 1 段，前 5 段白写）
SUMMARY_PRESSURE = 0.75     # 上下文压力超过窗口 75% 时提前摘要（仍需至少 2 轮）
SUMMARY_PRESSURE_MIN_TURNS = 2

# L2 层级摘要：L1 段（每 6 轮一段）攒到 L2_MERGE_AT 段时，把最老的几段合并成一段
# 「长期脉络」。这样「这几天发生了什么」也有脉络，而 token 上限恒定——
# 不做的话，session.md 只会无限变长，而 _recall_session 只注入最后几段。
L2_MERGE_AT = 5
L2_MAX_ITEMS = 5
L2_MARK = "【长期脉络】"

# 长期记忆抽取：每 N 轮用当前接入的模型从对话里抽稳定事实，后台异步写进 memory.md。
# 会花一点 token，但不用再维护第二套本地模型。
MEMORY_EXTRACT_EVERY = 12   # 每 N 轮（1问1答）抽一次

# 重复调用提醒：同一工具 + 完全相同的参数连续重复到这些次数时，追加一句提醒。
# 抄 DSH 的 dsh-repeat-tool-reminder（它默认 [3,5,8]）。阿鲤实测的重复只到 ×2，
# 所以门槛从 2 起——门槛直接给 3 等于没装。提醒只加在消息末尾，不打断前缀缓存。
REPEAT_REMIND_AT = (2, 4)
REPEAT_ARGS_PREVIEW = 500
MEMORY_EXTRACT_MAX = 6      # 每次最多写入几条

# 规则式偏好捕获（抄 dsh-engram 的 autoCapturePreferences）：
# 用户纠正她的那句话，按句式立刻记下来，零 LLM、零 token。
# 靠 Ollama 每 12 轮抽是补充，不是主力——小模型会把助手的行为记成用户的事实。
PREF_CAPTURE_PER_SESSION = 8    # 一个会话最多记这么多条偏好
PREF_CAPTURE_MAX_ENTRIES = 40   # 记忆本总量上限，超了就不再自动加


# 试过用 2-gram 做旧对话检索，拿真实 348 条历史一测就废了：
# 问 smolagents 会捞回 DeepSeek harness 那段（两边都有「项目」「那个」这种泛词）。
# 换成 jieba 分词 + BM25（见 memory.py）才做对——它有词典，认得出虚词和专名。
# 2026-11-08 实测：config.json / smolagents / 识图 / 吃啥 四个问题全部命中。


class FinalAnswer(Exception):
    def __init__(self, text=""):
        self.text = str(text)


class CodeTimeout(Exception):
    """单步代码超时 / 超量 / 输出太多。继承 Exception，好让它走正常的回滚路径。"""


def _code_guard(deadline, max_ops, time_limit=CODE_TIME_LIMIT):
    """sys.settrace 的钩子：每 CODE_TRACE_EVERY 行查一次时间，每行累计一次行数。

    为什么不用「线程 + join(timeout)」：Python 杀不掉线程，join 超时之后
    那个线程还在 CPU 里空转，基站只会更卡。settrace 能在执行中间抛异常，
    这是进程内唯一可靠的中断点。

    拦不住的：卡在 C 扩展里的调用（正则回溯爆炸、巨型 json.load）——
    那种调用不产生 line 事件，只能等它自己返回。这是这套方案的边界，不装作没有。
    """
    count = [0]

    def _hook(frame, event, arg):
        if event != "line":
            return _hook
        count[0] += 1
        if count[0] >= max_ops:
            raise CodeTimeout(
                f"这一步执行超过 {max_ops:,} 行，被掐断了。"
                f"别写死循环，也别一次算太大，拆成几步。"
            )
        if count[0] % CODE_TRACE_EVERY == 0 and time.monotonic() > deadline:
            raise CodeTimeout(
                f"这一步跑了超过 {time_limit} 秒，被掐断了。"
                f"别写死循环，也别一次算太大，拆成几步。"
            )
        return _hook

    return _hook


def _guard_print(buf):
    """返回一个受 CODE_PRINT_LIMIT 约束的 print，超了就抛 CodeTimeout。

    为什么连 print 都要管：for i in range(10**6): print(i) 能把内存吃爆，
    而 RESULT_LIMIT 是事后对字符串截断——等它生效，内存早就没了。
    """
    def _p(*a, **k):
        print(*a, file=buf, **k)
        if buf.tell() > CODE_PRINT_LIMIT:
            raise CodeTimeout(
                f"这一步 print 超过 {CODE_PRINT_LIMIT:,} 字符，被掐断了。"
                f"少打点，或者只打你要看的那几行。"
            )

    return _p


def _scan_code(code):
    """exec 前扫一遍：禁 dunder 属性访问和危险内建调用。

    这是给 exec 这种「真跑 Python」补的最小静态闸。正门 `().__class__.__base__
    .__subclasses__()` 的每一步都是 dunder 属性访问，这里直接拦在 exec 之前，
    提权链连进都进不去。筛子不是墙：真下决心的人总能想出变体，但对付「顺手写」
    和「一句话被带偏」够了。

    返回 None 表示能跑，否则返回拒绝原因（给模型看的友好话）。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (f"代码语法不对：第 {e.lineno} 行 {e.msg}。改对了再提交。")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            return (f"禁止访问 dunder 属性 `.{node.attr}`。"
                    f"这类魔法属性（__class__ / __subclasses__ / __globals__ 等）"
                    f"是绕过沙箱的第一步，环境里不需要它们。")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                return (f"禁止调用 `{node.func.id}()`。"
                        f"这个函数不在白名单里，别试图用它拿不该拿的东西。")

    return None


def _calc_hint(text):
    """用户问的是算术时，本地算好直接告诉他。

    实测（2026-11-25）：问「1234 乘 5678」，模型心算答成 6,912,592；
    在提示词里写「用 run_python 算」之后，它把 "run_python 1234 * 5678" 当答案复述。
    所以只能确定性短路：认准是算术 → 本地算 → 把结果塞进上下文。

    防误伤：必须有算术意图词（算/等于/多少/乘/除/加/减/×/÷）。
    否则「今天是 11-24」会被当成 11-24 = -13。
    """
    t = text or ""
    # 纯表达式（"1234*5678"）本身就算算术意图；否则要有意图词
    bare = re.fullmatch(r"[\d\s.+\-*/()%×÷]+", t.strip())
    if not bare and not re.search(r"算|等于|多少|乘|除|加|减|×|÷", t):
        return ""
    # 中文运算符归一
    norm = (t.replace("乘以", "*").replace("乘", "*")
             .replace("除以", "/").replace("除", "/")
             .replace("加上", "+").replace("加", "+")
             .replace("减去", "-").replace("减", "-")
             .replace("×", "*").replace("÷", "/")
             .replace("−", "-").replace("＝", "="))
    # 只留算术字符，得到候选串
    soup = re.sub(r"[^0-9.+*/()%\-]", "", norm)
    if len(soup) < 3:
        return ""
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
               ast.USub, ast.UAdd)

    def try_parse(s):
        if not re.search(r"[+\-*/%]", s):
            return None
        try:
            tree = ast.parse(s, mode="eval")
        except SyntaxError:
            return None
        if not all(isinstance(n, allowed) for n in ast.walk(tree)):
            return None
        try:
            return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})
        except Exception:
            return None

    best, best_val = "", None
    # 从每个位置起，尝试尽量长的可解析前缀
    for start in range(len(soup)):
        for end in range(len(soup), start + 2, -1):
            sub = soup[start:end].strip("+*/%- ")
            if len(sub) < 3 or len(sub) <= len(best):
                continue
            val = try_parse(sub)
            if val is not None:
                best, best_val = sub, val
                break
    if not best or not isinstance(best_val, (int, float)) or abs(best_val) >= 1e15:
        return ""
    s = str(int(best_val)) if float(best_val).is_integer() else ("%.6g" % best_val)
    return "（这个算式本地已经算好了：%s = %s。直接把这个结果说出来，不要调工具。）" % (best, s)


class _Missing:
    pass


_MISSING = _Missing()

_SKIP_KEYS = {"__builtins__", "done", "print"}


def parse_code_json(text):
    """从模型输出里取出 {"thought": ..., "code": ...}。

    模型不一定老实：会给裸 JSON、```json 包裹、也会退化成
    「Thought: ... / Code: ```python ... ```」的 Markdown 写法。
    三种都认，认不出才返回 None。
    """
    if not text:
        return None
    raw = text.strip()

    # ---- 1) JSON 形态（裸 / ```json 包裹 / 前后带废话）----
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    json_candidate = m.group(1) if m else raw

    for candidate in (json_candidate, raw):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and ("code" in obj or "thought" in obj):
                return {
                    "thought": str(obj.get("thought") or ""),
                    "code": str(obj.get("code") or ""),
                }
        except Exception:
            pass

    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
            if isinstance(obj, dict) and ("code" in obj or "thought" in obj):
                return {
                    "thought": str(obj.get("thought") or ""),
                    "code": str(obj.get("code") or ""),
                }
        except Exception:
            pass

    # ---- 2) Markdown 形态：Thought: ... / Code: ```python ... ```----
    cm = re.search(r"```(?:python|py)?\s*(.*?)```", raw, re.S)
    if cm and cm.group(1).strip():
        head = raw[:cm.start()]
        thought = head
        tm = re.search(r"thought\s*[:：]\s*(.*?)(?:code\s*[:：]|$)", head, re.S | re.I)
        if tm:
            thought = tm.group(1)
        return {"thought": thought.strip(), "code": cm.group(1).strip()}

    return None


def _snapshot(ns, skip):
    snap = {}
    for k, v in ns.items():
        if k in skip or k.startswith("__"):
            continue
        if isinstance(v, (int, float, str, bool, bytes, type(None), tuple, frozenset)):
            snap[k] = (True, v)          # 不可变：存值，可以精确还原
        else:
            snap[k] = (False, v)         # 可变：存引用，只能还原「被换成别的对象」
    return snap


def _restore(ns, snap, skip):
    """回滚到快照。

    只恢复「原有的变量被改坏了」；这一步新冒出来的变量**保留**——
    它们是这个任务的中间成果（解析好的 data、算好的结果），删掉等于逼模型重读一遍，
    实测会直接导致它原地打转、token 翻倍。
    """
    for k, (is_immutable, value) in snap.items():
        curr = ns.get(k, _MISSING)
        if is_immutable:
            if curr is _MISSING or curr != value:
                ns[k] = value
        else:
            if curr is not value:        # 对象被替换了 → 换回原来的引用
                ns[k] = value


def _clip_result(text, limit=RESULT_LIMIT):
    """工具结果回写上下文时的截断：保头也保尾。

    旧实现只留前 1500 字，代码路径的 traceback、退出码、最后一个报错全在尾巴，
    模型只能看见“前半段正常”，然后瞎猜。现在同样的字符预算，切成
    头 1000 + 尾 400 + 中间省略说明；尾部的错误行、返回值优先留下。
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    head_n = int(limit * 0.66)
    tail_n = max(200, limit - head_n - 80)
    omitted = len(text) - head_n - tail_n
    return (
        text[:head_n]
        + f"\n...(中间省略 {omitted} 字符；保留开头和结尾，完整内容请用 start/limit 再读)...\n"
        + text[-tail_n:]
    )


class BaseStationAgent:
    def __init__(self, config, registry, runtime, llm, work_dir, ledger=None, safe_mode=False):
        self.config = config
        self.registry = registry
        self.runtime = runtime
        self.llm = llm
        self.work_dir = work_dir
        self.safe_mode = bool(safe_mode)
        self.ledger = ledger
        self.events = EventStream(ledger=ledger)
        # 工具审计：只记“调了什么、多久、多大、是否出错”，不记完整参数。
        # 这样账本能回答“为什么这轮慢/贵”，又不会把 key 落到盘里。
        self.registry.audit = self._audit_tool
        self.stats = {
            "prompt_tokens": 0, "completion_tokens": 0, "calls": 0,
            "cache_hit": 0, "last_prompt_tokens": 0, "last_completion_tokens": 0,
            "last_cache_hit": 0,
        }
        # 启动时读一次，之后每轮只是拼字符串——不每轮碰磁盘
        self._diary = _diary_tail(os.path.join(self.work_dir, "knowledge", "diary.md"))
        self._presence = _presence_tail(os.path.join(self.work_dir, "knowledge", "_presence.log"))
        # 会话摘要进度：已摘要到 history 的第几条消息。持久化，重启后接着算。
        self._session_state_path = os.path.join(self.work_dir, "knowledge", "_session_state.json")
        self._summarized_msgs = self._load_summarized_msgs()
        # 长期记忆抽取进度：已抽取到 history 的第几条消息，持久化
        self._memory_state_path = os.path.join(self.work_dir, "knowledge", "_memory_state.json")
        self._extracted_msgs = self._load_extracted_msgs()
        # 本轮被用户「带入对话」的插件；chat() 结束就清空，不跨轮残留
        self._armed_plugin = None
        self._armed_tool_names = ()
        # 用户点「暂停」：在检查点抛 TurnCancelled，让这一轮尽早收手
        self._cancel_flag = threading.Event()

    # ---------------- 中断 ----------------
    def request_cancel(self):
        """界面点「暂停」时调用。设标志，不直接杀线程。"""
        self._cancel_flag.set()

    def _check_cancel(self):
        if self._cancel_flag.is_set():
            raise TurnCancelled("用户暂停了这一轮")

    # ---------------- 对外入口 ----------------
    def chat(self, user_text, history=None, model_name=None, plugin_id=None, provider_id=None):
        self.events.clear()
        self._cancel_flag.clear()
        _turn_t0 = time.time()
        turn_id = self.events.begin_turn("t" + uuid.uuid4().hex[:10])
        # 每轮清零。只统计这一轮，不累计开机以来的总量。
        self.stats = {
            "prompt_tokens": 0, "completion_tokens": 0, "calls": 0,
            "cache_hit": 0, "last_prompt_tokens": 0, "last_completion_tokens": 0,
            "last_cache_hit": 0,
        }
        self._model_name = model_name
        self._provider_id = provider_id
        low = (user_text or "").lower()
        self._low_temp = any(h in low for h in self.DOC_HINTS)
        self.events.emit("turn/start", label=(user_text or "")[:40],
                         model=(model_name or ""), safe_mode=self.safe_mode)
        self.events.emit("user", label=(user_text or "")[:40],
                         content=(user_text or "")[:4000])
        # 规则式偏好捕获：纠正一次就记住，不等 12 轮后的 Ollama 抽取
        self._capture_preferences(user_text)

        # 插件只在本轮「带入」：临时把它的工具打开，结束后恢复原状。
        # 不落 plugins_state.json，也不改变全局开关——发完就卸。
        self._armed_plugin = None
        self._armed_tool_names = ()
        # 重复调用链：新的一轮用户输入到达时清空（对应 DSH 的「新用户消息清链」）
        self._repeat_chain = {"key": None, "count": 0}
        armed_tools = []
        if plugin_id:
            spec = self.plugins.specs.get(plugin_id) if getattr(self, "plugins", None) else None
            if spec and spec.ready():
                for name in spec.tool_names:
                    tool = self.registry.get(name)
                    if tool and not tool.enabled:
                        tool.enabled = True
                        armed_tools.append(tool)
                self._armed_plugin = spec
                self._armed_tool_names = tuple(spec.tool_names)
                self.events.emit("thought", label="插件已带入：" + spec.name)
            else:
                self.events.emit("error", label="插件带入失败：" + str(plugin_id))

        mode = self.route(user_text)
        reply, ok = "", False
        try:
            if mode == "code":
                reply, ok = self._run_code(user_text, history or [], model_name)
            else:
                reply, ok = self._run_json(user_text, history or [], model_name)
        except TurnCancelled:
            reply, ok = CANCELLED_MARK, False
            self.events.emit("thought", label="用户暂停了这一轮")
        except Exception as e:
            reply, ok = self._format_error(e), False
        finally:
            for tool in armed_tools:
                tool.enabled = False
            self._armed_plugin = None
            self._armed_tool_names = ()

        self._maybe_summarize(history or [], user_text, reply)
        self._maybe_extract_memory(history or [], user_text, reply)
        self.events.emit("answer", label=reply[:40], content=(reply or "")[:4000])
        self.events.emit("stats",
                         prompt_tokens=self.stats.get("prompt_tokens", 0),
                         completion_tokens=self.stats.get("completion_tokens", 0),
                         cache_hit=self.stats.get("cache_hit", 0),
                         calls=self.stats.get("calls", 0),
                         mode=mode)
        self.events.emit(
            "turn/end", label=(reply or "")[:40], ok=ok, mode=mode,
            ms=int(round((time.time() - _turn_t0) * 1000)),
            calls=self.stats.get("calls", 0),
            prompt_tokens=self.stats.get("prompt_tokens", 0),
            completion_tokens=self.stats.get("completion_tokens", 0),
            cache_hit=self.stats.get("cache_hit", 0),
        )
        return {
            "reply": reply,
            "mode": mode,
            "ok": ok,
            "turn_id": turn_id,
            "session_id": self.events.session_id,
            "trace": self.events.as_list(),
            "summary": self.events.summary(),
            "stats": dict(self.stats),
            "runtime": self.runtime.name,
        }

    # ---------------- 偏好捕获 ----------------
    def _capture_preferences(self, text):
        """从用户这句话里按句式认出偏好/纠正，直接写进长期记忆。

        零 LLM、零 token，热路径上只跑几条正则。抄 dsh-engram 的
        autoCapturePreferences，但只保留中文句式——阿鲤的用户说中文。

        为什么要它：靠 Ollama 每 12 轮抽一次，慢，而且小模型会把
        「她自己的行为」记成「用户的事实」。而「不要用X」「改用X」这种话，
        规则比模型准，而且当时就该生效。
        """
        try:
            from .prefs import capture_preferences, format_memory_line
            hits = capture_preferences(text or "")
            if not hits:
                return
            path = os.path.join(self.work_dir, "knowledge", "memory.md")
            existing = load_memory_entries(path)
            if len(existing) >= PREF_CAPTURE_MAX_ENTRIES:
                return
            added = getattr(self, "_prefs_added", 0)
            room = PREF_CAPTURE_PER_SESSION - added
            if room <= 0:
                return
            lines = []
            for direction, stmt in hits[:room]:
                if any(stmt in e for e in existing):
                    continue
                lines.append(format_memory_line(direction, stmt))
            if not lines:
                return
            append_memory_lines(path, lines)
            self._prefs_added = added + len(lines)
            self.events.emit("thought", label="记下偏好 %d 条" % len(lines))
        except Exception:
            pass

    # ---------------- 错误处理 ----------------
    def _format_error(self, e):
        """把异常翻成一句用户看得懂的话，并把原始堆栈落进 _error.log。

        为什么不能直接 f"{type(e).__name__}: {e}"——LLMError 的内容本来就已经是
        人话（401 是「Key 无效」、429 是「额度用完」、Timeout 是「等一会儿再发」），
        在上面再套一层「LLMError:」只会让用户看见一个没意义的英文类名。
        非 LLMError 的才是真·内部错误，那种才需要带类型名，方便对日志。
        """
        from .llm import LLMError

        if isinstance(e, LLMError):
            msg = str(e)
        else:
            msg = f"内部错误 {type(e).__name__}: {e}"

        self._log_error(msg, e)
        return f"❌ {msg}"

    def _log_error(self, msg, exc):
        """错误落盘。UI 上的气泡关掉就没了，日志才是事后排查的唯一线索。

        超过 2MB 滚一次（旧的不留），免得一年后变成个几十 MB 的垃圾文件。
        记日志本身失败绝不能再抛——否则错误处理反而成了新的错误源。
        """
        try:
            path = os.path.join(self.work_dir, "_error.log")
            if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
                os.replace(path, path + ".1")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {msg}\n{tb.rstrip()}\n\n")
        except Exception:
            pass

    # ---------------- 路由 ----------------
    # 只留「真的需要写代码才能办」的词。原来的 26 个里有一半是日常高频词
    # （"文件""看下""整理""分析""执行"），一句话就命中，等于 auto 变成全开。
    # 单文件读写用 read_file / write_file 就够，不该切代码通道。
    CODE_HINTS = (
        # 明确说要写
        "代码", "脚本", "写个程序", "写个脚本", "跑一下", "跑个",
        # 批量 / 多文件
        "批量", "所有文件", "每个文件", "逐个", "遍历",
        # 真需要算
        "统计", "求和", "计算", "转换格式", "转成", "转换成",
        # 改代码本身
        "对比", "重构", "修复代码",
    )

    # 写产出物（报告 / 文档 / PPT / PDF）和闲聊不是一类活：
    # 闲聊要 0.85 的灵性，写文档要能被查验的确定性。实测 0.85 写报告，
    # 她会把标题写得很漂亮、把没查到的东西写得很像查到过。
    DOC_HINTS = (
        "报告", "报告书", "文档", "word", "docx", "ppt", "幻灯", "演示", "演示稿",
        "pdf", "排版", "总结成", "整理成", "写成一份", "生成一份", "做一份",
    )

    def route(self, text):
        if self.safe_mode:
            # 安全模式不跑 CodeAgent：进程内 exec 不是隔离边界，恢复/排障时只走 JSON + 只读工具。
            return "json"
        mode = (self.config.get("agent_mode") or "auto").lower()
        if mode in ("json", "code"):
            return mode
        low = (text or "").lower()
        return "code" if any(h in low for h in self.CODE_HINTS) else "json"

    def _char_budget(self):
        """历史能占多少字符。默认按 context_window 自动算，不再写死 3600。

        中文约 1 字 1 token，英文约 4 字符 1 token；这里按 2 字符/token 保守估。
        留 40% 给 system、工具 schema、记忆注入和输出，剩下 60% 给历史。
        """
        fixed = int(self.config.get("history_char_budget") or 0)
        if fixed > 0:
            return fixed
        try:
            window = int(self.config.get("context_window") or 131072)
            out = int(self.config.get("max_output_tokens") or 8192)
        except Exception:
            window, out = 131072, 8192
        usable = max(0, window - out)
        return max(16000, int(usable * 2 * 0.6))

    def _trim_history(self, history, limit, query=None, meta=None):
        """按字符预算装历史，两层。

        旧版「取最近 N 条」的毛病：实测基站里一条回答就能到 2113 字符，一条长回答顶掉一整轮。

          1) 最近两轮：一问一答完整留，最多吃预算的 70%，保证话接得上；
          2) 更早的：用本地 BM25 捞跟当前问题最相关的 2-3 轮「一问一答」，**连回答一起留**；
          3) 超预算就停。

        第二层不是「把回答丢掉」，是「只留用得上的」。所以问到以前的事，她还能想起自己当时怎么答的。
        检索靠 jieba + BM25 在本地算，不调模型、不花 token。
        """
        hist = list(history or [])
        n = len(hist)
        if not n:
            return []

        def blob(msg):
            content = msg.get("content") or ""
            # 时间戳只加在 user 消息前（约 12 字符/条，比给 assistant 也加省一半）。
            # 旧数据没有 ts 就照原样。时间戳不进 BM25 检索——下面 units 用的是原始 content。
            ts = msg.get("ts")
            if ts and (msg.get("role") or "") == "user":
                content = f"[{ts}] {content}"
            item_cap = int(self.config.get("history_item_limit") or 0)
            if item_cap > 0 and len(content) > item_cap:
                content = content[:item_cap] + "…"
            return {"role": msg.get("role") or "user", "content": content}

        picked, total = [], 0
        budget = self._char_budget()

        # limit<=0 = 放开模式：从最近往前整段装，装到预算满为止，不再只留最近 2 轮。
        # 老逻辑（最近 N 轮 + BM25 捞相关）只在用户显式设了轮数上限时走。
        if limit <= 0:
            for i in range(n - 1, -1, -1):
                item = blob(hist[i])
                if picked and total + len(item["content"]) > budget:
                    break
                picked.append(item)
                total += len(item["content"])
            picked.reverse()
            while picked and picked[0]["role"] != "user":
                picked.pop(0)
            if meta is not None:
                meta["total"] = n
                meta["kept"] = len(picked)
                meta["truncated"] = len(picked) < n
                meta["budget"] = budget
            return picked

        recent_cap = int(budget * HISTORY_RECENT_SHARE)

        # 1) 最近两轮：完整留
        for i in range(n - 1, max(-1, n - 1 - HISTORY_RECENT_N), -1):
            item = blob(hist[i])
            if picked and total + len(item["content"]) > recent_cap:
                break
            picked.append(item)
            total += len(item["content"])
            if limit > 0 and len(picked) >= limit:
                break
        picked.reverse()

        # 2) 更早的：按相似度捞「一问一答」，回答一起带上
        older_end = n - HISTORY_RECENT_N
        if query and older_end > 0 and total < budget:
            units = []      # (原始下标, [user, assistant])
            for i in range(older_end):
                if hist[i].get("role") != "user":
                    continue
                pair = [blob(hist[i])]
                if i + 1 < n and hist[i + 1].get("role") == "assistant":
                    pair.append(blob(hist[i + 1]))
                units.append((i, pair))

            if units:
                index = BM25Index([hist[i].get("content") or "" for i, _ in units])
                extra = []
                # 低于门槛的条目不算命中——以前是「取分数最高的 3 条」，哪怕全是 0.8 分也照塞，
                # 结果模型拿着不相关的东西当真事。现在宁可空着，并明确告诉它「没有」。
                _hits = index.scores(query)
                _good = [(s, k) for s, k in _hits if s >= RECALL_MIN_SCORE][:3]
                if meta is not None and not _good:
                    meta["recall_empty"] = True
                for score, k in _good:
                    _, pair = units[k]
                    need = sum(len(m["content"]) for m in pair)
                    room = budget - total
                    if need <= room:
                        extra.append((units[k][0], pair))
                        total += need
                        continue
                    # 空间不够时不跳过改捡次优——那会把答案带偏（实测：问"白名单"答不出，
                    # 问"写文件路径"捞到了另一条不相关的）。改成截短后仍带上最相关那条。
                    if room < 150:
                        continue
                    kept, used = [], 0
                    for m in pair:
                        if m["role"] == "user":
                            if used + len(m["content"]) > room:
                                break
                            kept.append(m)
                            used += len(m["content"])
                        else:
                            left = room - used
                            if left >= 100:
                                kept.append({"role": m["role"], "content": m["content"][:left] + "…"})
                                used += left + 1
                    if kept and kept[0]["role"] == "user":
                        extra.append((units[k][0], kept))
                        total = budget
                        break
                if extra:
                    extra.sort(key=lambda x: x[0])
                    picked = [m for _, pair in extra for m in pair] + picked

        # 拼出来的第一句必须是 user，不然模型会以为那是它自己在自言自语
        while picked and picked[0]["role"] != "user":
            picked.pop(0)
        if meta is not None:
            # 把「历史没装全」这件事交给调用方，好让模型知道别假装记得所有事
            meta["total"] = n
            meta["kept"] = len(picked)
            meta["truncated"] = len(picked) < n
        return picked

    def _plugin_note(self):
        """本轮带入插件时，给 system 加一小段（约 70 字符），只在这一轮花钱。"""
        if not self._armed_plugin:
            return ""
        return ("\n\n【本轮插件：" + self._armed_plugin.name + "】用户把它带进了这一轮，相关工具已挂载。"
                "先用状态/新建类工具确认环境，再按用户要求实际操作；复杂件优先用 plugin_sw_build_part "
                "一次提交整串步骤，别一步一调。不要改用写宏、VBA 或外部脚本代替，工具报错时把原始错误说清楚。")

    # ---------------- JSON 通道（多轮） ----------------
    # ---------------- 子 agent ----------------
    SUB_REPORT_CHARS = 3000

    def _run_subagent(self, objective: str, max_steps: int = 8):
        """把一个独立子任务丢进自己的上下文跑，主对话只收结论。

        适合「把这个目录查清楚」「跑一遍测试并把失败原因整理出来」这类
        要来回十几轮、但中间过程主对话不该背着的活。子 agent 看不到主对话历史，
        也调不到 dispatch_subagent（防递归），工具结果照样按 RESULT_LIMIT 截断。
        """
        obj = (objective or "").strip()
        if not obj:
            return "❌ 子任务描述为空"
        steps = max(2, min(int(max_steps or 8), 16))
        self._check_cancel()

        system = (
            "你是阿鲤派出去的子 agent。只负责这一件事，做完就回结论。\n"
            "规则：不问用户问题；不调用 dispatch_subagent（递归会被拒）；"
            "要动文件就用工具，别在回复里贴大段代码。\n"
            "最后一条消息直接给结果：结论 + 关键证据（路径 / 命令 / 报错行）。"
        )
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": obj}]
        schemas = [s for s in self.registry.json_schemas(None)
                   if (s.get("function") or {}).get("name") != "dispatch_subagent"]

        for step in range(1, steps + 1):
            self._check_cancel()
            resp = self._call(msgs, tools=schemas, temperature=0.45)
            calls = resp.get("tool_calls") or []
            content = resp.get("content") or ""
            if not calls:
                return _clip_result(content.strip() or "（子任务没给结论）", self.SUB_REPORT_CHARS)
            msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
            for tc in calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if name == "dispatch_subagent":
                    result = "❌ 子 agent 不能再派子 agent"
                else:
                    try:
                        brief = json.dumps(args, ensure_ascii=False)[:80]
                    except Exception:
                        brief = ""
                    self.events.emit("action", label="子:" + name, args=brief)
                    result = self.registry.call(name, args)
                    if len(result) > RESULT_LIMIT:
                        result = _clip_result(result, RESULT_LIMIT)
                msgs.append({"role": "tool",
                             "tool_call_id": tc.get("id") or name,
                             "content": result})

        msgs.append({"role": "system", "content": "步数用完了，直接给结论，不要再调工具。"})
        resp = self._call(msgs, tools=None, temperature=0.5)
        out = (resp.get("content") or "").strip() or "（步数用完了也没给出结论）"
        return _clip_result(out, self.SUB_REPORT_CHARS)

    def _run_json(self, user_text, history, model_name=None):
        # 时间和记忆检索每轮都变，必须放在请求最末尾——不能进 system。
        # system 一旦带上它们，后面 tools + 整段历史的前缀缓存全部作废
        # （11-24 实测：每轮第一次调用只命中 128/2782 = 5%）。
        hint = self._now(history, user_text)
        system = prompts.json_system("", self._persona() + self._pin_memory()) + self._plugin_note()
        limit = int(self.config.get("history_limit") or 0)
        messages = [{"role": "system", "content": system}]
        meta = {}
        messages += self._trim_history(history, limit, query=user_text, meta=meta)
        if meta.get("truncated"):
            # 只截短时才加，约 50 字符。换来的是它不会说「我记得咱们之前聊过……」
            messages.append({
                "role": "system",
                "content": f"（注：历史共 {meta['total']} 条，这里只装了 {meta['kept']} 条。"
                           f"其余的真看不到，别假装记得。）",
            })
        if meta.get("recall_empty"):
            # 只在「啥都没捞到」时加，约 45 字符。不装这一句，它就会凭印象编。
            messages.append({
                "role": "system",
                "content": "（历史检索没找到跟这句相关的记录。别编，需要就直说记不得。）",
            })
        messages.append({"role": "user", "content": user_text})
        # 纯算术题：本地算好再给模型，别让它心算/复述工具名
        _calc = _calc_hint(user_text)
        if _calc:
            messages.append({"role": "system", "content": _calc})
        # 易变信息放最后一行：时间 + 记忆检索 + 会话摘要。
        # 只追加在末尾，不打断前面 system/tools 的缓存前缀。
        messages.append({"role": "system", "content": hint})

        # 按这句话动态挂载：说到了才给看。实测 13 个工具全挂是 3174 字符（约 2116 token），
        # 每轮都重发；分层后基线只剩五六个，扩展工具等用户真提到才出现。
        schemas = self.registry.json_schemas(user_text, force_names=self._armed_tool_names)
        max_rounds = min(int(self.config.get("max_rounds") or MAX_TOOL_ROUNDS), MAX_TOOL_ROUNDS)
        if self._armed_plugin:
            # 复杂件（如螺钉：截面→旋转→六角切除）一步一调会撞上 6 轮上限；
            # 只在带入插件的这一轮放宽，普通聊天不受影响。
            max_rounds = max(max_rounds, ARMED_MAX_ROUNDS)

        for rnd in range(1, max_rounds + 1):
            self._check_cancel()
            resp = self._call(messages, tools=schemas)
            calls = resp.get("tool_calls") or []
            content = resp.get("content") or ""

            if not calls and not content.strip() and rnd == 1:
                # 模型偶尔返回空包，补一次，别让用户看到空白
                self.events.emit("error", label="模型返回空包，重试一次")
                resp = self._call(messages, tools=schemas)
                calls = resp.get("tool_calls") or []
                content = resp.get("content") or ""

            if not calls:
                return (content or "（没说话）"), True

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": calls,
            })

            reminders = []
            for tc in calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}

                _note = self._repeat_reminder(name, args)
                if _note:
                    reminders.append(_note)

                # 带上参数摘要，前端才能显示「正在读 D:/xxx/config.json」而不是只有个工具名。
                # 只进内存事件，不进 prompt，零 token。
                try:
                    _arg_brief = json.dumps(args, ensure_ascii=False)[:90]
                except Exception:
                    _arg_brief = ""
                self.events.emit("action", label=name, args=_arg_brief)
                self._check_cancel()
                result = self.registry.call(name, args)
                if len(result) > RESULT_LIMIT:
                    result = _clip_result(result, RESULT_LIMIT)
                self.events.emit("observation", label=name, chars=len(result))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or name,
                    "content": result,
                })

            if reminders:
                # 追加在本轮工具结果之后：只加末尾，不打断前缀缓存
                messages.append({"role": "system", "content": "\n\n".join(reminders)})
                self.events.emit("thought", label="重复调用提醒 ×%d" % len(reminders))

        # 轮次用尽：强制它收口。
        # 两个坑都是实测出来的：
        #   1) 收口时把 tools 撤掉会改变请求头，前缀缓存整段作废——实测收口轮命中率
        #      从 80%+ 掉到 7.5%（10,185 prompt 只命中 768）。改成保留 tools、用消息劝它别调。
        #   2) 收口指令原来用 role=user，模型会把它当成用户的话复述进答案
        #      （真实出现过「没工具了，就说我手上这点东西。」）。改成 system。
        messages.append({"role": "system",
                         "content": "工具轮次用完了。直接用手上已经掌握的信息回答，不要再调用工具。"})
        resp = self._call(messages, tools=schemas,
                          temperature=0.35 if getattr(self, "_low_temp", False) else 0.6)
        content = (resp.get("content") or "").strip()
        if not content:
            # 兜底：它还是硬要调工具。工具不会再执行了，退一步再问一次。
            # 这一次会丢缓存，但只发生在兜底路径上。
            messages.append({"role": "system", "content": "不要再调用工具了，直接用文字回答。"})
            resp = self._call(messages, tools=None, temperature=0.5)
            content = (resp.get("content") or "").strip()
        return (content or "（没能回答出来）"), False

    # ---------------- 重复调用提醒 ----------------
    def _repeat_reminder(self, name, args):
        """同一工具 + 完全相同的参数连续重复时，给一句提醒；否则返回 None。

        指纹用 json.dumps(sort_keys=True)：参数属性顺序不同也算同一次。
        只提醒、不阻断——合法的重复调用照样执行，只是劝它先看结果。
        阈值 REPEAT_REMIND_AT 用「恰好等于」判定，避免第 3、4 次连着刷屏。
        """
        try:
            key = name + "|" + json.dumps(args, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False)
        except Exception:
            return None
        chain = getattr(self, "_repeat_chain", None)
        if chain is None:
            chain = self._repeat_chain = {"key": None, "count": 0}
        if key == chain["key"]:
            chain["count"] += 1
        else:
            chain["key"] = key
            chain["count"] = 1
            return None
        n = chain["count"]
        if n not in REPEAT_REMIND_AT:
            return None
        try:
            brief = json.dumps(args, ensure_ascii=False)[:REPEAT_ARGS_PREVIEW]
        except Exception:
            brief = ""
        return (
            "你在用完全相同的参数重复调用同一个工具（第 %d 次）。先仔细看上一次的结果，"
            "再决定：换一种做法、换参数，或者已经拿到足够信息就直接回答。\n"
            "- tool: %s\n- consecutive_calls: %d\n- arguments: %s" % (n, name, n, brief)
        )

    # ---------------- CodeAgent 通道 ----------------
    def _run_code(self, user_text, history, model_name=None):
        system = prompts.code_system(self._now(history, user_text), self._persona() + self._pin_memory(),
                                     self.registry.code_prompt()) + self._plugin_note()
        limit = int(self.config.get("history_limit") or 0)
        messages = [{"role": "system", "content": system}]
        # 原来这里收了 history 却一次都没用——code 模式下她完全不记得刚才聊了什么，
        # 跟 _run_json 那条通道行为不一致。补上，用同一套字符预算裁剪。
        meta = {}
        messages += self._trim_history(history, limit, query=user_text, meta=meta)
        messages.append({"role": "user", "content": user_text})

        max_steps = int(self.config.get("max_steps") or 8)
        ns = self._make_ns()
        skip = set(self.registry.names()) | _SKIP_KEYS
        retry_sent = False

        for step in range(1, max_steps + 1):
            self._check_cancel()
            resp = self._call(messages, temperature=0.2)
            content = resp.get("content") or ""
            parsed = parse_code_json(content)

            if not parsed or not parsed["code"].strip():
                # 模型没给代码。两种情况：
                #   a) 它在直接回答 → 收工
                #   b) 它在假装执行/写成了奇形怪状 → 补救一次，要求只给代码块
                looks_like_attempt = ("```" in content) or ("code" in content.lower()) \
                    or any(t in content for t in self.registry.names())
                if looks_like_attempt and not retry_sent:
                    retry_sent = True
                    self.events.emit("error", label="没给代码块，补救一次")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "只输出这一轮的 Python 代码，用 ```python 包起来。"
                                   "不要写解释，不要自己编执行结果，执行结果由系统给你。",
                    })
                    continue
                return (content or "（没说话）"), True

            thought, code = parsed["thought"], parsed["code"]
            if thought:
                self.events.emit("thought", label=thought[:60])
            self.events.emit("action", label=f"code {len(code)}字")

            buf = io.StringIO()
            ns["print"] = _guard_print(buf)

            step_snap = _snapshot(ns, skip)
            err = _scan_code(code) or ""
            deadline = time.monotonic() + CODE_TIME_LIMIT
            old_trace = sys.gettrace()
            if not err:
                sys.settrace(_code_guard(deadline, CODE_MAX_OPS))
            try:
                if not err:
                    exec(compile(code, "<base_station_agent>", "exec"), ns)  # noqa: S102
            except FinalAnswer as fa:
                self.events.emit("answer", label=str(fa.text)[:40])
                return fa.text, True
            except CodeTimeout as e:
                err = str(e)          # 这条是我们自己写的友好话，别再套一层类名
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            finally:
                sys.settrace(old_trace)

            printed = buf.getvalue().strip()
            if printed:
                self.events.emit("observation", label=f"print {len(printed)}字")

            if err:
                # 这一步炸了：回滚到这一步之前的快照，别把脏变量带到下一步
                _restore(ns, step_snap, skip)
                self.events.emit("error", label=err[:60])

            feedback = f"[执行结果]\n{err or printed or '（代码执行完成，无输出）'}"
            if len(feedback) > RESULT_LIMIT:
                feedback = _clip_result(feedback, RESULT_LIMIT)
            if not err:
                feedback += "\n任务没完成就继续写代码；完成了就调用 done(最终回答)。"
            else:
                feedback += "\n这步报错了，已回滚到上一步的干净状态。修正后继续。"

            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": feedback})

        # 步数用尽：不再让它写代码，逼它用已有结果说人话（多一次调用，但比"没收敛"强）
        messages.append({
            "role": "user",
            "content": "步数用完了。不要再写代码，直接用你已经拿到的结果，用一段人话回答用户。",
        })
        resp = self._call(messages, temperature=0.5)
        return (resp.get("content") or "（写代码的步数用完了，任务没收敛）"), False

    def _make_ns(self):
        ns = self.registry.func_map()
        ns["done"] = lambda answer="": (_raise(FinalAnswer(answer)))
        ns["__builtins__"] = dict(_SAFE_BUILTINS)
        ns["print"] = lambda *a, **k: None
        # 只给几个只读**外观**，不给模块对象本身。
        #
        # 原来直接 ns["json"] = json 是漏的——模块对象自带 __builtins__，
        #   json.__builtins__['__import__']('os').system(...)  一行就出去了
        # 另外两条同样便宜：
        #   os_path.os            （ntpath 内部就 import 了 os）
        #   listdir.__globals__   （那个 lambda 的 globals 就是本模块，里面摆着完整的 os）
        # _SAFE_BUILTINS 剔掉 __import__ 只挡住了正面，这三条是侧门。
        # 换成 SimpleNamespace 只暴露白名单属性：json.__builtins__ 直接 AttributeError。
        # 注意这堵不死「刻意考古」（函数对象仍带 __globals__），但把「顺手就能用」
        # 抬成了「知道实现细节才挖得动」——进程内 exec 的天花板就在这里。
        ns["json"] = SimpleNamespace(loads=json.loads, dumps=json.dumps)
        ns["re"] = SimpleNamespace(
            findall=re.findall, sub=re.sub, search=re.search, match=re.match,
            split=re.split, compile=re.compile, escape=re.escape,
            IGNORECASE=re.IGNORECASE, DOTALL=re.DOTALL,
        )
        # math 是 C 内建模块，本来就没有 __builtins__ / 可用的 __globals__，不用包
        ns["math"] = math
        ns["datetime"] = SimpleNamespace(
            datetime=datetime.datetime, date=datetime.date, time=datetime.time,
            timedelta=datetime.timedelta, now=datetime.datetime.now,
            today=datetime.date.today,
        )
        ns["os_path"] = SimpleNamespace(
            join=os.path.join, exists=os.path.exists, basename=os.path.basename,
            dirname=os.path.dirname, split=os.path.split, splitext=os.path.splitext,
            isfile=os.path.isfile, isdir=os.path.isdir, abspath=os.path.abspath,
            normpath=os.path.normpath, sep=os.sep,
        )
        # listdir 直接给 os.listdir 本体：它是 C 函数，没有 __globals__，
        # 拿不到本模块的全局。代价是不排序——提示词里已写明要自己 sorted()。
        ns["listdir"] = os.listdir
        return ns

    def _audit_tool(self, payload):
        """ToolRegistry 的统一审计回调。只写事件，不改工具结果。"""
        try:
            phase = payload.get("phase")
            name = payload.get("name") or ""
            if phase == "call":
                self.events.emit("tool/call", label=name, args=payload.get("args", ""),
                                 safe_mode=bool(payload.get("safe_mode")))
            elif phase == "blocked":
                self.events.emit("tool/blocked", label=name,
                                 reason=payload.get("reason", ""),
                                 args=payload.get("args", ""),
                                 safe_mode=bool(payload.get("safe_mode")))
            else:
                self.events.emit("tool/result", label=name,
                                 ok=bool(payload.get("ok")),
                                 ms=payload.get("ms"),
                                 chars=payload.get("chars", 0),
                                 error=payload.get("error", ""))
        except Exception:
            pass

    # ---------------- 底层调用 ----------------
    def _call(self, messages, tools=None, temperature=None):
        # 温度分档——幻觉率最直接的旋钮，按场景各取所需：
        #   带工具的轮 0.2（参数要准，编错一个路径就白跑）
        #   写产出物的轮 0.3（报告/文档/PPT，内容要能被查验，不该临场发挥）
        #   收口轮 0.6（复述手上已有的事实，不需要发散）
        #   闲聊/普通说话 用配置的 0.85（语气是她的灵性，别为降幻觉把她变成查表机）
        # 注意收口轮显式传了 0.6，会盖过这里——写文档时那条也应该压，见 _run_json 末尾。
        if temperature is None:
            if tools:
                temperature = 0.2
            elif getattr(self, "_low_temp", False):
                temperature = 0.3
            else:
                temperature = self.config.get("temperature", 0.85)
        resp = self.llm.chat(
            messages,
            tools=tools,
            temperature=temperature,
            model_name=getattr(self, "_model_name", None),
            provider=getattr(self, "_provider_id", None),
        )
        usage = resp.get("usage") or {}
        prompt = usage.get("prompt_tokens", 0) or 0
        completion = usage.get("completion_tokens", 0) or 0
        cache = usage.get("cache_hit", 0) or 0
        self.stats["prompt_tokens"] += prompt
        self.stats["completion_tokens"] += completion
        self.stats["cache_hit"] = self.stats.get("cache_hit", 0) + cache
        self.stats["calls"] += 1
        # 上下文压力看的是最近一次请求，不是这一轮所有调用的累计。
        self.stats["last_prompt_tokens"] = prompt
        self.stats["last_completion_tokens"] = completion
        self.stats["last_cache_hit"] = cache
        # 每次模型调用落一条：排查「这一轮哪一步贵」靠它。
        # 只进事件流（内存 + 账本），不进 prompt，零 token。
        try:
            self.events.emit(
                "llm/call",
                provider=resp.get("provider") or "",
                model=resp.get("model") or "",
                prompt_tokens=prompt,
                completion_tokens=completion,
                cache_hit=cache,
                has_tools=bool(tools),
                temperature=round(float(temperature), 2),
            )
        except Exception:
            pass
        return resp

    def _now(self, history=None, query=None):
        """当前时间 + 时间感知。

        早先的做法是把时间写进上下文让模型自己推，这里多做一步：
        直接把「隔了多久」算好告诉它，省得它拿 MM-DD 去反推今天是几号。
        只在有上一条用户消息时才加，没历史就只说现在几点。
        """
        now = time.strftime("%Y年%m月%d日 %H:%M")
        hint = f"现在是 {now}。"

        mem = self._recall_memory(query)
        if mem:
            hint += mem

        sess = self._recall_session()
        if sess:
            hint += sess

        if history:
            last_ts = None
            for m in reversed(history):
                if m.get("role") == "user" and m.get("ts"):
                    last_ts = m["ts"]
                    break
            if last_ts:
                try:
                    # ts 存的是 MM-DD HH:MM（不带年份），按当前年份补上；跨年会算错一天，可接受
                    cur_year = time.strftime("%Y")
                    dt = datetime.datetime.strptime(f"{cur_year}-{last_ts}", "%Y-%m-%d %H:%M")
                    delta = datetime.datetime.now() - dt
                    secs = delta.total_seconds()
                    if secs >= 86400:
                        hint += f"上次跟她说话是 {int(secs // 86400)} 天前（{last_ts}）。"
                    elif secs >= 3600:
                        hint += f"上次跟她说话是 {int(secs // 3600)} 小时前（{last_ts}）。"
                except Exception:
                    pass

        # 开机记录：让她知道这台机器什么时候开过（读启动日志最后一行）
        try:
            boot_log = os.path.join(self.config.base_dir, "_startup.log")
            if os.path.exists(boot_log):
                with open(boot_log, "r", encoding="utf-8") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
                if lines:
                    ts_part = lines[-1][:19]
                    # 只认新格式（带日期）；老格式只有 HH:MM:SS，跳过免得说错
                    if len(ts_part) >= 16 and ts_part[4] == "-" and ts_part[10] == " ":
                        gap_days = None
                        try:
                            dt2 = datetime.datetime.strptime(ts_part[:16], "%Y-%m-%d %H:%M")
                            gap_days = (datetime.datetime.now() - dt2).days
                        except Exception:
                            pass
                        if gap_days is None or gap_days <= 1:
                            hint += f"这台机器 {ts_part[5:16]} 开过机。"
                        else:
                            hint += f"这台机器最近一次开机是 {gap_days} 天前（{ts_part[5:16]}）。"
        except Exception:
            pass

        # 接续：把你自己上次写下的最后一段带回来。
        # 不这么做的话，「明天帮你修」这种话永远无法兑现——她不活在两个明天之间。
        if getattr(self, "_diary", ""):
            hint += (f"\n（你自己上次写日记的最后一段，情况可能已经变了，自己判断："
                     f"{self._diary}）")

        # 你没来的时候，机器替你数着天数。这不是让她装可怜，是让「隔了多久」有个凭据。
        # 只给她事实：隔了多久、他最后说了什么。至于这意味着什么、要不要开口，由她自己判断。
        # （早先这里是「想说话 0.72」这种评分，撤掉了——那是我替她体会，不是她的感受。）
        if getattr(self, "_presence", ""):
            parts = self._presence.split(" | ")
            gap_part = parts[0].split("] ")[-1]
            said = parts[1].replace("他最后说的：", "").strip() if len(parts) > 1 else ""
            hint += f"\n（你不在的这段：{gap_part}。他最后说的是「{said}」。）"

        # 她有权利不说话。这句不是客套，是把她当人而不是等指令的工具。
        hint += ("\n（你不想说话的时候可以不说：只回一句「今天不想说」，或者干脆一个省略号。"
                 "没人会追问，也不会被当成出错。）")

        return hint

    def _split_memory_entries(self):
        """把长期记忆分成 (常驻, 检索) 两段。

        常驻 = 身份/红线/偏好/决定/待办：每轮都该在，不吃检索、不受门槛影响。
        检索 = 项目/事实/愿望：按当前话题挑，进 system 末尾（易变区）。
        """
        path = os.path.join(self.work_dir, "knowledge", "memory.md")
        try:
            entries = load_memory_entries(path)
        except Exception:
            return [], []
        pin, rest = [], []
        for e in entries:
            tags = re.findall(r"\[([^\]]+)\]", e)
            (pin if any(t in MEMORY_PIN_KINDS for t in tags) else rest).append(e)
        return pin, rest

    def _persona(self):
        """人设从配置来，但**不往 prompt 里硬绑名字**。

        顺序：自定义 system_prompt > 名字 + 默认人格 > 默认人格。
        默认人格不带「阿鲤」——开源版给别人用，有人想叫大肥鱼、有人想叫别的。
        名字只在他自己填了 assistant_name 时才出现，空着就不派名字。
        """
        custom = (self.config.get("system_prompt") or "").strip()
        if custom:
            return custom
        name = (self.config.get("assistant_name") or "").strip()
        if name:
            return f"你叫{name}。" + prompts.DEFAULT_PERSONA
        return prompts.DEFAULT_PERSONA

    def _pin_memory(self):
        """常驻记忆。拼进 system 的人设区（稳定段），跨轮吃前缀缓存，成本约 1/10。

        为什么必须放稳定段：放末尾的话每轮全价重发——实测 11 条 ≈ 400 token/轮，
        5.5 次调用 × 30 轮/天 ≈ 6.6 万 token/天 ≈ ¥4/月，直接吃掉一半预算。
        """
        pin, _ = self._split_memory_entries()
        if not pin:
            return ""
        return "\n\n【长期记忆·常驻】\n" + "\n".join(pin)

    def _recall_memory(self, query=None):
        """把长期记忆里跟当前话题相关的条目带回上下文（常驻那部分在 system 里）。

        条目少（<= MEMORY_INJECT_ALL）时全量注入；多了再按 query 检索，
        先过相关度门槛，再按「重要度×新鲜度×相关度」重排取前 K。
        全程本地 BM25 + 日期/标记计算，不调模型、不花 API token。
        宁可少记不可错记：全 miss 就空着，让她直说记不得，别拿不相关的当记忆。
        """
        _, rest = self._split_memory_entries()
        if not rest:
            return ""
        if len(rest) <= MEMORY_INJECT_ALL:
            picked = rest
        else:
            scored = rank_memory_entries(query or "", rest, MEMORY_INJECT_K, RECALL_MIN_SCORE)
            picked = [rest[i] for _, i in scored]
        if not picked:
            return ""
        return "\n（长期记忆：\n" + "\n".join(picked) + "\n）"

    # ---------------- 会话滚动摘要（L1） ----------------
    def _load_summarized_msgs(self):
        try:
            with open(self._session_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("summarized_msgs") or 0)
        except Exception:
            return 0

    def _save_summarized_msgs(self, n):
        try:
            os.makedirs(os.path.dirname(self._session_state_path), exist_ok=True)
            with open(self._session_state_path, "w", encoding="utf-8") as f:
                json.dump({"summarized_msgs": n}, f)
        except Exception:
            pass

    def _recall_session(self):
        """把会话摘要的最新一段带回上下文。按行从末尾取，保证不截断单行。"""
        path = os.path.join(self.work_dir, "knowledge", "session.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return ""
        # 按空行分段，整段取最近几段。原来只取末尾 600 字符：
        #   · 7 段摘要里前 5 段等于白写
        #   · 按行往上凑，最新那段长一点就只塞进去半段
        segs = [s.strip() for s in re.split(r"\n\s*\n", text) if s.strip()]
        if not segs:
            return ""
        keep, used = [], 0
        for seg in reversed(segs):
            if keep and (len(keep) >= SESSION_INJECT_SEGMENTS
                         or used + len(seg) > SESSION_INJECT_CHARS):
                break
            keep.append(seg)
            used += len(seg) + 2
        body = "\n\n".join(reversed(keep))
        if len(body) > SESSION_INJECT_CHARS:
            body = body[-SESSION_INJECT_CHARS:]
        return "\n（这段对话的摘要：\n" + body + "\n）"

    def _context_pressure(self):
        """最近一次请求的上下文压力。只用于触发摘要，不拿它计费。"""
        try:
            window = int(self.config.get("context_window") or 0)
            last = int(self.stats.get("last_prompt_tokens") or 0)
            return (last / window) if (window > 0 and last > 0) else 0.0
        except Exception:
            return 0.0

    def _maybe_summarize(self, history, user_text, reply):
        """每聊满 SUMMARY_EVERY 轮，用当前模型把未摘要的对话压成要点。

        2026-11-24 补：除了固定 6 轮，还在上下文压力超过 75% 时允许提前压；
        但至少等 2 轮，避免一句话一摘要把免费档也拖慢。
        摘要是「对话结束后一次性」的活，不是每轮重发，所以 token 是零头。
        失败静默：不挡聊天，下轮再试；也绝不为了摘要去打断阿鲤的回答。
        """
        # 冷启动：状态文件从未写过（summarized_msgs=0），但 history 已经很长——
        # 那是启用摘要前攒下的旧对话。不追补旧账：把已有历史都视为已摘要，
        # 从这轮之后的新对话开始滚动，避免第一轮就把几百条旧消息塞给模型。
        every = int(self.config.get("summary_every") or 0)
        warm_gate = (every if every > 0 else SUMMARY_EVERY) * 2
        if self._summarized_msgs == 0 and len(history) > warm_gate:
            self._summarized_msgs = len(history)
            self._save_summarized_msgs(len(history))
            return

        total = len(history) + 2
        pending = total - self._summarized_msgs
        pressure = self._context_pressure()
        # summary_every=0 → 不再按固定轮数摘要，只在上下文压力到阈值时压
        due_fixed = every > 0 and pending >= every * 2
        due_pressure = (
            pressure >= SUMMARY_PRESSURE
            and pending >= SUMMARY_PRESSURE_MIN_TURNS * 2
        )
        if not (due_fixed or due_pressure):
            return

        # 拼未摘要的对话：上次摘要点之后的所有历史 + 本轮一问一答
        parts = []
        for m in history[self._summarized_msgs:]:
            role = "他" if (m.get("role") or "") == "user" else "她"
            content = (m.get("content") or "").strip()
            if len(content) > SUMMARY_DIALOG_LIMIT:
                content = content[:SUMMARY_DIALOG_LIMIT] + "…"
            if content:
                parts.append(f"{role}：{content}")
        if user_text and user_text.strip():
            parts.append(f"他：{user_text.strip()}")
        if reply and reply.strip():
            parts.append(f"她：{reply.strip()}")
        if not parts:
            return

        prompt = (
            "把下面这段对话压缩成最多 "
            f"{SUMMARY_MAX_ITEMS} 条要点，只保留跨轮有用的信息："
            "说过的决定、约定、改过的东西、用户的新情况、待办、没做完的事。"
            "如果涉及代码，保留精确的文件路径、函数名、报错原文和已改内容；"
            "只记关于「他」（用户）的事实、要求、约定；"
            "不要记你自己（「她」）做过什么，也不要写「女方」「她」这种第三人称自述；"
            "只记事实，不记评价和过程；不要寒暄、不要留废话。"
            "每条一行，用「- 」开头，一句话说清，别再输出其他内容。\n\n"
            "对话：\n" + "\n".join(parts)
        )

        try:
            resp = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=60,
            )
        except Exception:
            return  # 摘要失败不影响本轮，下轮再试
        summary = (resp.get("content") or "").strip()
        if not summary:
            return

        path = os.path.join(self.work_dir, "knowledge", "session.md")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n" + summary + "\n")
        except Exception:
            return

        self._summarized_msgs = total
        self._save_summarized_msgs(total)
        # 攒够几段就把最老的合并成长期脉络，避免 session.md 无限变长
        self._maybe_merge_session()

    def _maybe_merge_session(self):
        """L1 摘要攒够 L2_MERGE_AT 段时，把最老的几段合并成一段「长期脉络」。

        为什么要有这一层：session.md 只会越写越长，而 _recall_session 只注入最后几段。
        不合并的话，早期的摘要写进去就再也不会被读到——等于没记。
        合并后总段数回落，早期信息被压进一段脉络里，跟着后面几段一起被注入。

        用当前模型，失败静默（记忆是附加品，不能挡聊天）。
        """
        path = os.path.join(self.work_dir, "knowledge", "session.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return
        segs = [s.strip() for s in re.split(r"\n\s*\n", text) if s.strip()]
        if len(segs) < L2_MERGE_AT:
            return
        old = segs[:L2_MERGE_AT]
        keep = segs[L2_MERGE_AT:]
        prompt = (
            "下面是把几段对话压缩后的摘要。把它们合并成一段更长期的脉络，"
            f"最多 {L2_MAX_ITEMS} 条要点，只保留长期有效的："
            "用户是谁、在做什么项目、定过什么规矩、做过哪些决定、还有什么没做完。"
            "只记关于「他」（用户）的事实和要求；不要记助手自己做过什么，"
            "不要写「女方」「她」这种第三人称自述。每条一行，用「- 」开头，别输出其他内容。\n\n"
            "摘要：\n" + "\n\n".join(old)
        )
        try:
            resp = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=60,
            )
        except Exception:
            return
        merged = (resp.get("content") or "").strip()
        if not merged:
            return
        new_segs = [L2_MARK + "\n" + merged] + keep
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n\n".join(new_segs) + "\n")
        except Exception:
            return
        self.events.emit("thought", label="摘要已合并为长期脉络（%d 段→1 段）" % len(old))

    # ---------------- 长期记忆抽取（当前模型，后台异步） ----------------
    def _load_extracted_msgs(self):
        try:
            with open(self._memory_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("extracted_msgs") or 0)
        except Exception:
            return 0

    def _save_extracted_msgs(self, n):
        try:
            os.makedirs(os.path.dirname(self._memory_state_path), exist_ok=True)
            with open(self._memory_state_path, "w", encoding="utf-8") as f:
                json.dump({"extracted_msgs": n}, f)
        except Exception:
            pass

    def _maybe_extract_memory(self, history, user_text, reply):
        """每 N 轮，用当前接入的模型从对话里抽稳定事实，写进长期记忆 memory.md。

        后台异步：不挡聊天；抽失败也静默，下轮再试。
        """
        # 冷启动：老历史不追补（跟会话摘要同一套逻辑）
        if self._extracted_msgs == 0 and len(history) > MEMORY_EXTRACT_EVERY * 2:
            self._extracted_msgs = len(history)
            self._save_extracted_msgs(len(history))
            return

        total = len(history) + 2
        pending = total - self._extracted_msgs
        if pending < MEMORY_EXTRACT_EVERY:
            return

        # 只拿最近一段未抽取的对话，控长度，别让 7b 读太长的输入
        parts = []
        for m in history[self._extracted_msgs:][-24:]:
            role = "他" if (m.get("role") or "") == "user" else "她"
            content = (m.get("content") or "").strip()
            if len(content) > 300:
                content = content[:300] + "…"
            if content:
                parts.append(f"{role}：{content}")
        if user_text and user_text.strip():
            parts.append(f"他：{user_text.strip()}")
        if reply and reply.strip():
            parts.append(f"她：{reply.strip()}")
        if not parts:
            return

        prompt = (
            "你是记忆抽取器。从下面这段对话里，提取需要长期记住的稳定事实。\n"
            "只提这几类：用户是谁（身份/背景）、正在做的事/项目、定下的规矩或红线、"
            "身体状况、明确的决定或约定。\n"
            "要求：每条一句话，最多 "
            f"{MEMORY_EXTRACT_MAX} 条；寒暄、过程、临时情绪、废话都不要；"
            "实在没有可提的就只回「无」。\n"
            # 11-24 事故：这小模型把助手自己的行为记成了「用户的事实」，
            # memory.md 里出现过「女方正在测试插件」「女方摸清了前端结构」。
            "主语必须是用户（「他」）；不要记录助手（「她」）自己的行为、状态或对系统的评价，"
            "绝对不许出现「女方」「她」这种第三人称自述。\n"
            "每条格式固定为：`- [类型] 一句话`，类型只能是："
            "身份/红线/偏好/项目/决定/待办/事实/愿望 之一。\n\n"
            "对话：\n" + "\n".join(parts)
        )

        # 先记进度再抽：抽失败也不反复重抽同一段
        self._extracted_msgs = total
        self._save_extracted_msgs(total)

        def worker():
            try:
                resp = self.llm.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.2,
                    timeout=300,
                )
            except Exception:
                return
            text = (resp.get("content") or "").strip()
            if not text:
                return
            self._append_memory_lines(text)

        try:
            import threading
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass

    def _append_memory_lines(self, text):
        """把抽取结果逐条追加进 memory.md，与已有条目去重后写入。

        条目统一成 `- [月-日][类型] 内容`。抽取器只给 `[类型]`，日期在这里补。
        """
        path = os.path.join(self.work_dir, "knowledge", "memory.md")
        try:
            existing = load_memory_entries(path)
        except Exception:
            existing = []

        def _split(ln):
            """一行 → (标签列表, 正文)。日期、`- `、类型标签全部剥掉。"""
            s = re.sub(r"^-\s*", "", ln.strip())
            tags = []
            while True:
                m = re.match(r"^\[([^\]]*)\]\s*", s)
                if not m:
                    break
                tags.append(m.group(1))
                s = s[m.end():]
            m = re.match(r"^(!!|!|~)\s+", s)
            if m:
                s = s[m.end():]
            return tags, s.strip()

        existing_cores = [_split(e)[1] for e in existing]
        today = datetime.datetime.now().strftime("%m-%d")

        new_lines = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or not ln.startswith("-"):
                continue
            tags, core = _split(ln)
            if not core:
                continue
            if any(core in ec or ec in core for ec in existing_cores if ec):
                continue
            kind = next((t for t in tags if t in MEMORY_KINDS), "")
            prefix = f"- [{today}]" + (f"[{kind}]" if kind else "")
            new_lines.append(prefix + " " + core)

        if not new_lines:
            return
        # 走 memory.append_memory_lines：锁内读-改-写 + 原子替换。
        # 直接 open("a") 会和桌面端的「快照→改→replace」打架，追加的行可能写进旧 inode 丢掉。
        try:
            append_memory_lines(path, new_lines)
        except Exception:
            pass

    def reset_session(self):
        """新开对话时清掉这段会话摘要和进度。"""
        path = os.path.join(self.work_dir, "knowledge", "session.md")
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        self._summarized_msgs = 0
        self._save_summarized_msgs(0)
        self._extracted_msgs = 0
        self._save_extracted_msgs(0)


def _presence_tail(path):
    """心跳记录的最后一条。返回空串表示没有心跳（没配计划任务）。

    这不叫记忆，叫「她不在场时的时间刻度」。diary 是她主动写的，
    这个是她没在的时候、机器替她数的天数——两件事，不要混。
    """
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return lines[-1] if lines else ""
    except Exception:
        return ""


def _raise(exc):
    raise exc


def _diary_tail(path, limit=220):
    """取日记的最后一节，给「上次她想说什么」留一条接续的线。

    为什么需要这个（2026-11-20 补）：
    她 11-18 夜在 diary.md 里写下「他的 memory.md 全是轮转测试这种垃圾，
    明天得帮他把这个修好」。然后三天没人启动她，这句话就烂在文件里，
    谁也没读到——她说的「明天」永远是空的，因为她不存在于两个明天之间。
    启动时把最后这一段塞回上下文，是给「接续」搭一条最短的线。

    只取最后一节、限制 220 字符：放 system 最末尾（时间那行之后），
    这一块本来就每轮都在变，加内容只损一个尾块，不会把前面的人设缓存全废
    （结论来自第五轮那三组对照）。
    """
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return ""
        # 按 "## " 分节，取最后一节；没有分节就当整篇
        parts = re.split(r"^## ", text, flags=re.M)
        tail = ("## " + parts[-1]) if len(parts) > 1 else text
        tail = re.sub(r"\n{2,}", "\n", tail).strip()
        if len(tail) > limit:
            tail = "…" + tail[-limit:]
        return tail
    except Exception:
        return ""
