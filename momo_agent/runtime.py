"""执行环境抽象。

抄自 OpenHands：执行环境（Runtime）和 agent 分开，同一个接口两种实现——
DockerRuntime（断网容器，基站原来就有）和 LocalRuntime（本地受限 exec，快但不算安全边界）。
engine 只认 Runtime 接口，换环境不用改 agent 代码。
"""

import ast
import io
import json
import os
import shutil
import subprocess
import time
import uuid

def _docker_candidates():
    """按机器现查 Docker 位置，不写死某个账户的目录。

    先走 PATH；再试 Docker Desktop 的默认安装位置（用户级和系统级都看，
    新旧两版目录名不同：Programs\\DockerDesktop 与 Docker\\Docker）。
    """
    cands = ["docker"]
    local = os.environ.get("LOCALAPPDATA") or ""
    program_files = os.environ.get("ProgramFiles") or ""
    if local:
        cands.append(os.path.join(local, "Programs", "DockerDesktop",
                                  "resources", "bin", "docker.exe"))
    if program_files:
        cands.append(os.path.join(program_files, "Docker", "Docker",
                                  "resources", "bin", "docker.exe"))
    return cands


DOCKER_CANDIDATES = _docker_candidates()

# LocalRuntime 允许的内置函数：够写常规逻辑，但没有文件/进程/反射能力
_SAFE_BUILTINS = {
    "print": print, "len": len, "str": str, "int": int, "float": float,
    "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "range": range, "enumerate": enumerate, "zip": zip, "sorted": sorted,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "any": any, "all": all, "isinstance": isinstance,
    "True": True, "False": False, "None": None,
}

OUT_LIMIT = 3000
ERR_LIMIT = 1500

# ---- cocoon 静态闸（2026-11-21 自审补的洞）----
# 实测：cocoon 只对显式挂载路径打 ACL，没挂的地方（AppData、C 盘用户目录）
# 全按用户权限走——沙箱里 open(secrets.json) 能真读到，裸 socket 能真出网。
# 所以进 cocoon 的代码必须先过静态筛子，再在 wrapper 里用运行时白名单跑：
# 静态闸挡「明写」，白名单挡「别名绕」（f = open; f() 时 namespace 里根本没有 open），
# cocoon Job Object 兜底资源（死循环/内存/进程数）。
_SANDBOX_SAFE_MODULES = {
    "json", "math", "re", "random", "datetime", "time", "collections",
    "itertools", "functools", "statistics", "string", "decimal", "fractions",
    "heapq", "bisect", "array", "textwrap", "difflib", "uuid",
}

_SANDBOX_FORBIDDEN_CALLS = {
    "exec", "eval", "compile", "open", "__import__", "input",
    "getattr", "setattr", "delattr", "hasattr", "vars", "globals", "locals",
    "breakpoint",
}


def scan_sandbox_code(code):
    """cocoon 后端的静态筛子。返回 None 放行，返回字符串为拒绝原因。

    和 engine._scan_code 的分工：那边管 _run_code（引擎内 exec，运行时
    namespace 本来就没危险名）；这边管 cocoon（进程里 builtins 全开，
    静态闸+运行时白名单缺一不可）。规则保持一致：禁 dunder 属性访问、
    禁危险内建调用，另加 import 白名单（只放无文件/进程/网络能力的计算库）。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误：{e}"
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and node.attr.startswith("__") and node.attr.endswith("__")):
            return f"不许碰双下划线属性 .{node.attr}（提权链）"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _SANDBOX_FORBIDDEN_CALLS):
            return f"不许调用 {node.func.id}()"
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _SANDBOX_SAFE_MODULES:
                    return f"不许 import {a.name}（只放计算类标准库）"
        if isinstance(node, ast.ImportFrom):
            if node.level:
                return "不许相对导入"
            top = (node.module or "").split(".")[0]
            if top not in _SANDBOX_SAFE_MODULES:
                return f"不许 from {node.module} import（只放计算类标准库）"
    return None


# 进 cocoon 的 wrapper：用户代码不直接跑，先在沙箱进程里重建一份
# 白名单 builtins（含受限 __import__），再把用户代码塞进这个 namespace 执行。
# 即使静态闸被别名绕过（f = open; f()），运行时这里也没有 open 可拿。
_WRAPPER_SRC = '''
import builtins as _b
import importlib

_ALLOWED = _ALLOWED_MODULES_
_NAMES = _BUILTIN_NAMES_

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    # 按 __import__ 协议收参（import 语句会传 5 个），只认模块名；
    # 相对导入（level>0）静态闸已拦，这里再拒一次兜底。
    if level or name.split(".")[0] not in _ALLOWED:
        raise ImportError("sandbox: 模块 %s 不在白名单" % name)
    return importlib.import_module(name)

_w = {n: getattr(_b, n) for n in _NAMES}
_w["__import__"] = _safe_import

ns = {"__builtins__": _w}
exec(compile(_USER_CODE_, "<sandbox>", "exec"), ns)
'''


def _build_wrapper(user_code):
    """把用户代码嵌进 wrapper。repr() 嵌入保证任意字符串安全；
    占位符替换顺序固定，用户代码最后注入，不会污染前面的替换。"""
    names = sorted(k for k in _SAFE_BUILTINS if k not in ("True", "False", "None"))
    src = (_WRAPPER_SRC
           .replace("_ALLOWED_MODULES_", repr(sorted(_SANDBOX_SAFE_MODULES)))
           .replace("_BUILTIN_NAMES_", repr(names))
           .replace("_USER_CODE_", repr(user_code)))
    return src


def _truncate(text, limit):
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n...(截断)"
    return text


class BaseRuntime:
    name = "base"

    def available(self):
        return True

    def run_python(self, code, timeout=60):
        raise NotImplementedError


class DockerRuntime(BaseRuntime):
    name = "docker"

    def __init__(self, work_dir, temp_dir=None):
        self.work_dir = os.path.abspath(work_dir)
        self.temp_dir = os.path.abspath(temp_dir or self.work_dir)
        self._docker = None
        self._avail = None  # None=还没问过；缓存 docker info 的结果，别每轮都问

    def _find_docker(self):
        if self._docker:
            return self._docker
        for cand in DOCKER_CANDIDATES:
            if os.path.isabs(cand):
                found = cand if os.path.exists(cand) else None
            else:
                found = shutil.which(cand)
            if found:
                self._docker = found
                return found
        return None

    def available(self):
        """不只查 docker.exe 在不在，还要问一句 daemon 起没起。

        老版本只判断 exe 存在，Docker Desktop 装了但没启动时会报「可用」，
        真跑起来才失败——模型拿着假信号去调工具，白跑一轮。
        `docker info` 是唯一可靠的说法，8 秒超时够它返回，结果缓存住。
        """
        if self._avail is not None:
            return self._avail
        docker = self._find_docker()
        if not docker:
            self._avail = False
            return False
        try:
            r = subprocess.run(
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=8,
                encoding="utf-8", errors="replace",
            )
            self._avail = r.returncode == 0 and bool((r.stdout or "").strip())
        except Exception:
            self._avail = False
        return self._avail

    def run_python(self, code, timeout=60):
        docker = self._find_docker()
        if not docker:
            return "❌ 找不到 docker 命令：Docker Desktop 没装或没启动。"

        tmp_dir = os.path.join(self.temp_dir, "_sandbox_tmp")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            code_path = os.path.join(tmp_dir, "code.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return f"❌ 创建临时文件失败：{e}"

        mount = tmp_dir.replace("\\", "/")
        cmd = [
            docker, "run", "--rm", "--network", "none",
            "-m", "256m", "--cpus", "0.5",
            "-v", f"{mount}:/work", "-w", "/work",
            "python:3.11-slim", "python", "/work/code.py",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, encoding="utf-8", errors="replace")
            out, err = _truncate(proc.stdout, OUT_LIMIT), _truncate(proc.stderr, ERR_LIMIT)
            if proc.returncode == 0:
                return f"✅ 沙箱执行成功：\n{out}" if out else "✅ 执行成功（无输出）"
            return f"⚠️ 报错：\n{out}\n{err}"
        except subprocess.TimeoutExpired:
            return f"❌ 执行超时（{timeout}秒），已终止。"
        except Exception as e:
            return f"❌ 执行失败：{e}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class LocalRuntime(BaseRuntime):
    """本地受限 exec。

    注意（照抄 smolagents 的原话）：这不是安全边界，只挡了 import/open/eval，
    挡不住真正懂 Python 的攻击者。单人自用够用，别拿它跑不可信代码。
    """

    name = "local"

    def __init__(self, work_dir):
        self.work_dir = os.path.abspath(work_dir)

    def run_python(self, code, timeout=60):
        ns = {"__builtins__": dict(_SAFE_BUILTINS)}
        buf = io.StringIO()
        ns["print"] = lambda *a, **k: print(*a, file=buf, **k)

        t0 = time.time()
        err = ""
        try:
            exec(compile(code, "<base_station>", "exec"), ns)  # noqa: S102
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        cost = round(time.time() - t0, 2)

        out = _truncate(buf.getvalue(), OUT_LIMIT)
        if err:
            return f"⚠️ 本地执行报错（{cost}s）：\n{err}\n{out}"
        return f"✅ 本地执行成功（{cost}s）：\n{out}" if out else f"✅ 执行成功（{cost}s，无输出）"


class CocoonRuntime(BaseRuntime):
    """cocoon-sandbox 子进程沙箱：真隔离、真杀、资源限制。

    用 Job Object 做进程级隔离，接替 LocalRuntime 的裸 exec。为什么需要它：
    _run_code 的 AST 闸拦得住「模型直接写的代码」，但拦不住「模型写
    run_python("逃逸代码")」——那正是 LocalRuntime 裸 exec 的旁路。

    三层防线（2026-11-21 自审后重设计）：
    1. scan_sandbox_code 静态闸：禁 dunder、危险内建、非白名单 import；
    2. wrapper 运行时白名单：沙箱进程里重建受限 builtins + 受限 __import__，
       别名绕过（f = open; f()）也拿不到危险名字；
    3. cocoon Job Object：超时真杀、--mem 内存上限、--max-procs 进程数、
       cwd 指向临时目录避免隐式挂载基站目录。

    实测过的边界：裸 socket 挡不住（代理 sinkhole 只挡讲规矩的 HTTP 客户端）——
    所以 import 白名单必须把 socket/urllib 挡在门外，这是 socket 的唯一闸。
    cocoon 文件系统对未挂载路径全开（secrets.json 实测可读）——所以 open
    必须挡在静态闸+白名单两层里。
    """

    name = "cocoon"

    def __init__(self, work_dir, temp_dir=None, cocoon_bin=None):
        self.work_dir = os.path.abspath(work_dir)
        self.temp_dir = os.path.abspath(temp_dir or self.work_dir)
        self.cocoon_bin = cocoon_bin or os.path.join(self.work_dir, "cocoon", "cocoon.exe")
        self._avail = None

    def available(self):
        if self._avail is not None:
            return self._avail
        self._avail = os.path.exists(self.cocoon_bin)
        return self._avail

    def run_python(self, code, timeout=60):
        err = scan_sandbox_code(code)
        if err:
            return f"❌ 代码被静态闸拦下：{err}"

        if not self.available():
            return "❌ 找不到 cocoon.exe：沙箱后端缺失。"

        # 唯一子目录：并发调用互不干扰，finally 只删自己的
        tmp_dir = os.path.join(
            self.temp_dir, "_sandbox_tmp",
            f"{os.getpid()}_{uuid.uuid4().hex[:8]}")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            code_path = os.path.join(tmp_dir, "code.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(_build_wrapper(code))
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return f"❌ 创建临时文件失败：{e}"

        # --timeout 交给 cocoon 真杀（Job Object）；subprocess 只留 15 秒余量。
        # cwd 指向 tmp：cocoon 默认 --ro 挂「当前目录」，若继承基站的
        # D:\MomoDesktop 会隐式挂载整个基站目录（实测挂大目录要 140 秒）。
        cmd = [
            self.cocoon_bin, "run", "--runtime", "python",
            "--timeout", str(timeout), "--mem", "512", "--no-gpu",
            "--json", "-f", code_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout + 15, encoding="utf-8",
                                  errors="replace", cwd=tmp_dir)
            # 超时时 cocoon 会先往 stdout 打一行 WARN 日志再跟 JSON，
            # 直接 json.loads 整串会失败，取首个 { 到末个 } 之间的部分解析。
            raw = proc.stdout or ""
            s, e = raw.find("{"), raw.rfind("}")
            data = {}
            if s != -1 and e > s:
                try:
                    data = json.loads(raw[s:e + 1])
                except Exception:
                    data = {}
            out = _truncate(data.get("stdout", ""), OUT_LIMIT)
            err = _truncate(data.get("stderr", ""), ERR_LIMIT)
            if not data:
                # JSON 都没解析出来：把原始输出带回去，别只给一句空报错
                return f"⚠️ 沙箱返回异常（无法解析结果）：\n{_truncate(raw, ERR_LIMIT)}"
            if data.get("timed_out"):
                return f"❌ 执行超时（{timeout}秒），已终止。"
            if data.get("oom_killed"):
                return "❌ 内存超限，已终止。"
            if data.get("exit_code") == 0:
                return f"✅ 沙箱执行成功：\n{out}" if out else "✅ 执行成功（无输出）"
            return f"⚠️ 报错：\n{out}\n{err}"
        except subprocess.TimeoutExpired:
            return f"❌ 执行超时（{timeout}秒），已终止。"
        except Exception as e:
            return f"❌ 执行失败：{e}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class LazyRuntime(BaseRuntime):
    """启动时不去问 Docker 起没起，第一次真要跑代码时才问。

    实测 `docker info` 在 Docker 没启动时要白等 1.42 秒，而基站启动路径上
    根本用不到 runtime（`_run_code` 走的是自己的 _make_ns + 本地 exec，
    `run_python` 工具又是 json_visible=False 模型看不见）。
    换句话说这 1.42 秒是纯白花，全部落在用户等待窗口出现的时间上。
    """

    name = "auto"

    def __init__(self, work_dir, prefer_docker=True, temp_dir=None, cocoon_bin=None):
        self.work_dir = work_dir
        self.prefer_docker = prefer_docker
        self.temp_dir = temp_dir
        self.cocoon_bin = cocoon_bin
        self._real = None

    def available(self):
        return True

    def run_python(self, code, timeout=60):
        if self._real is None:
            self._real = make_runtime(
                self.work_dir,
                self.prefer_docker,
                temp_dir=self.temp_dir,
                cocoon_bin=self.cocoon_bin,
            )
        return self._real.run_python(code, timeout)


def make_runtime(work_dir, prefer_docker=True, temp_dir=None, cocoon_bin=None):
    """优先 Docker；Docker 不可用则走 cocoon 子进程沙箱；再不可用才回落本地 exec。"""
    if prefer_docker:
        d = DockerRuntime(work_dir, temp_dir=temp_dir)
        if d.available():
            return d
    c = CocoonRuntime(work_dir, temp_dir=temp_dir, cocoon_bin=cocoon_bin)
    if c.available():
        return c
    return LocalRuntime(work_dir)


def make_lazy_runtime(work_dir, prefer_docker=True, temp_dir=None, cocoon_bin=None):
    """给启动路径用：立刻返回，Docker 探测推到第一次 run_python。"""
    return LazyRuntime(work_dir, prefer_docker, temp_dir=temp_dir, cocoon_bin=cocoon_bin)
