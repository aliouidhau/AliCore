"""外部插件示例 —— 把本文件复制成你自己的名字就能用。

约定三件事：
  1. PLUGIN 字典：id / name / category / description（source 可选）
  2. TOOLS 列表：每个工具 name + description + func
  3. 可选 ready() / status()，不写就等于「永远就绪」

加载规则：
  - 放在 <插件库>/ext/*.py，以 _ 开头的文件会被跳过
  - 语法错、PLUGIN 或 TOOLS 缺失、func 不是可调用对象 —— 这个文件整体跳过，不影响别人
  - 工具默认 json_visible=False，也就是不常驻上下文，靠 tags 命中或被「带入对话」才挂上
"""

PLUGIN = {
    "id": "hello_ext",
    "name": "示例插件",
    "category": "工具",
    "description": "演示外部插件怎么写，正式用请复制本文件改名。",
    "source": "你自己",
}


def _hello(name=""):
    return f"你好，{name or '世界'}。这条回复来自 ext 里的自定义插件。"


def ready():
    # 这里可以检查依赖，比如某个 exe 在不在；返回 False 会在插件列表里显示未就绪
    return True


def status():
    return "就绪（示例）"


TOOLS = [
    {
        "name": "ext_hello",
        "description": "示例工具：传入 name 返回一句问候",
        "func": _hello,
        "json_visible": False,
        "tags": ("示例", "你好", "hello", "自定义插件"),
    },
]
