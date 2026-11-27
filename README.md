# 阿鲤 · AliCore

跑在自己电脑上的本地 AI 桌面助手：`pywebview` 壳 + 单文件 HTML 前端 + Python agent 内核。
能聊天、读写文件、跑代码、生成文档，带一套能真的编译运行、有隐藏断言判题的 C/Python 教学靶场。

> 这是「避险版」仓库：只放代码、教程内容和示例配置。会话、记忆、学习进度、个人配置
> 全部在运行时目录；语音音频不进仓库，没有音频时用 Windows 内置语音现场合成。

## 结构

```
AliCore/
├─ ali_app.py          # 入口：窗口 + API 薄壳
├─ ui_v2.html           # 单文件前端（HTML/CSS/JS 一体）
├─ alicore_agent/       # 内核：引擎 / 模型 / 工具 / 记忆 / 会话
├─ assets/              # 图标与立绘（不含语音）
├─ workspace/           # 靶场：40 课教程 / 练习模板 / 题库 / 分步导学 / 语音稿
├─ tools/               # GPT-SoVITS 语音合成辅助脚本
├─ config.example.json  # 配置模板，复制成 config.json 再用
├─ requirements.txt
└─ ali_app.spec         # PyInstaller 打包配置
```

## 快速开始（Windows）

1. **Python 3.12**
   ```
   pip install -r requirements.txt
   ```
2. **接入模型**（第一次打开时模型菜单是空的，这是故意的）：
   - 点输入框右侧的模型按钮 →「接入 API」
   - 填服务商名字 + Base URL + API Key；程序会去 `GET {Base URL}/v1/models` 真的拉一次模型列表，拉到了才算接入成功
   - Base URL 一般填到 `/v1` 为止，例如 `https://你的中转站地址/v1`；填域名也行，程序会自动补 `/v1`
   - Key 存在 `%APPDATA%\AliCore\secrets.json`，不写进 config.json，也不下发给界面
   - 目前只支持 OpenAI 兼容协议的服务商；Anthropic 原生协议不支持
3. **启动**
   ```
   python ali_app.py
   ```
   或双击 `阿鲤桌面端.bat`（会自动找 `.venv` 或系统 Python）

4. 左侧「工作空间」进靶场：教程只读、练习场随便改、实战模式有判题。

## 靶场里有什么

- **41 份教程**：Python 25 份 + C 16 份，机械专业场景（零件计算器、库存报表、成绩管理）
- **练习场**：45 个起始模板，改完点运行，`.c` 用 TinyCC 真编译
- **实战题库**：88 道（改错 / 写原理 / 从零写），C 用隐藏断言防作弊
- **分步导学**：40 课 × 6 步，数据在 `workspace/lesson_steps.json`

C 语言需要 TinyCC：仓库不打包编译器。首次点 C 教程或 C 题目时会弹窗提示，点「下载并启用」
就从 GNU Savannah 官方镜像拉 `tcc-0.9.27-win64-bin.zip`（约 0.5 MB），解压到
`<data>\_runtime\tcc\tcc\tcc.exe`，不写系统目录、不需要管理员权限。也可以手动放一份，
或用 `ALICORE_RUNTIME_DIR` 指向已有运行时；没装只影响 `.c`，Python 照常用。

## 上下文与工具（开源版和内部版不一样）

内部版为了压月耗做了很多限制：历史只留 10 轮（约 3600 字符）、每 6 轮强制摘要、GLM 免费档兜底。开源版把这些全部放开，按「拿来干活」的标准重配：

- **历史**：默认不按轮数截，按 `context_window` 自动算字符预算（131072 上下文约 147k 字符，实测 160 条历史全量装下）。装不下才从最老的丢。想写死就在 `config.json` 里填 `history_char_budget`。
- **摘要**：默认只在上下文压力到 75% 时触发，不再每 6 轮硬摘。`summary_every` 填 0 就是不按轮数摘要。
- **单条消息**：默认不截断。老版写死 400 字符，一条长回答会被腰斩。

常驻工具（每轮都在清单里，15 个）：

`read_file` / `list_files` / `write_file` / `edit_file` / `apply_patch` / `search_text` / `run_command` / `run_python` / `http_get` / `web_search` / `todo_write` / `todo_read` / `remember` / `search_memory` / `dispatch_subagent`

- `apply_patch`：一次提交多处改动的统一 diff，逐段精确匹配；对不上就报哪段没对上，不会改进错误的行
- `run_command`：git / npm / pip / pytest / 构建，`cwd` 指定工作目录
- `run_python`：隔离环境里跑 Python，要第三方库时用它
- `http_get`：抓公网网页或接口，本机/内网地址拒绝
- `web_search`：联网搜索，key 自己填（Tavily / 博查 / Serper），仓库不内置任何搜索 key
- `todo_write` / `todo_read`：多步任务先列清单，模型自己勾进度
- `dispatch_subagent`：把一件独立的事派给子 agent，它有自己的上下文，主对话只收结论；子 agent 不能再派子 agent

执行隔离：`sandbox: true`（默认）按 **Docker 断网容器 → cocoon 子进程 → 本地受限 exec** 依次降级。每次 `run_python` 的返回开头会标 `[docker·断网容器]` / `[cocoon·子进程]` / `[local·未隔离]`，一眼能看出这次到底隔离没隔离。

`run_command`、`run_python`、`edit_file`、`apply_patch`、`delete_file`、`move_file`、`open_path` 执行前会在界面弹「允许一次」。安全模式下只留只读工具。

## 语音怎么来（仓库不带音频）

默认走 **Windows 内置中文语音（SAPI5）**：免费、离线、不联网，讲解文字不会发到任何服务器。
首次点讲课/讲解时现场合成，缓存到 `<data>\_runtime\tts_cache`，同一段第二次是秒开。
程序里点「语音音色 ⚙」能看到当前引擎和两个音频目录。

想换成自己的音色：

- 手里已经有 wav：教程放 `assets/voice/tutorial/<课key>/<节id>.wav`，练习放
  `assets/voice/practice/<题id>.wav`，重启后优先用你的音频，缺失的段仍回落到内置语音
- 想克隆音色：装 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，用 5 秒参考音频合成：

```
set ALICORE_TTS_HOME=D:\GPT-SoVITS
set ALICORE_TTS_REF=D:\voice_ref\my.wav
set ALICORE_TTS_PROMPT=参考音频里说的那句话

python tools/tutorial_voice_tts.py
python tools/voice_practice_tts.py
python tools/voice_normalize.py
```

## 数据放在哪

| 内容 | 默认位置 |
|---|---|
| 配置 / 会话 / 记忆 / 工作区 | `%APPDATA%\AliCore` |
| 日志 / 沙箱临时文件 | `%APPDATA%\AliCore\_runtime` |
| 密钥 | `%APPDATA%\AliCore\secrets.json` |

代码目录**永远不写**用户数据。要换位置：

```
python ali_app.py --data-dir D:\my-alicore-data --runtime-dir D:\my-alicore-runtime
```

或设环境变量 `ALICORE_DATA_DIR` / `ALICORE_RUNTIME_DIR`。

## 授权

| 部分 | 授权 |
|---|---|
| 代码 | **MIT**（见 LICENSE） |
| 教程 / 题库 / 分步导学（`workspace/`） | **CC BY-NC-SA 4.0**：可学习、可改、可分享，不可商用 |
| 语音音频（`assets/voice/`） | 不随仓库分发（音色版权） |
| 立绘 / 图标 | 仅作者自用，开源前请自行替换或确认版权 |

## 借鉴与致谢

这个项目不是从零发明的，几处核心设计来自下面这些开源项目。有直接沿用、有改写、也有只借思路；每处具体落在哪个文件，都在那个文件的注释里写清了。

| 来源 | 许可 | 用在哪 |
|---|---|---|
| [HuggingFace/smolagents](https://github.com/huggingface/smolagents) | Apache-2.0 | CodeAgent 通道的循环骨架（一轮一个代码块 → 执行结果回灌 → `done()` 收口）；跨轮持久命名空间；工具=普通函数、按函数签名生成 schema；执行护栏的数值 |
| [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | MIT | UI / Agent / Runtime 的架构分层；Runtime 抽象（同一接口挂多套执行环境）；事件流（Action / Observation）；工具与 agent 解耦 |
| [princeton-nlp/SWE-agent](https://github.com/princeton-nlp/SWE-agent) | MIT | Agent-Computer Interface（ACI）的几条硬规则：编辑前先过 linter、空结果必须明说「执行成功但没命中」 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | Apache-2.0 | 只作对照，没有沿用代码：`edit_file` 故意不做模糊匹配（aider 用约 20KB 处理这块）；`code_map` 用标准库 `ast` 而不是它的 repomap + tree-sitter |
| [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 及其生态 | MIT | HTTP 退避重试（dsh-llm-retry）；规则式偏好捕获、凭证脱敏（dsh-engram，npm 包，维护者 skepsun） |

BM25 打分公式出自信息检索的公开文献（Robertson & Zaragoza, 2009），实现是按公式自己写的。

## 已知限制

- 只在 Windows 上跑过（pywebview 走 WinForms 后端）
- 代码执行默认走 Docker；没装 Docker 时退回本地受限 exec
- 没有安装器、没有自动更新
- 个人项目，测试集中在核心链路
- 教程按机械专业场景写，不是通用教材
