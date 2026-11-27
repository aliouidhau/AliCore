"""apply_patch：一次落地多处改动的统一 diff。

edit_file 一次只能改一段，重构一个文件要来回十几轮；apply_patch 让模型把
所有 hunk 一次性提交，逐个精确匹配。不做模糊匹配：上下文对不上就报是哪一段
没对上，让它 read_file 重看，而不是猜着改进错误的行。

只改已存在的文件。新增文件用 write_file，删除用 delete_file——diff 里的
/dev/null 一律拒绝，避免把「新建」写成空文件覆盖。
"""

import os
import re

from .tools_fs import _resolve, _in_whitelist, _dangerous, write_file

_HUNK_RE = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@")


def _clean_path(raw):
    p = (raw or "").strip()
    if not p or p in ("/dev/null", "dev/null"):
        return ""
    # diff 常见的 a/ b/ 前缀；带引号或带时间戳的尾巴也去掉
    if p.startswith(("a/", "b/")):
        p = p[2:]
    if "\t" in p:
        p = p.split("\t")[0]
    return p.strip().strip('"')


def _parse(patch):
    """把统一 diff 文本拆成 [{path, hunks:[{old:[...], new:[...]}]}]。

    old = 上下文行 + 删除行，new = 上下文行 + 新增行。行首一个字符定类型：
    空格=上下文、-=删、+=加、空行=上下文（有些工具会把纯空行写成裸空行）。
    """
    lines = patch.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    files = []
    cur = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                return [], "--- 后面没有跟 +++，diff 不完整"
            path = _clean_path(lines[i + 1][4:])
            if not path:
                return [], "这个 diff 指向 /dev/null（新建或删除文件）。新建请用 write_file"
            cur = {"path": path, "hunks": []}
            files.append(cur)
            i += 2
            continue
        m = _HUNK_RE.match(ln)
        if m:
            if cur is None:
                return [], "遇到 @@ 但前面没有 ---/+++ 文件头"
            i += 1
            old, new, saw = [], [], False
            while i < len(lines):
                body = lines[i]
                if _HUNK_RE.match(body) or body.startswith("--- "):
                    break
                if body.startswith("+"):
                    new.append(body[1:])
                elif body.startswith("-"):
                    old.append(body[1:])
                elif body.startswith(" "):
                    b = body[1:]
                    old.append(b)
                    new.append(b)
                elif body.strip() == "":
                    old.append("")
                    new.append("")
                else:
                    # 不认识的杂行（如 "\ No newline at end of file"）忽略
                    if body.startswith("\\"):
                        i += 1
                        continue
                    break
                saw = True
                i += 1
            if not saw:
                return [], "有 @@ 段是空的"
            cur["hunks"].append({"old": old, "new": new})
            continue
        i += 1
    if not files:
        return [], "没解析出任何文件段。格式：--- a/x.py / +++ b/x.py / @@ -1,3 +1,4 @@"
    return files, None


def _find_block(lines, old, pos):
    """从 pos 起找 old 的连续匹配；返回 (起点, 命中次数)。"""
    hits = []
    n = len(lines) - len(old) + 1
    for s in range(max(0, pos), max(0, n)):
        if lines[s:s + len(old)] == old:
            hits.append(s)
            if len(hits) > 1:
                break
    if not hits:
        return -1, 0
    return hits[0], len(hits)


def _apply_hunks(content, hunks):
    """按顺序应用 hunk。返回 (新全文, 错误说明)。保持原文件的 CRLF/LF 风格。"""
    crlf = "\r\n" in content
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pos = 0
    added = removed = 0
    for idx, h in enumerate(hunks, 1):
        old, new = h["old"], h["new"]
        if not old:
            return None, f"第 {idx} 段只有新增行、没有上下文，定不了位置。至少留一行没改的原文"
        start, count = _find_block(lines, old, pos)
        if start < 0:
            preview = "\n".join(old[:8])
            tail = "\n...(还有更多)" if len(old) > 8 else ""
            return None, (f"第 {idx} 段没匹配上。这段原文应该是：\n{preview}{tail}\n"
                          "检查空格/缩进/换行是否完全一致；不确定就先 read_file 看实际内容")
        if count > 1:
            preview = "\n".join(old[:6])
            return None, (f"第 {idx} 段匹配到不止一处，不唯一。多留几行上下文再提交：\n{preview}")
        lines[start:start + len(old)] = new
        pos = start + len(new)
    text = "\n".join(lines)
    if crlf:
        text = text.replace("\n", "\r\n")
    return text, None


def _stat(hunks):
    """粗略统计 +/- 行数，只用于回执，不影响落地。"""
    plus = minus = 0
    for h in hunks:
        for ln in h["old"]:
            if ln not in h["new"]:
                minus += 1
        for ln in h["new"]:
            if ln not in h["old"]:
                plus += 1
    return plus, minus


def apply_patch(patch: str, dry_run: bool = False):
    """一次改多处/多文件。提交统一 diff，逐段精确匹配；对不上就整体不写。

    dry_run=True 只校验和统计，不落盘。适合先看看能不能打上。
    """
    if not patch or not patch.strip():
        return "❌ patch 为空"
    files, err = _parse(patch)
    if err:
        return "❌ " + err

    report = []
    pending = []
    failed = False
    for f in files:
        path = f["path"]
        full = _resolve(path)
        if not _in_whitelist(full):
            report.append(f"❌ {path}：不在允许目录内")
            failed = True
            continue
        hit = _dangerous(full)
        if hit:
            report.append(f"❌ {path}：路径含危险关键词 {hit}")
            failed = True
            continue
        if not os.path.exists(full):
            report.append(f"❌ {path}：文件不存在（新建请用 write_file）")
            failed = True
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            report.append(f"❌ {path}：读取失败 {e}")
            failed = True
            continue

        text, why = _apply_hunks(content, f["hunks"])
        if why:
            report.append(f"❌ {path}：{why}")
            failed = True
            continue
        if full.lower().endswith(".py"):
            try:
                compile(text, full, "exec")
            except SyntaxError as e:
                report.append(f"❌ {path}：改完语法不对（第 {e.lineno} 行 {e.msg}），没有写入")
                failed = True
                continue

        plus, minus = _stat(f["hunks"])
        pending.append((full, text, path, plus, minus))

    # 两阶段：全部校验通过才动笔。一个文件都不能落下，避免「改了一半」的中间态。
    if failed:
        return ("⚠️ 校验没通过，整批没有写入（只要有一段对不上，任何一个文件都不动）\n"
                + "\n".join(report))
    if dry_run:
        for _, _, path, plus, minus in pending:
            report.append(f"✅ {path}：可以打上（+{plus} / -{minus}），dry_run 未写入")
        return "🔍 dry_run 校验完成，全部能打上\n" + "\n".join(report)

    for full, text, path, plus, minus in pending:
        msg = write_file(full, text)
        if msg.startswith("✅"):
            report.append(f"✅ {path}：+{plus} / -{minus} 已写入")
        else:
            report.append(f"❌ {path}：{msg}")
    return "✅ patch 全部落地\n" + "\n".join(report)


def register_patch_tool(registry):
    registry.register(
        "apply_patch",
        "一次改多处/多文件：提交统一 diff（--- a/x / +++ b/x / @@ … @@；行首空格=上下文，-=删，+=加）",
        apply_patch,
        json_visible=True,
    )
    return registry
