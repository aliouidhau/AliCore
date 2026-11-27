# 更新记录

## v1.0.0 · 2026-11-27

第一个开源版本。

**能做什么**

- 本地桌面 AI 搭档，pywebview + HTML 界面，不联网也能开；对话走你自己的 OpenAI 兼容中转站
- 54 个工具：读写文件、全文搜索、跑 Python / shell、生成 PDF / Word / PPT、读 PDF、代码地图、记忆读写、派子 agent
- 40 课编程靶场：Python 24 课 + C 16 课，配 45 道练习；教程只读、练习场随便改、实战模式有判题
- 8 个内置插件：技能库、SolidWorks 直驱、文档 OCR、Blender 人物建模、VRM、架构图、白板、数据库建模
- 外部插件：往 `plugins/ext/` 丢一个 py 就能加工具，不用碰主程序
- 上下文自己管：按模型上下文自动算字符预算，到 75% 触发摘要，工具结果超预算时保头保尾

**和内部版的差别**

- 模型接入改成空白起步：仓库不带任何预置服务商和 key，第一次打开自己填
- 插件只带能跑的 8 个。三维建模（cadquery/trimesh）、Umi-OCR、记忆去重、supermemory 的实现代码还在 `plugins.py`，需要的话自己加回 `build_plugins()`
- 人设不硬绑：`system_prompt` 默认空，`assistant_name` 单独配，想叫什么自己填
- 语音音频不随仓库分发，`tools/` 里给了合成脚本，自己生成

**打包**

- `ali_app.spec` 可直接 PyInstaller onedir 打包，实测 114 MB
- 界面、图标、靶场、示例配置打进 `_internal`，首次启动播种到 `%APPDATA%\AliCore\workspace`；之后改的是数据目录那份，换新 exe 不会覆盖
- `plugins/` 和 `config.example.json` 放在 exe 同级，方便改
- `.github/workflows/smoke.yml` 每次提交自动跑语法检查和内核冒烟

**已知限制**

- 只支持 OpenAI 兼容协议，Anthropic 原生协议不支持
- 代码执行按 Docker → cocoon → 本地受限 exec 依次降级；没装 Docker 时返回开头会标 `[local·未隔离]`
- 没有安装器、自动更新、代码签名；Windows SmartScreen 首次运行会提示未知发布者
- 架构图没装 Graphviz 时用 Pillow 渲染，跨层连线走底部通道，不如 Graphviz 好看但能出图
