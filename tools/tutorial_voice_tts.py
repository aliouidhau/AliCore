# -*- coding: utf-8 -*-
"""教程语音播报批量合成（GPT-SoVITS 本地工具）

用法（先按 README 配好音色环境变量）：
    python tools/tutorial_voice_tts.py
    ... tutorial_voice_tts.py 01         # 只做某一课
    ... tutorial_voice_tts.py --force    # 全部重做

音色由环境变量决定（仓库不带音频，也不带参考音色）：
    ALICORE_TTS_HOME   GPT-SoVITS 安装目录
    ALICORE_TTS_REF    参考音色 wav
    ALICORE_TTS_PROMPT 参考音频对应的文字
"""
import json
import os
import sys
import time
import wave

import numpy as np

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTS_HOME = os.environ.get("ALICORE_TTS_HOME", "")
REF_AUDIO = os.environ.get("ALICORE_TTS_REF", "")
PROMPT_TEXT = os.environ.get("ALICORE_TTS_PROMPT", "")
SCRIPT_JSON = os.path.join(APP_DIR, "workspace", "tutorial_voice.json")
OUT_ROOT = os.path.join(APP_DIR, "assets", "voice", "tutorial")

if not TTS_HOME or not REF_AUDIO:
    print("需要先设置：ALICORE_TTS_HOME（GPT-SoVITS 目录）、ALICORE_TTS_REF（参考音色 wav）、ALICORE_TTS_PROMPT（参考音频对应文字）", flush=True)
    raise SystemExit(2)

sys.path.insert(0, TTS_HOME)
sys.path.insert(0, os.path.join(TTS_HOME, "GPT_SoVITS"))
os.chdir(TTS_HOME)

from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config  # noqa: E402


def normalize(a, target_peak=0.9):
    a = np.asarray(a)
    a = a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak > 1e-6:
        a = a * (target_peak / peak)
    return (np.clip(a, -1.0, 1.0) * 32767.0).astype(np.int16), peak


def save(path, sr, audio):
    a, peak = normalize(audio)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())
    d = a.astype(np.float32) / 32768.0
    return len(a) / sr, peak, float(np.sqrt(np.mean(d ** 2)))


def main():
    args = sys.argv[1:]
    only = [a for a in args if not a.startswith("--")] or None
    force = "--force" in args
    with open(SCRIPT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    lessons = data.get("lessons") or []

    cfg = TTS_Config(os.path.join(TTS_HOME, "GPT_SoVITS", "configs", "tts_infer.yaml"))
    cfg.device = "cuda"
    cfg.is_half = True
    print("[lecture] 模型版本:", cfg.version, flush=True)
    tts = TTS(cfg)

    total = 0.0
    for les in lessons:
        key = str(les.get("key") or "")
        if only and key not in only:
            continue
        out_dir = os.path.join(OUT_ROOT, key)
        os.makedirs(out_dir, exist_ok=True)
        print("== %s %s" % (key, les.get("title")), flush=True)
        for sec in les.get("sections") or []:
            sid = str(sec.get("id"))
            out_path = os.path.join(out_dir, "%s.wav" % sid)
            if os.path.exists(out_path) and not force:
                print("[skip]", key, sid, flush=True)
                continue
            inputs = {
                "text": sec["text"], "text_lang": "zh",
                "ref_audio_path": REF_AUDIO, "prompt_text": PROMPT_TEXT, "prompt_lang": "zh",
                "top_k": 15, "top_p": 1.0, "temperature": 1.0,
                "text_split_method": "cut5", "batch_size": 1, "speed_factor": 0.96,
                "split_bucket": False, "return_fragment": False,
                "fragment_interval": 0.25, "seed": 42,
                "parallel_infer": True, "repetition_penalty": 1.35,
            }
            t0 = time.time()
            try:
                for sr, audio in tts.run(inputs):
                    dur, peak, rms = save(out_path, sr, audio)
                    total += dur
                    print("[ok] %s/%s %.2fs peak=%.3f rms=%.4f cost=%.1fs" % (key, sid, dur, peak, rms, time.time() - t0), flush=True)
                    break
            except Exception as e:
                print("[fail]", key, sid, repr(e)[:200], flush=True)
    print("[done] 本次音频总时长 %.1fs" % total, flush=True)


if __name__ == "__main__":
    main()
