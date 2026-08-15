"""验证 ref_text 修正后 TTS 内容是否正常：合成 → whisper 转写 → 对比原文。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tts_provider.cosyvoice import CosyVoiceProvider

# 用 unicode 转义避免 shell 编码问题
text = "\u300a\u4e0a\u53e4\u5377\u8f74\u300b\u554a\u2026 \u90a3\u662f\u53e6\u4e00\u4e2a\u9700\u8981\u201c\u6ce8\u89c6\u201d\u7684\u4e16\u754c\u3002"
p = CosyVoiceProvider()
p.preload()
t0 = time.time()
path = p.synthesize(text, character_id="miku")
print(f"[verify] 合成耗时: {time.time() - t0:.1f}s")
if not path:
    print("[verify] 合成失败")
    sys.exit(1)
print(f"[verify] wav: {path}")

import whisper
m = whisper.load_model("small")
r = m.transcribe(path, language="zh")
print()
print("=== 实际发音 ===")
print(r["text"].strip())
print("=== 预期文本 ===")
print(text)
p.cleanup()