"""原子写工具。

为什么必须原子：config.json / secrets.json / chat_history.json 都是「一坏就完」的文件。
普通 open("w") 是先截断再写，写到一半断电或崩溃，文件就剩空/半截——不是丢一次修改，是丢全部。
做法照 DSH 的 dsh-atomic-write：同目录临时文件 → os.replace 覆盖（同盘 rename 是原子的）。
不做 fsync：那是「既原子又持久」，成本高；这里只要「永远读不到半截」。
"""

import json
import os
import tempfile
import time


def atomic_write_text(path, text, mode=0o644):
    """把 text 原子地写到 path。失败时目标文件保持原样，临时文件被清掉。"""
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix="_" + os.path.basename(path))
    try:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text if isinstance(text, str) else str(text))
            f.flush()
        # Windows 上目标被占用 / 杀软扫描时会短暂拒绝 rename，有界重试
        last = None
        for i in range(6):
            try:
                os.replace(tmp, path)
                return path
            except OSError as e:
                last = e
                time.sleep(0.02 * (2 ** i))
        raise last
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data, mode=0o644, indent=2):
    return atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent), mode=mode)
