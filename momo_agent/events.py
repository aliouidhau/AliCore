"""事件流。

抄自 OpenHands 的思路：agent 的每一步都变成一条事件（动作 / 观察 / 错误 / 回答），
UI 只订阅事件、不参与执行。

两层：
  · 内存 events：给前端轮询，keep=40，会滚动；
  · ledger：可选的落盘账本，只追加，不随内存滚动丢失。
"""

import time
import uuid


class EventStream:
    def __init__(self, keep=40, ledger=None):
        self.keep = keep
        self.ledger = ledger
        self.events = []
        # 单调递增的序号：前端靠它做增量拉取。不能靠列表下标——
        # 超过 keep 会从头截断，下标会整体前移，前端就会重复或漏读。
        self._seq = 0
        self.session_id = (
            getattr(ledger, "session_id", None)
            or ("s" + uuid.uuid4().hex[:12])
        )
        self.turn_id = None

    def begin_turn(self, turn_id=None):
        self.turn_id = turn_id or ("t" + uuid.uuid4().hex[:10])
        return self.turn_id

    def emit(self, kind, **data):
        self._seq += 1
        event = {
            "seq": self._seq,
            "kind": kind,
            "ts": round(time.time(), 3),
            "session_id": self.session_id,
            **data,
        }
        if self.turn_id:
            event["turn_id"] = self.turn_id
        self.events.append(event)
        if len(self.events) > self.keep:
            self.events = self.events[-self.keep:]
        if self.ledger is not None:
            try:
                self.ledger.append(event)
            except Exception:
                pass
        return event

    def clear(self):
        """只清内存流，不清账本。账本是追加式事实，不该被新的一轮抹掉。"""
        self.events = []
        self._seq = 0
        self.turn_id = None

    def as_list(self):
        return list(self.events)

    def summary(self):
        """给前端的一句话轨迹，例如：读文件 → 改代码 → 写回。"""
        icons = {
            "user": "你说",
            "thought": "想",
            "action": "做",
            "observation": "看",
            "error": "错",
            "answer": "答",
            "tool/call": "调",
            "tool/result": "回",
            "tool/blocked": "拦",
            "turn/start": "始",
            "turn/end": "终",
        }
        return " → ".join(
            f"{icons.get(e['kind'], e['kind'])}:{e.get('label', '')}"
            for e in self.events
            if e.get("label") and e.get("kind") not in ("turn/start", "turn/end")
        )


def turn_outline(events, limit=50, prompt_preview=50, answer_preview=120):
    """把事件流水折成「轮次大纲」。纯函数，不碰磁盘，方便单测和复用。

    抄 DSH 的 dsh-session-turn-outline：日志是唯一真相源，大纲是它的一次 fold。
    只保留人真正会看的那几个字段——轮号、时间、耗时、你说的、她回的、花多少。
    """
    turns = {}
    order = []
    for e in events or []:
        tid = e.get("turn_id")
        if not tid:
            continue
        t = turns.get(tid)
        if t is None:
            t = turns[tid] = {
                "turn_id": tid,
                "session_id": e.get("session_id", ""),
                "seq": e.get("ledger_seq") or e.get("seq"),
                "start": None, "end": None, "ms": None,
                "user": "", "answer": "",
                "mode": "", "ok": None,
                "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hit": 0,
                "_llm": 0, "_tool": 0, "_action": 0,
            }
            order.append(tid)
        kind = e.get("kind")
        if kind == "turn/start":
            t["start"] = e.get("ts")
        elif kind == "user":
            t["user"] = (e.get("content") or e.get("label") or "")[:prompt_preview]
        elif kind == "answer":
            t["answer"] = (e.get("content") or e.get("label") or "")[:answer_preview]
        elif kind == "llm/call":
            t["_llm"] += 1
        elif kind == "stats":
            t["calls"] = e.get("calls") or t["calls"]
            t["prompt_tokens"] = e.get("prompt_tokens") or t["prompt_tokens"]
            t["completion_tokens"] = e.get("completion_tokens") or t["completion_tokens"]
            t["cache_hit"] = e.get("cache_hit") or t["cache_hit"]
            t["mode"] = e.get("mode") or t["mode"]
        elif kind == "tool/call":
            t["_tool"] += 1
        elif kind == "action":
            t["_action"] += 1
        elif kind == "turn/end":
            t["end"] = e.get("ts")
            t["ok"] = e.get("ok")
            t["mode"] = e.get("mode") or t["mode"]
            if e.get("ms") is not None:
                t["ms"] = e.get("ms")

    out = [turns[tid] for tid in order]
    for t in out:
        if not t["calls"]:
            t["calls"] = t["_llm"]
        t["tool_calls"] = t["_tool"] or t["_action"]
        if t["ms"] is None and t["start"] and t["end"]:
            t["ms"] = int(round((t["end"] - t["start"]) * 1000))
        for k in ("_llm", "_tool", "_action"):
            t.pop(k, None)
    return out[-limit:] if limit else out
