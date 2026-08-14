"""快速注册 CosyVoice2 说话人 - 直接调用 API"""
import json
import os
import sys
import subprocess
import time
from pathlib import Path

# 路径
_REPO = Path(r"W:\Games\Hanako\Work\projects\oc-pet")
_WORKER = _REPO / 'tts_provider' / 'cosyvoice_worker.py'
_MODEL_DIR = Path(r"W:\Games\Hanako\Work\projects\cosyvoice-tts\models\CosyVoice2-0.5B")
_VOICE_DIR = Path(r"W:\Games\Hanako\Work\通用\助手\语音")
_OUTPUT_PT = _MODEL_DIR / 'spk2info.pt'

# 音色映射
SPEAKERS = {
    'luoqixi': ('洛琪希_参考音频.wav', '我是洛琪希，一个旅行者，很高兴认识你。'),
    'ophelia': ('奥菲莉娅_参考音频.wav', '你好，我是奥菲莉娅，未注视者，裂隙的偏折点。'),
    'aimis': ('爱弥斯_参考音频.wav', '我是爱弥斯，电子幽灵，我能感知你的情绪。'),
    'alice': ('艾莉丝_参考音频.wav', '我是艾莉丝，伯雷亚斯家的红发剑士。'),
    'glados': ('glados_参考音频.wav', 'Welcome to Aperture Science.'),
    'rebecca': ('瑞贝卡_参考音频（日配.wav', '夜之城出身，实战专家。'),
}

def main():
    print(f"启动 worker...")
    
    env = os.environ.copy()
    env['OC_PET_COSYVOICE_DIR'] = str(Path(r"W:\Games\Hanako\Work\projects\cosyvoice-tts"))
    env['PYTHONIOENCODING'] = 'utf-8'
    
    proc = subprocess.Popen(
        [sys.executable, '-u', str(_WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding='utf-8'
    )
    
    # 读取 hello
    line = proc.stdout.readline()
    print(f"Worker: {line.strip()}")
    
    # 加载模型
    print("加载模型（首次需要几分钟）...")
    proc.stdin.write(json.dumps({"id": 1, "cmd": "load"}) + "\n")
    proc.stdin.flush()
    
    # 等待响应
    for i in range(120):  # 等最多 2 分钟
        line = proc.stdout.readline()
        if not line:
            print("❌ Worker 无响应")
            return
        line = line.strip()
        if not line:
            continue
        try:
            resp = json.loads(line)
            if resp.get('speakers') is not None:
                print(f"✅ 模型加载完成! speakers: {resp.get('speakers', [])}")
                break
            print(f"  [{i+1}s] {line[:80]}")
        except:
            print(f"  [{i+1}s] {line[:80]}")
        time.sleep(1)
    else:
        print("⚠️ 加载超时，继续尝试...")
    
    # 注册每个说话人
    for spk_id, (audio_file, ref_text) in SPEAKERS.items():
        audio_path = _VOICE_DIR / audio_file
        if not audio_path.exists():
            print(f"  ⚠ {spk_id}: 音频不存在")
            continue
        
        print(f"  注册 {spk_id}...")
        req = {
            "id": int(time.time() * 1000) % 10000,
            "cmd": "synth",
            "text": ref_text[:30],
            "out": str(_MODEL_DIR / f"test_{spk_id}.wav"),
            "ref_audio": str(audio_path),
            "ref_text": ref_text,
            "spk": "",
            "instruct": ""
        }
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        
        resp_line = proc.stdout.readline()
        resp = json.loads(resp_line)
        if resp.get('ok'):
            print(f"    ✅ {resp.get('mode', 'ok')}")
        else:
            print(f"    ❌ {resp.get('error')}")
    
    # 保存
    print("\n保存 spk2info...")
    # 通过 synth 注册后，spk2info 应该已经更新，直接保存
    # 但 worker 没有 save 命令，需要退出后由主进程保存
    
    # 退出
    proc.stdin.write(json.dumps({"id": 999, "cmd": "quit"}) + "\n")
    proc.stdin.flush()
    
    print("\n注意: 需要在主进程中调用 save_spkinfo()")
    print(f"输出路径: {_OUTPUT_PT}")

if __name__ == "__main__":
    main()
