"""学习进度 / 错题本 / 复习调度。

为什么单独一个模块：这是「学习」数据，不是「对话」数据。放在 momo_agent 里
是为了能脱离 pywebview 单测——momo_app 一 import 就要拉起整个桌面端。

三条约束（2026-11-26）：
1. 零 token：纯本地 JSON，不调模型。
2. 零侵入：不碰教程正文——教程语音绑的是行号，改一行就错位（见 _check_voice_sync）。
3. 只追加：_progress.json 坏了就当空的，不能让靶场打不开。进度可以丢，工具不能死。

数据形状（version 1）::

    {
      "version": 1,
      "lessons": {"01": {"first_at": "...", "last_at": "...", "reads": 2,
                          "done": false, "done_at": ""}},
      "cases":   {"py01": {"attempts": 2, "fails": 1, "pass_at": "",
                            "last_at": "...", "next_review": "2026-11-27",
                            "reinforced": false}},
      "updated": "2026-11-26T15:00:00"
    }

复习规则（故意简单，能讲清楚比"算法先进"重要）：
    · 错了        → 明天再推一次
    · 错过又过了  → 3 天后再推一次（巩固一次）
    · 一次就过    → 不再推
"""

import json
import os
import re
from datetime import date, datetime, timedelta

from . import fsutil

DEFAULT = {"version": 1, "lessons": {}, "cases": {}, "updated": ""}

# 01_认识Python.py / C06_指针入门.c / 24_毕业设计_零件库存报表.py
_LESSON_RE = re.compile(r"^(?:(\d{1,2})_|[Cc](\d{1,2})_)")


def _today(now=None):
    if now is None:
        return date.today()
    if isinstance(now, datetime):
        return now.date()
    if isinstance(now, date):
        return now
    return date.fromisoformat(str(now)[:10])


def _stamp(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S") \
        if isinstance(now, datetime) or now is None else str(now)


def lesson_key(filename):
    """从文件名推断课号：01 / C06；不是教程文件就返回 ""。"""
    base = os.path.basename(str(filename or "").replace("\\", "/"))
    m = _LESSON_RE.match(base)
    if not m:
        return ""
    if m.group(1):
        return "%02d" % int(m.group(1))
    return "C%02d" % int(m.group(2))


def lesson_title(filename):
    """从文件名取课名：'C06_指针入门.c' → '指针入门'。"""
    base = os.path.splitext(os.path.basename(str(filename or "")))[0]
    base = _LESSON_RE.sub("", base, count=1)
    return base.replace("_", " ").strip() or base


def load(path):
    """读进度；文件不存在/损坏都返回空结构，绝不抛。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT))
    for k, v in DEFAULT.items():
        if k not in data or not isinstance(data[k], type(v)):
            data[k] = json.loads(json.dumps(v))
    return data


def save(path, data):
    data["updated"] = _stamp()
    fsutil.atomic_write_json(path, data)
    return data


def record_lesson_read(path, filename, now=None):
    """记一次「翻开了这课」。返回课号；不是教程就返回 ""。"""
    key = lesson_key(filename)
    if not key:
        return ""
    data = load(path)
    e = data["lessons"].get(key) or {}
    stamp = _stamp(now)
    e["first_at"] = e.get("first_at") or stamp
    e["last_at"] = stamp
    e["reads"] = int(e.get("reads") or 0) + 1
    e.setdefault("done", False)
    e.setdefault("done_at", "")
    data["lessons"][key] = e
    save(path, data)
    return key


def mark_lesson_done(path, key, done=True, now=None):
    data = load(path)
    e = data["lessons"].get(key) or {"first_at": "", "reads": 0}
    e["done"] = bool(done)
    e["done_at"] = _stamp(now) if done else ""
    if not e.get("first_at"):
        e["first_at"] = _stamp(now)
    data["lessons"][key] = e
    save(path, data)
    return True


def record_practice(path, case_id, passed, now=None, reinforce_days=3):
    """记一次实战提交，并按规则排下一次复习。"""
    cid = str(case_id or "").strip()
    if not cid:
        return {}
    today = _today(now)
    data = load(path)
    e = data["cases"].get(cid) or {
        "attempts": 0, "fails": 0, "pass_at": "", "last_at": "",
        "next_review": "", "reinforced": False,
    }
    e["attempts"] = int(e.get("attempts") or 0) + 1
    e["last_at"] = _stamp(now)
    if passed:
        e["pass_at"] = e.get("pass_at") or _stamp(now)
        if int(e.get("fails") or 0) > 0 and not e.get("reinforced"):
            # 错过又过了：巩固一次
            e["next_review"] = (today + timedelta(days=reinforce_days)).isoformat()
            e["reinforced"] = True
        else:
            e["next_review"] = ""
    else:
        e["fails"] = int(e.get("fails") or 0) + 1
        e["reinforced"] = False
        e["next_review"] = (today + timedelta(days=1)).isoformat()
    data["cases"][cid] = e
    save(path, data)
    return e


def due_reviews(path, today=None):
    """今天该复习的题号（按逾期越久越靠前）。"""
    today = _today(today)
    data = load(path)
    due = []
    for cid, e in data["cases"].items():
        nr = str((e or {}).get("next_review") or "")
        if not nr:
            continue
        try:
            when = date.fromisoformat(nr[:10])
        except ValueError:
            continue
        if when <= today:
            due.append((when.isoformat(), cid))
    due.sort()
    return [cid for _, cid in due]


def record_quiz(path, filename, score, total, now=None):
    """记一次每课自检的成绩（挂在课程记录上）。"""
    key = lesson_key(filename)
    if not key:
        return ""
    data = load(path)
    e = data["lessons"].get(key) or {"first_at": "", "reads": 0}
    e["quiz"] = {"score": int(score), "total": int(total), "at": _stamp(now)}
    if not e.get("first_at"):
        e["first_at"] = _stamp(now)
    data["lessons"][key] = e
    save(path, data)
    return key


def get_steps(path, key):
    """某一课的分步导学完成情况：{step_id: true}。"""
    data = load(path)
    e = data["lessons"].get(str(key)) or {}
    return dict(e.get("guide") or {})


def record_step(path, key, step_id, passed=True, now=None):
    """记一步导学完成。全部步骤完成时自动把这课标记为学完。"""
    key = str(key or "").strip()
    if not key:
        return {}
    data = load(path)
    e = data["lessons"].get(key) or {"first_at": "", "reads": 0}
    guide = dict(e.get("guide") or {})
    guide[str(step_id)] = bool(passed)
    e["guide"] = guide
    if not e.get("first_at"):
        e["first_at"] = _stamp(now)
    data["lessons"][key] = e
    save(path, data)
    return guide


def review_cases(path, today=None):
    """错题本：所有「还没掌握」的题 —— 今天该复习的 + 一次都没通过的。

    返回 [{"id", "reason": "due"|"wrong", "next_review"}]，due 排前面。
    """
    today = _today(today)
    data = load(path)
    out = []
    for cid, e in data["cases"].items():
        e = e or {}
        nr = str(e.get("next_review") or "")
        reason = ""
        if nr:
            try:
                if date.fromisoformat(nr[:10]) <= today:
                    reason = "due"
            except ValueError:
                pass
        if not reason and not e.get("pass_at") and int(e.get("fails") or 0) > 0:
            reason = "wrong"
        if reason:
            out.append({"id": cid, "reason": reason, "next_review": nr})
    out.sort(key=lambda x: (0 if x["reason"] == "due" else 1, x["next_review"]))
    return out


def catalog(tutorials_dir, voice_file=None):
    """扫教程目录，给出有序课程表（Python 线在前，C 线在后）。"""
    titles = {}
    if voice_file and os.path.exists(voice_file):
        try:
            with open(voice_file, "r", encoding="utf-8") as f:
                vdata = json.load(f)
            for les in vdata.get("lessons") or []:
                k = str(les.get("key") or "")
                if k and les.get("title"):
                    titles[lesson_key(k + "_x") or k] = les["title"]
        except Exception:
            titles = {}

    out = []
    if not os.path.isdir(tutorials_dir):
        return out
    for name in sorted(os.listdir(tutorials_dir)):
        if not name.lower().endswith((".py", ".c")):
            continue
        key = lesson_key(name)
        if not key:
            continue
        is_c = key.startswith("C")
        num = int(key[1:] if is_c else key)
        out.append({
            "key": key,
            "file": name,
            "title": titles.get(key) or lesson_title(name),
            "line": "C" if is_c else "PY",
            "num": num,
            "_sort": (1 if is_c else 0, num),
        })
    out.sort(key=lambda x: x["_sort"])
    for item in out:
        item.pop("_sort", None)
    # 每条线各自的总数，用来显示「第 6/12 课」
    total = {}
    for item in out:
        total[item["line"]] = total.get(item["line"], 0) + 1
    seen = {}
    for item in out:
        seen[item["line"]] = seen.get(item["line"], 0) + 1
        item["index"] = seen[item["line"]]
        item["total"] = total[item["line"]]
    return out


def summary(path, catalog_list, cases, today=None):
    """给靶场顶部那条状态栏用的汇总。"""
    today = _today(today)
    data = load(path)
    lessons = data["lessons"]
    cases_state = data["cases"]

    current = None
    for item in catalog_list:
        state = lessons.get(item["key"]) or {}
        if state.get("last_at") and not state.get("done"):
            current = dict(item, done=False, reads=int(state.get("reads") or 0))
    if current is None:
        # 没有「读到一半」的课：找第一课还没标记学完的
        for item in catalog_list:
            state = lessons.get(item["key"]) or {}
            if not state.get("done") and not state.get("last_at"):
                current = dict(item, done=False, reads=0)
                break
    if current is None and catalog_list:
        current = dict(catalog_list[-1], done=True, reads=0)

    nxt = None
    if current is not None:
        idx = next((i for i, it in enumerate(catalog_list) if it["key"] == current["key"]), -1)
        if 0 <= idx < len(catalog_list) - 1:
            nxt = catalog_list[idx + 1]

    meta = {str(c.get("id")): c for c in (cases or [])}
    review = []
    for item in review_cases(path, today):
        c = meta.get(item["id"]) or {}
        st = cases_state.get(item["id"]) or {}
        review.append({
            "id": item["id"],
            "reason": item["reason"],
            "next_review": item["next_review"],
            "title": c.get("title") or item["id"],
            "topic": c.get("topic") or "",
            "level": c.get("level") or 0,
            "lang": c.get("lang") or "",
            "attempts": int(st.get("attempts") or 0),
            "fails": int(st.get("fails") or 0),
        })
    due = []
    for cid in due_reviews(path, today):
        c = meta.get(cid) or {}
        st = cases_state.get(cid) or {}
        due.append({
            "id": cid,
            "title": c.get("title") or cid,
            "topic": c.get("topic") or "",
            "level": c.get("level") or 0,
            "lang": c.get("lang") or "",
            "attempts": int(st.get("attempts") or 0),
            "fails": int(st.get("fails") or 0),
        })

    done_keys = {k for k, v in lessons.items() if (v or {}).get("done")}
    passed = {k for k, v in cases_state.items() if (v or {}).get("pass_at")}
    return {
        "today": today.isoformat(),
        "current": current,
        "next": nxt,
        "due": due,
        "review": review,
        "stats": {
            "lessons_read": len([k for k, v in lessons.items() if (v or {}).get("reads")]),
            "lessons_done": len(done_keys),
            "lessons_total": len(catalog_list),
            "cases_passed": len(passed),
            "cases_total": len(meta),
            "review": len(review),
        },
    }
