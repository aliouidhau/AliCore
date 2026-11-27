"""插件层：把「学习资料\\插件」里的项目变成阿鲤可手动开关的工具。

设计原则：
  1. 插件默认全部关闭。用户在「管理插件」里手动开启，阿鲤才看得到、调得动。
  2. 一个插件 = 若干工具。开/关只切换工具可见性，不卸载代码，重启后状态保持。
  3. 依赖懒加载：工具函数里才 import 重型包，启动路径不因为装没装 cadquery 而变慢。
  4. 每个插件都给 ready/status，没就绪的诚实标出来，不假装已经接上。
"""

import base64
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import requests

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runtime_root():
    """可选组件的运行时目录：显式环境变量优先，否则跟数据目录同一处。"""
    env = os.environ.get("ALICORE_RUNTIME_DIR")
    if env:
        return os.path.abspath(env)
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AliCore", "_runtime")


def _env_dir(name, default):
    """环境变量优先；返回绝对路径，不校验存在性（由各插件自己报告未就绪）。"""
    value = os.environ.get(name)
    return os.path.abspath(value) if value else os.path.abspath(default)


PLUGIN_LIB = _env_dir("ALICORE_PLUGIN_LIB", os.path.join(_APP_DIR, "plugins"))
OUTPUT_DIR = os.path.join(_APP_DIR, "output", "plugins")
STATE_FILE = os.path.join(_APP_DIR, "plugins_state.json")
MAX_READ_CHARS = 12000


def configure_storage(data_dir=None):
    """插件状态和产出物跟 data_dir 走；插件库仍在 app 目录，不搬。"""
    global OUTPUT_DIR, STATE_FILE
    if data_dir:
        data = os.path.abspath(data_dir)
        OUTPUT_DIR = os.path.join(data, "output", "plugins")
        STATE_FILE = os.path.join(data, "plugins_state.json")
    return {"output_dir": OUTPUT_DIR, "state_file": STATE_FILE}

OLLAMA_URL = "http://127.0.0.1:11434"
DOC_OCR_MODEL = "deepseek-ocr"


def _find_sw_exe():
    """SolidWorks 可执行文件。环境变量优先，其次常见安装位，都没有就留空。

    不再把 D:\\sw2020 这类个人路径写死在开源版里——别人机器上没有这个目录。
    """
    env = os.environ.get("ALICORE_SW_EXE")
    if env and os.path.exists(env):
        return os.path.abspath(env)
    for cand in (
        r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe",
        r"C:\Program Files (x86)\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe",
        r"D:\sw2020\SOLIDWORKS\SLDWORKS.exe",
    ):
        if os.path.exists(cand):
            return cand
    return ""


SW_EXE = _find_sw_exe()
SW_SIDECAR_DIR = os.path.join(PLUGIN_LIB, "Millwright", "sidecar")


# ------------------------------------------------------------------ 工具函数

def _safe_read(path, limit=MAX_READ_CHARS):
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return None, f"❌ 文件不存在：{p}"
    if os.path.isdir(p):
        return None, f"❌ 是目录不是文件：{p}"
    if os.path.getsize(p) > 800_000:
        return None, f"❌ 文件太大（{os.path.getsize(p)//1024}KB），超过 800KB 上限"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return None, f"❌ 读取失败：{e}"
    if len(text) > limit:
        text = text[:limit] + f"\n…(后面还有 {len(text) - limit} 字，用 plugin_read_skill 指定 path 和更细的关键词)"
    return text, ""


def _skill_roots():
    return [
        os.path.join(PLUGIN_LIB, "skills", "skills"),
        os.path.join(PLUGIN_LIB, "superpowers", "skills"),
    ]


def plugin_search_skills(query: str):
    """按关键词搜本地技能库，返回技能名和路径。"""
    q = (query or "").strip().lower()
    roots = [r for r in _skill_roots() if os.path.isdir(r)]
    if not roots:
        return "❌ 技能库目录不存在，插件尚未就绪。"
    hits = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            # 技能本体就是 SKILL.md 或 README.md；其它文件先不进结果，减少噪音
            for name in filenames:
                if name.upper() not in ("SKILL.MD", "README.MD"):
                    continue
                p = os.path.join(dirpath, name)
                rel = os.path.relpath(p, PLUGIN_LIB)
                if q and q not in rel.lower():
                    # 目录名没命中再扫正文第一段，命中词才要，别把所有技能都吐出来
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            head = f.read(600).lower()
                    except Exception:
                        continue
                    if q not in head:
                        continue
                hits.append(rel)
                if len(hits) >= 12:
                    break
            if len(hits) >= 12:
                break
        if len(hits) >= 12:
            break
    if not hits:
        return f"（搜索成功，但没有命中「{query or ''}」）换个词，比如技能名、用途或英文名。"
    return "技能库命中：\n" + "\n".join(f"- {h}" for h in hits)


def plugin_read_skill(path: str):
    """读一个具体技能文件（技能库里的相对路径）。"""
    if not path:
        return "❌ path 为空。先用 plugin_search_skills 找，再读。"
    p = os.path.abspath(os.path.normpath(path))
    if not p.lower().startswith(os.path.abspath(PLUGIN_LIB).lower() + os.sep):
        p = os.path.join(PLUGIN_LIB, path)
    p = os.path.abspath(os.path.normpath(p))
    lib = os.path.abspath(PLUGIN_LIB)
    if not (p == lib or p.lower().startswith(lib.lower() + os.sep)):
        return f"❌ 只允许读插件库内的文件：{PLUGIN_LIB}"
    text, err = _safe_read(p)
    if err:
        return err
    return f"（{os.path.relpath(p, PLUGIN_LIB)}）\n{text}"


def plugin_mesh_info(path: str):
    """读 3D 网格文件（STL/OBJ/GLB/STEP），返回尺寸、体积、面积、网格数和是否水密。"""
    try:
        import trimesh
    except Exception:
        return "❌ trimesh 未安装。先在管理插件里点开这个插件，等待依赖安装完成。"
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return f"❌ 文件不存在：{p}"
    try:
        mesh = trimesh.load(p, force="mesh")
    except Exception as e:
        return f"❌ 读取网格失败：{type(e).__name__}: {e}"
    try:
        bbox = mesh.bounds
        ext = bbox[1] - bbox[0]
        info = [
            f"文件：{p}",
            f"类型：{type(mesh).__name__}",
            f"包围盒：{ext[0]:.2f} × {ext[1]:.2f} × {ext[2]:.2f}（X×Y×Z）",
            f"体积：{float(mesh.volume):.4f}",
            f"表面积：{float(mesh.area):.4f}",
            f"面数：{len(mesh.faces)}，顶点数：{len(mesh.vertices)}",
            f"水密：{'是' if mesh.is_watertight else '否'}",
            f"破面：{'有' if not mesh.is_watertight else '无'}",
        ]
        return "\n".join(info)
    except Exception as e:
        return f"❌ 计算网格信息失败：{type(e).__name__}: {e}"


def _parse_size(size):
    parts = [float(x) for x in re.split(r"[,xX*× ]", (size or "").strip()) if x.strip()]
    if len(parts) == 1:
        return parts[0], parts[0], parts[0]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0]
    raise ValueError("尺寸格式不对，写 长,宽,高，例如 50,30,20")


def plugin_cad_primitive(kind: str, size: str, out_path: str = ""):
    """生成一个参数化基础 3D 零件，导出 STEP。kind=box/cylinder/sphere/wedge。"""
    try:
        import cadquery as cq
    except Exception as e:
        return f"❌ cadquery 未安装或不可用：{type(e).__name__}。请在管理插件里确认该插件已开启并完成依赖安装。"
    kind = (kind or "").strip().lower()
    try:
        l, w, h = _parse_size(size)
    except Exception as e:
        return f"❌ {e}"
    try:
        if kind in ("box", "立方体", "长方体"):
            solid = cq.Workplane("XY").box(l, w, h)
            suffix = "box"
        elif kind in ("cylinder", "圆柱", "圆柱体"):
            solid = cq.Workplane("XY").circle(min(l, w) / 2).extrude(h)
            suffix = "cylinder"
        elif kind in ("sphere", "球", "球体"):
            solid = cq.Workplane("XY").sphere(max(l, w, h) / 2)
            suffix = "sphere"
        elif kind in ("wedge", "楔形", "楔块"):
            solid = cq.Workplane("XY").wedge(l, w, h, 0, 0, l, 0)
            suffix = "wedge"
        else:
            return "❌ 不支持的 kind，可选 box / cylinder / sphere / wedge"
    except Exception as e:
        return f"❌ 建模失败：{type(e).__name__}: {e}"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|]', "_", kind)[:30] or "part"
    p = os.path.join(OUTPUT_DIR, f"{stem}_{int(time.time())}.step")
    if out_path:
        p = os.path.abspath(os.path.normpath(out_path))
        if not p.lower().endswith(".step"):
            p += ".step"
        os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        cq.exporters.export(solid, p)
        return f"✅ 已生成 STEP：{p}"
    except Exception as e:
        return f"❌ 导出失败：{type(e).__name__}: {e}"


def _umi_exe():
    """找 Umi-OCR 可执行文件。发布包解压在 release/ 下，exe 再深一层。"""
    root = os.path.join(PLUGIN_LIB, "Umi-OCR")
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 发布包可能很大，只找两层
        if os.path.relpath(dirpath, root).count(os.sep) > 3:
            dirnames[:] = []
            continue
        for name in filenames:
            if name.lower() in ("umi-ocr.exe", "umiocr.exe"):
                candidates.append(os.path.join(dirpath, name))
        if candidates:
            break
    return candidates[0] if candidates else ""


def _run_umi(args, timeout=120):
    exe = _umi_exe()
    if not exe:
        return "❌ 找不到 Umi-OCR.exe。该插件尚未就绪（发布包未解压或未安装）。"
    try:
        proc = subprocess.run(
            [exe] + args, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            cwd=os.path.dirname(exe),
        )
    except subprocess.TimeoutExpired:
        return "❌ OCR 超时，图片可能太大，先缩小再试。"
    except Exception as e:
        return f"❌ 调用 Umi-OCR 失败：{type(e).__name__}: {e}"
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        return out
    if proc.returncode == 0:
        return "✅ OCR 完成，但 CLI 没有回传文本。用 plugin_ocr_image 指定 --output 文件再试。"
    err = (proc.stderr or "").strip()
    return f"❌ OCR 返回错误（{proc.returncode}）：{err[:400]}"


def _looks_like_error(text):
    t = (text or "").lower()
    for mark in ("已在运行", "is already running", "[error]", "error:", "❌"):
        if mark.lower() in t:
            return True
    return False


def plugin_ocr_image(path: str):
    """本地 OCR 识别一张图片，返回文字。不联网、不花 token。"""
    if not path:
        return "❌ path 为空"
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return f"❌ 文件不存在：{p}"
    out = os.path.join(OUTPUT_DIR, "_ocr_last.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    last = ""
    for _ in range(3):
        last = _run_umi(["--path", p, "--output", out], timeout=120)
        try:
            with open(out, "r", encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except Exception:
            text = ""
        if text and not _looks_like_error(text):
            return text
        # 首次调用 Umi-OCR 还没起服务，等它起来再试
        if "拒绝" in last or "refused" in last.lower() or "失败" in last:
            time.sleep(3)
            continue
        break
    return last if last.startswith("❌") else last


def plugin_ocr_qrcode(path: str):
    """识别图片里的二维码，返回二维码内容。"""
    if not path:
        return "❌ path 为空"
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return f"❌ 文件不存在：{p}"
    out = os.path.join(OUTPUT_DIR, "_qrcode_last.txt")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    last = ""
    for _ in range(3):
        last = _run_umi(["--qrcode_read", p, "--output", out], timeout=60)
        try:
            with open(out, "r", encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except Exception:
            text = ""
        if text and not _looks_like_error(text):
            return text
        if "拒绝" in last or "refused" in last.lower() or "失败" in last:
            time.sleep(3)
            continue
        break
    return last if last.startswith("❌") else last


def _ollama_running():
    try:
        r = requests.get(OLLAMA_URL + "/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_model_ready(model):
    try:
        r = requests.get(OLLAMA_URL + "/api/tags", timeout=5)
        if r.status_code != 200:
            return False
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
        return any(n == model or n.startswith(model + ":") for n in names)
    except Exception:
        return False


def plugin_doc_ocr(path: str):
    """本地 DeepSeek-OCR 识别文档图片，输出 Markdown。零 token，用完即卸载。"""
    if not path:
        return "❌ path 为空"
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return f"❌ 文件不存在：{p}"
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except Exception as e:
        return f"❌ 读取图片失败：{e}"
    if len(raw) > 12 * 1024 * 1024:
        return "❌ 图片超过 12MB，先缩小再试"
    b64 = base64.b64encode(raw).decode()
    payload = {
        "model": DOC_OCR_MODEL,
        "messages": [{
            "role": "user",
            "content": "Free OCR. Convert the document to Markdown, preserving layout, tables and formulas.",
            "images": [b64],
        }],
        "stream": False,
        # keep_alive=0：识别完立即卸载模型，把显存还给系统
        "keep_alive": 0,
        "options": {"temperature": 0},
    }
    try:
        resp = requests.post(OLLAMA_URL + "/api/chat", json=payload, timeout=300)
    except requests.Timeout:
        return "❌ 本地 OCR 超时。首次调用要加载模型，稍等几秒再试一次。"
    except Exception as e:
        return f"❌ 调用本地 OCR 失败：{type(e).__name__}: {e}"
    if resp.status_code != 200:
        return f"❌ 本地 OCR 返回 {resp.status_code}：{resp.text[:300]}"
    try:
        data = resp.json()
        text = ((data.get("message") or {}).get("content") or "").strip()
    except Exception:
        return "❌ 本地 OCR 响应不是合法 JSON"
    return text or "（OCR 完成，但没有识别到文字）"


# ------------------------------------------------------------------ Blender / MPFB 人物建模

BLENDER_EXE = os.environ.get("ALICORE_BLENDER_EXE") or os.path.join(_runtime_root(), "blender", "blender.exe")
BLENDER_OUTPUT = os.path.join(OUTPUT_DIR, "blender")


def _mpfb_ext_root():
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Blender Foundation", "Blender", "5.0",
                        "extensions", "blender_org", "mpfb")


def _blender_mpfb_ready():
    return (os.path.exists(BLENDER_EXE)
            and os.path.exists(os.path.join(_mpfb_ext_root(), "__init__.py")))


def _human_script(gender, skin, hair, out_glb, out_obj):
    """拼出 Blender 端捏人脚本（headless 跑 bpy + MPFB）。"""
    g = "1.0" if str(gender).lower() in ("female", "woman", "女", "女性", "1") else "0.0"

    skin_block = ""
    if skin:
        fname = skin if str(skin).endswith(".mhmat") else skin + ".mhmat"
        skin_block = (
            '_skin = AssetService.find_asset_absolute_path(' + json.dumps(fname) + ', asset_subdir="skins")\n'
            'if _skin:\n'
            '    HumanService.set_character_skin(_skin, basemesh, skin_type="MAKESKIN")\n'
            '    print("ALI_SKIN: ok")\n'
            'else:\n'
            '    print("ALI_SKIN: missing")\n'
        )

    hair_block = ""
    if hair:
        fname = hair if str(hair).endswith(".mhclo") else hair + ".mhclo"
        hair_block = (
            '_hair = AssetService.find_asset_absolute_path(' + json.dumps(fname) + ', asset_subdir="hair")\n'
            'if _hair:\n'
            '    HumanService.add_mhclo_asset(_hair, basemesh, asset_type="Hair")\n'
            '    print("ALI_HAIR: ok")\n'
            'else:\n'
            '    print("ALI_HAIR: missing")\n'
        )

    obj_block = ""
    if out_obj:
        obj_block = (
            'bpy.ops.wm.obj_export(filepath=' + json.dumps(out_obj) + ')\n'
            'print("ALI_OBJ: ok")\n'
        )
    else:
        obj_block = 'print("ALI_OBJ: skipped")\n'

    script = (
        'import bpy, os\n'
        'import bl_ext.blender_org.mpfb as mpfb\n'
        'if mpfb.MPFB_CONTEXTUAL_INFORMATION is None:\n'
        '    mpfb.register()\n'
        'from bl_ext.blender_org.mpfb.services.humanservice import HumanService\n'
        'from bl_ext.blender_org.mpfb.services.assetservice import AssetService\n'
        'from bl_ext.blender_org.mpfb.services.targetservice import TargetService\n'
        'from bl_ext.blender_org.mpfb.entities.objectproperties import HumanObjectProperties\n'
        'basemesh = HumanService.create_human()\n'
        'HumanObjectProperties.set_value("gender", ' + g + ', entity_reference=basemesh)\n'
        'HumanObjectProperties.set_value("african", 0.0, entity_reference=basemesh)\n'
        'HumanObjectProperties.set_value("asian", 0.0, entity_reference=basemesh)\n'
        'HumanObjectProperties.set_value("caucasian", 0.0, entity_reference=basemesh)\n'
        'TargetService.reapply_macro_details(basemesh)\n'
        + skin_block +
        hair_block +
        '_assets = [\n'
        '    ("eyes", "low-poly.mhclo", "Eyes"),\n'
        '    ("eyebrows", "eyebrow001.mhclo", "Eyebrows"),\n'
        '    ("eyelashes", "eyelashes01.mhclo", "Eyelashes"),\n'
        '    ("tongue", "tongue01.mhclo", "Tongue"),\n'
        '    ("teeth", "teeth_base.mhclo", "Teeth"),\n'
        ']\n'
        'for _sub, _fn, _at in _assets:\n'
        '    _p = AssetService.find_asset_absolute_path(_fn, asset_subdir=_sub)\n'
        '    if _p:\n'
        '        HumanService.add_mhclo_asset(_p, basemesh, asset_type=_at)\n'
        '        print("ALI_ASSET: ok " + _at)\n'
        'HumanService.add_builtin_rig(basemesh, "game_engine")\n'
        'print("ALI_RIG: ok")\n'
        'os.makedirs(os.path.dirname(' + json.dumps(out_glb) + '), exist_ok=True)\n'
        'bpy.ops.object.select_all(action="DESELECT")\n'
        'for o in bpy.data.objects:\n'
        '    o.select_set(True)\n'
        'bpy.ops.export_scene.gltf(filepath=' + json.dumps(out_glb) + ', export_format="GLB", use_selection=True)\n'
        'print("ALI_GLB: ok")\n'
        + obj_block +
        '_total = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH")\n'
        'print("ALI_VERTS: " + str(_total))\n'
        'print("ALI_OBJS: " + str(len(bpy.data.objects)))\n'
    )
    return script


def plugin_blender_human(gender="female", skin=None, hair=None):
    """本地 Blender + MPFB 生成参数化人体，导出 GLB/OBJ。零 token。"""
    if not _blender_mpfb_ready():
        return "❌ Blender 或 MPFB2 未就绪。设 ALICORE_BLENDER_EXE 指向 blender.exe，并装好 MPFB2 扩展。"
    try:
        os.makedirs(BLENDER_OUTPUT, exist_ok=True)
    except Exception as e:
        return f"❌ 无法创建输出目录：{e}"
    import datetime
    import tempfile
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_glb = os.path.join(BLENDER_OUTPUT, "human_" + stamp + ".glb")
    out_obj = os.path.join(BLENDER_OUTPUT, "human_" + stamp + ".obj")
    script = _human_script(gender, skin, hair, out_glb, out_obj)
    tmp = os.path.join(tempfile.gettempdir(), "_ali_blender_human_" + stamp + ".py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(script)
    except Exception as e:
        return f"❌ 写临时脚本失败：{e}"
    try:
        proc = subprocess.run(
            [BLENDER_EXE, "--background", "--python", tmp],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "❌ Blender 生成超时（600 秒）。"
    except Exception as e:
        return f"❌ 调 Blender 失败：{type(e).__name__}: {e}"
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    stdout = proc.stdout or ""
    verts = ""
    for line in stdout.splitlines():
        if line.startswith("ALI_VERTS:"):
            verts = line.split(":", 1)[1].strip()
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + stdout)[-600:]
        return f"❌ Blender 退出码 {proc.returncode}：\n{tail}"
    if not os.path.exists(out_glb):
        return f"❌ 未生成 GLB。stdout 尾部：\n{stdout[-600:]}"
    obj_part = "OBJ：" + out_obj if os.path.exists(out_obj) else ""
    return (f"✅ 人物已生成（{gender}）\n"
            f"GLB：{out_glb}\n{obj_part}\n"
            f"顶点数：{verts}")


def _vrm_ext_root():
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Blender Foundation", "Blender", "5.0",
                        "extensions", "blender_org", "vrm")


def _blender_vrm_ready():
    return (os.path.exists(BLENDER_EXE)
            and os.path.exists(os.path.join(_vrm_ext_root(), "__init__.py")))


def _vrm_script(vrm_path, out_png, out_glb):
    """拼出 headless 导入 VRM(1.0) → MToon 三渲二渲染 → 导出 GLB 的脚本。"""
    glb_block = ""
    if out_glb:
        glb_block = (
            'try:\n'
            '    os.makedirs(os.path.dirname(' + json.dumps(out_glb) + '), exist_ok=True)\n'
            '    bpy.ops.object.select_all(action="DESELECT")\n'
            '    for o in bpy.data.objects:\n'
            '        o.select_set(True)\n'
            '    bpy.ops.export_scene.gltf(filepath=' + json.dumps(out_glb) + ', export_format="GLB", use_selection=True)\n'
            '    print("ALI_GLB_OK")\n'
            'except Exception as _e:\n'
            '    print("ALI_GLB_FAIL: " + str(_e))\n'
        )
    else:
        glb_block = 'print("ALI_GLB: skipped")\n'

    script = (
        'import bpy\n'
        'import bl_ext.blender_org.vrm\n'  # 触发 register，否则 import_scene.vrm 找不到
        'from mathutils import Vector\n'
        'bpy.ops.import_scene.vrm(filepath=' + json.dumps(vrm_path) + ')\n'
        'print("ALI_VRM_IMPORTED")\n'
        'for o in list(bpy.data.objects):\n'
        '    if o.name in ("Cube", "Light", "Camera"):\n'
        '        bpy.data.objects.remove(o, do_unlink=True)\n'
        'meshes = [o for o in bpy.data.objects if o.type == "MESH"]\n'
        'minv = [1e9]*3; maxv = [-1e9]*3\n'
        'for o in meshes:\n'
        '    for v in o.bound_box:\n'
        '        w = o.matrix_world @ Vector(v)\n'
        '        for i in range(3):\n'
        '            minv[i] = min(minv[i], w[i]); maxv[i] = max(maxv[i], w[i])\n'
        'center = Vector([(minv[i]+maxv[i])/2 for i in range(3)])\n'
        'height = maxv[2]-minv[2]\n'
        'world = bpy.data.worlds.new("W")\n'
        'world.use_nodes = True\n'
        'bg = world.node_tree.nodes.get("Background")\n'
        'if bg:\n'
        '    bg.inputs[0].default_value = (0.85, 0.87, 0.90, 1.0)\n'
        '    bg.inputs[1].default_value = 1.0\n'
        'bpy.context.scene.world = world\n'
        'dist = max(height * 2.2, 2.6)\n'
        'target = Vector((center[0], center[1], minv[2] + height * 0.5))\n'
        'cam_data = bpy.data.cameras.new("Cam")\n'
        'cam_data.lens = 50\n'
        'cam = bpy.data.objects.new("Cam", cam_data)\n'
        'bpy.context.scene.collection.objects.link(cam)\n'
        'cam.location = Vector((target[0], target[1] - dist, target[2]))\n'
        'cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()\n'
        'bpy.context.scene.camera = cam\n'
        'sun_data = bpy.data.lights.new("Sun", "SUN")\n'
        'sun_data.energy = 3.5\n'
        'sun = bpy.data.objects.new("Sun", sun_data)\n'
        'bpy.context.scene.collection.objects.link(sun)\n'
        'sun.rotation_euler = (1.0, 0.1, 0.6)\n'
        'area_data = bpy.data.lights.new("Fill", "AREA")\n'
        'area_data.energy = 300\n'
        'area_data.size = 3\n'
        'area = bpy.data.objects.new("Fill", area_data)\n'
        'bpy.context.scene.collection.objects.link(area)\n'
        'area.location = Vector((target[0], target[1] - 1.5, target[2] + 0.3))\n'
        'area.rotation_euler = (target - area.location).to_track_quat("-Z", "Y").to_euler()\n'
        'scene = bpy.context.scene\n'
        'scene.render.engine = "BLENDER_EEVEE"\n'
        'scene.render.resolution_x = 900\n'
        'scene.render.resolution_y = 1200\n'
        'scene.render.filepath = ' + json.dumps(out_png) + '\n'
        'scene.render.image_settings.file_format = "PNG"\n'
        'bpy.ops.render.render(write_still=True)\n'
        'print("ALI_PNG_OK")\n'
        + glb_block +
        'verts = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH")\n'
        'print("ALI_VERTS: " + str(verts))\n'
        'print("ALI_MESHES: " + str(len(meshes)))\n'
    )
    return script


def plugin_vrm_render(vrm_path, export_glb=True):
    """本地 Blender + VRM addon 导入 VRM(1.0)，三渲二渲染预览图，可选导出 GLB。零 token。"""
    if not _blender_vrm_ready():
        return "❌ Blender 或 VRM 插件未就绪。设 ALICORE_BLENDER_EXE 指向 blender.exe，并装好 VRM addon。"
    p = os.path.abspath(os.path.normpath(vrm_path))
    if not os.path.exists(p):
        return f"❌ VRM 文件不存在：{p}"
    if not p.lower().endswith(".vrm"):
        return "❌ 只支持 .vrm 文件（VRM 1.0）。"
    try:
        os.makedirs(BLENDER_OUTPUT, exist_ok=True)
    except Exception as e:
        return f"❌ 无法创建输出目录：{e}"
    import datetime
    import tempfile
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_png = os.path.join(BLENDER_OUTPUT, "vrm_" + stamp + ".png")
    out_glb = os.path.join(BLENDER_OUTPUT, "vrm_" + stamp + ".glb") if export_glb else ""
    script = _vrm_script(p, out_png, out_glb)
    tmp = os.path.join(tempfile.gettempdir(), "_ali_vrm_" + stamp + ".py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(script)
    except Exception as e:
        return f"❌ 写临时脚本失败：{e}"
    env = dict(os.environ)
    env["BLENDER_VRM_AUTOMATIC_LICENSE_CONFIRMATION"] = "true"
    try:
        proc = subprocess.run(
            [BLENDER_EXE, "--background", "--python", tmp],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace", env=env,
        )
    except subprocess.TimeoutExpired:
        return "❌ Blender 处理超时（600 秒）。"
    except Exception as e:
        return f"❌ 调 Blender 失败：{type(e).__name__}: {e}"
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    stdout = proc.stdout or ""
    if proc.returncode != 0 or "ALI_PNG_OK" not in stdout:
        tail = ((proc.stderr or "") + "\n" + stdout)[-800:]
        return f"❌ VRM 处理失败：\n{tail}"
    if not os.path.exists(out_png):
        return f"❌ 未生成预览图。stdout 尾部：\n{stdout[-500:]}"
    verts = ""
    for line in stdout.splitlines():
        if line.startswith("ALI_VERTS:"):
            verts = line.split(":", 1)[1].strip()
    parts = ["✅ VRM 已处理（三渲二预览）", f"预览图：{out_png}"]
    if out_glb and os.path.exists(out_glb):
        parts.append(f"GLB：{out_glb}")
    parts.append(f"顶点数：{verts}")
    return "\n".join(parts)


def _runtime_bin(name, exe):
    """在运行时目录下找便携组件的可执行文件。"""
    root = os.path.join(_runtime_root(), name)
    if not os.path.isdir(root):
        return ""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.relpath(dirpath, root).count(os.sep) > 4:
            dirnames[:] = []
            continue
        for f in filenames:
            if f.lower() == exe.lower():
                hits.append(os.path.join(dirpath, f))
    return hits[0] if hits else ""


def _dot_exe():
    """找 Graphviz 的 dot.exe：环境变量 > 运行时便携包 > 常见安装位 > PATH。"""
    env = os.environ.get("ALICORE_DOT_EXE")
    if env and os.path.exists(env):
        return env
    found = _runtime_bin("graphviz", "dot.exe")
    if found:
        return found
    for cand in (r"C:\Program Files\Graphviz\bin\dot.exe",
                 r"C:\Program Files (x86)\Graphviz\bin\dot.exe"):
        if os.path.exists(cand):
            return cand
    import shutil
    return shutil.which("dot") or ""


DISTILL_EXE = os.path.join(_runtime_root(), "distill.exe")
DISTILL_DIR = os.path.join(_APP_DIR, "knowledge", "distill")
DISTILL_CONFIG = os.path.join(DISTILL_DIR, "distill.yaml")


def _run_distill(args, timeout=120):
    if not os.path.exists(DISTILL_EXE):
        return "❌ distill 二进制不存在，插件尚未就绪。"
    os.makedirs(DISTILL_DIR, exist_ok=True)
    cmd = [DISTILL_EXE]
    if os.path.exists(DISTILL_CONFIG):
        cmd += ["--config", DISTILL_CONFIG]
    cmd += args
    try:
        proc = subprocess.run(
            cmd,
            cwd=DISTILL_DIR, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "❌ distill 执行超时。"
    except Exception as e:
        return f"❌ 调用 distill 失败：{type(e).__name__}: {e}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"❌ distill 返回错误（{proc.returncode}）：{(err or out)[:500]}"
    return out or "✅ 完成"


def plugin_memory_store(text: str, tags: str = "", source: str = ""):
    """把一条事实/偏好/约束写入本地去重记忆库。"""
    if not text or not text.strip():
        return "❌ text 为空。"
    args = ["memory", "store", "--text", text.strip(),
            "--embedding-provider", "ollama"]
    if tags:
        for t in [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]:
            args += ["--tags", t]
    if source and source.strip():
        args += ["--source", source.strip()]
    return _run_distill(args)


def plugin_memory_recall(query: str, max_results: int = 5):
    """按相关度+新鲜度召回本地记忆。"""
    if not query or not query.strip():
        return "❌ query 为空。"
    try:
        n = max(1, min(20, int(max_results)))
    except Exception:
        n = 5
    return _run_distill(["memory", "recall", "--query", query.strip(),
                         "--max-results", str(n),
                         "--embedding-provider", "ollama"])


def plugin_memory_stats():
    """查看本地记忆库统计。"""
    return _run_distill(["memory", "stats"])


def _diagram_paths(title, out_path):
    """算出输出 png 路径与 stem（主渲染和后备渲染共用）。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|]', "_", title or "arch")[:30] or "arch"
    if out_path:
        p = os.path.abspath(os.path.normpath(out_path))
        if not p.lower().endswith(".png"):
            p += ".png"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        stem = os.path.splitext(os.path.basename(p))[0]
    else:
        p = os.path.join(OUTPUT_DIR, f"{stem}_{int(time.time())}.png")
    return p, stem


def _render_diagram_fallback(node_lines, edge_lines, title, p, direction):
    """Graphviz 不在时的后备：Pillow 画分层方块图。

    不追求 Graphviz 的自动布局质量，只保证「不装外部二进制也能出图」。
    分层用最长路径松弛，有环时用迭代次数兜住。
    """
    from PIL import Image, ImageDraw, ImageFont

    nodes, order = {}, []
    for line in node_lines:
        key, label = (line.split(":", 1) if ":" in line else (line, line))
        k = key.strip()
        if not k or k in nodes:
            continue
        nodes[k] = (label or k).strip()
        order.append(k)

    edges, seen = [], set()
    for line in edge_lines:
        parts = [x.strip() for x in re.split(r'\s*-{1,2}>\s*', line) if x.strip()]
        if len(parts) < 2:
            continue
        a, b = parts[0], parts[1]
        if a in nodes and b in nodes and (a, b) not in seen:
            seen.add((a, b))
            edges.append((a, b))

    level = {k: 0 for k in nodes}
    for _ in range(len(nodes) + 1):
        changed = False
        for a, b in edges:
            if level[b] <= level[a]:
                level[b] = level[a] + 1
                changed = True
        if not changed:
            break

    layers = {}
    for k in order:
        layers.setdefault(level[k], []).append(k)
    n_layers = len(layers)
    max_per = max((len(v) for v in layers.values()), default=1) or 1

    bw, bh, gx, gy, pad, head, route = 170, 54, 62, 26, 34, 56, 40
    lr = (direction or "LR").upper() in ("LR", "RL")
    if lr:
        W = pad * 2 + n_layers * bw + max(0, n_layers - 1) * gx
        H = pad * 2 + head + max_per * bh + max(0, max_per - 1) * gy + route
    else:
        W = pad * 2 + max_per * bw + max(0, max_per - 1) * gx + route
        H = pad * 2 + head + n_layers * bh + max(0, n_layers - 1) * gy

    reverse = (direction or "LR").upper() in ("RL", "BT")
    pos = {}
    for lv, keys in layers.items():
        lv_eff = (n_layers - 1 - lv) if reverse else lv
        for i, k in enumerate(keys):
            if lr:
                x = pad + lv_eff * (bw + gx)
                y = pad + head + i * (bh + gy)
            else:
                x = pad + i * (bw + gx)
                y = pad + head + lv_eff * (bh + gy)
            pos[k] = (x, y)

    img = Image.new("RGB", (max(W, 480), max(H, 260)), "#0d1418")
    d = ImageDraw.Draw(img)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    try:
        f_title = ImageFont.truetype(font_path, 19)
        f_node = ImageFont.truetype(font_path, 13)
    except Exception:
        f_title = f_node = ImageFont.load_default()

    d.text((pad, 18), title or "架构图", font=f_title, fill="#dfe8ee")
    d.text((max(W, 480) - pad, 24), "Pillow 后备渲染（未检测到 Graphviz）",
           font=f_node, fill="#5b6f7d", anchor="ra")

    import math

    def _arrow(pts):
        """沿折线画线，末端补一个箭头。"""
        d.line(pts, fill="#3f6577", width=2)
        p1, p2 = pts[-2], pts[-1]
        ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        size = 7
        for s in (+0.5, -0.5):
            d.line([p2, (p2[0] - size * math.cos(ang + s), p2[1] - size * math.sin(ang + s))],
                   fill="#3f6577", width=2)

    for a, b in edges:
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        if lr:
            p1 = (x1 + bw, y1 + bh / 2)
            p2 = (x2, y2 + bh / 2)
            if level[b] - level[a] > 1:
                # 跨层：从源盒下方走一条公共通道，绕开中间的层
                band = H - pad - 8
                _arrow([p1, (p1[0] + 12, p1[1]), (p1[0] + 12, band),
                        (p2[0] - 16, band), (p2[0] - 16, p2[1]), p2])
            else:
                _arrow([p1, p2])
        else:
            p1 = (x1 + bw / 2, y1 + bh)
            p2 = (x2 + bw / 2, y2)
            if level[b] - level[a] > 1:
                band = W - pad - 8
                _arrow([p1, (p1[0], p1[1] + 12), (band, p1[1] + 12),
                        (band, p2[1] - 16), (p2[0], p2[1] - 16), p2])
            else:
                _arrow([p1, p2])

    for k, label in nodes.items():
        x, y = pos[k]
        d.rounded_rectangle([x, y, x + bw, y + bh], radius=9,
                            outline="#3a5568", fill="#141e26", width=1)
        text = label if d.textlength(label, font=f_node) <= bw - 18 else label[:11] + "…"
        d.text((x + bw / 2, y + bh / 2), text, font=f_node, fill="#cfdae2", anchor="mm")

    img.save(p)
    return f"✅ 已生成架构图（Pillow 后备）：{p}"


def plugin_architecture_diagram(nodes: str, edges: str, title: str = "架构图", out_path: str = "", direction: str = "LR"):
    """用代码生成系统架构拓扑图，输出 PNG。nodes 每行「名称:备注」，edges 每行「A -> B」。"""
    node_lines = [x.strip() for x in (nodes or "").splitlines() if x.strip()]
    edge_lines = [x.strip() for x in (edges or "").splitlines() if x.strip()]
    if not node_lines:
        return "❌ nodes 为空。每行一个节点，例如：网关:gateway\\n数据库:postgres"
    if len(node_lines) > 40 or len(edge_lines) > 80:
        return "❌ 节点最多 40 个、连线最多 80 条。"

    direction = (direction or "LR").upper()
    if direction not in ("LR", "RL", "TB", "BT"):
        direction = "LR"
    p, _ = _diagram_paths(title, out_path)

    dot = _dot_exe()
    if not dot:
        try:
            return _render_diagram_fallback(node_lines, edge_lines, title, p, direction)
        except Exception as e:
            return f"❌ 后备渲染也失败了：{type(e).__name__}: {e}"

    os.environ["PATH"] = os.path.dirname(dot) + os.pathsep + os.environ.get("PATH", "")
    try:
        from diagrams import Diagram, Node, Edge
    except Exception as e:
        return f"❌ diagrams 未安装：{type(e).__name__}"

    try:
        with Diagram(title or "架构图", filename=os.path.splitext(p)[0], show=False,
                     direction=direction, outformat="png"):
            made = {}
            for line in node_lines:
                if ":" in line:
                    key, label = line.split(":", 1)
                else:
                    key, label = line, line
                key = re.sub(r'\W+', "_", key.strip()) or "node"
                made[key] = Node(label.strip())
            for line in edge_lines:
                parts = [x.strip() for x in re.split(r'\s*-{1,2}>\s*', line) if x.strip()]
                if len(parts) < 2:
                    continue
                a = re.sub(r'\W+', "_", parts[0])
                b = re.sub(r'\W+', "_", parts[1])
                if a in made and b in made:
                    made[a] >> Edge() >> made[b]
        return f"✅ 已生成架构图：{p}"
    except Exception as e:
        return f"❌ 渲染失败（可能是 Graphviz 未就绪）：{type(e).__name__}: {e}"


_STATIC_SERVERS = {}


def _start_static_server(key, dist_dir, port):
    """在本机起一个静态文件服务，供白板/数据库建模这类前端插件使用。"""
    existing = _STATIC_SERVERS.get(key)
    if existing and existing["thread"].is_alive():
        return existing["url"]
    dist = os.path.abspath(dist_dir)
    if not os.path.exists(os.path.join(dist, "index.html")):
        return f"❌ 未找到构建产物：{dist}（可能还没 build）"

    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = functools.partial(SimpleHTTPRequestHandler, directory=dist)
    for attempt in range(10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            port += 1
    else:
        return "❌ 本地端口都被占用了。"
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    _STATIC_SERVERS[key] = {"thread": thread, "server": server, "url": url}
    return url


def plugin_open_drawdb(open_browser: bool = True):
    """启动本地数据库建模工具 DrawDB，返回可打开的本地地址。"""
    dist = os.path.join(PLUGIN_LIB, "drawdb", "dist")
    res = _start_static_server("drawdb", dist, 8756)
    if res.startswith("❌"):
        return res
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(res)
        except Exception:
            pass
    return f"✅ DrawDB 已启动：{res}"


def plugin_open_excalidraw(open_browser: bool = True):
    """启动本地白板工具 Excalidraw，返回可打开的本地地址。"""
    dist = os.path.join(PLUGIN_LIB, "excalidraw", "excalidraw-app", "build")
    res = _start_static_server("excalidraw", dist, 8758)
    if res.startswith("❌"):
        return res
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(res)
        except Exception:
            pass
    return f"✅ Excalidraw 已启动：{res}"


# ------------------------------------------------------------ SolidWorks sidecar

_SW_LOCK = threading.Lock()
_SW_PROC = None


def _sw_client():
    """惰性拉起 Millwright 的 Python sidecar，复用一条 COM 连接。"""
    global _SW_PROC
    if _SW_PROC is not None and _SW_PROC.poll() is None:
        return _SW_PROC
    if not os.path.isdir(SW_SIDECAR_DIR):
        raise RuntimeError(
            "Millwright sidecar 目录不存在：" + SW_SIDECAR_DIR
            + "（检查 ALICORE_PLUGIN_LIB 是否指向插件库）"
        )
    env = dict(os.environ)
    env["PYTHONPATH"] = SW_SIDECAR_DIR + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "sw_agent"],
            cwd=SW_SIDECAR_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except Exception as e:
        raise RuntimeError(f"拉起 sidecar 失败：{e}")
    try:
        proc.stdout.readline()  # ready 帧，先吞掉
    except Exception:
        pass
    _SW_PROC = proc
    return proc


def _sw_rpc(method, params=None, timeout=180):
    """往 sidecar 发一行 JSON-RPC，等同一个 id 且带 ok 的帧回来。"""
    global _SW_PROC
    with _SW_LOCK:
        try:
            proc = _sw_client()
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "code": "NO_SIDECAR"}
        req = {"id": int(time.time() * 1000), "method": method, "params": params or {}}
        try:
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as e:
            _SW_PROC = None
            return {"ok": False, "error": f"sidecar 已断开：{e}"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = proc.stdout.readline()
            except Exception as e:
                _SW_PROC = None
                return {"ok": False, "error": f"读 sidecar 失败：{e}"}
            if not line:
                _SW_PROC = None
                return {"ok": False, "error": "sidecar 进程意外退出"}
            try:
                obj = json.loads(line.strip())
            except Exception:
                continue
            if obj.get("id") == req["id"] and "ok" in obj:
                return obj
        _SW_PROC = None
        return {"ok": False, "error": "sidecar 调用超时"}


def _sw_call(name, args):
    if not os.path.exists(SW_EXE):
        return "❌ 未检测到 SolidWorks，插件尚未就绪。设 ALICORE_SW_EXE 指向 SLDWORKS.exe。"
    if not os.path.isdir(SW_SIDECAR_DIR):
        return ("❌ 缺少 Millwright sidecar：" + SW_SIDECAR_DIR
                + "\n把插件库放到 app/plugins，或设 ALICORE_PLUGIN_LIB 指向插件库根目录。")
    r = _sw_rpc("call", {"name": name, "args": args})
    if not r.get("ok"):
        code = r.get("code")
        err = r.get("error")
        hint = {
            "NO_CONNECTION": "SolidWorks 没在运行，请先手动打开 SolidWorks 再试。",
            "NO_DOCUMENT": "SolidWorks 里没有打开的文档，先新建或打开一个零件。",
            "NO_SIDECAR": "sidecar 未就绪，检查插件库路径与 ALICORE_PLUGIN_LIB。",
        }.get(code, "")
        msg = f"❌ {name} 失败（{code or '未知错误'}）：{err}"
        return msg + (f"｜{hint}" if hint else "")
    data = r.get("data")
    if isinstance(data, dict):
        clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
        return json.dumps(clean, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


def plugin_sw_status():
    """查 SolidWorks 是否连接、版本、当前打开的文档。"""
    return _sw_call("sw_status", {})


def plugin_sw_new_part():
    """在 SolidWorks 里新建一个零件文档。"""
    return _sw_call("new_part", {})


def plugin_sw_sketch_rect(plane: str = "top", width_mm: float = 0, height_mm: float = 0,
                          x_mm: float = 0, y_mm: float = 0):
    """在基准面上画矩形草图。plane=top/front/right。"""
    if width_mm <= 0 or height_mm <= 0:
        return "❌ width_mm 和 height_mm 必须大于 0。"
    start = _sw_call("start_sketch", {"plane": plane})
    if start.startswith("❌"):
        return start
    return _sw_call("sketch_rectangle", {"x": x_mm, "y": y_mm, "width": width_mm, "height": height_mm})


def plugin_sw_sketch_circle(plane: str = "top", diameter_mm: float = 0,
                            x_mm: float = 0, y_mm: float = 0):
    """在基准面上画圆草图。plane=top/front/right，diameter_mm 是直径。"""
    if diameter_mm <= 0:
        return "❌ diameter_mm 必须大于 0。"
    start = _sw_call("start_sketch", {"plane": plane})
    if start.startswith("❌"):
        return start
    return _sw_call("sketch_circle", {"x": x_mm, "y": y_mm, "radius": diameter_mm / 2})


def plugin_sw_extrude(depth_mm: float = 0, flip: bool = False):
    """把当前草图拉伸成实体。"""
    if depth_mm <= 0:
        return "❌ depth_mm 必须大于 0。"
    return _sw_call("extrude", {"depth": depth_mm, "flip": flip})


def plugin_sw_cut_extrude(depth_mm: float = 0, through_all: bool = False):
    """用当前草图拉伸切除。through_all=True 表示完全贯穿。"""
    args = {"through_all": through_all}
    if depth_mm > 0:
        args["depth"] = depth_mm
    return _sw_call("cut_extrude", args)


def plugin_sw_mass_properties():
    """读当前模型的质量/体积/表面积/重心。"""
    return _sw_call("mass_properties", {})


def plugin_sw_list_features(limit: int = 0):
    """列当前零件的特征树（名称/类型/是否压缩）。"""
    args = {}
    if limit > 0:
        args["limit"] = limit
    return _sw_call("list_features", args)


def plugin_sw_save_as(path: str):
    """按扩展名保存/导出当前文档（sldprt/step/stl/pdf/dxf 等）。"""
    if not path:
        return "❌ 需要给出目标绝对路径（含扩展名）。"
    return _sw_call("save_as", {"path": path})


def plugin_sw_set_view(orientation: str = "isometric"):
    """切换视图方向，方便人工核对模型。"""
    return _sw_call("set_view_orientation", {"orientation": orientation})


def plugin_sw_start_sketch(plane: str = "top", face: str = ""):
    """显式进入草图编辑状态。plane=top/front/right（首个草图用）；face=top/bottom/front/back/left/right（在现有实体的面上画）。"""
    # start_sketch 在已有活跃草图时会静默失败；先退出再开，保证它真的是「开新草图」。
    _sw_call("exit_sketch", {})
    if face:
        return _sw_call("start_sketch", {"face": face})
    return _sw_call("start_sketch", {"plane": plane})


def plugin_sw_exit_sketch():
    """退出当前草图。拉伸/切除前若提示草图未闭合，先退出草图再试。"""
    return _sw_call("exit_sketch", {})


def plugin_sw_revolve(angle_deg: float = 360, cut: bool = False):
    """把当前草图绕草图里的中心线旋转成实体。cut=True 表示旋转切除（退刀槽/环槽）。画螺钉杆和头部必须用它。"""
    return _sw_call("revolve", {"angle": angle_deg, "cut": cut})


def _sw_parse_steps_text(text):
    """把「工具 参数=值」文本解析成 sidecar 的 steps 数组。

    sidecar 自带的 steps_text 解析器按空格切参数，points="0,0 1,0" 会被切坏；
    这里用 shlex 保留引号内的空格，再自己转成正确类型。
    """
    steps = []
    for raw in re.split(r"\||\n", text or ""):
        line = raw.strip()
        if not line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        if not tokens:
            continue
        params = {}
        for token in tokens[1:]:
            if "=" not in token:
                continue
            key, val = token.split("=", 1)
            low = val.lower()
            if low in ("true", "false"):
                params[key] = low == "true"
                continue
            try:
                params[key] = int(val)
            except ValueError:
                try:
                    params[key] = float(val)
                except ValueError:
                    params[key] = val
        steps.append({"tool": tokens[0], "params": params})
    return steps


def plugin_sw_build_part(steps_text: str = "", part: str = ""):
    """一次提交整件建模步骤，按顺序执行，失败即停并报第几步——复杂件优先用这个，不要一步一步调。
steps_text 每行或每段一个步骤，格式「工具 参数=值」，用 | 或换行分隔；带空格的参数用双引号包住。
例：start_sketch plane=top | sketch_rectangle x=-20 y=-15 width=40 height=30 | extrude depth=10
旋转件例：start_sketch plane=front | sketch_centerline x1=0 y1=0 x2=0 y2=-10 | sketch_polyline points="0,0 1,0 1,-6 1.9,-6 1.9,-8 0,-8" | revolve angle=360"""
    text = (steps_text or "").strip()
    if not text:
        return "❌ 需要 steps_text：按「工具 参数=值」给出整件步骤，用 | 或换行分隔。"
    steps = _sw_parse_steps_text(text)
    if not steps:
        return "❌ steps_text 解析为空，检查格式是否为「工具 参数=值」。"
    # 如果当前已有草图开着，batch 里的第一个 start_sketch 会静默失败；
    # 先在 batch 外退出，让「开新草图」这个动作成立（不能把 exit_sketch 放进 batch，
    # sidecar 的预检只按 batch 内状态判断，会误报「前面没有活跃草图」）。
    try:
        st = json.loads(_sw_call("edit_state", {}))
        if st.get("editing_sketch") and any(s["tool"] == "start_sketch" for s in steps):
            _sw_call("exit_sketch", {})
    except Exception:
        pass
    args = {"steps": steps}
    if part:
        args["part"] = part
    return _sw_call("build_part", args)


# ------------------------------------------------------------------ 插件定义

@dataclass
class PluginSpec:
    id: str
    name: str
    category: str
    description: str
    tool_names: list = field(default_factory=list)
    default_enabled: bool = False
    source: str = ""

    def ready(self):
        raise NotImplementedError

    def status(self):
        raise NotImplementedError


class SkillsPlugin(PluginSpec):
    def ready(self):
        return any(os.path.isdir(r) for r in _skill_roots())

    def status(self):
        return "就绪" if self.ready() else "技能目录缺失"


class ModelingPlugin(PluginSpec):
    def ready(self):
        return (importlib.util.find_spec("cadquery") is not None
                and importlib.util.find_spec("trimesh") is not None)

    def status(self):
        parts = []
        parts.append("cadquery " + ("已装" if importlib.util.find_spec("cadquery") else "未装"))
        parts.append("trimesh " + ("已装" if importlib.util.find_spec("trimesh") else "未装"))
        return " / ".join(parts)


class OcrPlugin(PluginSpec):
    def ready(self):
        return bool(_umi_exe())

    def status(self):
        return "就绪" if self.ready() else "未解压（缺 Umi-OCR.exe）"


class DeepseekOcrPlugin(PluginSpec):
    def ready(self):
        return _ollama_running() and _ollama_model_ready(DOC_OCR_MODEL)

    def status(self):
        if not _ollama_running():
            return "Ollama 未运行"
        if not _ollama_model_ready(DOC_OCR_MODEL):
            return "未拉取 deepseek-ocr 模型"
        return "就绪"


class BlenderPlugin(PluginSpec):
    def ready(self):
        return _blender_mpfb_ready()

    def status(self):
        if not os.path.exists(BLENDER_EXE):
            return "Blender 未装（设 ALICORE_BLENDER_EXE 指向 blender.exe）"
        if not os.path.exists(os.path.join(_mpfb_ext_root(), "__init__.py")):
            return "MPFB2 扩展未装"
        return "就绪"


class BlenderVRMPlugin(PluginSpec):
    def ready(self):
        return _blender_vrm_ready()

    def status(self):
        if not os.path.exists(BLENDER_EXE):
            return "Blender 未装（设 ALICORE_BLENDER_EXE 指向 blender.exe）"
        if not os.path.exists(os.path.join(_vrm_ext_root(), "__init__.py")):
            return "VRM addon 未装"
        return "就绪"


class PendingPlugin(PluginSpec):
    def __init__(self, id, name, category, description, reason):
        super().__init__(id=id, name=name, category=category, description=description)
        self.reason = reason

    def ready(self):
        return False

    def status(self):
        return self.reason


class DiagramPlugin(PluginSpec):
    def ready(self):
        """有 Graphviz 走真布局；没有就用 Pillow 后备，所以只要 Pillow 在就算就绪。"""
        return importlib.util.find_spec("PIL") is not None

    def status(self):
        if _dot_exe():
            return "就绪（Graphviz 真布局）"
        if importlib.util.find_spec("PIL") is not None:
            return "就绪（Pillow 后备，装 Graphviz 可提升布局）"
        return "未装 Pillow，也没检测到 Graphviz"


class DistillPlugin(PluginSpec):
    def ready(self):
        return os.path.exists(DISTILL_EXE)

    def status(self):
        return "就绪" if self.ready() else "未构建 distill.exe"


class WebAppPlugin(PluginSpec):
    def __init__(self, id, name, category, description, dist_rel, tools):
        super().__init__(id=id, name=name, category=category, description=description,
                         tool_names=tools)
        self.dist = os.path.join(PLUGIN_LIB, dist_rel)

    def ready(self):
        return os.path.exists(os.path.join(self.dist, "index.html"))

    def status(self):
        return "已打包" if self.ready() else "未构建（缺 dist/index.html）"


class SolidWorksPlugin(PluginSpec):
    def ready(self):
        """三样齐了才算就绪：exe、sidecar、COM 桥。

        旧版只看 exe 在不在，结果侧栏显示「就绪」，点了却因为 sidecar 目录缺失
        直接报 Popen 失败——这种假阳性比诚实地说「未就绪」更误导人。
        """
        return (bool(SW_EXE) and os.path.exists(SW_EXE)
                and os.path.isdir(SW_SIDECAR_DIR)
                and importlib.util.find_spec("win32com") is not None)

    def status(self):
        parts = []
        parts.append("SolidWorks " + ("已装" if SW_EXE and os.path.exists(SW_EXE) else "未检测到"))
        parts.append("sidecar " + ("就绪" if os.path.isdir(SW_SIDECAR_DIR) else "缺失"))
        parts.append("pywin32 " + ("已装" if importlib.util.find_spec("win32com") else "未装"))
        return " / ".join(parts)


def build_plugins():
    """开源版只带能直接跑起来的几个插件。

    三维建模（cadquery/trimesh）、Umi-OCR、distill、supermemory 这几类要么依赖重、
    要么需要额外编二进制或后端，开源用户装不上只会看到一片「未就绪」，所以默认不带。
   它们的实现仍在 plugins.py 里，需要的人把对应那一项加回下面的列表即可。
    """
    builtin = [
        SkillsPlugin(
            id="skills",
            name="技能库",
            category="能力",
            description="搜索并读取 skills / superpowers 本地技能，零 token。",
            tool_names=["plugin_search_skills", "plugin_read_skill"],
            source="skills + superpowers",
        ),
        SolidWorksPlugin(
            id="solidworks",
            name="SolidWorks 直驱",
            category="建模",
            description="阿鲤直驱 SolidWorks 2020：画草图、拉伸/切除、查质量、导出。",
            tool_names=[
                "plugin_sw_status", "plugin_sw_new_part",
                "plugin_sw_sketch_rect", "plugin_sw_sketch_circle",
                "plugin_sw_extrude", "plugin_sw_cut_extrude",
                "plugin_sw_start_sketch", "plugin_sw_exit_sketch", "plugin_sw_revolve",
                "plugin_sw_build_part",
                "plugin_sw_mass_properties", "plugin_sw_list_features",
                "plugin_sw_save_as", "plugin_sw_set_view",
            ],
            source="Millwright sidecar",
        ),
        DeepseekOcrPlugin(
            id="doc_ocr",
            name="文档 OCR",
            category="工具",
            description="本地 DeepSeek-OCR 把文档/扫描件/公式截图转 Markdown，零 token，用完即释放显存。",
            tool_names=["plugin_doc_ocr"],
            source="DeepSeek-OCR + Ollama",
        ),
        BlenderPlugin(
            id="blender_human",
            name="Blender 人物建模",
            category="建模",
            description="本地 Blender + MPFB2 参数化生成人体（性别/皮肤/发型），导出 GLB/OBJ，零 token。",
            tool_names=["plugin_blender_human"],
            source="Blender 5.0 + MPFB2",
        ),
        BlenderVRMPlugin(
            id="vrm",
            name="VRM 二次元角色",
            category="建模",
            description="本地 Blender + VRM addon 导入二次元 VRM(1.0)，三渲二渲染预览图，导出 GLB，零 token。",
            tool_names=["plugin_vrm_render"],
            source="Blender 5.0 + VRM addon",
        ),
        DiagramPlugin(
            id="diagrams",
            name="架构图",
            category="绘图",
            description="用代码生成系统架构拓扑图。",
            tool_names=["plugin_architecture_diagram"],
            source="diagrams + Graphviz",
        ),
        WebAppPlugin(
            id="excalidraw",
            name="白板",
            category="绘图",
            description="手绘风白板，画流程图/草图。",
            dist_rel=os.path.join("excalidraw", "excalidraw-app", "build"),
            tools=["plugin_open_excalidraw"],
        ),
        WebAppPlugin(
            id="drawdb",
            name="数据库建模",
            category="绘图",
            description="拖拽建表、连关系并生成 SQL。",
            dist_rel=os.path.join("drawdb", "dist"),
            tools=["plugin_open_drawdb"],
        ),
    ]
    return builtin + _load_external_specs(PLUGIN_LIB)


class ExternalPlugin(PluginSpec):
    """plugins/ext/*.py 里加载的用户插件。

    模块约定（除 PLUGIN / TOOLS 外都可选）：

        PLUGIN = {"id": "my_plugin", "name": "我的插件",
                  "category": "工具", "description": "做点什么"}
        TOOLS  = [{"name": "my_tool", "description": "给模型看的说明",
                   "func": my_func, "json_visible": False, "tags": ("关键词",)}]

        def ready():  return True        # 可选，缺省视为就绪
        def status(): return "就绪"       # 可选

    加载失败（语法错、字段缺、func 不可调用）会被静默跳过，不影响内置插件。
    """

    def __init__(self, meta, tools, module):
        super().__init__(
            id=str(meta["id"]),
            name=str(meta.get("name") or meta["id"]),
            category=str(meta.get("category") or "扩展"),
            description=str(meta.get("description") or ""),
            tool_names=[str(t["name"]) for t in tools],
            source=str(meta.get("source") or "ext"),
        )
        self._module = module
        self._tools = tools

    def ready(self):
        fn = getattr(self._module, "ready", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        return True

    def status(self):
        fn = getattr(self._module, "status", None)
        if callable(fn):
            try:
                return str(fn())
            except Exception as e:
                return f"状态检查失败：{e}"
        return "就绪" if self.ready() else "未就绪"


def _load_external_specs(lib_dir):
    """扫 ext 目录下的 *.py，按约定转成 ExternalPlugin。

    两个来源都扫：插件库的 ext，以及 app 自带的 plugins/ext（放示例和随仓库分发的插件）。
    去重按 id，先扫插件库、再扫 app 目录。
    """
    roots = []
    for r in (os.path.join(lib_dir, "ext"), os.path.join(_APP_DIR, "plugins", "ext")):
        r = os.path.abspath(r)
        if r not in roots and os.path.isdir(r):
            roots.append(r)
    if not roots:
        return []
    out, seen = [], set()
    for ext in roots:
        for fname in sorted(os.listdir(ext)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(ext, fname)
            modname = "alicore_ext_" + os.path.splitext(fname)[0]
            try:
                mspec = importlib.util.spec_from_file_location(modname, path)
                if mspec is None or mspec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(mspec)
                mspec.loader.exec_module(mod)
            except Exception:
                continue
            meta = getattr(mod, "PLUGIN", None)
            tools = getattr(mod, "TOOLS", None)
            if not isinstance(meta, dict) or not isinstance(tools, (list, tuple)):
                continue
            pid = str(meta.get("id") or "").strip()
            if not pid or pid in seen:
                continue
            valid = []
            for t in tools:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name") or "").strip()
                if not name or not callable(t.get("func")):
                    continue
                valid.append(t)
            if not valid:
                continue
            seen.add(pid)
            out.append(ExternalPlugin(meta, valid, mod))
    return out


# ------------------------------------------------------------------ 管理器

class PluginManager:
    def __init__(self, base_dir=None):
        self.base_dir = base_dir
        self.state_path = os.path.join(base_dir, "plugins_state.json") if base_dir else STATE_FILE
        self.specs = {p.id: p for p in build_plugins()}
        self.state = self._load()
        self.registry = None

    def _load(self):
        state = {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state = data
        except Exception:
            pass
        out = {}
        for pid, spec in self.specs.items():
            if pid in state:
                out[pid] = bool(state[pid])
            else:
                out[pid] = spec.default_enabled
        return out

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def view(self):
        out = []
        for spec in self.specs.values():
            out.append({
                "id": spec.id,
                "name": spec.name,
                "category": spec.category,
                "description": spec.description,
                "source": spec.source,
                "enabled": self.state.get(spec.id, False),
                "ready": spec.ready(),
                "status": spec.status(),
                "tools": spec.tool_names,
            })
        return out

    def is_enabled(self, plugin_id):
        return bool(self.state.get(plugin_id, False))

    def set_enabled(self, plugin_id, enabled):
        spec = self.specs.get(plugin_id)
        if not spec:
            return False, "❌ 未知插件"
        if enabled and not spec.ready():
            return False, f"❌ {spec.name} 尚未就绪：{spec.status()}"
        self.state[plugin_id] = bool(enabled)
        self._save()
        if self.registry:
            for tool in spec.tool_names:
                self.registry.set_enabled(tool, bool(enabled))
        return True, "已开启" if enabled else "已关闭"

    def bind(self, registry):
        self.registry = registry

    def register_all(self, registry):
        """把全部插件工具注册进工具表，按当前开关设初始可见性。"""
        self.bind(registry)
        registry.register(
            "plugin_search_skills",
            "搜本地技能库，返回技能名和相对路径",
            plugin_search_skills,
            json_visible=False,
            tags=("技能", "skill", "插件", "学", "方法"),
            enabled=self.is_enabled("skills"),
        )
        registry.register(
            "plugin_read_skill",
            "读技能库里的某个技能文件（用 plugin_search_skills 给的相对路径）",
            plugin_read_skill,
            json_visible=False,
            tags=("技能", "skill", "插件", "学", "方法"),
            enabled=self.is_enabled("skills"),
        )
        registry.register(
            "plugin_cad_primitive",
            "生成参数化基础 3D 零件并导出 STEP。kind=box/cylinder/sphere/wedge，size 写 长,宽,高",
            plugin_cad_primitive,
            json_visible=False,
            tags=("建模", "零件", "cad", "step", "3d", "三维", "模型"),
            enabled=self.is_enabled("modeling"),
        )
        registry.register(
            "plugin_mesh_info",
            "读 3D 网格文件，返回尺寸/体积/面积/面数/是否水密",
            plugin_mesh_info,
            json_visible=False,
            tags=("网格", "stl", "obj", "glb", "3d", "三维", "模型", "分析"),
            enabled=self.is_enabled("modeling"),
        )
        sw_tags = ("sw", "solidworks", "solid", "零件", "草图", "拉伸", "切除",
                   "旋转", "整件", "机械", "cad", "三维建模", "特征", "质量", "导出step")
        registry.register(
            "plugin_sw_status",
            "查 SolidWorks 是否连接、版本与当前文档",
            plugin_sw_status,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_new_part",
            "在 SolidWorks 里新建零件",
            plugin_sw_new_part,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_sketch_rect",
            "在 SolidWorks 基准面上画矩形草图（plane=top/front/right）",
            plugin_sw_sketch_rect,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_sketch_circle",
            "在 SolidWorks 基准面上画圆草图（直径）",
            plugin_sw_sketch_circle,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_extrude",
            "把 SolidWorks 当前草图拉伸成实体",
            plugin_sw_extrude,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_cut_extrude",
            "用 SolidWorks 当前草图拉伸切除（支持完全贯穿）",
            plugin_sw_cut_extrude,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_start_sketch",
            "显式进入 SolidWorks 草图编辑状态（plane 基准面 / face 现有实体面）",
            plugin_sw_start_sketch,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_exit_sketch",
            "退出 SolidWorks 当前草图（拉伸/切除前草图未闭合时用）",
            plugin_sw_exit_sketch,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_revolve",
            "把 SolidWorks 当前草图绕中心线旋转成实体/旋转切除（angle_deg，cut）",
            plugin_sw_revolve,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_build_part",
            "一次提交整件 SolidWorks 建模步骤（复杂件优先）：steps_text 每行/每段『工具 参数=值』，用 | 或换行分隔，"
            "带空格的参数用双引号；例：start_sketch plane=top | sketch_rectangle x=-20 y=-15 width=40 height=30 | extrude depth=10。"
            "六角孔用 6 条 sketch_line 画，不要用 sketch_polygon。按顺序执行，失败即停并报第几步。",
            plugin_sw_build_part,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_mass_properties",
            "读 SolidWorks 当前模型的质量/体积/表面积/重心",
            plugin_sw_mass_properties,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_list_features",
            "列 SolidWorks 当前零件的特征树",
            plugin_sw_list_features,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_save_as",
            "把 SolidWorks 当前文档按扩展名保存/导出（sldprt/step/stl/pdf…）",
            plugin_sw_save_as,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_sw_set_view",
            "切换 SolidWorks 视图方向（等轴测/前视等）",
            plugin_sw_set_view,
            json_visible=False,
            tags=sw_tags,
            enabled=self.is_enabled("solidworks"),
        )
        registry.register(
            "plugin_ocr_image",
            "本地 OCR 识别图片文字，返回文本（离线、零 token）",
            plugin_ocr_image,
            json_visible=False,
            tags=("ocr", "识别", "文字", "图片", "截图", "识字", "扫描"),
            enabled=self.is_enabled("ocr"),
        )
        registry.register(
            "plugin_ocr_qrcode",
            "识别图片里的二维码内容",
            plugin_ocr_qrcode,
            json_visible=False,
            tags=("二维码", "qr", "扫码", "识别"),
            enabled=self.is_enabled("ocr"),
        )
        registry.register(
            "plugin_doc_ocr",
            "本地 DeepSeek-OCR 识别文档图片并输出 Markdown（传本地图片绝对路径）",
            plugin_doc_ocr,
            json_visible=False,
            tags=("ocr", "文档", "扫描", "公式", "markdown", "识别文字", "转文字", "截图识字"),
            enabled=self.is_enabled("doc_ocr"),
        )
        registry.register(
            "plugin_blender_human",
            "本地 Blender+MPFB2 生成参数化人体并导出 GLB/OBJ。gender=female/male，skin=皮肤名，hair=发型名，都可不传",
            plugin_blender_human,
            json_visible=False,
            tags=("建模", "人物", "人体", "blender", "捏人", "角色", "3d", "三维", "模型", "导出glb"),
            enabled=self.is_enabled("blender_human"),
        )
        registry.register(
            "plugin_vrm_render",
            "本地 Blender+VRM 导入二次元 VRM(1.0) 模型，三渲二渲染预览图并导出 GLB。vrm_path=本地.vrm文件绝对路径",
            plugin_vrm_render,
            json_visible=False,
            tags=("vrm", "二次元", "动漫", "三渲二", "模型", "角色", "3d", "三维", "渲染", "预览"),
            enabled=self.is_enabled("vrm"),
        )
        registry.register(
            "plugin_memory_store",
            "把一条事实/偏好/约束写入本地去重记忆库",
            plugin_memory_store,
            json_visible=False,
            tags=("记忆", "记住", "记住这", "记下来", "约束", "偏好", "规则"),
            enabled=self.is_enabled("distill"),
        )
        registry.register(
            "plugin_memory_recall",
            "按相关度+新鲜度召回本地记忆",
            plugin_memory_recall,
            json_visible=False,
            tags=("记忆", "回想", "以前", "还记得", "查记忆"),
            enabled=self.is_enabled("distill"),
        )
        registry.register(
            "plugin_memory_stats",
            "查看本地记忆库统计",
            plugin_memory_stats,
            json_visible=False,
            tags=("记忆", "统计", "记忆库"),
            enabled=self.is_enabled("distill"),
        )
        registry.register(
            "plugin_architecture_diagram",
            "用 nodes/edges 生成系统架构拓扑图 PNG。nodes 每行「名称:备注」，edges 每行「A -> B」",
            plugin_architecture_diagram,
            json_visible=False,
            tags=("架构图", "拓扑", "diagram", "流程", "关系图", "绘图"),
            enabled=self.is_enabled("diagrams"),
        )
        registry.register(
            "plugin_open_drawdb",
            "启动本地数据库建模工具 DrawDB 并返回地址",
            plugin_open_drawdb,
            json_visible=False,
            tags=("数据库", "建表", "sql", "er图", "建模", "drawdb"),
            enabled=self.is_enabled("drawdb"),
        )
        registry.register(
            "plugin_open_excalidraw",
            "启动本地白板 Excalidraw 并返回地址",
            plugin_open_excalidraw,
            json_visible=False,
            tags=("白板", "草图", "流程图", "excalidraw", "画图", "手绘"),
            enabled=self.is_enabled("excalidraw"),
        )
        # 外部插件：plugins/ext/*.py 里声明的工具在这里动态注册
        for spec in self.specs.values():
            if not isinstance(spec, ExternalPlugin):
                continue
            for t in spec._tools:
                registry.register(
                    str(t["name"]),
                    str(t.get("description") or spec.description or spec.name),
                    t["func"],
                    json_visible=bool(t.get("json_visible", False)),
                    tags=tuple(t.get("tags") or ()),
                    enabled=self.is_enabled(spec.id),
                )
        return registry


def install_plugin_tools(registry, base_dir=None):
    """构建插件管理器并挂到注册表。"""
    manager = PluginManager(base_dir)
    manager.register_all(registry)
    return manager
