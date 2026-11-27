# tools/ — 可选脚本，不是运行依赖

这三个脚本是作者自己批量生成语音用的，AliCore 本体不需要它们。
语音默认走 Windows 内置 SAPI5，装不装这些都不影响启动。

## 额外依赖

```
pip install numpy
```

`numpy` 只在这里用，所以没写进 `requirements.txt`。

## 额外环境变量

| 变量 | 指向 |
|---|---|
| `ALICORE_TTS_HOME` | GPT-SoVITS 目录 |
| `ALICORE_TTS_REF` | 参考音色 wav |
| `ALICORE_TTS_PROMPT` | 参考音频里说的那句话 |

## 三个脚本

- `tutorial_voice_tts.py` —— 给教程正文批量合成讲解音频
- `voice_practice_tts.py` —— 给练习题批量合成
- `voice_normalize.py` —— 统一音频响度（peak / rms）

生成物默认落在 `assets/voice/`，这个目录被 `.gitignore` 排除，不随仓库分发。
