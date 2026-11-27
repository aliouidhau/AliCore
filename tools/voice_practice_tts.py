# -*- coding: utf-8 -*-
"""实战语音讲解批量合成（GPT-SoVITS 本地工具）

用法（改完 workspace/practice_voice.json 的讲解稿后重跑）：
    python tools/voice_practice_tts.py
    ... voice_practice_tts.py py01        # 只重做一题
    ... voice_practice_tts.py --force     # 全部重做（覆盖已有 wav）

音色由环境变量决定（仓库不带音频，也不带参考音色）：
    MOMO_TTS_HOME   GPT-SoVITS 安装目录
    MOMO_TTS_REF    参考音色 wav
    MOMO_TTS_PROMPT 参考音频对应的文字
"""
import json
import os
import sys
import time
import wave

import numpy as np

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTS_HOME = os.environ.get("MOMO_TTS_HOME", "")
REF_AUDIO = os.environ.get("MOMO_TTS_REF", "")
PROMPT_TEXT = os.environ.get("MOMO_TTS_PROMPT", "")
SCRIPT_JSON = os.path.join(APP_DIR, "workspace", "practice_voice.json")
OUT_DIR = os.path.join(APP_DIR, "assets", "voice", "practice")

if not TTS_HOME or not REF_AUDIO:
    print("需要先设置：MOMO_TTS_HOME（GPT-SoVITS 目录）、MOMO_TTS_REF（参考音色 wav）、MOMO_TTS_PROMPT（参考音频对应文字）", flush=True)
    raise SystemExit(2)

sys.path.insert(0, TTS_HOME)
sys.path.insert(0, os.path.join(TTS_HOME, "GPT_SoVITS"))
os.chdir(TTS_HOME)

from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config  # noqa: E402


def normalize(a, target_peak=0.89):
    a = np.asarray(a)
    a = a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak > 1e-6:
        a = a * (target_peak / peak)
    return (np.clip(a, -1.0, 1.0) * 32767.0).astype(np.int16), peak


def save(path, sr, audio):
    a, peak = normalize(audio, 0.89)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())
    d = a.astype(np.float32) / 32768.0
    return len(a) / sr, peak, float(np.sqrt(np.mean(d ** 2)))


def main():
    args = [a for a in sys.argv[1:]]
    only = next((a for a in args if not a.startswith("--")), None)
    force = "--force" in args
    with open(SCRIPT_JSON, "r", encoding="utf-8") as f:
        items = json.load(f)["items"]

    cfg = TTS_Config(os.path.join(TTS_HOME, "GPT_SoVITS", "configs", "tts_infer.yaml"))
    cfg.device = "cuda"
    cfg.is_half = True
    print("[voice] 模型版本:", cfg.version, flush=True)
    t0 = time.time()
    tts = TTS(cfg)
    print("[voice] 加载耗时 %.1fs" % (time.time() - t0), flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    total = 0.0
    for it in items:
        cid = it["id"]
        if only and cid != only:
            continue
        out_path = os.path.join(OUT_DIR, cid + ".wav")
        if os.path.exists(out_path) and not force:
            print("[skip]", cid, flush=True)
            continue
        inputs = {
            "text": it["text"], "text_lang": "zh",
            "ref_audio_path": REF_AUDIO, "prompt_text": PROMPT_TEXT, "prompt_lang": "zh",
            "top_k": 15, "top_p": 1.0, "temperature": 1.0,
            "text_split_method": "cut5", "batch_size": 1, "speed_factor": 0.96,
            "split_bucket": False, "return_fragment": False,
            "fragment_interval": 0.25, "seed": 42,
            "parallel_infer": True, "repetition_penalty": 1.35,
        }
        t1 = time.time()
        try:
            for sr, audio in tts.run(inputs):
                dur, peak, rms = save(out_path, sr, audio)
                total += dur
                print("[ok] %s %.2fs peak=%.3f rms=%.4f cost=%.1fs" % (cid, dur, peak, rms, time.time() - t1), flush=True)
                break
        except Exception as e:
            print("[fail]", cid, repr(e)[:200], flush=True)
    print("[done] 本次音频总时长 %.1fs" % total, flush=True)


if __name__ == "__main__":
    main()
