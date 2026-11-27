"""每课自检：预测输出。

为什么是"预测输出"而不是手写题库：
- 36 课 × 3 题 = 108 道，手写既慢，教程一改就全过时；
- 预测输出测的正是"读懂了没有"——不读代码就只能瞎猜；
- 答案来自本地真实运行，不存在"答案写错"这种低级错误。

零 token：不调模型。零侵入：不碰教程正文（语音绑行号）。
"""

import re
import subprocess
import time

MAX_LINES = 3  # 题量上限：第一行、最后一行、总行数


def run(cmd, cwd=None, timeout=12, env=None):
    """跑一次代码，拿真实输出。返回 {ok, stdout, stderr, code, seconds, msg}。"""
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "", "code": -1,
                "seconds": round(time.time() - started, 2),
                "msg": f"这课运行超过 {timeout} 秒（可能需要输入或本身很慢），不适合做自检。"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "code": -1,
                "seconds": round(time.time() - started, 2), "msg": f"运行失败：{e}"}
    return {"ok": True, "stdout": (proc.stdout or "")[:20000],
            "stderr": (proc.stderr or "")[:4000], "code": proc.returncode,
            "seconds": round(time.time() - started, 2), "msg": ""}


def usable(res):
    """这次运行能不能拿来出题：要正常退出、要有输出。

    自己就跑挂的课（需要 input()、本来就报错）不适合"预测输出"——
    学生预测一个崩溃程序的输出没有意义。
    """
    if not res.get("ok"):
        return False, res.get("msg") or "运行失败"
    if res.get("code") != 0:
        err = (res.get("stderr") or "").strip().splitlines()
        tail = err[-1][:80] if err else f"退出码 {res.get('code')}"
        if "EOFError" in (res.get("stderr") or ""):
            return False, "这一课需要键盘输入，自检做不了；直接在练习场运行，按提示输入。"
        return False, f"这一课自己运行就报错了（{tail}），先修好再做自检。"
    return True, ""


def lines_of(text):
    """非空输出行（保留行内容，去掉行尾空白）。"""
    return [ln.rstrip() for ln in str(text or "").splitlines() if ln.strip()]


def build_questions(stdout):
    """从真实输出生成 1~3 道题。答案只留在后端，不下发前端。"""
    lines = lines_of(stdout)
    qs = []
    if not lines:
        return qs
    qs.append({"id": "q1", "kind": "text",
               "ask": "这课运行后，第一行输出是什么？", "answer": lines[0]})
    if len(lines) >= 2:
        qs.append({"id": "q2", "kind": "text",
                   "ask": "最后一行输出是什么？", "answer": lines[-1]})
    if len(lines) >= 3:
        qs.append({"id": "q3", "kind": "int",
                   "ask": "这课一共输出多少行？（填数字）", "answer": str(len(lines))})
    return qs[:MAX_LINES]


def public(questions):
    """给前端的题目：去掉答案。"""
    return [{"id": q["id"], "ask": q["ask"], "kind": q["kind"]} for q in questions]


_PUNCT = "。.,!?;:、\"'“”‘’「」『』()（）[]【】<>《》 "


def _norm(s):
    s = str(s or "").replace("\u3000", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip(_PUNCT).lower()


def judge(q, user):
    """判一道题。文字题宽容标点/空白，整数题只认数字。"""
    expected = _norm(q.get("answer"))
    got = _norm(user)
    if not got:
        return False
    if q.get("kind") == "int":
        m = re.search(r"-?\d+", got)
        try:
            return bool(m) and int(m.group(0)) == int(expected)
        except ValueError:
            return False
    if got == expected:
        return True
    # 允许"输出是：xxx"这种包一层话的写法
    return len(expected) >= 2 and len(got) >= 2 and expected in got


def check(stdout, answers):
    """对照答案。返回逐题结果 + 得分。"""
    qs = build_questions(stdout)
    results = []
    for q in qs:
        user = (answers or {}).get(q["id"], "")
        results.append({
            "id": q["id"],
            "ask": q["ask"],
            "kind": q["kind"],
            "user": str(user or ""),
            "expected": q["answer"],
            "correct": judge(q, user),
        })
    score = sum(1 for r in results if r["correct"])
    return {"results": results, "score": score, "total": len(results)}
