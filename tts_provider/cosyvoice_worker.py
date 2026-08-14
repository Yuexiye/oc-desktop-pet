"""CosyVoice 子进程 worker —— 把 4.6GB 模型挡在桌宠进程之外。

为什么需要它
------------
CosyVoice2 的加载链（cosyvoice → funasr/matcha → torch + lightning +
diffusers → onnxruntime → wetext）实测 **48 秒**：import 17s + 模型构造 31s。
这段时间里 torch 在加载 C 扩展 DLL、反序列化 2GB 的 llm.pt，会**长时间独占
GIL**——即使把 preload() 丢进后台线程，Qt 主线程一样被饿死，表现为 UI 冻结、
eventFilter 延迟飙到几百毫秒。

唯一彻底的解法是换进程：主进程不再 import torch，只通过管道收发 JSON。
等待管道读取时 CPython 会释放 GIL，事件循环全程不受影响。

协议
----
stdin  逐行 JSON 请求，stdout 逐行 JSON 响应，请求/响应用 ``id`` 配对。

    {"id":1,"cmd":"load"}
        -> {"id":1,"ok":true,"speakers":[...],"sample_rate":24000}
    {"id":2,"cmd":"synth","text":"...","out":"...wav",
     "ref_audio":"...","ref_text":"...","spk":"","instruct":""}
        -> {"id":2,"ok":true,"path":"...wav","mode":"zero_shot"}
    {"id":3,"cmd":"ping"}   -> {"id":3,"ok":true,"loaded":true}
    {"id":4,"cmd":"quit"}   -> 进程退出

stdout 洁癖
-----------
modelscope / torch / tqdm 都会往 fd 1 打印进度条，足以污染协议流。启动第一件
事就是把真正的 stdout ``dup`` 出来自己独占，再让 fd 1 指向 stderr——这样连 C
层的直接写入也一并改道，协议流保证只有我们自己的 JSON。
"""
from __future__ import annotations

import json
import os
import sys
import traceback

# ── 必须在任何重型 import 之前完成 stdout 隔离 ──
_PROTO_FD = os.dup(1)          # 抢下真正的 stdout
os.dup2(2, 1)                  # fd 1 从此指向 stderr，库噪声全部改道
sys.stdout = sys.stderr        # Python 层的 print 也一并改道
_proto = os.fdopen(_PROTO_FD, "w", encoding="utf-8", buffering=1)

def _resolve_cosyvoice_dir():
    """定位 cosyvoice-tts 目录（与 provider 端一致的解析顺序）。"""
    env = os.environ.get("OC_PET_COSYVOICE_DIR", "").strip()
    if env:
        return env
    # oc-pet 父目录下的 cosyvoice-tts（两仓库并排放即零配置）
    adjacent = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "cosyvoice-tts",
    )
    if os.path.isdir(adjacent):
        return adjacent
    return r"W:/Games/Hanako/Work/projects/cosyvoice-tts"


COSYVOICE_DIR = _resolve_cosyvoice_dir()
MODEL_NAME = os.environ.get("OC_PET_COSYVOICE_MODEL", "CosyVoice2-0.5B")

_model = None


def _send(obj: dict) -> None:
    _proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _proto.flush()


def _log(msg: str) -> None:
    print(f"[cosyvoice-worker] {msg}", file=sys.stderr, flush=True)


def _load() -> dict:
    """加载模型，返回 speakers / sample_rate。已加载则直接返回。"""
    global _model
    if _model is None:
        src = COSYVOICE_DIR
        matcha = os.path.join(COSYVOICE_DIR, "third_party", "Matcha-TTS")
        for d in (src, matcha):
            if d not in sys.path:
                sys.path.insert(0, d)
        # torchaudio.load 依赖 torchcodec（本环境 DLL 失败），用 soundfile 替代
        if COSYVOICE_DIR not in sys.path:
            sys.path.insert(0, COSYVOICE_DIR)
        import patch_torchaudio
        patch_torchaudio.apply()

        model_path = os.path.join(COSYVOICE_DIR, "models", MODEL_NAME)
        if not os.path.isdir(model_path):
            raise FileNotFoundError(f"CosyVoice model dir not found: {model_path}")

        _log(f"loading {model_path} ...")
        from cosyvoice.cli.cosyvoice import CosyVoice2
        import torch
        # 有 CUDA 就开 fp16：RTX 3060 上 fp16 比 fp32 快约 2 倍（tensor core），
        # 大幅压低 LLM 自回归解码的耗时（这是合成延迟的主要瓶颈）。
        # CPU 环境下 fp16 反而危险，保持 fp32。
        _model = CosyVoice2(model_path, fp16=torch.cuda.is_available())
        _log("model ready (fp16=%s)" % torch.cuda.is_available())

        # 实锤：打印 ORT 实际使用的执行提供器（确认没退回 CPU）
        try:
            import onnxruntime as ort
            _log("ORT %s | providers=%s"
                 % (ort.__version__, ort.get_available_providers()))
        except Exception as e:
            _log("ORT provider 查询失败: %s" % e)

    return {
        "speakers": list(_model.frontend.spk2info.keys()),
        "sample_rate": int(_model.sample_rate),
        "cuda_available": bool(torch.cuda.is_available()),
        "device": ("cuda" if torch.cuda.is_available()
                   else "cpu"),
    }


def _emit(result, out_path: str) -> bool:
    """把推理结果的第一段写成 wav。"""
    import soundfile as sf

    for item in result:
        arr = item["tts_speech"].squeeze().cpu().numpy()
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        sf.write(out_path, arr, _model.sample_rate)
        return True
    return False


def _synth(req: dict) -> dict:
    if _model is None:
        _load()

    text = (req.get("text") or "").strip()
    out_path = req.get("out") or ""
    if not text or not out_path:
        return {"ok": False, "error": "text/out required"}

    ref_audio = req.get("ref_audio") or ""
    ref_text = req.get("ref_text") or ""
    instruct = req.get("instruct") or ""
    spk = req.get("spk") or ""

    # ① 指令+零样本（有 instruct + 参考音频）：v2 的 inference_instruct2 是唯一
    #    支持指令的 v2 路径（v1 的 inference_instruct 有 assert，CosyVoice2 会崩）。
    if instruct and ref_audio and os.path.exists(ref_audio):
        try:
            result = _model.inference_instruct2(text, instruct, ref_audio, stream=False)
            if _emit(result, out_path):
                return {"ok": True, "path": out_path, "mode": "instruct_zs"}
            _log("instruct2 produced no audio, falling back to zero_shot")
        except Exception as e:
            _log(f"instruct2 failed ({e}), falling back to zero_shot")

    # ② 零样本克隆（有参考音频）
    if ref_audio and ref_text and os.path.exists(ref_audio):
        try:
            result = _model.inference_zero_shot(text, ref_text, ref_audio, stream=False)
            if _emit(result, out_path):
                return {"ok": True, "path": out_path, "mode": "zero_shot"}
            _log("zero-shot produced no audio, falling back to SFT")
        except Exception as e:
            _log(f"zero-shot failed ({e}), falling back to SFT")

    # ③ SFT 兑底（仅模型原生说话人；zero-shot 注册的音色在这里会 KeyError，
    #    因为 spk2info 存的是 llm_embedding/flow_embedding 而非 embedding）
    #    所以统一走 ①②，只有完全没有参考音频的调用才会落到这里。
    spk_list = list(_model.frontend.spk2info.keys())
    if not spk_list:
        return {"ok": False, "error": "no SFT speakers available"}
    if spk not in spk_list:
        spk = spk_list[0]

    result = _model.inference_sft(text, spk, stream=False)
    mode = "sft"

    if _emit(result, out_path):
        return {"ok": True, "path": out_path, "mode": mode, "spk": spk}
    return {"ok": False, "error": "model produced no audio"}


def main() -> None:
    _send({"event": "hello", "pid": os.getpid()})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception as e:
            _send({"ok": False, "error": f"bad request json: {e}"})
            continue

        rid = req.get("id")
        cmd = req.get("cmd")

        if cmd == "quit":
            _send({"id": rid, "ok": True})
            break
        
        if cmd == "save_spkinfo":
            try:
                _model.save_spkinfo()
                _send({"id": rid, "ok": True})
            except Exception as e:
                _send({"id": rid, "ok": False, "error": str(e)})
            break

        try:
            if cmd == "ping":
                resp = {"ok": True, "loaded": _model is not None}
            elif cmd == "load":
                resp = {"ok": True, **_load()}
            elif cmd == "synth":
                resp = _synth(req)
            elif cmd == "add_spk":
                try:
                    _model.add_zero_shot_spk(req.get("text", ""), req.get("ref_audio", ""), req.get("spk_id", ""))
                    _send({"id": rid, "ok": True})
                except Exception as e:
                    _send({"id": rid, "ok": False, "error": str(e)})
            else:
                resp = {"ok": False, "error": f"unknown cmd: {cmd}"}
        except Exception as e:
            _log(traceback.format_exc())
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        resp["id"] = rid
        _send(resp)


if __name__ == "__main__":
    main()
