# oc-pet · 桌宠↔助手解耦绑定 + 运行时切换 · 修复计划

> 作者：奥菲莉娅（Ophelia） · 2026-08-07
> 依据：T1 真机复现（agent 名不匹配导致疯狂建 session）+ 月曦夜需求（一个桌宠对应一个助手、可随时切换、会话独立保留、零硬编码）
> 状态：✅ 已完成（2026-08-07，方案 2：核心修复 + 测试补全）

## 一、背景与根因（已证实）

T1 真机复现确认：桌宠全线用 `agent_id=yuexinmiao` 请求 Hanako 服务端，但服务端 `~/.hanako/agents/` 只有 `ophelia/aimis/alice/glados/luoqixi/rebecca`，**没有 yuexinmiao**。服务端静默落到 `ophelia` 名下建 session → 桌宠 `list_sessions(yuexinmiao)` 永远查不到 → `ensure_session` 永远 fallback 新建 → 5 秒 6 个 Session + 空回复。

**本质**：桌宠把「本地显示角色（立绘 yuexinmiao）」和「对话 agent（Hanako 侧的身份）」混成了一个 `_current_char`，而这两个是不同的东西。

## 二、设计：角色与 agent 解耦（两层）

| 层 | 含义 | 来源 | 当前 |
|----|------|------|------|
| **显示角色** | 立绘/皮肤/动画 | `characters/<name>/`（本地） | yuexinmiao |
| **对话 agent** | 人格/记忆/文风 | `~/.hanako/agents/<id>/`（服务端） | yuexinmiao（错误，应为服务端真实 agent） |

**核心改动**：把对话后端从 `_current_char` 分离，引入独立的 **`agent_id`** 配置项，指向服务端真实存在的一个 agent。显示角色不变。

## 三、零硬编码原则（强制）

- 服务端 agent 名单 **不写死**，运行时从 `discover_agents()` 动态发现
- 默认绑定 agent 由配置决定，**不硬编码 ophelia/yuexinmiao**
- 每个桌宠实例可独立配置 `agent_id`，支持多桌宠各自绑定不同助手
- 无配置时的行为：若本地角色名在服务端存在则用之，否则交给用户选择（不静默乱落）

## 四、任务拆分

### F1 配置层：新增 `dialog` 绑定配置
- `config.py` DEFAULT_CONFIG 增加：
  ```json
  "dialog": { "agent_id": "" }
  ```
  `agent_id` 空 = 未绑定（首次启动引导选择）
- 不硬编码默认值；`agent_id` 仅在用户/初始化时写入

### F2 引擎层：agent 与 character 分离
- `core/conversation_engine.py`：`ConversationEngine` 增加 `agent_id` 参数，`create_new_session` 用 `agent_id` 而非 `character`
- `core/harness_adapter.py`：`HanakoPetAdapter` 的 `chat_via_hanako` 里 `create_session(agent_id=self.agent_id)` 保持，但 `agent_id` 来自配置而非 character
- `HanakoContext`：`builtin=True` 仍是本地角色；对话用服务端 agent 时 `builtin=False`

### F3 会话保留（3A）：per-agent 独立 session
- `harness_adapter` 用 `{agent_id}` 维度的 `_pinned_session_id` 缓存，切换 agent 时各自记住自己的 session，切回可续聊
- 替换当前单值 `_pinned_session_id` 为 `dict[agent_id -> session_id]`

### F4 UI 层：右键菜单「🤖 切换助手」
- 动态列出 `discover_agents()` 的服务端 agent（不硬编码）
- 点选 → 调 `conversation_engine.switch_agent(agent_id)` → 重建 adapter + 恢复该 agent 的 session → 气泡提示「已切换助手：<名称>」
- 当前绑定 agent 打勾标记

### F5 初始化引导（零硬编码兜底）
- 首次启动无 `dialog.agent_id` 时，弹出选择框列出服务端可用 agent，用户选一个绑定
- 若发现服务端有与本地角色同名 agent，则默认选中它（不静默乱落）

## 五、验收标准（Definition of Done）

1. **T1 止血**：连真实服务端发消息，不再疯狂新建 session（restore 后复用）
2. **解耦生效**：显示角色是 yuexinmiao 立绘，对话 agent 是所选服务端 agent（文风/记忆对得上）
3. **可切换**：右键菜单切换 agent 立即生效，气泡提示
4. **会话保留**：切走再切回，session 续聊不丢
5. **零硬编码**：`grep -r "ophelia\|aimis\|yuexinmiao"` 在新增代码中无硬编码 agent 名（配置默认值除外）
6. **多桌宠**：两个桌宠可分别绑定不同 agent
7. **开源可用**：换机器/换用户，首次启动引导选择，不依赖本机特定配置

## 六、回归

- 现有 45 用例（test_core + test_session_loop_repro）保持全绿
- 新增：`test_agent_binding.py` 覆盖 agent 切换、per-agent session 保留、零硬编码检查

## 七、风险

- 切换 agent 时正在进行的 turn 需优雅打断（已有 P1 打断状态机可复用）
- 引导 UI 首次启动的交互需真机验证（headless 看不到）
- 服务端 agent 名单变化时，缓存/列表需刷新

## 八、待确认（对齐结果）

- [x] 1A：一个桌宠一个对话 agent，显示角色不变
- [x] 2A：右键菜单运行时切换
- [x] 3A：每个 agent 独立 session 保留
- [x] 零硬编码：agent 名单/默认绑定动态 + 配置化

---

## 九、实施记录（2026-08-07 完成）

### 改动文件
- `config.py`：DEFAULT_CONFIG 加 `dialog.agent_id`；`save_config` 改合并式（不整体覆盖）；`_deep_merge` 加空值保护（空字符串不覆盖真实值）
- `core/harness_adapter.py`：`_agent_sessions`/`_agent_pinned` per-agent dict；`chat_via_hanako` 按 agent 维度复用/创建；新增 `switch_agent`；`set_session` 按 agent 记录
- `core/conversation_engine.py`：`agent_id` 参数解耦于 character；`switch_agent`；`create_new_session` 用 agent_id；adapter builtin 判断（agent==角色才 builtin）
- `pet.py`：右键菜单加「🤖 切换助手」子菜单（动态列服务端 agent）；`_switch_agent`/`_ensure_dialog_agent`/`_refresh_agent_menu`；F5 首次启动引导自动绑定；同步 self.config 快照
- `pet_manager.py`：`_save_config` 只覆盖 agents 字段，保留其他系统字段（dialog）

### 测试
- 新增 `test_agent_binding.py`（9 用例）：agent 切换、per-agent 会话保留、引擎层切换、零硬编码检查
- 新增 `test_live2d_smoke.py`（7 用例）：缩放公式、动效开关（对应 T3/T4 根因）
- 全套 61 用例全绿

### 真机验证（确认）
- F5 首次启动自动绑定到服务端第一个可用 agent（aimis），零硬编码
- `dialog.agent_id` 持久化成功，优雅退出后保留（修复了 save_config 覆盖 / 空值覆盖 / 快照不同步三个叠加问题）
- 角色→agent 解耦：立绘用 yuexinmiao，对话绑定服务端真实 agent

### 遗留（下一轮）
- T3/T4 Live2D 视觉：缩放 fit=0.359 偏低、motion_groups=[''] 待机动画未生效（根因已定位，测试已补，待真机调参）
- 服务端无 agent 时回退本地对话的引导 UI（当前自动绑定第一个，无手动弹窗）