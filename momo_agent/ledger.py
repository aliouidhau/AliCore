"""事件账本：只追加、可按日期回放。

和 events.py 的分工：
  · EventStream 是这一轮的内存事件流，给 UI 轮询、给 trace 摘要用，超过 40 条会滚动；
  · EventLedger 是落盘的账本，聊天、工具调用、代码运行、文件操作都写进去，
    重启不丢，方便回放、排查和以后做“从事件重建 UI”。

账本按天分文件，避免一年后长成一个几十 MB 的单文件。写入失败只记内存事件，
绝不让日志把聊天打断；这是日志，不是业务。
"""

import json
import os
import threading
import time
import uuid
from collections import deque


def _today_path(root):
    return os.path.join(root, time.strftime("%Y-%m-%d") + ".jsonl")


class EventLedger:
    def __init__(self, root, session_id=None):
        self.root = os.path.abspath(root)
        self.session_id = session_id or ("s" + uuid.uuid4().hex[:12])
        self._lock = threading.RLock()
        self._seq = 0
        try:
            os.makedirs(self.root, exist_ok=True)
        except Exception:
            pass

    @property
    def path(self):
        return _today_path(self.root)

    def append(self, event):
        """写一条事件。返回写入后的副本；失败返回原事件，不抛。"""
        item = dict(event or {})
        try:
            with self._lock:
                self._seq += 1
                item.setdefault("ledger_seq", self._seq)
                item.setdefault("session_id", self.session_id)
                item.setdefault("ts", round(time.time(), 3))
                item.setdefault("kind", "unknown")
                path = self.path
                os.makedirs(self.root, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return item

    def read_recent(self, limit=200, session_id=None):
        """读最近 limit 条。跨天文件按日期从旧到新读，deque 只留尾部。"""
        try:
            limit = max(1, min(int(limit or 200), 2000))
        except Exception:
            limit = 200
        files = sorted(
            (os.path.join(self.root, n) for n in os.listdir(self.root)
             if n.endswith(".jsonl")),
            reverse=False,
        )
        if not files:
            return []
        buf = deque(maxlen=limit)
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if session_id and ev.get("session_id") != session_id:
                            continue
                        buf.append(ev)
            except Exception:
                continue
        return list(buf)

    def describe(self):
        return {
            "path": self.root,
            "today": self.path,
            "session_id": self.session_id,
        }
