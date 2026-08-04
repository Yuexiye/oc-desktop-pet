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
| 2026-08-03 | P1 打断状态机：conversation_engine 引入消息代际（generation），用户打断时 LLM/TTS/回调全链路作废旧结果；interrupt() 区分 interrupted/cancelled/completed；pet.py 三入口接入（新消息/语音开始/停止） |
| 2026-08-03 | 一桌宠一助手：HanakoMonitor 按 agent 过滤 WS 事件，只观测本桌宠对应助手会话 |
| 2026-08-03 | 拖拽/渲染/感知优化：异步防抖保存、fps 读角色定义、嵌套 JSON 解析、前台冷却修复 |
| 2026-08-03 | PetWindow 拆分：拆出 AnimationMixin / InteractionMixin / ChatMixin，pet.py 2749→2193 行，清理死代码 |

---

## 打断状态机（P1，2026-08-03）

**目标**：实现全链路打断——用户插话/发消息/点停止时，同时中断 LLM 生成、TTS 合成、音频播放，并区分打断状态，不粗暴丢弃。

**机制**：消息代际（generation）
- `conversation_engine` 维护 `_generation` 计数器，每次 `send(user)` / `interrupt()` 递增
- 每条消息带 `gen`，处理时用 `_is_stale(gen)` 检查是否过期（`gen < 当前`）
- 检查点：LLM 调用后 / TTS 合成前 / TTS 合成后 / 工具执行循环中
- 过期 → 丢弃该消息的 LLM/TTS/回调结果（不播放、不回调）

**打断状态**（`interrupt(reason)` → state）：
| reason | state | 行为 |
|---|---|---|
| `new_message` | `cancelled` | 旧回复作废，转入新对话 |
| `voice_start` | `interrupted` | 进入聆听，旧回复让位（barge-in） |
| `user_stop` | `interrupted` | 停止，保留待恢复 |

**LLM 层打断**：
- Hanako WS：`session_manager.abort()` 真正取消 LLM 思考（`chat_via_hanako` 返回 aborted → 代际检查丢弃）
- 直连 API：requests 无法真正取消，但代际检查会在 LLM 返回后丢弃结果（不播放）

**入口**（pet.py）：`_send_message`（new_message）、`_toggle_voice` 录音开始（voice_start）、`_tts_player.stop()`（播放层）

详见 `core/P1_INTERRUPT_PLAN.md`（实时进度）。

## PetWindow 功能索引（2026-08-03，按行号定位）

> pet.py 2749 行 / 145 个方法。看代码时按此索引**定位到区间**，不要整文件扫描。
> 以下行号基于 2026-08-03 提交，修改后需手动更新。

| 功能域 | 方法 | 行号区间 |
|---|---|---|
| **初始化/生命周期** | `__init__` / `set_hanako_ws` / `set_nurturing` / `closeEvent` | 92-433, 3038-3060 |
| **窗口设置** | `_setup_window` / `_toggle_passthrough` / `_setup_tray` / `_on_tray_activated` / `_toggle_visibility` | 518-557, 559-660 |
| **行为模式** | `_switch_behavior_mode` / `_get_behavior_params` | 665-692 |
| **缩放/透明度** | `_recalc_geometry` / `_apply_scale` / `_adjust_opacity` / `_opacity_up/down` | 711-755 |
| **UI 搭建** | `_setup_ui` / `_setup_animation` | 756-837 |
| **动画** | `_bob_tick` / `_gaze_tick(852)` / `_unified_tick` / `_set_anim_seq` / `_anim_tick` / `_show_anim_frame` / `_get_char_top_y` | 838-970 |
| **气泡定位** | `_reposition_bubble` / `_reposition_overlays` / `_head_local_point` | 971-982, 2028-2040 |
| **菜单** | `_setup_menu` / `_show_context_menu` | 983-1116, 2925 |
| **养成** | `_feed_item` / `_start_work` / `_on_work_finish` / `_build_mission_menu` / `_show_status_summary` | 1117-1355 |
| **输入/语音** | `_toggle_input` / `_toggle_voice` / `_on_voice_status` / `_do_voice_status` | 1356-1424 |
| **TTS/ASR 管理** | `_create_tts_provider` / `_maybe_reload_tts_provider` / `_create_asr_provider` | 1425-1626 |
| **设置/主题** | `_open_settings` / `_apply_settings` / `_refresh_theme_menu` / `_set_theme_mode` / `_open_activity_feed` / `_open_plugin_panel` / `_send_plugin_command` | 1627-1721 |
| **字符加载** | `load_character` / `_store_label_pos` | 1722-1748 |
| **事件过滤/拖拽** | `eventFilter` / `_drag_poll_tick` | 1749-1898 |
| **坐下/边缘** | `_check_edge_sitting` / `_enter_sitting` / `_exit_sitting` | 1899-2011 |
| **点击/抚摸** | `_schedule_pending_click` / `_fire_pending_click` / `_cancel_pending_click` / `_on_pet_pat` / `_on_pet_stroke` / `_pet_play_happy` / `_pet_revert` / `_reset_pet_combo` / `_quick_feed` | 2012-2140 |
| **聊天/发送** | `_toggle_chat` / `_send_message` / `_auto_hide_bubble` / `_on_think_timeout` / `_clear_hanako_bubble` / `_on_emotion_expired` / `_set_surface_emotion` | 2141-2265 |
| **引擎回复** | `_on_engine_reply` / `_do_engine_reply` / `_do_engine_reply_inner` / `_on_engine_status` / `_do_engine_status` / `_do_tool_progress` / `_create_new_session` | 2266-2407 |
| **物理回调** | `get_screen_geometry` / `get_pos` / `get_size` / `move_to` / `on_walk_finished` / `on_bounce_finished` / `on_facing_change` / `set_anim` | 2408-2462 |
| **交互标记/空闲** | `_mark_user_interaction` / `_can_idle_chatter` / `_do_idle_chatter` / `_break_check` / `_break_check_inner` | 2463-2545 |
| **前台/主动** | `_foreground_tick` / `_on_foreground_change` / `_on_proactive_trigger` | 2546-2618 |
| **视线/鼠标** | `_gaze_tick(2619)` / `_get_window_rect` / `_check_reaction_cooldown` / `_on_mouse_nearby` / `_on_mouse_hover` / `_on_mouse_chase` | 2619-2673 |
| **待机/追逐** | `_tick_idle_life` / `_do_look_around` / `_end_look_around` / `_do_stretch` / `_update_chase` / `_end_chase` / `_show_sticker` / `_on_mouse_startled` / `_on_mouse_leave` | 2674-2836 |
| **屏幕感知** | `_on_screen_emotion` / `_do_screen_emotion` / `_on_screen_proactive` / `_do_screen_proactive` | 2837-2924 |
| **气泡/Hanako** | `_show_bubble` / `_show_bubble_impl` / `_on_hanako_state` / `_do_hanako_state` | 2880-2910, 2959-3037 |

**已知问题**：`_gaze_tick` 在 852 与 2619 两次定义（后者覆盖前者，为死代码）；`_show_bubble` 在 2880 与 2897 为分派+实现。拆分时需清理。

## PetWindow 拆分计划（已完成，2026-08-03）

**现状**：`pet.py` 2193 行（原 2749 行），1 个 PetWindow 类 + 6 个 mixin。

**已完成拆分**（从 pet.py 迁移到 pet_mixins/）：
| mixin | 职责 | 行数 |
|---|---|---|
| `AnimationMixin` | 呼吸浮动/视线/动画序列/帧推进 | 74 |
| `InteractionMixin` | 鼠标事件/拖拽/抚摸/边缘坐下/喂食 | 306 |
| `ChatMixin` | 输入框/语音/发送/新建会话 | 155 |
| `AudioMixin`（已有） | 音频 | 61 |
| `GachaMixin`（已有） | 盲盒 | 101 |
| `StatusHudMixin`（已有） | 状态 HUD | 134 |

**约定**：mixin 用鸭子类型访问 PetWindow 属性（`self._xxx`），不显式 import pet；
跨域方法（如 `_unified_tick` 聚合物理/养成/待机）保留在 PetWindow。

**清理**：拆分时删除死代码 `_gaze_tick`（原在 pet.py 852 与 2619 重复定义，后者覆盖前者，现统一在 AnimationMixin）。

---

*最后更新：2026-08-03*
