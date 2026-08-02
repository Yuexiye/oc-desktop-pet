# 架构索引

> 给新对话的助手看：读完这份文档就能理解代码结构，不用逐文件扫描。

## 目录结构

```
oc-pet/
├── pet.py                    # 主窗口（1700+ 行），UI + 事件循环 + 所有交互
├── main.py                   # 入口
├── config.py                 # 配置加载/保存
├── env_config.py             # .env 环境变量读取
│
├── core/                     # 核心逻辑（无 UI 依赖）
│   ├── conversation_engine.py  # 对话引擎：LLM + TTS + 工具调用一体化
│   ├── capability_registry.py  # 能力路由器：关键词→直接执行，跳过 LLM
│   ├── perception.py           # 感知控制器：时间/情绪/屏幕/日报/Session/权限
│   ├── hanako_context.py       # Hanako 配置读取器：身份/记忆/模型/Session
│   ├── hanako_monitor.py       # Hanako 状态监控（TODO/通知/对话）
│   ├── narrative_engine.py     # 叙述引擎：空闲时微事件生成
│   ├── enhanced_environment.py # 增强环境扫描（窗口→结构化快照）
│   ├── memory_snapshot.py      # 记忆快照导出/导入
│   ├── tool_registry.py        # 插件工具注册表（扫描 manifest.json）
│   ├── tool_executor.py        # 插件工具执行器（Node.js subprocess）
│   ├── harness_adapter.py      # LLM 适配器（读 Hanako 配置 → API 调用）
│   ├── phone_activity.py       # 手机活动感知（MacroDroid HTTP 上报）
│   ├── phone_receiver.py       # 手机数据 HTTP 接收器
│   ├── multi_pet_bridge.py     # 多桌宠协作桥接
│   ├── window_interaction.py   # 窗口互动（桌宠靠近当前窗口）
│   ├── event_bus.py            # 轻量事件总线（发布/订阅，解耦任务系统）
│   ├── pet_state.py             # 养成状态管理器（衰减/挂起池回流/模式；发 attribute_changed）
│   ├── work/                   # 工作系统（WorkTimer + 注册表）
│   ├── items/                  # 物品系统
│   ├── save/                   # 存档（PetSaveManager）
│   ├── mission/                # 任务系统（池/追踪/生成/奖励/模板/编排）
│   └── gacha/                  # 盲盒系统（奖池/引擎）
│
├── ui/                       # UI 组件
│   ├── tts_player.py           # TTS 播放器（PySide6 QMediaPlayer）
│   ├── bubble.py               # 对话气泡
│   ├── settings_dialog.py      # 设置对话框
│   ├── plugin_panel.py         # 插件面板
│   └── startup_screen.py       # 启动画面
│
├── avatar/                   # 渲染系统
│   ├── base.py                 # AvatarRenderer 抽象接口
│   └── sprite_renderer.py      # 2D 帧精灵渲染器（自动扫描帧目录）
│
├── motion/                   # 运动系统
│   ├── physics.py              # 物理引擎（重力/弹跳/惯性）
│   ├── behavior.py             # 行为参数（idle/walk/模式切换）
│   ├── mouse_tracker.py        # 鼠标追踪
│   ├── foreground_watcher.py   # 前台窗口检测（ctypes Win32 API）
│   └── action_linker.py        # 动作联动
│
├── tts_provider/             # TTS 引擎
│   ├── base.py                 # TTSProvider 抽象接口
│   ├── cosyvoice.py            # CosyVoice2 本地模型
│   ├── api_tts.py              # OpenAI 兼容 API
│   └── mimo_tts.py             # 小米 MiMo TTS
│
├── asr_provider/             # ASR 引擎（语音输入）
├── plugins/                  # 桌宠本地插件
├── characters/               # 内置角色资源
├── docs/                     # 文档
│   ├── hatch-pet-guide.md      # 精灵生成指南（atlas 格式）
│   ├── pet-creation-mouth-frames.md  # 嘴型帧规范
│   └── pet-creation-mouth-frames.docx # 嘴型帧规范（Word）
│
└── tests/                    # 测试
```

## 核心数据流

```
用户输入
  ↓
pet.py._on_user_submit()
  ↓
ConversationEngine.send()
  ↓
后台线程._process_message()
  ├── 1. 帮助关键词？ → 直接返回
  ├── 2. 能力路由器匹配？ → CapabilityRouter.route() → 直接执行
  └── 3. LLM + 工具调用 → HanakoPetAdapter.chat()
                               ↓
                         on_reply(text, emotion, anim, audio_path)
                               ↓
                         pet.py._on_engine_reply()
                           ├── 气泡显示
                           ├── TTS 播放（on_start → 嘴型，on_end → idle）
                           └── 动画切换
```

## 感知系统数据流

```
PerceptionController（统一入口）
  ├── TimePerception        → 时段/周末
  ├── EmotionStateMachine   → 情绪（自动衰减）
  ├── SchedulePerception    → 日程
  ├── ScreenPerception      → 截图 + 视觉分析 + ActivityEvent
  │     ├── ForegroundWatcher.on_change → 事件触发截图
  │     ├── 黑名单过滤
  │     └── VISION_PROMPT → JSON → ActivityEvent
  ├── ProactiveScheduler    → 主动对话触发
  ├── PetPermissions        → 权限开关
  └── HanakoContext         → Session/记忆读取

build_context() → 注入 LLM prompt
```

## 关键类速查

| 类 | 文件 | 职责 |
|---|---|---|
| `MainWindow` (pet.py) | pet.py | 主窗口，UI + 事件循环 |
| `ConversationEngine` | conversation_engine.py | LLM + TTS + 工具调用 |
| `CapabilityRouter` | capability_registry.py | 关键词→能力快速路由 |
| `PerceptionController` | perception.py | 统一感知入口 |
| `ScreenPerception` | perception.py | 截图 + 视觉分析 |
| `ActivityEvent` | perception.py | 结构化活动事件 |
| `ScreenEvent` | perception.py | 截图元数据 |
| `PetPermissions` | perception.py | 权限开关 |
| `HanakoContext` | hanako_context.py | Hanako 配置读取 |
| `HanakoPetAdapter` | harness_adapter.py | LLM API 适配 |
| `ToolRegistry` | tool_registry.py | 插件工具发现 |
| `ToolExecutor` | tool_executor.py | 插件工具执行 |
| `SpriteRenderer` | sprite_renderer.py | 2D 帧精灵渲染 |
| `TTSTtsPlayer` | tts_player.py | TTS 音频播放 |
| `ForegroundWatcher` | foreground_watcher.py | 前台窗口检测 |
| `EmotionStateMachine` | perception.py | 情绪状态机 |
| `PhysicsEngine` | physics.py | 物理引擎 |

## 外部依赖

| 依赖 | 用途 |
|---|---|
| PySide6 | GUI、音频播放 |
| PIL/Pillow | 截图、图像处理 |
| requests | API 调用 |
| Hanako 本体 | 配置、插件、模型、Session |

## Hanako 集成点

```
~/.hanako/
├── provider-catalog.json     → 模型配置
├── agents/<agent>/
│   ├── identity.md           → 角色身份
│   ├── ishiki.md             → 行为规则
│   ├── description.md        → 描述
│   ├── pinned.md             → 置顶规则
│   ├── memory/               → 记忆文件
│   ├── sessions/*.jsonl      → Session 历史
│   ├── config.yaml           → Agent 配置
│   └── pet/frames/           → 精灵帧资源
├── plugins/                  → 插件（ToolRegistry 扫描）
└── pets/tts_cache/           → TTS 缓存
```

## 任务系统 / 事件总线 / 盲盒（2026-07-27 新增，依据 03-成长计划）

设计原则：**任务系统只监听现有系统的事件，不修改感知/工作/对话的内部逻辑与数据流（零侵入）**。
事件总线 `core/event_bus.py` 解耦发布者与订阅者；`MissionManager` 在 `set_nurturing()` 注入并订阅全部事件。

事件源（emit 点，均零侵入现有数据流）：
- `pet.py`：`chat_completed` / `work_completed` / `item_used`(喂食) / `window_interacted` / `screen_analyzed` / `proactive_triggered` / `level_up`(经 `QTimer.singleShot(0)` 延迟发射，避免奖励结算重入 `add_exp` 递归)
- `core/phone_activity.py`：`phone_event`（每次手机活动上报）
- `core/multi_pet_bridge.py`：`multi_pet_event`（子总线事件转发到全局总线）
- `core/mission/mission_manager.py`：`item_collected`（盲盒抽中虚拟物品）/ `gacha_opened`
- `core/pet_state.py`：`attribute_changed`（属性按 5 点桶变化才发，避免每秒刷屏）

```
事件总线事件 ──▶ MissionTracker.on_event ──▶ 匹配 active 任务条件 ──▶ 完成 ──▶ MissionRewardGrantor 结算
                                                                                    │
                                                                                    └─▶ EventBus.emit("mission_completed") ─▶ pet.py 气泡
双货币：通用货币复用 PetSave.money（03 的 credits）；盲盒能量为新增 gacha_energy（封顶 gacha_energy_max）。
盲盒：MissionManager.open_gacha() 消耗 gacha_energy/券，GachaEngine 按权重+保底抽取（见 core/gacha/）。
物品均为虚拟 token（GachaItem: id/name/icon/稀有度），抽中 item_type=="item" 即发 item_collected，
任务系统按 item_id 去重统计"不同物品"（成就/周常收集类）。无物理库存需求。
```

| 类 | 文件 | 职责 |
|---|---|---|
| `EventBus` | event_bus.py | 进程级发布/订阅，类方法风格 |
| `MissionManager` | mission/mission_manager.py | 编排：池+追踪+结算+盲盒，订阅事件总线 |
| `MissionPool` | mission/mission_pool.py | 激活任务、每日/每周 04:00 刷新、进度持久化 |
| `MissionTracker` | mission/mission_tracker.py | 事件→条件推进→完成判定 |
| `MissionRewardGrantor` | mission/mission_reward.py | 奖励结算（money/gacha_energy/exp/徽章） |
| `MissionGenerator` | mission/mission_generator.py | 按等级从模板抽样 |
| `GachaEngine` | gacha/gacha_engine.py | 权重抽奖 + 各池独立保底 |

> 注：早期"感知/渲染/运动"等章节仍沿用较早描述（如 perception.py 现含多个子模块、pet.py 行数等），
> 与本次新增的"任务系统/事件总线/盲盒"章节并列阅读即可。整体深度校对待排期。

## 近期变更

| 变更 | 内容 |
|---|---|
| PET-02 | TTS 口型回调 + 帧目录自动扫描 |
| PET-03 | 三种截图模式 + 隐私黑名单 |
| PET-04 | JSON 结构化活动事件 |
| PET-05 | 日报生成（Obsidian） |
| PET-06 | Session 识别（只读） |
| PET-07 | 跨 Session 协作（列表） |
| PET-08 | PetPermissions 权限开关 |
| 能力路由器 | 关键词→直接执行，跳过 LLM |
| 清理 | 删除 hanako_bridge.py 死代码 |
| 2026-07-27 | 新增事件总线 + 任务系统（core/mission）+ 盲盒（core/gacha）+ PetSave 双货币字段（gacha_energy 等）；事件源覆盖 pet.py / phone_activity / multi_pet_bridge / mission_manager / pet_state 共 10+ 处，零侵入现有数据流 |
| 2026-07-27 | attribute_changed 实质化：PetStateManager 按 5 点桶发属性变化事件；追踪器 attribute 条件改 `<attr>:<阈值>` 格式；新增 mood/hunger/energy/health 类任务 |

---

*最后更新：2026-07-27*
