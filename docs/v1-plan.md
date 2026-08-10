# OC 桌宠 v1 完成标准与任务拆分

> 起草依据：2026-08-07 项目评估。目标 —— 把「AI 伴侣 OS」量级的范围收拢成一个**开箱即用、可测试、单人可维护**的 v1。
> 原则：功能优先于特效，本地优先于云端，1 个角色跑通优先于 N 个角色并行。

---

## 一、v1 定位

**一句话**：单角色（默认「月薪喵」）Live2D AI 桌宠，对话 + 语音 + 基础交互 + 时间/情绪感知，**无 Hanako 也能启动**，新机器 10 分钟内跑起来。

**v1 不做（明确延后，避免范围失控）**：
- ❌ 多桌宠并行 / 跨桌宠协作（`multi_pet_bridge`）
- ❌ 任务系统 / 盲盒 / 养成数值（`core/mission`、`core/gacha`、`core/pet_state`）
- ❌ 手机双通道感知（MacroDroid + 掌心窗 linjian-peek）
- ❌ 屏幕视觉感知 + 视觉模型（需额外 API Key，回归到 v1.1）
- ❌ CRT 特效 / 粒子 / 场景背景等视觉 polish（保底保留基础气泡）
- ❌ ntfy 通知、直播/游戏联动 research
- ❌ 本地 CosyVoice TTS 作为 v1 必装项（改为**可选进阶**，见任务 D3）

> 注：以上内容代码保留、不删，只是不计入 v1 完成标准，避免「半成品功能」撑大验收面。

---

## 二、v1 完成标准（Definition of Done）

任意一台 Windows 10/11 机器，满足以下条件即视为 v1 达成：

| # | 标准 | 可验证方式 |
|---|------|-----------|
| 1 | **安装零歧义**：`requirements.txt` 安装不报错，README 步骤与实际一致（无文档漂移） | 全新 venv 跑一遍 README 快速开始 |
| 2 | **无 Hanako 可启动**：用内置默认身份兜底，不崩溃、不卡死 | 删除 `~/.hanako/` 后 `python main.py` |
| 3 | **Live2D 稳定渲染**：角色可见、自动呼吸/眨眼、按窗口缩放适配，**放大不闪退**（覆盖 Aug 5 回归） | 窗口拖到 300x520 并多次缩放无黑屏/崩溃 |
| 4 | **文字对话可用**：LLM 接入、回复进气泡；Hanako 可用时 tool calling 可运行 | 发一句「你好」收到回复气泡 |
| 5 | **TTS 开箱可用**：至少 1 个在线引擎（MIMO/OpenAI 兼容）默认可用；无 GPU 时 CosyVoice 优雅降级并提示 | 设置里切到在线 TTS 能出声 |
| 6 | **基础交互齐全**：拖拽、边缘吸附坐下、鼠标视线跟随、悬停反应、右键菜单（设置/退出） | 手动验收表全部通过 |
| 7 | **最小感知**：时间分段（早/中/晚/夜）+ 情绪状态机 + 自动衰减，注入对话上下文 | 深夜对话语气与早晨不同 |
| 8 | **设置 + 气泡**：能改 TTS/ASR/行为/主题并持久化 | 改完重启配置保留 |
| 9 | **CI 绿**：`pytest` 在 GitHub Actions 跑通（现有 42 用例 + 新增回归） | push 后 Actions 全绿 |
| 10 | **可打包**：PyInstaller spec 产出可运行 exe | 双击 exe 能起桌宠 |

---

## 三、任务拆分（每项 30–60 分钟，含验收）

### A. 文档与工程基线（先于一切）
- [ ] **A1 修正文档漂移**
  - 描述：把 `ARCHITECTURE.md` 的 pet.py 行数（1316→实际 1517）、目录树（对齐 `avatar/`+`factory.py`）与代码对齐；README 目录结构同步。
  - 验收：文档行数/路径与 `git grep` / `wc -l` 实际一致。
  - 文件：`ARCHITECTURE.md`、`README.md`
- [ ] **A2 清理死引用**
  - 描述：处理 `main.py` 的 `sandbox_runner` 引用（删分支或补文件）；把根目录 `_test_l2d_*.py` 移入 `tests/`。
  - 验收：`python -c "import ast; ast.parse(open('main.py').read())"` 通过；临时脚本不再散落根目录。
  - 文件：`main.py`、`_test_l2d_*.py` → `tests/`
- [ ] **A3 加 CI 骨架**
  - 描述：GitHub Actions 工作流 `pytest`；加 `conftest.py` 与 `pytest.ini`（标记 Qt 测试 skip）。
  - 验收：空仓库 push 也能跑通现有 42 用例；Qt 相关自动跳过。
  - 文件：`.github/workflows/ci.yml`、`conftest.py`、`pytest.ini`

### B. 安装与启动健壮性
- [ ] **B1 默认身份兜底**
  - 描述：无 `~/.hanako/` 时用 `characters/yuexinmiao/identity.md` 内置身份启动，不依赖外部配置。
  - 验收：删 `~/.hanako/` 后启动不抛异常，气泡能出。
  - 文件：`core/hanako_context.py`、`main.py`
- [ ] **B2 TTS/ASR 优雅降级**
  - 描述：无 GPU / 无 API Key 时，TTS 置为「静音 + 气泡文字」，并在设置面板明确提示，不阻断对话。
  - 验收：清空所有 Key 启动，对话正常、无报错弹窗。
  - 文件：`core/conversation_engine.py`、`ui/settings_dialog.py`

### C. Live2D 渲染稳定化
- [ ] **C1 缩放适配固化**
  - 描述：把 Aug 5 修过的「按高度缩放 + 窗口放大到 300x520」逻辑抽成稳定函数，避免回归。
  - 验收：窗口在 200×360 ~ 400×700 区间缩放，角色始终填满且不裁切、不闪退。
  - 文件：`avatar/gl_char_widget.py`、`avatar/live2d_renderer.py`
- [ ] **C2 渲染冒烟测试**
  - 描述：加 `tests/test_live2d_smoke.py`（mock GL 上下文，验证 model3.json 加载、SetScale 调用路径不抛错）。
  - 验收：CI 中该测试通过（headless 下只验加载链路，不验像素）。
  - 文件：`tests/test_live2d_smoke.py`

### D. 对话 + TTS 开箱可用
- [ ] **D1 直连 API 对话路径**
  - 描述：确保 `.env` 配置 OpenAI 兼容 LLM 时，不依赖 Hanako 也能对话（harness_adapter 双源读取）。
  - 验收：仅配 `.env` LLM，发消息收到回复。
  - 文件：`core/harness_adapter.py`、`env_config.py`
- [ ] **D2 在线 TTS 默认可用**
  - 描述：MIMO / OpenAI 兼容 TTS 作为默认可选引擎，设置面板一键切换并即时生效。
  - 验收：选 MIMO 后对话出声；切换无需重启。
  - 文件：`tts_provider/api_tts.py`、`tts_provider/mimo_tts.py`、`ui/settings_dialog.py`
- [ ] **D3 CosyVoice 标记为可选进阶**
  - 描述：把本地 CosyVoice 从「默认推荐」改为「进阶（需独显+4.6GB 模型）」，README 明确其运维成本；保留现有 worker 逻辑不动。
  - 验收：README 的 TTS 章节首屏不再默认推 CosyVoice；新用户走在线 TTS 不需要 CUDA。
  - 文件：`README.md`、`setup_tts.bat`

### E. 交互系统（v1 全集）
- [ ] **E1 拖拽 + 边缘吸附 + 弹跳**
  - 描述：左键拖拽跟手、释放弹跳、拖到边缘坐下；坐标异步防抖保存（已有逻辑固化测试）。
  - 验收：手动验收表通过；重启后位置保留。
  - 文件：`pet_mixins/interaction_mixin.py`、`motion/physics.py`
- [ ] **E2 鼠标视线跟随 + 悬停**
  - 描述：视线跟随鼠标、靠近反应、悬停表情（复用 AnimationMixin / BehaviorMixin 现有实现，补验收）。
  - 验收：鼠标绕桌宠移动，视线随之转。
  - 文件：`pet_mixins/animation_mixin.py`、`pet_mixins/behavior_mixin.py`
- [ ] **E3 右键菜单**
  - 描述：穿透/设置/退出三项可用；穿透模式切换不卡 UI。
  - 验收：右键三项均生效。
  - 文件：`pet_mixins/bubble_mixin.py`

### F. 最小感知
- [ ] **F1 时间分段**
  - 描述：早晨/中午/下午/晚上/深夜/凌晨 + 周末判定，注入 prompt。
  - 验收：改系统时间到凌晨，对话语气变化（或日志 `TimePerception` 输出正确段）。
  - 文件：`core/perception/time.py`、`core/perception/controller.py`
- [ ] **F2 情绪状态机**
  - 描述：happy/sad/thinking/surprised/neutral 五态 + 自动衰减，对话/交互触发；已有 `EmotionStateMachine` 补齐集成与单测。
  - 验收：`test_core` 已有 42 用例覆盖；对话后情绪切换正确。
  - 文件：`core/perception/emotion.py`、`core/conversation_engine.py`

### G. 设置与气泡
- [ ] **G1 设置持久化**
  - 描述：TTS/ASR/行为/主题改动写入 `config.json`，重启保留；主题 light/dark/auto 生效。
  - 验收：改主题→重启→主题保留。
  - 文件：`ui/settings_dialog.py`、`config.py`
- [ ] **G2 气泡基础体验**
  - 描述：气泡显示/自动消失/节流（已有逻辑），不挡操作；TTS 播放时嘴型同步。
  - 验收：连续多条回复气泡不堆叠溢出。
  - 文件：`ui/bubble.py`、`pet_mixins/audio_mixin.py`

### H. 测试与发布
- [ ] **H1 交互冒烟测试**（无头）
  - 描述：`tests/smoke_pet_features.py` 扩到覆盖启动→加角色→发消息→气泡出现的链路（mock Qt Signal）。
  - 验收：CI 全绿；新增回归用例 ≥ 5。
  - 文件：`tests/smoke_pet_features.py`
- [ ] **H2 PyInstaller 打包校验**
  - 描述：`oc_pet.spec` 产出 exe，验证不含 Hanako 也能起；记录打包命令到 README。
  - 验收：干净机器跑 exe 起桌宠。
  - 文件：`oc_pet.spec`、`README.md`

---

## 四、阶段排期（相对，单人节奏）

- **Phase 0（1–2 天）**：A1–A3 + B1–B2。先把地基和文档理顺，CI 跑起来。
- **Phase 1（2–3 天）**：C1–C2 + D1–D3。Live2D 稳定 + 对话/TTS 开箱可用。
- **Phase 2（2–3 天）**：E1–E3 + F1–F2。交互与最小感知。
- **Phase 3（1–2 天）**：G1–G2 + H1–H2。设置/气泡/测试/打包收尾。

> 粗估 **8–12 个工作日**可达 v1。多桌宠、任务、盲盒、手机/屏幕感知作为 v1.1+ 增量，各自独立验收，不阻塞 v1 发布。

---

## 五、验收红线（PM 提醒）
- 不把「能演示」当「完成」——v1 标准 #1–#10 必须逐条可验证。
- 每合并一个任务，CI 必须绿；禁止「本地能跑就行」。
- 范围蔓延预警：任何新功能未经 v1.1 排期评审不得塞进 v1。
