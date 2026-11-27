# plugins/ — 外部插件库

**这个目录默认是空的。** 插件库体积很大（带 node_modules，实测 4.4 GB），不进仓库。

里面装的是 `alicore_agent/plugins.py` 要用到的第三方项目，不是 Python 包，
所以 pip 装不进来，需要自己放：

```
plugins/
  Millwright/sidecar/          SolidWorks 直驱（sw_agent）
  skills/skills/               技能库
  superpowers/skills/          技能库（第二来源）
  excalidraw/excalidraw-app/build/   白板前端
  drawdb/dist/                 数据库建模前端
  Umi-OCR/Umi-OCR.exe          本地 OCR
  distill/                     记忆去重
  ...
```

## 两种放法

**一、放进本目录** —— 把插件库整个拷到 `plugins/` 下，结构与上面一致。

**二、放别处，用环境变量指过来** —— 插件库已经存在别的地方（比如原来那套工程里），
不想复制一份，设一个环境变量就行：

```bat
set ALICORE_PLUGIN_LIB=D:\你的路径\插件
```

Windows 永久生效：`setx ALICORE_PLUGIN_LIB "D:\你的路径\插件"`，重开终端。

## 相关环境变量

| 变量 | 作用 | 不设的后果 |
|---|---|---|
| `ALICORE_PLUGIN_LIB` | 插件库根目录 | 默认 `app/plugins`，为空则大部分插件「未就绪」 |
| `ALICORE_SW_EXE` | `SLDWORKS.exe` 路径 | 自动探 `Program Files` 两个常见位和 `D:\sw2020`，都没有就「未检测到」 |
| `ALICORE_BLENDER_EXE` | `blender.exe` 路径 | 默认找 `<runtime>/blender/blender.exe`，没有就「Blender 未装」 |

## 自己写插件

复制 `ext/example_plugin.py` 改名即可。一个文件一个插件，约定：

```python
PLUGIN = {"id": "my_plugin", "name": "我的插件", "category": "工具", "description": "..."}
TOOLS  = [{"name": "my_tool", "description": "给模型看的说明", "func": my_func, "tags": ("关键词",)}]
```

可选 `ready()` / `status()`。放进 `ext/` 就出现在插件列表，不用改仓库代码。
加载失败只跳过这一个文件，不影响内置插件。

## 架构图的后备渲染

没装 Graphviz 时，`plugin_architecture_diagram` 会退到 Pillow 画分层方块图：
能出图、能看层级和依赖方向，跨层依赖走底部通道绕开中间层，但不做 Graphviz 那种
自动绕线优化。装了 Graphviz（或设 `ALICORE_DOT_EXE`）会自动改走 `diagrams` 的真布局。

## 缺件会怎样

不会假装能用。插件列表里逐项写：

- `sidecar 缺失` —— 缺 `Millwright/sidecar`，SW 插件点不动
- `未装（缺 Umi-OCR.exe）` —— OCR 插件
- `未构建（缺 dist/index.html）` —— excalidraw / drawdb
- `缺 Graphviz(dot)` —— 架构图
- `未构建 distill.exe` —— 记忆去重

调用工具时也会直接回一句人话，告诉你缺哪一块、该设哪个变量，而不是抛异常。
