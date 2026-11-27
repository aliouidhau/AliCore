"""运行路径边界：app / data / runtime 三块分开。

AliCore 默认把用户数据放到 %APPDATA%\\AliCore，不写进代码仓库：
  app_dir     代码和前端资源：momo_app.py、ui_v2.html、cocoon、插件库
  data_dir    用户数据：config、history、knowledge、workspace、events
  runtime_dir 可丢弃的运行产物：临时沙箱、日志、缓存、TCC

需要换目录时用环境变量（ALICORE_DATA_DIR / ALICORE_RUNTIME_DIR）或启动参数覆盖。
"""

import os
from dataclasses import dataclass


def default_data_dir():
    """用户数据目录：优先环境变量，否则 %APPDATA%\\AliCore。"""
    explicit = os.environ.get("ALICORE_DATA_DIR")
    if explicit:
        return os.path.abspath(explicit)
    roaming = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(roaming, "AliCore")


@dataclass(frozen=True)
class AppPaths:
    app_dir: str
    data_dir: str
    runtime_dir: str


def resolve_paths(app_dir, data_dir=None, runtime_dir=None):
    app_dir = os.path.abspath(app_dir)
    data_dir = os.path.abspath(
        data_dir or os.environ.get("ALICORE_DATA_DIR") or default_data_dir()
    )
    runtime_dir = os.path.abspath(
        runtime_dir
        or os.environ.get("ALICORE_RUNTIME_DIR")
        or os.path.join(data_dir, "_runtime")
    )
    return AppPaths(app_dir=app_dir, data_dir=data_dir, runtime_dir=runtime_dir)


def ensure_dirs(paths):
    """只建运行必需目录；已存在就跳过。"""
    for d in (
        paths.data_dir,
        os.path.join(paths.data_dir, "events"),
        os.path.join(paths.data_dir, "knowledge"),
        os.path.join(paths.data_dir, "workspace"),
        paths.runtime_dir,
        os.path.join(paths.runtime_dir, "logs"),
        os.path.join(paths.runtime_dir, "temp"),
    ):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    return paths
