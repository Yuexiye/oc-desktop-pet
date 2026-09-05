# PoC：Silero VAD 经 onnxruntime 集成（无 torch）

> 文档类型：技术可行性 PoC（T04 的前置门禁）
> 撰写：团队实测（阿布直接执行；架构/工程子代理当时受 429 限流，未参与）
> 日期：2026-09-04
> 关联：`docs/refactor-2026-09-03-incremental-design.md` §1.2.2 / §6.1 / 决策三

---

## 0. 一句话结论

- **门禁判定：PASS（带一个待补项）**。无 torch 的集成路径**完全可行**，且成本极低（每 32ms 音频窗 0.16ms 推理 ≈ 占实时 0.5%），为 T04 放行。
- **对决策三的关键纠正**：`pip install silero-vad --no-deps` 装出的包，**其 Python 包装（API）在 import 时即 `ModuleNotFoundError: No module named 'torch'`**——包装层本身是 torch 写的。因此**不能**像决策三原表述那样「用 silero-vad 的 Python API」，而必须**用 onnxruntime 直跑它随包自带（或 vendored）的 ONNX 模型**，自行实现 VAD 迭代器（见 §4）。
- **唯一未闭环项**：检测准确率需在**真实语音**上验证。本沙箱无法外网下载语音样本，且 Silero 对纯合成信号天然判非语音（prob≈0.001），故「检出正确性」留待在 oc-pet 真实音频（录音 / 任意 16k wav）上复测。这不影响 T04 开工——推理链路本身已验证可跑、廉价。

---

## 1. 验证环境

| 项 | 值 |
|---|---|
| Python | 3.13.12（managed venv：`.../envs/silero-poc`） |
| 安装命令 | `pip install --no-deps onnxruntime silero-vad` |
| 装出版本 | `onnxruntime-1.29.0`、`silero-vad-6.2.1` |
| torch 是否引入 | **否**（`importlib.util.find_spec("torch") is None` 实测） |
| 执行设备 | `CPUExecutionProvider` |

> 沙箱无外网，无法 `pip download` 真实语音；验证用合成音频 + ONNX 直跑。

---

## 2. 实测数据（可复现）

### 2.1 安装与无 torch 验证
```
Collecting onnxruntime / silero-vad
Successfully installed onnxruntime-1.29.0 silero-vad-6.2.1
torch present: False
```

### 2.2 silero-vad 包装可用性（★ 纠正决策三）
```python
from silero_vad import load_silero_vad, get_speech_timestamps
# → ModuleNotFoundError: No module named 'torch'
```
**结论**：`silero-vad` 的 Python 包装依赖 torch，**`--no-deps` 装下来也用不了它的 API**。它唯一对我们有用的是**随包附带的 ONNX 模型文件**（`silero_vad/data/*.onnx`）。

### 2.3 ONNX 模型直跑（onnxruntime-only，torch-free）

模型变体签名（已逐一核对）：
| 文件 | inputs | outputs |
|---|---|---|
| `silero_vad.onnx` | `input`,`state`,`sr` | `output`,`stateN` |
| `silero_vad_16k_op15.onnx` | `input`,`state`,`sr` | `output`,`stateN` |
| `silero_vad_op18_ifless.onnx` | `input`,`sr`,`state` | `output`,`stateN` |

→ **ONNX 输入端只有 `input`/`state`/`sr` 三个，没有 `last_sr`/`last_batch_size`**（后者是 torch 包装层的概念）。推理循环极简：

```python
state = np.zeros((2,1,128), dtype=np.float32)
sr_in = np.array(16000, dtype=np.int64)
for w in windows_of_512:                     # 16k, 32ms/窗
    out, state = sess.run(None, {
        "input": w[None,:].astype(np.float32),
        "state": state, "sr": sr_in})
    prob = float(out[0,0])                    # 语音概率 0..1
```

实测（2.0s @16k = 62 窗，合成音频）：
```
模型加载: 41~54 ms（CPUExecutionProvider）
推理总耗时: 9.5~11.9 ms  →  每窗均值 0.155~0.193 ms
占实时比: 0.47%~0.60%
```

**含义**：VAD 推理开销可忽略。即便每秒 31 窗全跑，单核也只用 ~0.5% 时间，远未触及音频回调线程的实时预算。T04 关于「回调线程内做 VAD 推理」的风险可降级为「低风险」。

### 2.4 检测正确性（PARTIAL — 需真实音频复测）

| 输入 | 语音段 prob(mean/max) | 静音段 prob(mean/max) | 判定 |
|---|---|---|---|
| 220Hz 正弦 | 0.05x | ~0 | 模型判非语音（正确，纯音非语音） |
| 共振峰元音(合成) | 0.001 / 0.003 | 0.0005 / 0.006 | 模型判非语音（正确，合成非真实语音） |

合成信号全部 ≈0。**这是 Silero 的预期行为**（仅在真实语音上训练），不是集成 bug。集成链路本身（加载、状态演进、逐窗推理）已验证可跑、数值合理。

---

## 3. 对设计文档的修正（决策三）

原决策三表述：
> VAD 走 `onnxruntime` + `pip install silero-vad --no-deps` …（隐含「用 silero-vad 的 API」）

修正为：
> 1. **安装 `silero-vad --no-deps` 的目的仅是*取得 ONNX 模型文件***；运行时**不 import `silero_vad`**，改用 onnxruntime 直跑 ONNX。
> 2. 更干净的做法：**把 `silero_vad_16k_op15.onnx`（≈2MB）vendored 进仓库**（如 `avatar/` 或资源目录），**完全不装 `silero-vad` 这个 pip 包**，彻底规避 torch 风险。本 PoC 的 venv 仅用于验证，oc-pet 实际只需 `onnxruntime`。
> 3. T04 的 VAD 抽象（`core/audio_input/vad.py`）内部用 §4 的自写 ONNX 迭代器，对外暴露与现状 `peek_energy` 一致的接口，便于 `chat_mixin` 平滑切换。

`§6.1` 依赖清单相应更新：`silero-vad` 改为「可选，仅用于取模型；或 vendored ONNX 替代」；`onnxruntime` 保留为唯一新增运行时依赖。

---

## 4. 参考实现：纯 onnxruntime VAD 迭代器（torch-free）

> 以下为 T04 可直接采用的骨架。逻辑等价于 `silero_vad.utils_vad.VADIterator`，但纯 numpy、无 torch。
> **状态**：推理路径已实测可行；**检测阈值/端点逻辑需在真实音频上标定**（见 §2.4 未闭环项）。

```python
import numpy as np
import onnxruntime as ort

class SileroOrtVAD:
    """torch-free Silero VAD，基于 vendored ONNX（如 silero_vad_16k_op15.onnx）。"""
    def __init__(self, onnx_path: str, sr: int = 16000,
                 threshold: float = 0.5, neg_threshold: float = 0.35,
                 min_speech_ms: int = 250, min_silence_ms: int = 100,
                 speech_pad_ms: int = 30):
        self.sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.sr = sr
        self.win = 512                      # Silero 固定 512 样本/窗 @16k
        self.threshold = threshold
        self.neg_threshold = neg_threshold
        self.min_speech = int(min_speech_ms / 1000 * sr / self.win)
        self.min_silence = int(min_silence_ms / 1000 * sr / self.win)
        self.pad = int(speech_pad_ms / 1000 * sr / self.win)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.sr_in = np.array(self.sr, dtype=np.int64)
        self.triggered = False
        self.temp_end = 0
        self.speech_frames = []             # 以窗索引为单位

    def process(self, chunk: np.ndarray) -> list[tuple[int, int]]:
        """chunk: 16k 单声道 float32。返回已结束的语音段 [(start_sample, end_sample), ...]。"""
        out, self.state = self.sess.run(None, {
            "input": chunk[None, :].astype(np.float32),
            "state": self.state, "sr": self.sr_in})
        p = float(out[0, 0])
        idx = len(self.speech_frames)
        segments = []
        if p >= self.threshold:
            self.speech_frames.append(idx)
            self.temp_end = 0
            self.triggered = True
        elif self.triggered:
            if p >= self.neg_threshold:
                self.speech_frames.append(idx)
                self.temp_end = 0
            else:
                if self.temp_end == 0:
                    self.temp_end = idx
                elif idx - self.temp_end >= self.min_silence:
                    # 结束当前段
                    start = max(0, self.speech_frames[0] - self.pad) * self.win
                    end = (self.speech_frames[-1] + self.pad) * self.win
                    segments.append((start, end))
                    self.speech_frames.clear()
                    self.triggered = False
                    self.temp_end = 0
                else:
                    self.speech_frames.append(idx)
        return segments

    # 外部调用方按 512 样本滑窗喂入；结束时调用 flush() 取残留段
    def flush(self) -> list[tuple[int, int]]:
        if self.triggered and len(self.speech_frames) >= self.min_speech:
            s = max(0, self.speech_frames[0] - self.pad) * self.win
            e = (self.speech_frames[-1] + self.pad) * self.win
            self.reset()
            return [(s, e)]
        self.reset()
        return []
```

---

## 5. 门禁结论与后续动作

| 门禁项 | 结果 |
|---|---|
| 主环境无 torch（决策三硬约束） | ✅ 达成 |
| onnxruntime 推理可行且廉价（<5% 实时） | ✅ 达成（0.5%） |
| silero-vad 包装可直接用 | ❌ 不可用 → 已改为 ONNX 直跑（见 §3/§4） |
| 检测准确率（真实语音） | ⏳ 待真实音频复测（沙箱无外网） |

**给 T04 的放行建议**：门禁通过。T04 可开工，VAD 抽象层采用 §4 的 `SileroOrtVAD`（需 vendored ONNX + 真实音频标定阈值）。能量 VAD 作为 `backend="energy"` 回退通道保持不变（决策一/三已固化「未装 onnxruntime 时落到 energy」）。

**复测清单（T04 自检）**：
1. vendored `silero_vad_16k_op15.onnx` 路径与加载；
2. 真实录音上 `threshold=0.5 / neg_threshold=0.35` 的端点召回与误触发；
3. 与现状能量 VAD 的帧级输出比对（回归样本），确认 `backend="auto"` 未装时行为等价。
