"""momo_agent：基站重构后的 agent 包。

分层（抄自 OpenHands 的边界划分）：
    UI(pywebview/HTML)  →  Api(薄壳)  →  BaseStationAgent(引擎)  →  Runtime(执行环境)
UI 不执行任何动作，凭证只在后端，执行环境可切换（Docker / 本地）。

用法：
    from momo_agent import build_agent
    agent = build_agent(BASE_DIR)
    result = agent.chat("看看这个文件写了什么：D:\\MomoDesktop\\config.json")
"""

from __future__ import annotations

import os

from .config import Config
from .engine import BaseStationAgent, FinalAnswer
from .ledger import EventLedger
from .llm import LLMClient, LLMError
from .paths import resolve_paths, ensure_dirs
from .runtime import LocalRuntime, DockerRuntime, make_runtime, make_lazy_runtime
from .tools import ToolRegistry
from .tools_fs import register_fs_tools, configure_paths
from .tools_docs import register_doc_tools, configure_storage as configure_doc_storage
from .tools_sandbox import register_sandbox_tool
from .plugins import install_plugin_tools, configure_storage

__all__ = [
    "build_agent", "BaseStationAgent", "Config", "ToolRegistry",
    "make_runtime", "LocalRuntime", "DockerRuntime", "LLMClient", "LLMError",
    "FinalAnswer", "EventLedger",
]


def build_agent(base_dir, data_dir=None, runtime_dir=None, app_dir=None, safe_mode=False):
    """把配置、工具、运行时、模型、引擎串起来，返回一个可直接 chat 的 agent。

    base_dir 是旧入口，仍然兼容；新入口明确分成 app/data/runtime 三块：
      app_dir      代码和前端资源
      data_dir     配置、历史、记忆、工作区、事件账本
      runtime_dir  临时沙箱、日志、TCC 等可丢弃产物
    """
    base_dir = os.path.abspath(base_dir)
    app_dir = os.path.abspath(app_dir or base_dir)
    data_dir = os.path.abspath(data_dir or base_dir)
    runtime_dir = (runtime_dir or os.environ.get("ALICORE_RUNTIME_DIR")
                   or os.path.join(data_dir, "_runtime"))
    paths = ensure_dirs(resolve_paths(app_dir, data_dir, runtime_dir))

    cfg = Config(paths.data_dir)
    if safe_mode:
        # 安全模式只走 JSON + 只读工具；CodeAgent 的进程内 exec 不当作隔离边界。
        cfg.data["agent_mode"] = "json"

    # 用懒探测：Docker 起没起推到第一次 run_python 再问。
    # 启动路径上没人用 runtime，但 `docker info` 在 Docker 没启动时要白等 1.4 秒，
    # 那 1.4 秒直接压在「窗口出现」上。
    runtime = make_lazy_runtime(
        paths.data_dir,
        prefer_docker=bool(cfg.get("sandbox")),
        temp_dir=paths.runtime_dir,
        cocoon_bin=os.path.join(paths.app_dir, "cocoon", "cocoon.exe"),
    )

    # 文件工具的“记忆本”和基站代码保护目录跟着 app/data 走，不跟着旧硬编码。
    configure_paths(paths.data_dir, paths.app_dir)
    configure_storage(paths.data_dir)
    configure_doc_storage(paths.data_dir)

    registry = ToolRegistry(safe_mode=safe_mode)
    register_fs_tools(registry)
    register_doc_tools(registry)
    register_sandbox_tool(registry, runtime)
    plugins = install_plugin_tools(registry, paths.data_dir)

    ledger = EventLedger(os.path.join(paths.data_dir, "events"))
    llm = LLMClient(cfg)
    agent = BaseStationAgent(
        cfg, registry, runtime, llm, paths.data_dir,
        ledger=ledger, safe_mode=safe_mode,
    )
    # 插件管理器跟着 agent 走，Api 层靠 agent.plugins 拿到它
    agent.plugins = plugins
    agent.paths = paths
    return agent
