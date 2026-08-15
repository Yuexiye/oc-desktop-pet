"""验证 worker 子进程链路（含 CUDA DLL 注入）的合成耗时。

与桌宠真实路径完全一致：CosyVoiceProvider → spawn worker → 管道 synth。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tts_provider.cosyvoice import CosyVoiceProvider

t0 = time.time()
p = CosyVoiceProvider()
p.preload()
t1 = time.time()
print(f"[wtest] worker 加载完成: {t1 - t0:.1f}s", flush=True)

t2 = time.time()
path = p.synthesize("好。慢慢来。我在这儿。", character_id="miku")
t3 = time.time()
print(f"[wtest] 合成耗时: {t3 - t2:.1f}s → wav: {path}", flush=True)
print(f"[wtest] 全程: {t3 - t0:.1f}s", flush=True)
p.cleanup()