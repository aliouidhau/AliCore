"""会话存储：一个对话 = 一个目录 + append-only 的 session.jsonl。

做法照 DeepSeek Harness（.dsh/sessions）：会话从第一句话起就持续落盘，
每一轮往事件流里追加 user/assistant 记录；切换历史只是换一个「当前会话」
指针，不存在「先把当前整段存一遍、再载入另一段」的搬运。

事件行格式（每行一个 JSON，seq 单调递增）：
    {"type":"session", "v":1, "id":..., "createdAt":..., "title":...}
    {"type":"user/message", "seq":0, "time":ms, "data":{"content":..., "ts":"11-26 20:30"}}
    {"type":"assistant/message", "seq":1, "time":ms, "data":{...}}
    {"type":"session/title", "seq":2, "time":ms, "data":{"title":...}}
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid


def _now_ms():
    return int(time.time() * 1000)


class SessionStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._lock = threading.RLock()
        self._current = None
        self._seq = 0

    # ---------------- 路径 ----------------
    def _dir(self, sid):
        return os.path.join(self.root, sid)

    def _jsonl(self, sid):
        return os.path.join(self.root, sid, "session.jsonl")

    def _meta(self, sid):
        return os.path.join(self.root, sid, "meta.json")

    @property
    def current(self):
        return self._current

    def exists(self, sid):
        return bool(sid) and os.path.exists(self._jsonl(sid))

    # ---------------- 建/开 ----------------
    def create(self, title=""):
        """新开一个空会话，返回 id。"""
        with self._lock:
            sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            os.makedirs(self._dir(sid), exist_ok=True)
            header = {"type": "session", "v": 1, "id": sid,
                      "createdAt": _now_ms(), "title": title or "",
                      "parent": None}
            with open(self._jsonl(sid), "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(header, ensure_ascii=False) + "\n")
            self._current = sid
            self._seq = 0
            self._write_meta(sid, title=title, count=0, created=header["createdAt"], updated=_now_ms())
            return sid

    def open(self, sid):
        """切到某个会话。只换指针 + 读消息，不写任何东西。"""
        if not self.exists(sid):
            raise FileNotFoundError(sid)
        with self._lock:
            self._current = sid
            self._seq = self._last_seq(sid)
        return self.load_messages(sid)

    def open_last_or_create(self):
        """启动时用：打开最近碰过的会话，没有就新建一个。"""
        items = self.list()
        if items:
            return self.open(items[0]["id"])
        self.create()
        return []

    def delete(self, sid):
        """删掉一段会话（连同目录）。返回是否删的是当前会话。"""
        with self._lock:
            if not sid or not self.exists(sid):
                raise FileNotFoundError(sid)
            was_current = sid == self._current
            if was_current:
                self._current = None
                self._seq = 0
            shutil.rmtree(self._dir(sid))
            return was_current

    # ---------------- 写 ----------------
    def append(self, event_type, data, sid=None, title=None):
        """往当前（或指定）会话尾部追加一条事件；返回 seq。"""
        with self._lock:
            sid = sid or self._current
            if not sid or not self.exists(sid):
                sid = self.create()
            self._seq += 1
            rec = {"type": event_type, "seq": self._seq, "time": _now_ms(), "data": data}
            with open(self._jsonl(sid), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            meta = self._read_meta(sid)
            count = meta.get("count", 0) + (1 if event_type.endswith("/message") else 0)
            first = meta.get("first") or ""
            if not first and event_type == "user/message":
                first = (data.get("content") or "")[:60]
            self._write_meta(sid, title=title if title is not None else meta.get("title", ""),
                             count=count, first=first,
                             created=meta.get("createdAt") or _now_ms(),
                             updated=_now_ms())
            return self._seq

    def append_message(self, role, content, ts="", timestamp=None, sid=None):
        return self.append(
            "user/message" if role == "user" else "assistant/message",
            {"role": role, "content": content, "ts": ts,
             "timestamp": timestamp if timestamp is not None else time.time()},
            sid=sid,
        )

    def set_title(self, title, sid=None):
        self.append("session/title", {"title": title}, sid=sid, title=title)

    # ---------------- 读 ----------------
    def messages_of(self, sid):
        """读出一段会话的 user/assistant 消息，给引擎和前端用。"""
        return self.load_messages(sid)

    def load_messages(self, sid):
        out = []
        path = self._jsonl(sid)
        if not os.path.exists(path):
            return out
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") not in ("user/message", "assistant/message"):
                        continue
                    d = rec.get("data") or {}
                    out.append({
                        "role": d.get("role") or ("user" if rec["type"].startswith("user") else "assistant"),
                        "content": d.get("content") or "",
                        "ts": d.get("ts") or time.strftime("%m-%d %H:%M", time.localtime(rec.get("time", 0) / 1000)),
                        "timestamp": d.get("timestamp") or (rec.get("time", 0) / 1000.0),
                    })
        except Exception:
            return out
        return out

    def _last_seq(self, sid):
        seq = 0
        path = self._jsonl(sid)
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "rb") as f:
                # 倒着找最后一行，别把 700 条全扫一遍
                f.seek(0, os.SEEK_END)
                size = f.tell()
                pos = max(0, size - 4096)
                f.seek(pos)
                tail = f.read().decode("utf-8", "ignore").strip().splitlines()
                for line in reversed(tail):
                    try:
                        seq = int(json.loads(line).get("seq") or 0)
                        break
                    except Exception:
                        continue
        except Exception:
            return 0
        return seq

    def list(self, include_current=True):
        """列会话，最近碰过的排前面。只读 meta.json，不读正文。"""
        if not os.path.isdir(self.root):
            return []
        items = []
        for sid in os.listdir(self.root):
            if not os.path.isdir(self._dir(sid)):
                continue
            meta = self._read_meta(sid)
            if not meta:
                continue
            items.append({
                "id": sid,
                "name": sid,                 # 前端兼容字段
                "first": meta.get("first") or meta.get("title") or "",
                "title": meta.get("title") or "",
                "count": meta.get("count", 0),
                "created": meta.get("createdAt", 0),
                "updated": meta.get("updated", 0),
                "current": sid == self._current,
            })
        items = [i for i in items if include_current or not i["current"]]
        # 一条消息都没有的空会话不上列表：刚打开软件时那种「0 条」条目没意义
        items = [i for i in items if i.get("count")]
        items.sort(key=lambda i: i.get("updated") or 0, reverse=True)
        return items

    # ---------------- meta ----------------
    def _read_meta(self, sid):
        p = self._meta(sid)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception:
                return {}
        # 没有 meta 的老目录：从 jsonl 现算一次
        path = self._jsonl(sid)
        if not os.path.exists(path):
            return {}
        count, first, created = 0, "", 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not created and rec.get("type") == "session":
                        created = rec.get("createdAt") or 0
                    if rec.get("type") == "user/message":
                        count += 1
                        if not first:
                            first = (rec.get("data", {}).get("content") or "")[:60]
                    elif rec.get("type") == "assistant/message":
                        count += 1
        except Exception:
            pass
        meta = {"id": sid, "title": "", "first": first, "count": count,
                "createdAt": created, "updated": os.path.getmtime(path) * 1000}
        self._write_meta(sid, title="", count=count, first=first,
                         created=created, updated=meta["updated"])
        return meta

    def _write_meta(self, sid, title="", count=0, first="", created=0, updated=0):
        try:
            os.makedirs(self._dir(sid), exist_ok=True)
            tmp = self._meta(sid) + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"id": sid, "title": title, "first": first, "count": count,
                           "createdAt": created, "updated": updated}, f, ensure_ascii=False)
            os.replace(tmp, self._meta(sid))
        except Exception:
            pass

    # ---------------- 老档迁移 ----------------
    def migrate_legacy(self, legacy_dir, marker):
        """把老版 sessions/*.json（整段 messages 数组）搬成新格式。

        每个 json 变成一段会话；原文件改名 .migrated 保留，不删。
        用 marker 文件做一次性标记，重复跑不会翻倍。
        """
        if os.path.exists(marker):
            return 0
        moved = 0
        seen = set()
        if os.path.isdir(legacy_dir):
            for name in sorted(os.listdir(legacy_dir), reverse=True):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(legacy_dir, name)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, list) or not data:
                    continue
                # 同内容的档只搬一份：老实现点一次历史就复制一段，目录里
                # 全是同一段对话的副本，原样搬过去列表还是一团乱
                body = [(m.get("role"), m.get("content") or "") for m in data
                        if m.get("role") in ("user", "assistant")]
                digest = hashlib.sha256(
                    json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()
                if digest in seen:
                    self._retire(p)
                    continue
                seen.add(digest)
                created = self._time_from_name(name)
                self._create_from_messages(data, created=created)
                moved += 1
                self._retire(p)
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(_now_ms()))
        except Exception:
            pass
        return moved

    def import_messages(self, data, created=None):
        """把一段外部 messages 数组建成一个会话（老版 history.json 用）。"""
        return self._create_from_messages(data, created=created)

    def _retire(self, path):
        """老档搬完后改名留档，不删用户数据。"""
        dest = path + ".migrated"
        n = 1
        while os.path.exists(dest):
            dest = path + f".migrated{n}"
            n += 1
        try:
            os.rename(path, dest)
        except Exception:
            pass

    def _time_from_name(self, name):
        """从老档名 20261126_202741.json 里取创建时间戳（毫秒）。"""
        m = re.match(r"^(\d{8})_(\d{6})", name)
        if not m:
            return None
        try:
            t = time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return int(time.mktime(t) * 1000)
        except Exception:
            return None

    def _create_from_messages(self, data, created=None):
        created = created or _now_ms()
        # 会话 id 用它的创建时间，列表一眼能看出是哪天哪段
        sid = time.strftime("%Y%m%d-%H%M%S-", time.localtime(created / 1000.0)) + uuid.uuid4().hex[:4]
        os.makedirs(self._dir(sid), exist_ok=True)
        with open(self._jsonl(sid), "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "session", "v": 1, "id": sid,
                                "createdAt": created, "title": "", "parent": None},
                               ensure_ascii=False) + "\n")
        count, first = 0, ""
        for i, msg in enumerate(data):
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            ts = msg.get("ts") or time.strftime("%m-%d %H:%M")
            rec = {"type": "user/message" if role == "user" else "assistant/message",
                   "seq": i + 1, "time": created, "data": {
                       "role": role, "content": msg.get("content") or "",
                       "ts": ts, "timestamp": msg.get("timestamp") or (created / 1000.0)}}
            with open(self._jsonl(sid), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
            if role == "user" and not first:
                first = (msg.get("content") or "")[:60]
        self._write_meta(sid, title="", count=count, first=first, created=created, updated=created)
        self._seq = max(self._seq, len(data))
        return sid
