"""CosyVoice 合成耗时诊断脚本 — 走真实 zero_shot 路径（模拟 miku→ophelia）。

用法: python scripts/tts_timing.py
输出各段耗时：import / 模型加载 / 推理内部 / 写盘 / 总耗时
"""
import json
import os
import sys
import time

# 项目根（脚本在 scripts/ 下）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tts_provider.cosyvoice_worker import COSYVOICE_DIR, MODEL_NAME

sys.path.insert(0, COSYVOICE_DIR)
matcha = os.path.join(COSYVOICE_DIR, "third_party", "Matcha-TTS")
if matcha not in sys.path:
    sys.path.insert(0, matcha)

model_path = os.path.join(COSYVOICE_DIR, "models", MODEL_NAME)
print(f"model: {model_path}", flush=True)

# ophelia 参考音频（miku 默认音色）
refs = json.loads(open(os.path.join(COSYVOICE_DIR, "speaker_refs.json"), encoding="utf-8").read())
ophelia = refs.get("ophelia") or refs.get("ophelia_v3") or {}
ref_audio = ophelia.get("ref_audio", "")
ref_text = ophelia.get("ref_text", "")
print(f"ref_audio: {ref_audio}", flush=True)

t_start = time.time()
# 与 worker 相同：torchaudio.load 依赖 torchcodec（本环境 DLL 失败），用 soundfile 替代
import patch_torchaudio
patch_torchaudio.apply()
from cosyvoice.cli.cosyvoice import CosyVoice2
import torch
t_import = time.time()
print(f"[timing] import cosyvoice: {t_import - t_start:.1f}s  cuda={torch.cuda.is_available()}", flush=True)

_model = CosyVoice2(model_path, fp16=torch.cuda.is_available())
t_load = time.time()
print(f"[timing] model load: {t_load - t_import:.1f}s  fp16={torch.cuda.is_available()}", flush=True)

text = "好。慢慢来。我在这儿。"
t_synth_start = time.time()
print(f"[timing] 合成开始: {text!r}", flush=True)

import soundfile as sf
out_path = "logs/tts_timing_test.wav"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
try:
    result = _model.inference_zero_shot(text, ref_text, ref_audio, stream=False)
    n = 0
    for item in result:
        n += 1
        t1 = time.time()
        arr = item["tts_speech"].squeeze().cpu().numpy()
        sf.write(out_path, arr, _model.sample_rate)
        print(f"[timing] 第{n}段写盘: {time.time() - t1:.1f}s", flush=True)
    total = time.time() - t_synth_start
    print(f"[timing] 合成总耗时: {total:.1f}s（含推理+写盘）", flush=True)
    print(f"[timing] wav: {out_path}", flush=True)
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"[timing] 失败: {e}", flush=True)

print(f"[timing] 全程: {time.time() - t_start:.1f}s", flush=True)