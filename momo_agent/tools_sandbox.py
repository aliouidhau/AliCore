"""沙箱工具：把 Runtime 包成一个模型可调的函数。

CodeAgent 自己写的代码在受控命名空间里跑；需要真正隔离（或需要第三方库）时，
模型可以显式调用 run_python，走 Docker 断网容器。

注意：JSON 通道**不给**这个工具。它每轮都要重发 schema，而 JSON 通道里调 run_python
等于「对话里再套一层代码执行」，实际很少用；CodeAgent 通道本来就能直接写代码，也不需要它。
把它从 JSON 通道的工具清单里摘掉，每轮省一份 schema。
"""


def register_sandbox_tool(registry, runtime):
    def run_python(code: str):
        """在隔离环境里执行一段 Python 代码，返回标准输出。"""
        return runtime.run_python(code)

    registry.register(
        "run_python",
        "在隔离环境执行 Python，返回 stdout",
        run_python,
        json_visible=False,
    )
    return registry
