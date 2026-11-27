"""文件工具：读 / 列目录 / 写。

保留原有的三条安全设计（这是它更严谨的地方，重构不能丢）：
  1. 目录白名单——只能在指定目录里动
  2. 危险路径过滤——系统目录、可执行文件一律拒绝
  3. 写前自动备份——原文件先复制到 <data>\\_backup，备份失败就不写
另外保留「核心文件保护」：ali_app.py / ui_v2.html / config.json / chat_history.json 按名锁死；
alicore_agent 整包、_selftest 下的 .py、_heartbeat.py、requirements.txt 是基站代码，模型只读不写。
"""

import ast
import fnmatch
import os
import shutil
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = _HERE

# 默认值只是占位：真正的可写边界由 configure_paths() 按启动时的 app/data 目录重设。
ALLOWED_DIRS = [_HERE]

BACKUP_DIR = os.path.join(_HERE, "_backup")

# 删除一律走这里，不真删。文件躺在这儿随时能捞回来。
TRASH_DIR = os.path.join(BACKUP_DIR, "_trash")

# 明确拒绝的系统区：这些地方动一下就可能把机器搞坏，放多宽都不放开
DENY_DIRS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
    r"C:\Boot",
]


def _user_appdata_dirs():
    """当前账户的 AppData——不能写死用户名。

    密钥在 %APPDATA%\\AliCore\\secrets.json，靠这条 DENY 拦住 read_file。
    原来硬编码某个账户的 AppData，换个账户名（改用户名、重装、换机器）
    整条防线就失效了，密钥直接摆在 agent 嘴边。
    """
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        v = os.environ.get(env)
        if v:
            out.append(os.path.normpath(v))
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home:
        out.append(os.path.join(home, "AppData"))

    seen, uniq = set(), []
    for d in out:
        k = d.lower().rstrip("\\")
        if k and k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


DENY_DIRS += _user_appdata_dirs()

# 目录类：路径片段里出现这些就拒（子串匹配是对的，要的就是"在任何层级出现"）
DANGEROUS_DIRS = ("\\windows\\", "\\system32\\", "\\program files", "\\appdata\\")

# 后缀类：只对文件名后缀判断，**不做子串匹配**。
# 原来把 ".sys" 放进子串匹配里，D:\x\.system 这种目录会被误拒——那是误伤，不是漏洞。
DANGEROUS_EXTS = (".sys", ".dll", ".exe", ".bat", ".cmd", ".ps1")

CORE_FILES = ["ali_app.py", "ui_v2.html", "config.json", "chat_history.json"]

# 基站自己的代码：整包 + 自检目录下的 .py + 散落入口文件。她可以读（读自己的代码学习），但不许写。
# 2026-11-21 的实测教训：核心文件原来只锁 4 个文件名，她 2026-11-20 22:10 自己改了
# prompts.py 给自己加规矩——那次是好事；但同一条路也能删规矩，甚至改 tools_fs.py 拆自己的锁。
# alicore_agent 整包都锁；_selftest 只锁 .py（验证脚本改坏/篡改会毁掉自检），
# 测试的临时文件（_tmp_tools 之类）读写不拦。
AGENT_CODE_DIRS = [
    os.path.normcase(os.path.join(ALLOWED_DIRS[0], "alicore_agent")),
]
_SELFTEST_DIR = os.path.normcase(os.path.join(ALLOWED_DIRS[0], "_selftest"))

# 不在上面两个目录里、但同样是基站运行文件的，逐个点名。
AGENT_CODE_FILES = [
    os.path.normcase(os.path.join(ALLOWED_DIRS[0], "_heartbeat.py")),
    os.path.normcase(os.path.join(ALLOWED_DIRS[0], "requirements.txt")),
]


def _is_agent_code(full):
    """判断路径是不是基站自己的代码——模型只读不写的那部分。"""
    p = os.path.normcase(os.path.abspath(full))
    for d in AGENT_CODE_DIRS:
        if p == d or p.startswith(d + os.sep):
            return True
    if (p == _SELFTEST_DIR or p.startswith(_SELFTEST_DIR + os.sep)) and p.endswith(".py"):
        return True
    return p in AGENT_CODE_FILES

MAX_READ_BYTES = 500_000

# 长期记忆：早先版本用 knowledge/memory_log.txt，这里简化成“一个 list”。
# 它不塞进 system prompt——塞了每轮都要重发，涨 token；改成模型需要时自己 read_file 查。
MEMORY_PATH = os.path.join(ALLOWED_DIRS[0], "knowledge", "memory.md")
MAX_MEMORY_ITEMS = 200


def configure_paths(data_dir=None, app_dir=None):
    """按启动参数重设数据根和代码根。

    默认不调用，保持旧路径；调用后记忆本跟数据走，基站代码保护仍锁 app 目录。
    """
    global ALLOWED_DIRS, MEMORY_PATH, AGENT_CODE_DIRS, _SELFTEST_DIR, AGENT_CODE_FILES
    global BACKUP_DIR, TRASH_DIR, DATA_ROOT
    data = os.path.abspath(data_dir) if data_dir else os.path.abspath(ALLOWED_DIRS[0])
    app = os.path.abspath(app_dir) if app_dir else os.path.abspath(ALLOWED_DIRS[0])

    dirs = []
    # 本机历史遗留目录：存在才放行；换台机器这些目录不存在，自动跳过
    extras = ()
    for d in (app, data) + extras:
        k = os.path.normcase(os.path.abspath(d))
        if k not in [os.path.normcase(x) for x in dirs]:
            dirs.append(os.path.abspath(d))
    ALLOWED_DIRS = dirs
    MEMORY_PATH = os.path.join(data, "knowledge", "memory.md")
    BACKUP_DIR = os.path.join(data, "_backup")
    TRASH_DIR = os.path.join(BACKUP_DIR, "_trash")
    DATA_ROOT = data
    AGENT_CODE_DIRS = [os.path.normcase(os.path.join(app, "alicore_agent"))]
    _SELFTEST_DIR = os.path.normcase(os.path.join(app, "_selftest"))
    AGENT_CODE_FILES = [
        os.path.normcase(os.path.join(app, "_heartbeat.py")),
        os.path.normcase(os.path.join(app, "requirements.txt")),
    ]
    return {"data_dir": data, "app_dir": app, "memory_path": MEMORY_PATH}


def _resolve(path):
    return os.path.abspath(os.path.normpath(path))


def _in_whitelist(path):
    """放得很宽：除系统区和 AppData，任意盘符都允许。

    原先是 4 个目录的白名单，比现在的范围窄得多。改成反向黑名单后对齐需求
    「除了系统文件和危险文件，其他都能碰」。危险后缀仍由 _dangerous 拦，
    写前仍自动备份，所以放宽的是范围不是安全网。
    """
    full = _resolve(path)
    low = full.lower()
    for d in DENY_DIRS:
        dd = _resolve(d).lower()
        if low == dd or low.startswith(dd + os.sep):
            return False
    drive, _ = os.path.splitdrive(full)
    return bool(drive)


def _dangerous(path):
    low = _resolve(path).lower()
    for p in DANGEROUS_DIRS:
        if p in low:
            return p
    name = os.path.basename(low)
    for ext in DANGEROUS_EXTS:
        if name.endswith(ext):
            return ext
    return None


def _stamp(path):
    """修改时间的短格式，坏了就返回空串，不给列目录添乱。"""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def read_file(path: str, start: int = 0, limit: int = 0):
    """读取白名单目录内的文本文件。

    默认整读，返回纯内容（老行为，模型常要直接 json.loads / 原样处理）。
    给了 start/limit 就只读那一段并带行号——大文件只看局部时用这个，别整本塞进上下文。
    start 从 1 开始计（跟编辑器一致）；limit 为 0 表示读到末尾。
    """
    if not path:
        return "❌ 路径为空"
    full = _resolve(path)
    if not _in_whitelist(full):
        return f"❌ 拒绝读取：{path} 不在允许目录内（{', '.join(ALLOWED_DIRS)}）"
    hit = _dangerous(full)
    if hit:
        return f"❌ 拒绝读取：路径含危险关键词 {hit}"
    if not os.path.exists(full):
        # 模型手写路径常把反斜杠当转义吃掉（D:\demo\_x.txt → D:\demo_x.txt），
        # 这里给一份同目录的近似候选，让它下一步能自己纠
        parent = os.path.dirname(full)
        stem = os.path.basename(full).lower()
        tips = []
        if os.path.isdir(parent):
            for name in os.listdir(parent):
                if stem and (name.lower().startswith(stem[:4]) or stem.startswith(name.lower()[:4])):
                    tips.append(os.path.join(parent, name))
        hint = f"\n可能你想读的是：{tips[:3]}" if tips else ""
        return f"❌ 文件不存在：{path}（路径里的反斜杠建议写成正斜杠，如 D:/demo/x.txt）{hint}"
    if os.path.getsize(full) > MAX_READ_BYTES:
        return f"❌ 文件太大（{os.path.getsize(full)//1024}KB），超过 500KB 上限"
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"❌ 读取失败：{e}"

    if not start and not limit:
        # 老行为：只返回文件内容本身，不加路径前缀——模型常要直接 json.loads() 或拆分处理
        return content

    # 指定了范围：带行号返回。行号让模型能精确说「第 87 行」，也方便下一步做 search-replace。
    lines = content.splitlines()
    total = len(lines)
    s = max(1, int(start or 1))
    n = int(limit or 0)
    if s > total:
        return f"❌ start={s} 超出文件总行数（共 {total} 行）"
    if n <= 0:
        n = total - s + 1
    chunk = lines[s - 1: s - 1 + n]
    body = "\n".join(f"{s + i:>4}| {ln}" for i, ln in enumerate(chunk))
    return f"（{os.path.basename(full)} 第 {s}-{s + len(chunk) - 1} 行 / 共 {total} 行）\n{body}"


# 递归扫目录时统一跳过这些：缓存、虚拟环境、前端依赖，扫它们只会白烧时间
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", "venv", "webview_storage", "学习资料"}

# 内容搜索跳过的二进制/产物后缀
_SKIP_EXT = {".pyc", ".exe", ".dll", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".7z",
             ".mp4", ".wav", ".ico", ".bak", ".db", ".woff", ".woff2", ".ttf"}


def list_files(path: str = "", pattern: str = "", detail: bool = False):
    """列出白名单目录内的文件夹内容。

    给了 pattern 就递归按通配符找（`*.py`、`*test*`），只返回命中的完整路径——
    找文件不用一层层 list。不给 pattern 就是老行为，只列一层。
    detail=True 多带一个修改时间，用来判断「哪个是刚写的」。
    """
    full = _resolve(path or DATA_ROOT)
    if not _in_whitelist(full):
        return f"❌ 拒绝访问：{path} 不在允许目录内"
    if not os.path.isdir(full):
        return f"❌ 目录不存在：{path}"

    if pattern:
        # pattern 里有斜杠就是路径模式（`_tmp_*/*`），按相对路径匹配；
        # 没有斜杠就是文件名模式（`*.py`），只比文件名。
        # 不区分的话，路径写法会永远匹配不上，模型就把「没命中」当成「里面是空的」。
        use_path = ("/" in pattern) or ("\\" in pattern)
        pat = pattern.replace("\\", "/").lower()
        hits, capped = [], False
        for root, dirs, files in os.walk(full):
            if not _in_whitelist(root):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in list(dirs) + files:
                p = os.path.join(root, name)
                if use_path:
                    rel = os.path.relpath(p, full).replace("\\", "/").lower()
                    if not fnmatch.fnmatch(rel, pat):
                        continue
                elif not fnmatch.fnmatch(name.lower(), pattern.lower()):
                    continue
                if os.path.isdir(p):
                    # 目录也参与匹配，否则「找 _tmp_*」只能找到文件，同名目录被静默漏掉
                    line = f"{p}/  (目录)"
                else:
                    try:
                        size = os.path.getsize(p)
                        size_s = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
                    except Exception:
                        size_s = ""
                    line = f"{p} {size_s}".strip()
                if detail:
                    st = _stamp(p)
                    line += f"  {st}" if st else ""
                hits.append(line)
                if len(hits) >= 50:
                    capped = True
                    break
            if capped:
                break
        head = (f"{full} 按 pattern={pattern} 找到 {len(hits)} 个"
                + ("（已到 50 上限，收窄一下 pattern）" if capped else ""))
        if hits:
            return head + "\n" + "\n".join(hits)
        return (head + "\n（**列目录本身执行成功了，不是工具坏了。**这里确实没有名字匹配 pattern 的东西。"
                "pattern 只匹配名字、*不跨目录*；想看某个子目录里面，直接把它当 path 传进来。"
                "别据此判断这个目录是空的。）")

    try:
        items = []
        for name in sorted(os.listdir(full)):
            child = os.path.join(full, name)
            if os.path.isdir(child):
                line = f"{name}/"
            else:
                size = os.path.getsize(child)
                line = f"{name} {size//1024}KB" if size >= 1024 else f"{name} {size}B"
            if detail:
                st = _stamp(child)
                if st:
                    line += f"  {st}"
            items.append(line)
        total = len(items)
        # 一次列太多会被下游 1500 字符的硬上限拦腰截断，截掉的偏偏是末尾那几条——
        # 模型就会以为「就这些」，然后漏掉东西。按字符预算裁，把话说完整。
        budget = 1200          # 留 300 字符给头部和尾部的提示
        shown = []
        for line in items:
            budget -= len(line) + 1
            if budget < 0:
                break
            shown.append(line)
        if len(shown) < total:
            return (f"{full}（共 {total} 项，只列前 {len(shown)} 项）\n" + "\n".join(shown)
                    + f"\n…还有 {total - len(shown)} 项没列出。用 pattern 收窄（如 pattern='*.py'）能精确找。")
        return f"{full}（{total} 项）\n" + "\n".join(items)
    except Exception as e:
        return f"❌ 列目录失败：{e}"


def delete_file(path: str):
    """删文件——但不真删：移进 <data>/_backup/_trash，随时能捞回来。

    只处理单个文件，不删目录（rmtree 那种一删一串的，风险不是一个量级）。
    核心文件、系统区、危险后缀一律拒。
    """
    if not path:
        return "❌ 路径为空"
    full = _resolve(path)

    if any(os.path.basename(full) == cf for cf in CORE_FILES):
        return f"❌ 拒绝删除：{os.path.basename(full)} 是核心文件"
    if _is_agent_code(full):
        return f"❌ 拒绝删除：{full} 是基站代码，只读不删"
    if not _in_whitelist(full):
        return f"❌ 拒绝删除：{path} 在系统区或不允许的位置"
    hit = _dangerous(full)
    if hit:
        return f"❌ 拒绝删除：路径含危险关键词 {hit}"
    if os.path.isdir(full):
        return (f"❌ 这是目录，不删目录（一删一串的风险不是一个量级）：{full}"
                f"\n要清空它：先 list_files 把这个路径当 path 传进去，看里面有什么，再逐个 delete_file。")
    if not os.path.exists(full):
        return f"❌ 文件不存在：{full}"

    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(TRASH_DIR, f"{ts}_{os.path.basename(full)}")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(TRASH_DIR, f"{ts}_{n}_{os.path.basename(full)}")
            n += 1
        shutil.move(full, dest)
        size = os.path.getsize(dest)
        kb = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        return f"✅ 已移入回收区：{dest}（{kb}）。没真删，要彻底清掉得去那个目录手动删。"
    except Exception as e:
        return f"❌ 移动失败（文件没动）：{e}"


def move_file(src: str, dst: str):
    """移动或重命名：一个文件/文件夹从一个位置挪到另一个位置。

    三条规矩：两边都过白名单、目标已存在就拒（不覆盖）、核心文件名不接。
    移动不是删除，所以不走回收区——但也没法撤销，所以目标存在时宁可拒掉。
    """
    if not src or not dst:
        return "❌ 源或目标为空"
    s = _resolve(src)
    d = _resolve(dst)

    if not _in_whitelist(s):
        return f"❌ 拒绝移动：源 {src} 在系统区或不允许的位置"
    if not _in_whitelist(d):
        return f"❌ 拒绝移动：目标 {dst} 在系统区或不允许的位置"
    if any(os.path.basename(d) == cf for cf in CORE_FILES):
        return f"❌ 拒绝移动：{os.path.basename(d)} 是核心文件名，不接受"
    if _is_agent_code(s):
        return f"❌ 拒绝移动：源 {s} 是基站代码，动不得"
    if _is_agent_code(d):
        return f"❌ 拒绝移动：目标 {d} 落在基站代码目录里，不接受"
    hit = _dangerous(s) or _dangerous(d)
    if hit:
        return f"❌ 拒绝移动：路径含危险关键词 {hit}"
    if not os.path.exists(s):
        return f"❌ 源不存在：{s}"
    if os.path.exists(d):
        return f"❌ 目标已存在，不覆盖：{d}（换个名字，或者先 delete_file 掉它）"

    try:
        parent = os.path.dirname(d)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(s, d)
        kind = "目录" if os.path.isdir(d) else "文件"
        return f"✅ 已移动{kind}：{s} → {d}"
    except Exception as e:
        return f"❌ 移动失败（原位置没动）：{e}"


def open_path(path: str):
    """打开文件、文件夹或网页——等于你双击它一下。

    复用同一套黑名单，系统区不碰。网页只放行 http/https，
    免得变成一个「任何协议都能触发」的口子。
    """
    if not path:
        return "❌ 路径为空"

    raw = str(path).strip()
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        try:
            os.startfile(raw)
            return f"✅ 已用默认浏览器打开：{raw}"
        except Exception as e:
            return f"❌ 打开失败：{e}"

    full = _resolve(raw)
    if not _in_whitelist(full):
        return f"❌ 拒绝打开：{raw} 在系统区或不允许的位置"
    if not os.path.exists(full):
        return f"❌ 不存在：{full}"
    kind = "文件夹" if os.path.isdir(full) else "文件"
    try:
        os.startfile(full)
        return f"✅ 已打开{kind}：{full}"
    except Exception as e:
        return f"❌ 打开失败：{e}"


def write_file(path: str, content: str, append: bool = False):
    """写入白名单目录内的文件：先备份，再写。

    append=True 改成追加（写日志类内容用），备份规则不变——追加也算改文件。
    """
    if not path or content is None:
        return "❌ 路径或内容为空"
    full = _resolve(path)

    if any(os.path.basename(full) == cf for cf in CORE_FILES):
        return f"❌ 拒绝写入：{os.path.basename(full)} 是核心文件，模型不允许直接改"
    if _is_agent_code(full):
        return f"❌ 拒绝写入：{full} 是基站代码，只读不写（想改让学生甲自己来）"
    if not _in_whitelist(full):
        return f"❌ 拒绝写入：{path} 不在允许目录内"
    hit = _dangerous(full)
    if hit:
        return f"❌ 拒绝写入：路径含危险关键词 {hit}"

    # .py 目标先过语法门禁：在内存里把「写完之后的全文」compile 一遍，语法错不落盘。
    # edit_file 早就有这道（ACI 第 1 条），write_file 一直漏着——她写一段截断的代码
    # 进 prompts.py 这种启动路径文件，当场没事，下次重启基站直接起不来。
    if full.lower().endswith(".py"):
        try:
            merged = content
            if append and os.path.exists(full):
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    merged = f.read() + content
            compile(merged, full, "exec")
        except SyntaxError as e:
            return (f"❌ 写完的代码语法不对，**没有写入，文件保持原样**。\n"
                    f"  第 {e.lineno} 行：{e.msg}\n"
                    f"  出错那行：{(e.text or '').strip()[:120]}")
        except Exception as e:
            return f"❌ 语法检查没通过：{type(e).__name__}: {e}（没有写入）"

    backup_msg = ""
    if os.path.exists(full):
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(BACKUP_DIR, f"{os.path.basename(full)}.{ts}.bak")
            # 同一秒内写两次会重名，后面那次会把前面的备份盖掉——加序号躲开
            n = 1
            while os.path.exists(bak):
                bak = os.path.join(BACKUP_DIR, f"{os.path.basename(full)}.{ts}_{n}.bak")
                n += 1
            shutil.copy2(full, bak)
            backup_msg = f"（已备份到 {bak}）"
        except Exception as e:
            return f"❌ 备份失败，已放弃写入：{e}"

    verb = "追加" if append else "写入"
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ {verb}成功：{full}{backup_msg}"
    except Exception as e:
        return f"❌ {verb}失败：{e}"


def edit_file(path: str, old: str, new: str):
    """按原文块替换：只改 old 那一段，不用重写整个文件。

    这是改大文件最省的做法——改 300 行文件里的 5 行，输出从几千 token 降到几十。
    找不到 / 不唯一都直接报错让它补上下文重来，不做模糊匹配：
    那正是 aider 花 20KB 处理的事，基站场景简单，宁可让它重试一次。

    复用 write_file 的白名单、核心文件保护和自动备份，安全规则不重写一遍。
    """
    if not path or not old:
        return "❌ 路径或 old 为空"
    full = _resolve(path)
    if not _in_whitelist(full):
        return f"❌ 拒绝编辑：{path} 不在允许目录内"
    hit = _dangerous(full)
    if hit:
        return f"❌ 拒绝编辑：路径含危险关键词 {hit}"
    if not os.path.exists(full):
        return f"❌ 文件不存在：{path}"

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"❌ 读取失败：{e}"

    count = content.count(old)
    if count == 0:
        return ("❌ 没找到 old 那段原文。检查空格、缩进、换行是否完全一致，"
                "或者先 read_file 看一下实际内容（建议带上 start/limit 看局部）。")
    if count > 1:
        return f"❌ old 在文件里出现了 {count} 次，不唯一。给一段更长的、只出现一次的原文。"

    new_content = content.replace(old, new, 1)

    # ACI 第 1 条（SWE-agent）：编辑前跑 linter，语法不对就不让落地。
    # 只在 .py 上做——json/html 不是 Python 语法，compile 会全错。
    # 本地 CPU，零 token；回报是「改坏了」根本写不进盘，不用等备份回滚。
    if full.lower().endswith(".py"):
        try:
            compile(new_content, full, "exec")
        except SyntaxError as e:
            return ("❌ 改完语法不对，**没有写入，文件保持原样**。\n"
                    f"  第 {e.lineno} 行：{e.msg}\n"
                    f"  出错那行：{(e.text or '').strip()[:120]}\n"
                    "把 old/new 修好再提交一次。")
        except Exception as e:
            return f"❌ 语法检查没通过：{type(e).__name__}: {e}（没有写入）"

    return write_file(full, new_content)


def search_text(keyword: str, path: str = "", max_hits: int = 30):
    """在白名单目录里搜文件内容，返回 `文件:行号: 那一行`。

    用来回答「哪个文件里写了 XXX」。只搜文本，跳过二进制、产物和超大文件。
    大小写不敏感——用户不会记得自己在代码里写的是 Arg 还是 arg。
    """
    if not keyword:
        return "❌ keyword 为空"
    full = _resolve(path or DATA_ROOT)
    if not _in_whitelist(full):
        return f"❌ 拒绝搜索：{path} 不在允许目录内"
    if not os.path.exists(full):
        return f"❌ 路径不存在：{path}"

    kw = keyword.lower()
    hits, scanned = [], 0
    # os.walk 只遍历目录：传单个文件时它直接返回空，循环一次都不进，
    # 结果就是「扫了 0 个文件」——她搜 DENY_DIRS 时撞过这个。
    # 单文件就手工造一个 (目录, [], [文件名])，让下面同一套逻辑复用。
    if os.path.isfile(full):
        roots = [(os.path.dirname(full), [], [os.path.basename(full)])]
    else:
        roots = os.walk(full)
    for root, dirs, files in roots:
        if not _in_whitelist(root):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if os.path.splitext(name)[1].lower() in _SKIP_EXT:
                continue
            p = os.path.join(root, name)
            try:
                if os.path.getsize(p) > MAX_READ_BYTES:
                    continue
            except Exception:
                continue
            scanned += 1
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for ln, line in enumerate(f, 1):
                        if kw in line.lower():
                            hits.append(f"{p}:{ln}: {line.strip()[:160]}")
                            if len(hits) >= max_hits:
                                break
            except Exception:
                continue
            if len(hits) >= max_hits:
                break
        if len(hits) >= max_hits:
            break

    head = (f"搜「{keyword}」命中 {len(hits)} 处（扫了 {scanned} 个文件）"
            + ("，已到上限，可能有更多" if len(hits) >= max_hits else ""))
    if hits:
        return head + "\n" + "\n".join(hits)
    # ACI 第 4 条（SWE-agent）：空结果必须明说「是执行成功、不是工具坏了」。
    # 只写「没有命中」时，模型会把它读成「这里什么都没有」——实测踩过（把 pattern 没匹配
    # 当成目录是空的，报了「都清完了」，实际里面还有 3 个文件）。
    return (head + "\n（**搜索本身执行成功了，不是工具坏了。**这个范围里确实没有一行含这个词。"
            "要么换个关键词，要么确认 path 是不是扫对了地方——别据此判断这里什么都没有。）")


def code_map(path: str = "", max_files: int = 20):
    """扫 .py 文件，输出「文件 → 类/函数 + 行号」的紧凑结构图。

    用标准库 ast，不依赖 tree-sitter——aider 的 repomap 是 27KB + 多语言 grammar，
    基站只改自己的 Python，ast 能拿到 80% 的价值，还是零依赖。
    目的是：模型不用整读 400 行文件，就知道里面有什么、该从第几行动手。
    """
    full = _resolve(path or DATA_ROOT)
    if not _in_whitelist(full):
        return f"❌ 拒绝访问：{path} 不在允许目录内"
    if not os.path.exists(full):
        return f"❌ 路径不存在：{path}"

    if os.path.isfile(full):
        targets = [full]
    else:
        targets = []
        for root, dirs, files in os.walk(full):
            if not _in_whitelist(root):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if name.endswith(".py") and not name.endswith(".pyc"):
                    targets.append(os.path.join(root, name))
            if len(targets) >= max_files:
                break

    if not targets:
        return (f"（扫描执行成功了，不是工具坏了。）{full} 这一层没有 .py 文件。"
                f"如果它在子目录里，把 path 指到那个子目录。")

    base = full if os.path.isdir(full) else os.path.dirname(full)
    out = []
    for p in targets[:max_files]:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            total = len(src.splitlines())
            tree = ast.parse(src)
        except Exception as e:
            out.append(f"{os.path.relpath(p, base)}：解析失败（{type(e).__name__}）")
            continue
        items = []
        for node in tree.body:          # 只看顶层，够定位又不噪音
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                items.append(f"{node.name}({node.lineno})")
        out.append(f"{os.path.relpath(p, base)}（{total} 行）："
                   + ("  ".join(items) if items else "（无顶层定义）"))

    head = f"{full}，扫了 {min(len(targets), max_files)} 个 .py"
    if len(targets) >= max_files:
        head += "（到上限了，收窄 path 或调大 max_files）"
    return head + "\n" + "\n".join(out)


def remember(text: str, kind: str = "事实"):
    """把一条事实记进长期记忆（knowledge/memory.md）。下次开新对话还在。

    只记跨会话有用的：用户是谁、在做什么项目、定过什么规矩、踩过什么坑。
    不记寒暄、不记一次性的「帮我看看这个文件」。超过 200 条自动只留最近的。

    2026-11-25 改：走 memory.append_memory_lines —— 锁内读-改-写 + 原子落盘 + 凭证脱敏。
    以前是裸 open("w")：桌面端正在编辑记忆时，这里的写入会把它整个覆盖掉。
    """
    if not text or not text.strip():
        return "❌ text 为空"
    try:
        from .memory import append_memory_lines, MEMORY_KINDS
        k = kind if kind in MEMORY_KINDS else "事实"
        item = "- [%s][%s] %s" % (datetime.now().strftime("%m-%d"), k, text.strip())
        append_memory_lines(MEMORY_PATH, [item], max_entries=MAX_MEMORY_ITEMS)
        return "✅ 记住了：%s" % text.strip()
    except Exception as e:
        return "❌ 记忆写入失败：%s" % e


def search_memory(query: str, k: int = 3):
    """在长期记忆里按关键词检索，返回最相关的几条。

    本地 BM25 算，不调模型、不花 token。和 remember 配对：记进去的事，
    不用整读 memory.md，直接按词捞出来。条目一多，这比 read_file 快得多。
    """
    if not query or not query.strip():
        return "❌ query 为空"
    from .memory import load_memory_entries, search_memory_entries

    entries = load_memory_entries(MEMORY_PATH)
    if not entries:
        return "（记忆本还是空的，没有可查的。）"
    scored = search_memory_entries(query.strip(), entries, k)
    if not scored:
        return f"没找到和「{query.strip()}」相关的记忆。"
    return "\n".join(f"（相关度 {s:.1f}）{entries[i]}" for s, i in scored)


def get_current_time():
    """获取当前系统时间。"""
    now = datetime.now()
    week = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    return f"现在是 {now.strftime('%Y年%m月%d日')} {week} {now.strftime('%H:%M:%S')}"


def register_fs_tools(registry):
    # 描述刻意写短：schema 每轮重发，工具名本身已经够语义，不靠长描述撑理解
    # 描述刻意压到最短：schema 每轮重发，工具名本身已经够语义。
    # 只补模型真会搞错的点（path 格式、pattern 通配、edit 只换 old）。

    # ---- 基线：这五个几乎每轮都可能用到，永远挂着 ----
    registry.register("read_file", "读文件；可选 start/limit 读某几行(带行号)", read_file)
    registry.register("list_files", "列目录；pattern 递归找(*.py)，detail 带修改时间", list_files)
    registry.register("write_file", "写文件(自动备份)；append=true 则追加不是覆盖", write_file)
    registry.register("search_text", "搜内容，返回 文件:行号:行", search_text)
    registry.register("remember", "记一条跨会话有用的事", remember)
    registry.register("search_memory", "按关键词查记忆本，返回最相关几条", search_memory)

    # ---- 按需：只有这句话提到了才挂上 ----
    # 拆开的理由：13 个工具全挂是 3174 字符（约 2116 token），每轮都重发。
    # 基线五个约 1100 字符，剩下的等用户真说了再给——看不见的工具模型不会去调。
    registry.register(
        "delete_file", "删文件(移入回收区可恢复,不真删;只删文件不删目录)", delete_file,
        json_visible=False,
        tags=("删", "移除", "清理", "清掉", "清空", "弄掉", "扔掉", "腾出", "回收", "delete", "remove"),
    )
    registry.register(
        "move_file", "移动或重命名(不覆盖已存在的目标)", move_file,
        json_visible=False,
        tags=("移动", "重命名", "改名", "挪", "搬到", "移到", "换个名字", "move", "rename"),
    )
    registry.register(
        "open_path", "打开文件/文件夹/网页,等于双击它", open_path,
        json_visible=False,
        tags=("打开", "双击", "open", "launch"),
    )
    registry.register(
        "edit_file", "只替换 old 那一段(自动备份)", edit_file,
        json_visible=True,
    )
    registry.register(
        "code_map", "扫 .py 出 文件→函数(行号) 结构图", code_map,
        json_visible=False,
        tags=("函数", "结构", "地图", "架构", "类", "def ", "code_map"),
    )

    # 时间不进工具清单：system 每轮都注入当前时间，留着这个工具只会让它多跑一轮（实测省不掉，
    # 提示「别查」它照查不误——工具在清单里就是最强信号）。前端要用仍可调 Api.get_current_time。
    registry.register("get_current_time", "当前时间", get_current_time, json_visible=False)
    return registry
