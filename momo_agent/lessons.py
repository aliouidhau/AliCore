"""分步导学：把一课拆成「能一步步完成」的小步，每步都有验收。

为什么要有这个东西：
    学生打开一课，看到 130 行注释 + 一个完整程序，只能"读完"，不能"学会"。
    学会需要的是：做一件事 → 立刻知道对不对 → 卡住有台阶 → 做完有下一个动作。

一课的固定结构（6 步）：
    s1 跑通     运行一段最小代码，看它输出什么
    s2 预测     先想输出，再对照（读代码，不是跑代码）
    s3 改一点   改一个数/一行，让输出变成指定值（最小动手）
    s4 补一个   给骨架，填一个函数体（有断言判逻辑）
    s5 写一个   从零写一个小函数（有断言判逻辑）
    s6 说清楚   3 个概念题（关键词判，零 token）

每一步都有 hints 列表（按顺序点开：思路 → 骨架 → 答案）。这不是偷懒，
这是「最近发展区」：学生跳一下够不到时，得有个梯子。
"""

import json
import os
import re
import subprocess
import tempfile
import time

STEP_KINDS = ("run", "predict", "edit", "code", "quiz")


def load(path):
    """读导学数据；坏了就当空，不能让靶场打不开。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"lessons": {}}
    except Exception:
        return {"lessons": {}}


def lesson_list(data):
    return sorted((data.get("lessons") or {}).keys())


def get(data, key):
    """取一课的「可下发版本」：隐藏答案（参考解、断言、预测答案、关键词）。"""
    les = (data.get("lessons") or {}).get(str(key))
    if not les:
        return None
    out = {
        "key": str(key),
        "title": les.get("title") or str(key),
        "goal": les.get("goal") or "",
        "steps": [],
    }
    for i, s in enumerate(les.get("steps") or []):
        item = {
            "id": s.get("id") or ("s%d" % (i + 1)),
            "kind": s.get("kind") or "run",
            "title": s.get("title") or "",
            "instruction": s.get("instruction") or "",
            "hints": list(s.get("hints") or []),
            "starter": s.get("starter") or "",
            "code": s.get("code") or "",
            "lang": s.get("lang") or "c",
        }
        if item["kind"] == "quiz":
            item["questions"] = [
                {"id": q.get("id"), "ask": q.get("ask")}
                for q in (s.get("questions") or [])
            ]
        out["steps"].append(item)
    return out


def _norm_line(s):
    s = str(s or "").replace("\u3000", " ").rstrip()
    return re.sub(r"[ \t]+$", "", s)


def _norm_text(s):
    s = str(s or "").replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", s)


def _stdout_lines(stdout):
    return [_norm_line(x) for x in str(stdout or "").splitlines() if x.strip()]


def judge(step, payload, run_c, run_py=None):
    """判一步。返回 {passed, detail, msg}。

    payload 由前端给：改代码的题传 code，预测/问答题传 text 或 answers。
    run_any(code, test) / run_py(code, test) 由桌面端注入（真的编译/解释运行）。
    步骤用 step["lang"] 选通道：python 课走解释器，其余走 tcc。
    """
    kind = step.get("kind")
    check = step.get("check") or {}
    payload = payload or {}
    is_py = str(step.get("lang") or "c").lower() == "python"

    def run_any(code, test=None):
        if is_py and run_py is not None:
            return run_py(code, test)
        return run_c(code, test)

    if kind in ("run", "edit", "code"):
        code = str(payload.get("code") or "")
        if not code.strip():
            return {"passed": False, "detail": "", "msg": "先写点代码。"}
        if kind == "code" or check.get("kind") == "c_function":
            test = str(check.get("test") or "")
            if not test:
                return {"passed": False, "detail": "", "msg": "这道小题没配验收规则。"}
            res = run_any(code, test)
            if not res.get("ok"):
                return {"passed": False, "detail": res.get("stderr") or "",
                        "msg": res.get("msg") or "运行失败"}
            passed = bool(res.get("passed"))
            detail = (res.get("stdout") or "") + (res.get("stderr") or "")
            if passed:
                return {"passed": True, "detail": detail,
                        "msg": "通过。这一步的行为正是题目要的。"}
            return {"passed": False, "detail": detail,
                    "msg": "还没对上。先看输出/报错，再对照题目要求。"}
        # run / edit：比输出
        res = run_any(code, None)
        if not res.get("ok"):
            return {"passed": False, "detail": res.get("stderr") or "",
                    "msg": res.get("msg") or "运行失败"}
        lines = _stdout_lines(res.get("stdout"))
        if check.get("type") == "stdout_exact":
            expect = [_norm_line(x) for x in str(check.get("expect") or "").splitlines()
                      if x.strip()]
            passed = lines == expect
            detail = "实际输出：\n" + "\n".join(lines)
            return {"passed": passed, "detail": detail,
                    "msg": "输出正是要求的。" if passed else
                           "输出和要求的还不一样，逐行比一下。"}
        needle = str(check.get("value") or "")
        passed = needle in str(res.get("stdout") or "")
        detail = "实际输出：\n" + "\n".join(lines)
        return {"passed": passed, "detail": detail,
                "msg": "跑通了，输出里有这句。" if passed else
                       "输出里还找不到「%s」，检查 printf 的写法。" % needle}

    if kind == "predict":
        expect = [_norm_line(x) for x in (check.get("expect") or [])]
        got = [_norm_line(x) for x in str(payload.get("text") or "").splitlines() if x.strip()]
        passed = got == expect
        detail = ""
        if not passed:
            detail = "实际输出：\n" + "\n".join(expect)
        return {"passed": passed, "detail": detail,
                "msg": "预测对了，说明你读懂了这几行。" if passed else
                       "不一致。看看哪一行的计算和你想的不一样。"}

    if kind == "quiz":
        answers = payload.get("answers") or {}
        results = []
        ok = True
        for q in step.get("questions") or []:
            qid = str(q.get("id"))
            got = _norm_text(answers.get(qid) or "").lower()
            keys = [str(k).lower() for k in (q.get("expect") or [])]
            # 同一个答案允许几种说法："分号|;"
            hit = bool(got) and all(
                any(alt in got for alt in k.split("|")) for k in keys)
            ok = ok and hit
            results.append({"id": qid, "ok": hit, "hint": q.get("hint") or ""})
        detail = "\n".join(("✅" if r["ok"] else "❌") + " " + r["id"] +
                           ("" if r["ok"] else "  " + r["hint"]) for r in results)
        return {"passed": ok, "detail": detail,
                "msg": "这一课的概念你记住了。" if ok else "有几问答得不对，看看提示。"}

    return {"passed": False, "detail": "", "msg": "未知的步骤类型：%s" % kind}


# ---------------- 运行代码（桌面端会传 tcc / python 路径） ----------------

def run_c(code, tcc, workdir, test=None, timeout=15, env=None):
    """跑一段 C。给了 test 就用隐藏断言 harness（防止改输出蒙过去）。"""
    started = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="ali_lesson_") as tmp:
            src = os.path.join(tmp, "student.c")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write(str(code or ""))
            if test:
                harness = (
                    "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
                    "#define main student_main\n"
                    '#include "student.c"\n'
                    "#undef main\n"
                    "int main(void) {\n    setbuf(stdout, NULL);\n"
                    + "".join("    " + ln + "\n" for ln in str(test).splitlines())
                    + "    return 0;\n}\n"
                )
                target = os.path.join(tmp, "check.c")
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    f.write(harness)
            else:
                target = src
            proc = subprocess.run([tcc, "-run", target], cwd=tmp,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, env=env)
            passed = proc.returncode == 0
            return {"ok": True, "passed": passed,
                    "stdout": (proc.stdout or "")[:8000],
                    "stderr": (proc.stderr or "")[:4000],
                    "seconds": round(time.time() - started, 2), "msg": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "运行超过 %d 秒（是不是死循环？）" % timeout,
                "stderr": "", "stdout": ""}
    except Exception as e:
        return {"ok": False, "msg": "运行失败：%s" % e, "stderr": "", "stdout": ""}


def run_py(code, python_exe, workdir, test=None, timeout=15, env=None):
    started = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="ali_lesson_") as tmp:
            src = os.path.join(tmp, "student.py")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write(str(code or ""))
            if test:
                wrapper = (
                    "import runpy, sys\n"
                    "ns = runpy.run_path(%r, run_name='__main__')\n"
                    "globals().update(ns)\n" % src
                )
                target = os.path.join(tmp, "check.py")
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    f.write(wrapper + str(test) + "\n")
            else:
                target = src
            proc = subprocess.run([python_exe, "-u", target], cwd=tmp,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, env=env)
            return {"ok": True, "passed": proc.returncode == 0,
                    "stdout": (proc.stdout or "")[:8000],
                    "stderr": (proc.stderr or "")[:4000],
                    "seconds": round(time.time() - started, 2), "msg": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "运行超过 %d 秒（是不是死循环？）" % timeout,
                "stderr": "", "stdout": ""}
    except Exception as e:
        return {"ok": False, "msg": "运行失败：%s" % e, "stderr": "", "stdout": ""}
