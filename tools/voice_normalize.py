# -*- coding: utf-8 -*-
"""把教程语音统一峰值（默认 0.85），让一节一节听起来音量一致，也避免高 peak 段子接近削波。

用法：
    python tools\\voice_normalize.py [目录 ...]
默认处理 assets/voice/tutorial。
"""
import glob
import os
import sys
import wave

import numpy as np

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = 0.85


def normalize(path):
    with wave.open(path, "rb") as w:
        sr, n, sw, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(n)
    if sw != 2:
        return None
    a = np.frombuffer(raw, dtype=np.int16)
    peak = float(np.max(np.abs(a))) / 32768.0 if a.size else 0.0
    if peak <= 0.0:
        return None
    if abs(peak - TARGET) < 0.01:
        return peak, peak
    out = np.clip(a.astype(np.float32) * (TARGET / peak), -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(out.tobytes())
    return peak, float(np.max(np.abs(out))) / 32768.0


def main():
    dirs = sys.argv[1:] or [os.path.join(APP_DIR, "assets", "voice", "tutorial")]
    total = 0
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "**", "*.wav"), recursive=True)):
            r = normalize(p)
            if not r:
                continue
            total += 1
            if abs(r[0] - r[1]) > 0.001:
                print("[norm] %s %.3f -> %.3f" % (os.path.relpath(p, APP_DIR), r[0], r[1]), flush=True)
    print("[done] %d 个文件" % total, flush=True)


if __name__ == "__main__":
    main()
