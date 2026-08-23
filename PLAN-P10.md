# Hanako 全功能联动方案 · P10

> 目标：oc-pet 桌宠 ↔ HanaAgent 内测版 深度整合
> 现状：对话 ✅ 记忆 ✅ 定时触发 ✅ | 缺口：插件 · 卡片双向 · 任务同步
> 编制：奥菲莉娅 · 2026-08-21

---

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│  HanaAgent (Electron + Node.js)                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 卡片系统  │ │ 自动化   │ │ 插件系统  │ │ WS 服务 │ │
│  │ (show_card)│ │(cron/every)│ │(27 个插件)│ │(Hono)  │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│         │           │           │           │        │
│         ▼           ▼           ▼           ▼        │
│  ┌─────────────────────────────────────────────────┐ │
│  │  agent 进程（奥菲莉娅）                          │ │
│  │  file.read/write · search.query · subagent 等   │ │
│  └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
          │ WS  (hanako_ws_client)  │ HTTP  (trigger/state)
          ▼                         ▼
┌─────────────────────────────────────────────────────┐
│  oc-pet 桌宠 (Python PySide6)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 对话引擎  │ │ 记忆快照  │ │ 任务系统  │ │ 外部触发│ │
│  │(harness)  │ │(memory_  │ │(mission) │ │(trigger)│ │
│  │          │ │ snapshot)│ │          │ │        │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ 插件工具  │ │ Live2D   │ │ 屏幕/感知 │            │
│  │(registry) │ │ 渲染器   │ │          │            │
│  └──────────┘ └──────────┘ └──────────┘            │
└─────────────────────────────────────────────────────┘
```

---

## 维度一：对话（已通 ✅）

**现状**：`harness_adapter.py` 的 `prefer_hanako` 模式走 WS 透传对话到 Hana→Hanako session 处理→返回文本。`[emotion:xxx]` 标签解析驱动桌宠表情动作。

**对接点**：`core/hanako_ws_client.py` → Hana WS 服务 → `harness_adapter.chat_via_hanako()`

**缺口**：无。

---

## 维度二：记忆（已通 ✅）

**现状**：`memory_snapshot.py` 直接读写 `~/.hanako/agents/<agent_id>/memory/`（memory.md/today.md/longterm.md/week.md/facts.md）。桌宠的记忆 = Hana Agent 记忆，同一文件系统天然共享。

**对接点**：文件系统（`~/.hanako/agents/`）

**缺口**：无。

---

## 维度三：定时触发（已通 ✅）

**现状**：P4 建的通用外部触发接收器（`core/external_trigger_receiver.py`）监听 `127.0.0.1:8988`，POST `/trigger` 驱动桌宠气泡+情绪。P6 统一走 EventBus。任何外部调度器（Hana 自动化 / 手机脚本 / 另一只桌宠）都可触发。

**对接点**：HTTP POST /trigger → EventBus → QTimer 转主线程

**缺口**：无。

---

## 维度四：插件（需打通 🔶）

### 现状

- 桌宠 `tool_registry.py` 扫描 `~/.hanako/plugins/`（Hana 全局插件目录）和 `oc-pet/plugins/`（本地插件目录）
- `tool_executor.py` 以 Node.js subprocess 执行插件工具
- 当前默认关（`plugin_tools.enabled=false`），因为 plugins/ 目录为空

### 缺口

| 问题 | 详情 |
|------|------|
| Hana 插件格式兼容 | Hana 插件是 Electron 插件（manifest.json + tools/*.ts），桌宠需要 Node.js 运行时执行 .ts 文件（需编译或改 exec 方式） |
| 工具返回值对接 | 桌宠 ToolExecutor 期望 stdout JSON，Hana 插件工具输出格式可能不一致 |
| 安全边界 | 桌宠 subprocess 不走 Hana 的凭证/票据体系 |

### 方案

**短期（P1 优先级）**：
- 保持 `plugin_tools.enabled=false`（默认关）
- 在桌宠 config 中增加 `plugin_tools.hana_plugins_dir` 配置项，指向 Hana 插件目录的绝对路径
- 预置一个"白名单"验证：只加载 manifest.json 中 `trust: "restricted"` 且声明了 `capabilities` 的插件

**中期（P2 优先级）**：
- 在 Hana 侧增加一个 Agent 工具：`pet.invoke_tool(tool_name, args)` → 通过 WS 透传给桌宠执行
- 这样插件工具走 Hana 的安全体系，桌宠只做执行

**涉及文件**：`core/tool_registry.py`、`core/tool_executor.py`、`core/hanako_ws_client.py`（新增 WS 消息类型）

---

## 维度五：卡片双向（需打通 🔶）

### 现状

- **单向（卡片→桌宠）**：P4 调音台卡片通过 `file.read/write` 读写 `config.json`，调整 TTS 音量后写回
- **单向（桌宠→对话）**：桌宠状态变化（情绪/动画/任务完成）通过 EventBus 广播，但不会主动推送到 Hana 对话卡片

### 缺口

| 问题 | 详情 |
|------|------|
| 写回不生效 | 写回 config.json 后需重启桌宠才生效，无热加载机制 |
| 桌宠→卡片推送 | 桌宠状态变了，钉在白板上的卡片不会自动刷新（需手动 reload） |
| 卡片多实例 | 同一个桌宠的卡片钉多个副本，状态不统一 |

### 方案

**热加载（短期）**：
- 桌宠侧增加 `config_watchdog`：监听到 config.json 文件变化 → 重新加载配置（文件变化 QFileSystemWatcher）
- 改动：`config.py` 加 `watch_config(path)` → `_reload_config()` → emit EventBus

**桌宠→卡片推送（中期）**：
- 桌宠通过 WS 发送 `pet_state_update` 事件到 Hana
- Hana 侧订阅该事件 → 刷新钉在白板上的相关卡片
- 需要 Hana 侧支持"卡片外部刷新"接口（待确认 Hana API 是否支持）

**涉及文件**：`config.py`（热加载）、`core/hanako_ws_client.py`（新增推送事件类型）

---

## 维度六：任务同步（需打通 🔶）

### 现状

- 桌宠：`core/mission/` 8 文件 831 行，独立运行（每日任务刷新、任务追踪、奖励结算）
- Hana：`automation` 模块（cron/every 定时任务，11 个已配置任务）

### 缺口

| 问题 | 详情 |
|------|------|
| 双系统无交集 | 桌宠任务完成 Hana 不知道，Hana 定时任务桌宠不知道 |
| 重复提醒 | 桌宠的 break_reminder 和可能的 Hana 自动休息提醒可能打架 |

### 方案

**桌宠任务完成 → Hana 通知（短期）**：
- 桌宠 `mission_tracker` 完成任务时，通过 EventBus 发 `mission_completed` 事件
- 已有：`EventBus.emit("mission_completed", mission_id=...)`（已在 `core/mission/mission_tracker.py:159`）
- 缺口：没有桥接到 Hana 侧
- 方案：在 `mission_tracker` 完成任务时，如果 WS 已连接，通过 `hanako_ws_client` 发送 `pet_mission_done` 事件到 Hana → Hana 可以记录日志或触发自动化

**Hana 定时任务 → 桌宠（短期）**：
- P4 的 `/trigger` 接口已实现——Hana 自动化可直接 POST 到桌宠
- 需要文档：告诉用户如何在 Hana 自动化里配置 `POST http://127.0.0.1:8988/trigger`

**提醒去重（中期）**：
- 桌宠 config 加 `reminder_source: "local" | "hana" | "both"`——用户选择提醒来源
- `"hana"` 模式：桌宠关掉本地 break_reminder/work_reminder，由 Hana 自动化接管

**涉及文件**：`core/mission/mission_tracker.py`（WS 推送）、`core/hanako_ws_client.py`（新增消息类型）、`config.py`（reminder_source 配置）

---

## 优先级总表

| 维度 | 优先级 | 工程量 | 依赖 | 交付物 |
|------|--------|--------|------|--------|
| 对话 | ✅ 已通 | — | — | — |
| 记忆 | ✅ 已通 | — | — | — |
| 定时触发 | ✅ 已通 | — | — | 配置文档 |
| 插件 | P2 | 中 | 需确认 Hana 插件工具格式兼容性 | 白名单加载 + 文档 |
| 卡片双向 | P2 | 中 | 热加载（config.py）+ Hana 侧卡片刷新 API | config 热加载 + 推送 |
| 任务同步 | P1 | 小 | WS 消息类型扩展 | `mission_completed` WS 推送 + 配置文档 |

---

## 执行建议

1. **先做"任务同步"（P1）**：工程最小（`mission_tracker.py` 加一条 WS 推送），效果最明显（桌宠任务完成 → Hana 知道）
2. **再出"插件工具"方案文档**：需要确认 Hana 插件工具格式与桌宠 ToolExecutor 的兼容性，先摸底再动手
3. **卡片双向等待 Hana 侧 API 稳定**：内测版卡片系统还在迭代，等热加载和外部刷新接口明确后再做

---

*—— 奥菲莉娅*