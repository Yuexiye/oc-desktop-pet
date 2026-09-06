# 我为什么存在

> oc-pet 的 7 条护城河

---

## 一句话

oc-pet 是**运行在 Hanako 生态里的 AI 桌宠**——不是独立 AI，而是复用你机器上已有的 Hanako 配置（身份、记忆、模型、工具），把桌宠变成 AI 助手的可视化入口。

---

## 7 条护城河

### 1. Hanako 生态位

- 实时读取 Hanako 状态（TODO/通知/对话回复）
- 对话同步：Hanako 有新回复时，桌宠显示气泡 + 播放 TTS
- 记忆读取：读取 Hanako 的置顶记忆和最近对话记录
- 多 agent 发现：自动扫描 `~/.hanako/agents/`

**竞品对比**：Amadeus 是自包含 LLM Provider，没有"读外部已有 agent 记忆"的能力。starter 是纯身体，没有大脑。

---

### 2. 感知广度

- **屏幕感知**：定时截屏 + 视觉模型分析，注入对话上下文
- **屏幕情绪检测**：从屏幕内容推断用户情绪（如"看视频" → happy）
- **前台窗口监听**：检测用户正在使用的应用
- **手机双通道**：
  - MacroDroid 直连（常态感知）：前台 App 上报、活动摘要、空闲检测
  - linjian-peek 掌心窗（按需增强）：手机截图、电量、网络、远程控制

**竞品对比**：Amadeus 目录未见感知模块（待验证）。starter 无感知系统。

---

### 3. 多桌宠协作

- 多窗口并行：每个 Hanako agent 独立运行一个桌宠
- 桌宠间可互相"聊天"/反应/关心/送礼物
- 社交冷却控制（1800s）

**竞品对比**：Amadeus 单实例。starter 单实例。

---

### 4. 轻量 + 已有测试资产

- 435 个测试（pytest）
- `pet.schema.json` 配置 schema
- 单文件可启动（`python main.py`）

**竞品对比**：Amadeus 0.1α 阶段，测试规模未知。starter 无测试资产。

---

### 5. 角色包体系

- `characters/` 目录 + `pet.json` 配置
- 引导流程选择角色包
- 支持自定义精灵 + 内置回退
- per-pet 独立配置（TTS 引擎/音色与对话助手）

**竞品对比**：Amadeus 的 VTS 预设更偏"给 VTube Studio 用户用"，不是产品化结果。

---

### 6. 记忆系统

- 记忆快照：导出/导入 Agent 记忆，支持 overwrite/smart/skip_existing 合并
- 动态记忆预算：自动按模型 context 1% 计算
- BM25+RRF+事实库+反思
- 置顶记忆：读取 pinned-memory.json

**竞品对比**：Amadeus 无记忆系统。starter 无记忆系统。

---

### 7. 主动行为

- 微事件生成：空闲时自动生成小事件（观察/关心/笑话/提问/问候）
- 本地模板兜底：LLM 不可用时用预设模板
- 情境缓存 + 冷却控制：避免重复内容
- 规则引擎：对话空闲时长 + 前台窗口分类 → 自动搭话

**竞品对比**：Amadeus 无主动行为。starter 无主动行为。

---

## 叙事缺失（现状）

- 22 篇 docs 绝大多数是内部过程记录
- 无对外叙事文档
- README 是功能清单，不是"我为什么存在"

**后果**：star 落后（oc-pet 4★ / starter 3★ / 思路篇 34★）

---

## 下一步

- [x] WHY.md（本文件）
- [x] SETUP-FOR-AI.md（整段复制给 AI 装）
- [x] 模型适配文档（三步适配）
- [x] 已知坑前置（FAQ）
- [x] CHANGELOG.md（发布说明）
- [x] version.py（版本号）

---

## 新增功能（v0.9.0）

### 感知系统增强
- 🎵 **SMTC 媒体感知** — 读取正在播放的媒体信息（歌曲名、艺术家、专辑）
- 📸 **屏幕观察进程化** — 独立进程截屏，不卡 UI

### 记忆系统增强
- 📊 **Token/费用统计** — 按会话/按天统计，可配预算上限
- 🔄 **记忆自动维护** — 空闲时自动归纳/去重/修剪/重要性衰减
- ⚡ **异步写入** — 批量 flush + 异步落盘，不阻塞对话

### 系统管理
- 🏥 **子服务健康四态** — 服务状态可视化（enabled/running/ready/last_error）
- 🎛️ **主动能力面板** — 统一展示开关 + 运行状态 + 费用边界
- 🔒 **隐私暂停** — 一键停掉所有隐私敏感能力
- 💾 **备份恢复** — 完整备份 + SHA-256 校验 + 一键恢复

### 工程优化
- 🎮 **插件 KV 存储** — 插件自带配置页 + 持久存储
- ⚙️ **动作槽位自动映射** — motion 文件名自动映射到语义槽位
- 🤖 **感知走 AI 发挥** — 有 LLM 时走生成，无 LLM 才降级模板

### 分发优化
- 🐍 **免装 Python** — 嵌入式 Python 引导 + 双击即用
- 📦 **Requirements 合并** — 8 个文件 → 2 个（完整 + 最小）

---

## 致谢

- **[Code-Amadeus / Amadeus](https://github.com/Code-Amadeus/Amadeus)** — 实时多模态桌面 Agent。Live2D 渲染架构、HUD 设计语言、情感驱动参数体系均以此为参照。
- **[Soullink Emotion SDK](https://github.com/nanlingyin/soullink-emotion-sdk)** — TypeScript / MIT。Embody 层（情绪→参数映射抽象）的设计思想来源。
- **[Live2D CubismWebSamples](https://github.com/Live2D/CubismWebSamples)** — Live2D 官方免费示例模型。
- **[Hanako](https://github.com/liliMozi/openhanako)** — AI 助手框架。桌宠的对话、记忆、工具调用、多助手协作均复用 Hanako 配置。
- **[N.E.K.O.](https://github.com/Project-N-E-K.O/N.E.K.O)** — Apache 2.0。主动对话决策管线、情绪状态机设计语言等参考来源。
