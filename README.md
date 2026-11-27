# 阿鲤 · AliCore

跑在自己电脑上的本地 AI 桌面助手：`pywebview` 壳 + 单文件 HTML 前端 + Python agent 内核。
能聊天、读写文件、跑代码、生成文档，带一套能真的编译运行、有隐藏断言判题的 C/Python 教学靶场。

> 这是「避险版」仓库：只放代码、教程内容和示例配置。会话、记忆、学习进度、个人配置
> 全部在运行时目录；语音音频不进仓库（自己用 `tools/` 里的脚本合成）。

## 结构

```
AliCore/
├─ momo_app.py          # 入口：窗口 + API 薄壳
├─ ui_v2.html           # 单文件前端（HTML/CSS/JS 一体）
├─ momo_agent/          # 内核：引擎 / 模型 / 工具 / 记忆 / 会话 / 额度
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
2. **准备 API key**（DeepSeek / 智谱 GLM / OpenAI 兼容中转任一）：
   - 配在 `%APPDATA%\AliCore\secrets.json`，或启动后点左下角「接入 API」
   - 也认环境变量：`MOMO_DEEPSEEK_KEY` / `MOMO_GLM_KEY` / `MOMO_OPENAI_KEY` / `MOMO_ANTHROPIC_KEY`
3. **启动**
   ```
   python momo_app.py
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

## 语音怎么来（仓库不带音频）

默认走 **Windows 内置中文语音（SAPI5）**：免费、离线、不联网，讲解文字不会发到任何服务器。
首次点讲课/讲解时现场合成，缓存到 `<data>\_runtime\tts_cache`，同一段第二次是秒开。
程序里点「语音音色 ⚙」能看到当前引擎和两个音频目录。

想换成自己的音色：

- 手里已经有 wav：教程放 `assets/voice/tutorial/<课key>/<节id>.wav`，练习放
  `assets/voice/practice/<题id>.wav`，重启后优先用你的音频，缺失的段仍回落到内置语音
- 想克隆音色：装 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，用 5 秒参考音频合成：

```
set MOMO_TTS_HOME=D:\GPT-SoVITS
set MOMO_TTS_REF=D:\voice_ref\my.wav
set MOMO_TTS_PROMPT=参考音频里说的那句话

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
python momo_app.py --data-dir D:\my-alicore-data --runtime-dir D:\my-alicore-runtime
```

或设环境变量 `ALICORE_DATA_DIR` / `ALICORE_RUNTIME_DIR`。

## 授权

| 部分 | 授权 |
|---|---|
| 代码 | **MIT**（见 LICENSE） |
| 教程 / 题库 / 分步导学（`workspace/`） | **CC BY-NC-SA 4.0**：可学习、可改、可分享，不可商用 |
| 语音音频（`assets/voice/`） | 不随仓库分发（音色版权） |
| 立绘 / 图标 | 仅作者自用，开源前请自行替换或确认版权 |

## 已知限制

- 只在 Windows 上跑过（pywebview 走 WinForms 后端）
- 代码执行默认走 Docker；没装 Docker 时退回本地受限 exec
- 没有安装器、没有自动更新
- 个人项目，测试集中在核心链路
- 教程按机械专业场景写，不是通用教材
