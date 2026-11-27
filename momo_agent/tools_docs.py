"""文档工具：PDF / Word / PPT。

依赖 reportlab、python-pptx、python-docx，已装在基站 venv 里。

三个工具共用一套轻标记，模型写起来便宜，解析也稳：
    # 标题
    ## 小节
    - 列表项
    空行分段
PPT 额外用 "## 页标题" 分页，页内 "- " 写要点；没有 ## 时按空行分页。

中文字体统一走微软雅黑：reportlab 直接注册 ttc，python-docx 写 w:eastAsia，
python-pptx 写 a:ea / a:cs——不设这两处，中文会回落宋体。
"""

import os
import re

OUTPUT_DIR = r"D:\MomoDesktop\output"
FONT_CN = r"C:\Windows\Fonts\msyh.ttc"
FONT_CN_BD = r"C:\Windows\Fonts\msyhbd.ttc"


def configure_storage(data_dir):
    """把文档输出目录挂到 data 边界，不再写死 app 目录。"""
    global OUTPUT_DIR
    OUTPUT_DIR = os.path.join(data_dir, "output")

def _deny_prefixes():
    """系统目录按本机实际位置拼，别写死某个账户名。"""
    home = os.path.expanduser("~")
    return (
        os.environ.get("SystemRoot") or r"C:\Windows",
        os.environ.get("ProgramFiles") or r"C:\Program Files",
        os.environ.get("ProgramData") or r"C:\ProgramData",
        os.path.join(home, "AppData"),
    )


DENY_PREFIXES = _deny_prefixes()


def _out_path(name, ext, out_path):
    """定输出路径。给了 out_path 就用它（拦系统目录），否则落到 output/。"""
    if out_path:
        p = os.path.abspath(os.path.normpath(out_path))
        low = p.lower()
        for d in DENY_PREFIXES:
            if low.startswith(d.lower()):
                raise ValueError(f"拒绝写入系统目录：{p}")
        if not low.endswith(ext):
            p += ext
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return p
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", (name or "文档").strip())[:60] or "文档"
    return os.path.join(OUTPUT_DIR, safe + ext)


def _parse_blocks(body):
    """解析成 [(kind, text)]，kind ∈ h1 / h2 / li / p。"""
    out = []
    for raw in (body or "").replace("\r", "").split("\n"):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("## "):
            out.append(("h2", s[3:].strip()))
        elif s.startswith("# "):
            out.append(("h1", s[2:].strip()))
        elif s.startswith(("- ", "* ", "• ")):
            out.append(("li", s[2:].strip()))
        else:
            out.append(("p", s))
    return out


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _report(path, note=""):
    size = os.path.getsize(path)
    kb = size / 1024
    unit = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.2f} MB"
    return f"已生成 {path}（{unit}）{note}"


# ---------------------------------------------------------------- PDF

# 读 PDF 的字符预算。engine 那边 RESULT_LIMIT 是 1500，这里留点余量，
# 免得工具自己先撑爆、模型拿到半截还不知道为什么。
PDF_TEXT_BUDGET = 1200
PDF_MAX_PAGES = 20       # 不暴露成参数：多一个字段就多一份 schema，内部定死更省


def _assert_readable(path):
    """读也要守同一条边界：系统区和 AppData 不碰。"""
    p = os.path.abspath(os.path.normpath(path))
    low = p.lower()
    for d in DENY_PREFIXES:
        if low.startswith(d.lower()):
            return None, f"❌ 拒绝读取：{p} 在系统目录里"
    if not os.path.exists(p):
        return None, f"❌ 文件不存在：{p}"
    return p, ""


def _page_range(spec, total):
    """'3' / '1-5' / '1,2,7' / 空（全部）→ 0 基索引列表。"""
    spec = (spec or "").strip().replace(" ", "")
    if not spec:
        return list(range(min(total, PDF_MAX_PAGES)))
    idx = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                s, e = int(a), int(b)
                if s > e:
                    s, e = e, s
                idx.extend(range(max(1, s) - 1, min(total, e)))
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= total:
                idx.append(n - 1)
    return sorted(set(i for i in idx if 0 <= i < total))[:PDF_MAX_PAGES]


def _suggest_same_name(path):
    """路径拼错时按文件名在允许目录里找一遍，返回真实路径或空串。

    为什么做这个：2026-11-18 实测一次真实对话，用户把路径里的反斜杠写成了下划线
    （MomoDesktop_selftest），模型花了 6 步里的 3 步在找文件，最后正好打满 max_rounds。
    文件不存在时顺手替它找一下，比让它自己摸黑省 2-3 轮——这不是省 token，是省轮次。
    """
    name = os.path.basename(path)
    if not name:
        return ""
    roots = (r"D:\MomoDesktop", r"D:\Momo_Project")
    # 这些目录里不会有用户找的文档，但一个 venv 就是几万个文件。
    # 实测 D:\Momo_Project\venv\Lib\site-packages 正好卡在「第 2 层」边界上，
    # 原来的 >2 判断拦不住它，于是每次路径拼错都要同步遍历几万个包文件。
    skip = {"venv", ".venv", "node_modules", "__pycache__", "site-packages",
            ".git", "webview_storage", "momo.学习资料", "_selftest"}
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\").count("\\")
        for dirpath, dirnames, filenames in os.walk(root):
            # 只往下找两层，再深就慢且容易撞到无关文件
            if dirpath.count("\\") - base_depth > 2:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in skip]
            if name in filenames:
                return os.path.join(dirpath, name)
    return ""


def read_pdf(path: str, pages: str = "") -> str:
    """读 PDF 的文字。pages 可写 "3" / "1-5" / "1,2,7"，不填从头读。"""
    from pypdf import PdfReader

    p, err = _assert_readable(path)
    if err:
        if "不存在" in err:
            found = _suggest_same_name(path)
            if found:
                return (f"{err}\n（不过我在 {found} 找到了同名文件。"
                        f"要用的话直接拿这个路径再调一次，别重试原来那个。）")
        return err
    if not p.lower().endswith(".pdf"):
        return f"❌ 不是 PDF：{p}"

    try:
        reader = PdfReader(p)
    except Exception as e:
        return f"❌ 打不开这个 PDF：{type(e).__name__}: {e}"

    if getattr(reader, "is_encrypted", False):
        # 加密状态下连页数都读不到——len(reader.pages) 会直接抛 FileNotDecryptedError，
        # 所以这句不能带页数（实测踩过，见 2026-11-18 read_pdf 用例）
        return "❌ 这个 PDF 是加密的，解不开。要读它得先去掉密码。"

    total = len(reader.pages)
    picked = _page_range(pages, total)
    if not picked:
        if total == 1:
            return "❌ 页码范围不对。这个 PDF 只有 1 页，pages 写 \"1\"。"
        return f"❌ 页码范围不对。这个 PDF 一共 {total} 页，写 1 到 {total} 之间的页。"

    parts, used = [], 0
    empty_pages = []
    for i in picked:
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            empty_pages.append(i + 1)
            continue
        block = f"[第 {i + 1} 页]\n{text}"
        if used + len(block) > PDF_TEXT_BUDGET:
            parts.append(f"{block[:max(80, PDF_TEXT_BUDGET - used)]}\n…（本页后面截断了）")
            used = PDF_TEXT_BUDGET
            break
        parts.append(block)
        used += len(block)

    # 扫描件：没有文字层。这条必须明说——不然模型拿到空内容会自己编一整页
    if not parts:
        if empty_pages and len(empty_pages) == len(picked):
            return (f"⚠️ 这个 PDF 的这几页里没有可提取的文字（共 {total} 页）。"
                    f"多半是扫描件/图片版，没有文字层。我看不到内容，别猜，"
                    f"要么你自己把关键页截个图给我，要么用别的方式提供原文。")
        return f"⚠️ 这几页没提出文字（共 {total} 页）。如果是扫描件就没有文字层。"

    if len(picked) == 1:
        header = f"共 {total} 页，这次给你第 {picked[0] + 1} 页："
    else:
        header = f"共 {total} 页，这次给你第 {picked[0] + 1}-{picked[-1] + 1} 页："
    if len(picked) < total:
        header += f"（其余 {total - len(picked)} 页没读，要哪几页用 pages 指定）"
    if empty_pages:
        header += f"\n（第 {'、'.join(map(str, empty_pages))} 页没有文字层，是空页或图）"
    if used >= PDF_TEXT_BUDGET:
        header += f"\n（字符到上限了，后面的内容没读完）"

    return header + "\n\n" + "\n\n".join(parts)


def make_pdf(title: str, body: str, out_path: str = "") -> str:
    """生成 PDF 报告。body 支持 # 一级标题 / ## 小节 / - 条目 / 空行分段。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    pdfmetrics.registerFont(TTFont("CN", FONT_CN, subfontIndex=0))
    pdfmetrics.registerFont(TTFont("CNB", FONT_CN_BD, subfontIndex=0))

    navy = colors.HexColor("#1B3A57")
    st = {
        "title": ParagraphStyle("t", fontName="CNB", fontSize=19, leading=28,
                                textColor=navy, spaceAfter=14),
        "h1": ParagraphStyle("h1", fontName="CNB", fontSize=14.5, leading=22,
                             textColor=navy, spaceBefore=14, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName="CNB", fontSize=12, leading=19,
                             textColor=colors.HexColor("#2F5C82"),
                             spaceBefore=9, spaceAfter=5),
        "p": ParagraphStyle("p", fontName="CN", fontSize=10.5, leading=18,
                            spaceAfter=7),
        "li": ParagraphStyle("li", fontName="CN", fontSize=10.5, leading=18,
                             leftIndent=14, spaceAfter=4),
    }

    path = _out_path(title, ".pdf", out_path)

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("CN", 8)
        canvas.setFillColor(colors.HexColor("#8A9AAA"))
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=22 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title=title or "文档", author="基站",
    )
    story = [Paragraph(_esc(title or "文档"), st["title"])]
    for kind, text in _parse_blocks(body):
        if kind == "h1":
            story.append(Paragraph(_esc(text), st["h1"]))
        elif kind == "h2":
            story.append(Paragraph(_esc(text), st["h2"]))
        elif kind == "li":
            story.append(Paragraph("• " + _esc(text), st["li"]))
        else:
            story.append(Paragraph(_esc(text), st["p"]))
    if len(story) == 1:
        story.append(Paragraph("（没有正文内容）", st["p"]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return _report(path)


# ---------------------------------------------------------------- Word

def make_docx(title: str, body: str, out_path: str = "") -> str:
    """生成 Word 文档。body 规则同 make_pdf。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    for lvl in ("Title", "Heading 1", "Heading 2"):
        s = doc.styles[lvl]
        s.font.name = "微软雅黑"
        rpr = s.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), "微软雅黑")
        s.font.color.rgb = RGBColor(0x1B, 0x3A, 0x57)

    doc.add_heading(title or "文档", level=0)
    for kind, text in _parse_blocks(body):
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "li":
            doc.add_paragraph(text, style="List Bullet")
        else:
            doc.add_paragraph(text)

    path = _out_path(title, ".docx", out_path)
    doc.save(path)
    return _report(path)


# ---------------------------------------------------------------- PPT

def _set_run(run, size, bold=False, color=None):
    """设字号字重颜色，并把东亚/复杂文种字体钉成微软雅黑。"""
    from pptx.oxml.ns import qn
    from pptx.util import Pt

    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "微软雅黑"
    if color is not None:
        run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", "微软雅黑")


def _split_slides(slides):
    """按 ## 分页；没有 ## 就按空行分页。返回 [(标题, [要点])]。"""
    chunks, cur = [], []
    for raw in (slides or "").replace("\r", "").split("\n"):
        s = raw.strip()
        if s.startswith("## "):
            if cur:
                chunks.append(cur)
            cur = ["__TITLE__" + s[3:].strip()]
        elif not s:
            continue
        else:
            cur.append(s)
    if cur:
        chunks.append(cur)

    out = []
    for ch in chunks:
        if ch and ch[0].startswith("__TITLE__"):
            head, rest = ch[0][9:], ch[1:]
        else:
            head, rest = "要点", ch
        bullets = [r[2:].strip() if r.startswith(("- ", "* ")) else r for r in rest]
        out.append((head, bullets))
    return out


def make_pptx(title: str, slides: str, out_path: str = "") -> str:
    """生成 PPT。slides 用 ## 页标题分页，页内 - 写要点。"""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches

    navy = RGBColor(0x1B, 0x3A, 0x57)
    blue = RGBColor(0x2F, 0x5C, 0x82)
    ink = RGBColor(0x2B, 0x33, 0x3B)
    grey = RGBColor(0x8A, 0x9A, 0xAA)
    accent = RGBColor(0x3D, 0x7F, 0x8C)
    white = RGBColor(0xFF, 0xFF, 0xFF)
    light = RGBColor(0xF3, 0xF6, 0xF9)

    prs = Presentation()
    W, H = Inches(13.333), Inches(7.5)
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]

    def tb(slide, x, y, w, h, lines, size, bold=False, color=ink,
           align=PP_ALIGN.LEFT, spacing=1.4, anchor=MSO_ANCHOR.TOP):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = 0
        for i, line in enumerate(lines if isinstance(lines, list) else [lines]):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.alignment = align
            para.line_spacing = spacing
            run = para.add_run()
            run.text = line
            _set_run(run, size, bold, color)
        return box

    # ---- 封面
    s = prs.slides.add_slide(blank)
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.16), H)
    bar.fill.solid(); bar.fill.fore_color.rgb = accent; bar.line.fill.background()
    rgb_bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.16), 0, W - Inches(0.16), H)
    rgb_bg.fill.solid(); rgb_bg.fill.fore_color.rgb = navy
    rgb_bg.line.fill.background()
    # 背景块要沉到最底层
    s.shapes._spTree.remove(rgb_bg._element)
    s.shapes._spTree.insert(2, rgb_bg._element)

    tb(s, 1.3, 2.85, 10.4, 1.4, [title or "演示文稿"], 40, True, white, spacing=1.25)
    tb(s, 1.3, 6.35, 9.0, 0.35, ["基站 · 自动生成"], 11, False, grey)

    # ---- 内容页
    pages = _split_slides(slides) or [("要点", ["（没有内容）"])]
    for idx, (head, bullets) in enumerate(pages, start=1):
        s = prs.slides.add_slide(blank)

        # 页眉：章节条 + 页码
        tag = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.9), Inches(0.62), Inches(0.06), Inches(0.22))
        tag.fill.solid(); tag.fill.fore_color.rgb = accent; tag.line.fill.background()
        tb(s, 1.08, 0.58, 8.0, 0.32, [f"PART {idx:02d}"], 10.5, False, grey)

        # 标题
        tb(s, 0.9, 1.15, 11.5, 0.9, [head], 30, True, navy, spacing=1.2)

        # 标题下的细线
        sep = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.9), Inches(2.05), Inches(11.5), Inches(0.015))
        sep.fill.solid(); sep.fill.fore_color.rgb = RGBColor(0xE5, 0xEB, 0xF0)
        sep.line.fill.background()

        # 要点：按条数调字号与间距，条数少时撑起来，条数多时收进去
        if bullets:
            n = len(bullets)
            if n <= 3:
                size, sp = 23, 1.95
            elif n <= 5:
                size, sp = 19, 1.75
            elif n <= 8:
                size, sp = 15.5, 1.6
            else:
                size, sp = 13, 1.5
            y0 = 2.2
            tb(s, 0.95, y0, 11.3, 4.6,
               ["· " + b for b in bullets], size, False, ink, spacing=sp,
               anchor=MSO_ANCHOR.MIDDLE if n <= 3 else MSO_ANCHOR.TOP)

        # 页脚
        tb(s, 0.9, 6.85, 3.0, 0.3, [f"{idx:02d}"], 10, False, grey)

    path = _out_path(title, ".pptx", out_path)
    prs.save(path)
    return _report(path, f"，共 {len(pages) + 1} 页")


def register_doc_tools(registry):
    """挂四个文档工具。描述压到一行——schema 每轮重发，多一个字都是成本。

    这四个是「产出物 / 读材料」，不是日常操作：只在用户明确提到报告/文档/PPT/PDF 时才挂上。
    全天挂着等于每轮白烧 900 多字符，而大部分对话根本用不到。
    """
    registry.register(
        "read_pdf",
        "读 PDF 的文字。pages 写 \"3\" 或 \"1-5\"，不填从头读",
        read_pdf,
        json_visible=False,
        tags=("pdf", "读", "看看这", "内容", "总结", "这份", "材料"),
    )
    registry.register(
        "make_pdf",
        "生成 PDF 文件。body 用 # 标题 / ## 小节 / - 条目 / 空行分段",
        make_pdf,
        json_visible=False,
        tags=("pdf", "报告", "文档", "总结成", "整理成", "写成一份", "生成一份", "做一份", "排版"),
    )
    registry.register(
        "make_docx",
        "生成 Word 文件。body 写法同 make_pdf",
        make_docx,
        json_visible=False,
        tags=("word", "docx", "文档", "报告", "总结成", "整理成", "写成一份", "生成一份", "做一份"),
    )
    registry.register(
        "make_pptx",
        "生成 PPT。slides 用 ## 页标题分页，页内 - 写要点",
        make_pptx,
        json_visible=False,
        tags=("ppt", "幻灯", "演示", "汇报", "汇报稿", "生成一份", "做一份", "做一版", "做一份演示"),
    )

