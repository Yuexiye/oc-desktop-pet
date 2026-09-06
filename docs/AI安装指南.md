# SETUP-FOR-AI.md

> 整段复制给 AI 装 oc-pet。用户整体复制本文件给 AI（如 Claude Code / Cursor / Windsurf），AI 按步骤完成安装。

---

## 任务

帮用户安装 oc-pet（AI 桌面伴侣），基于 PySide6，深度集成 Hanako 生态。

---

## 环境要求

- **Python**: 3.10+
- **操作系统**: Windows 10/11
- **Hanako**: 已安装并配置（桌宠读取 `~/.hanako/` 下的配置和角色数据）
  - Hanako 项目：<https://github.com/liliMozi/openhanako>
  - 安装后运行**至少一次**（生成 `~/.hanako/agents/` 与 `provider-catalog.json`）

---

## 安装步骤

### 1. 克隆仓库

```bash
git clone <repo-url> oc-pet
cd oc-pet
```

> 首次拉取如果因网络中断报 `early EOF`，重试一次即可（可加 `git config http.postBuffer 524288000` 增大缓冲）。

### 2. 安装依赖

**方式 A：完整安装（推荐）**

```bash
# 推荐：创建 venv，避免污染系统 Python
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

**方式 B：最小安装（核心功能）**

```bash
pip install -r requirements-minimal.txt
```

**方式 C：免装 Python（嵌入式）**

```bash
# 双击 setup-runtime.bat
# 或运行脚本
python scripts/embedded_python_bootstrap.py
```

> 方式 C 会下载嵌入式 Python（约 110MB），无需预装 Python。
> 适合不想装 Python 的用户，或需要分发给他人时使用。

### 3. 下载 Live2D 模型（首次）

桌宠不随仓库分发 Live2D 模型文件。需要自备或下载官方示例模型：

```bash
# 方式 A：下载官方 Haru 示例模型（推荐新手）
python tools/fetch_free_live2d_sample.py haru

# 方式 B：按角色目录 README 下载
# 见 characters/shizuku/README.md
```

**模型硬要求**（三条）：
- ✅ **裸文件**：解压后能看到 `.model3.json` + `.moc3` + 贴图
- ✅ **没加密**：模型文件可直接导入 VTube Studio
- ✅ **Cubism 3+**：支持 Cubism 3 或 4 的模型

> 💡 **判断标准**：能导入 VTube Studio 的模型就能用。下单前问卖家："是否提供 model3.json 素材文件？"

### 4. 启动

```bash
python main.py
```

或双击 `start_pet.bat`。

首次启动由引导流程选择角色包。

---

## 跑起来你会看到什么

- 🖱️ 眼睛跟着鼠标转（视线跟随）
- 👁️ 自动眨眼（每隔几秒）
- 😴 5 分钟没操作 → 犯困打哈欠
- 😵 15 分钟没操作 → 打瞌睡倒头
- 💬 10 分钟空闲 → 桌宠主动找你说话
- 🖱️ 左键拖动桌宠，释放后弹跳
- 📌 拖到屏幕边缘 → 桌宠坐下

---

## 配置

### config.json

```json
{
  "behavior": "normal",           // 行为模式: quiet/normal/active/cling
  "window_interaction": {
    "enabled": true,
    "cooldown_seconds": 30        // 窗口互动冷却时间（秒）
  },
  "memory": {
    "budget_chars": 0,            // 记忆预算字符数（0=自动）
    "budget_percent": 1.0         // 自动模式：模型 context 的百分比
  },
  "tts": {
    "enabled": true,
    "provider": "cosyvoice",      // TTS 引擎: cosyvoice(本地) / edge(微软免费) / mimo(在线) / api
    "volume": 0.8
  },
  "asr": {
    "provider": "whisper_local"   // ASR 引擎: whisper_local/mimo/api
  },
  "proactive": {
    "enabled": true,
    "cooldown_minutes": 10        // 主动对话冷却时间
  },
  "screen": {
    "enabled": true,
    "interval": 120,              // 截屏间隔（秒）
    "blur": true                  // 截图模糊（隐私保护）
  }
}
```

### .env 文件

```env
# LLM（可选，优先使用 Hanako 配置）
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat

# TTS（可选）
TTS_PROVIDER=mimo
TTS_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
TTS_API_KEY=sk-xxx

# ASR（可选）
ASR_PROVIDER=whisper_local

# 视觉模型（可选，用于屏幕感知）
VISION_BASE_URL=https://api.siliconflow.cn
VISION_API_KEY=sk-xxx
VISION_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# ntfy 通知（可选）
NTFY_TOPIC=your-topic-name

# 手机活动感知 - MacroDroid 直连（可选）
PHONE_RECEIVER_PORT=8077
PHONE_AUTH_TOKEN=your-secret-token

# 掌心窗 - linjian-peek 集成（可选）
LINJIAN_URL=https://xxx.onrender.com
LINJIAN_TOKEN=your-linjian-token
```

---

## 本地 CosyVoice TTS 部署（从零）

桌宠默认用**本地 CosyVoice2** 配音（无需联网/付费）。它跑在独立子进程里，不卡 UI；合成延迟约 **8–10 秒/句**（GPU + fp16）。

> 前置：Windows + **NVIDIA 显卡**（本地 TTS 需要 CUDA）；Python 3.10~3.12。
> 没有独显的机器会自动给出告警，可在「设置 → TTS」改用 MIMO / 在线 TTS。

### 一键引导

```bash
# 1) 把 cosyvoice-tts 仓库放到 oc-pet 的同级目录（两仓库并排即零配置），或：
setup_tts.bat --cosyvoice-repo https://your.git/cosyvoice-tts.git

# 2) 引导脚本会：建 venv → 装 CUDA 版 torch + 依赖 → 获取 cosyvoice-tts
#    → 下载 CosyVoice2-0.5B 模型（约 4.6GB，需联网）→ 写 .env
```

引导完成后直接 `start_pet.bat` 即可。

---

## 常见问题

### Q: 桌宠不说话？
A: 检查 LLM API 配置。桌宠会自动使用 Hanako 的配置，如果 Hanako 没配置，需要在 `.env` 中指定。

### Q: TTS 不工作？
A: TTS 是可选功能，不影响文字对话。在设置面板切换 TTS 引擎。

### Q: 屏幕感知不触发主动对话？
A: 当前版本屏幕感知只触发情绪，不触发主动对话。主动对话由 ProactiveScheduler 根据空闲时间和前台窗口触发。

### Q: 如何添加更多桌宠？
A: 在设置面板的"角色包"中添加，或在 `~/.hanako/agents/` 下创建新的 agent 目录。

### Q: ntfy 通知怎么用？
A: 1) 手机安装 ntfy app（Android/iOS）；2) 订阅一个 topic；3) 在 `.env` 中配置 `NTFY_TOPIC=your-topic`。

### Q: live2d-py 没装怎么办？
A: 运行 `pip install live2d-py`。或检查 `requirements.txt` 是否包含 live2d-py。

### Q: auth_token 没配置怎么办？
A: 桌宠会自动生成随机 token。如需固定 token，在 `.env` 设置 `PHONE_AUTH_TOKEN` 和 `EXTERNAL_TRIGGER_TOKEN`。

---

## 故障排查

### 启动报错：ModuleNotFoundError: No module named 'live2d'

```bash
pip install live2d-py
```

### 启动报错：ModuleNotFoundError: No module named 'PySide6'

```bash
pip install PySide6 PySide6-Addons
```

### 桌宠加载模型失败

1. 检查模型路径是否正确（config.json 的 `character` 字段）
2. 检查模型文件是否完整（`.model3.json` + `.moc3` + 贴图）
3. 检查模型是否加密（需裸文件）
4. 运行 `python tools/fetch_free_live2d_sample.py haru` 下载官方示例模型测试

### 测试跑不通过

```bash
python -m pytest tests/ -q
```

如有失败，检查：
- PySide6 版本是否匹配（requirements.txt 已锁定）
- live2d-py 是否安装
- 模型文件是否完整

---

## 许可

本项目采用**双重许可**：

- **开源许可**：[GNU AGPL v3](https://www.gnu.org/licenses/agpl-3.0.html) — 开源免费，但修改必须开源
- **商业许可**：闭源使用需购买商业授权，详见 [COMMERCIAL-LICENSE.md](./COMMERCIAL-LICENSE.md)

---

## 致谢

- **[Code-Amadeus / Amadeus](https://github.com/Code-Amadeus/Amadeus)** — 实时多模态桌面 Agent。Live2D 渲染架构、HUD 设计语言、情感驱动参数体系均以此为参照。
- **[Soullink Emotion SDK](https://github.com/nanlingyin/soullink-emotion-sdk)** — TypeScript / MIT。Embody 层（情绪→参数映射抽象）的设计思想来源。
- **[Live2D CubismWebSamples](https://github.com/Live2D/CubismWebSamples)** — Live2D 官方免费示例模型。
- **[Hanako](https://github.com/liliMozi/openhanako)** — AI 助手框架。
- **[N.E.K.O.](https://github.com/Project-N-E-K.O/N.E.K.O)** — Apache 2.0。主动对话决策管线、情绪状态机设计语言等参考来源。
