"""冒烟测试：不开窗口，只验证内核起得来。

CI 和本地都能跑：

    python tests/smoke.py

它会把数据目录指到临时目录，不碰你真实的 %APPDATA%\\AliCore。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 先隔离环境，再 import
tmp = tempfile.mkdtemp(prefix="alicore_smoke_")
os.environ["ALICORE_DATA_DIR"] = tmp
os.environ["ALICORE_SECRETS"] = os.path.join(tmp, "secrets.json")
os.environ["ALICORE_RUNTIME_DIR"] = os.path.join(tmp, "_runtime")

from ali_app import Api  # noqa: E402


def main():
    api = Api(safe_mode=False)

    cfg = api.get_config()
    assert cfg["agent_mode"] == "auto", f"agent_mode 应该是 auto，实际 {cfg.get('agent_mode')!r}"
    assert cfg["providers"] == [], "示例配置不该预置服务商"
    assert cfg["current_provider"] == "", "示例配置不该预选服务商"
    assert cfg["history_char_budget_effective"] > 0, "自动上下文预算没算出来"
    assert cfg["context_window"] == 131072, f"上下文默认值不对：{cfg.get('context_window')}"

    plugins = api.get_plugins()
    ids = [p["id"] for p in plugins]
    assert len(ids) >= 8, f"插件数量不对：{ids}"

    tools = api.get_tools()
    assert len(tools) >= 50, f"工具数量不对：{len(tools)}"

    reply = api.chat("你好")
    assert ("接入" in reply) or ("模型" in reply), f"没接模型时应该提示接入，实际：{reply!r}"

    assert not os.path.exists(os.environ["ALICORE_SECRETS"]), "没接模型却创建了 secrets.json"

    print(f"config      ok   自动预算 {cfg['history_char_budget_effective']:,} 字符")
    print(f"plugins     ok   {len(ids)} 个：{', '.join(ids)}")
    print(f"tools       ok   {len(tools)} 个")
    print("chat        ok   没接模型时返回的是人话")
    print("secrets     ok   没有提前创建")
    print("\nSMOKE PASSED")


if __name__ == "__main__":
    main()
