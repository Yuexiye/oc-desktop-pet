# oc-pet 桌宠优化计划书

> 优先级路线：Miku Live2D → 对话引擎 → 交互体验 → 内测接入 → 月薪喵 → 功能深化  
> 当前阶段：Phase 1 — Miku Live2D 情绪映射 **（已完成 ✓）**
> 编制：奥菲莉娅 · 2026-08-21

---

## Phase 1：Miku Live2D 情绪映射（第一刀）

### 现状

`characters/miku/pet.json` 中全部 6 种情绪映射到 `idle`，但模型实际资源：

| 资源 | 数量 | 文件 |
|------|------|------|
| Motion | 7 个 | idle, happy, waving, angry, sad, thinking, touch |
| Expression | 8 个 | 比心, 唱歌, 脸红, 葱, 圈圈, 前倾, QQ人, 水印 |
| 物理 | 1 个 | physics3.json |
| 口型组 | 已声明 | LipSync（Ids 为空，需确认是否启用） |

### 涉及文件

| 文件 | 操作 | 改动量 | 状态 |
|------|------|--------|------|
| `characters/miku/pet.json` | 修改 emotions 映射 | 10 行 | ✓ 已完成 |
| `avatar/live2d_renderer.py` | 补情绪→motion+expression 双通道播放 | 3 处 ≈30 行 | ✓ 已完成 |
| `pet_mixins/animation_mixin.py` | 验证 | 无需改动 | ✓ 已确认 |

### 具体方案

#### 1.1 修改 pet.json 情绪映射

```json
"emotions": {
  "happy":     { "anim": "happy",   "expression": "脸红" },
  "surprised": { "anim": "idle",    "expression": "圈圈" },
  "thinking":  { "anim": "thinking","expression": "前倾" },
  "sad":       { "anim": "sad",     "expression": "" },
  "angry":     { "anim": "angry",   "expression": "" },
  "neutral":   { "anim": "idle",    "expression": "" },
  "touched":   { "anim": "touch",   "expression": "脸红" }
}
```

新增 `touched` 情绪——对应触摸交互时播放 touch motion + 脸红 expression。

#### 1.2 改造 animation_mixin.py

当前 `set_emotion(emotion_name)` 只播一个 anim。改造为：
1. 读 `pet.json` 的 `emotions[emotion_name]`，提取 `anim` 和 `expression`
2. 先播 expression（Live2D 的 `Expression` 叠加，不打断 motion）
3. 再播 motion（替换当前动作）
4. 口型：若 TTS 正在播放，叠加 LipSync 参数

#### 1.3 改造 live2d_renderer.py

需要确认 `live2d-py` 的 API 是否支持：
- `model.SetExpression(exprName)` / `model.StartMotion(group, no, priority)` 
- 若不支持独立 expression 叠加，退化为 motion 切换时附带 expression 参数

### 验证标准

- 每种情绪触发时，miku 播放对应的 motion 动画
- 有 expression 的情绪（如 happy → 脸红）同时显示表情叠加
- 触摸（点击/拖拽）触发 touched → touch motion + 脸红
- 情绪切换时过渡自然，不卡顿

---

## Phase 2：对话引擎深化（第二刀）

### 待确认方向

- 当前 `conversation_engine.py` 728 行，代际打断 + TTS 异步化已有
- 深化方向：情绪感知对话——LLM 回复中带情绪标签 → 自动触发对应 motion/expression
- 具体：`conversation_engine.py` 的 `_process_message` 中解析 LLM 返回的 `[emotion:xxx]` 标签，通过信号发送到主线程触发情绪切换

### 涉及文件

- `core/conversation_engine.py` — 解析情绪标签
- `pet_mixins/chat_mixin.py` — 接收情绪信号
- `pet_mixins/animation_mixin.py` — 情绪波动过渡（非突变，中间插过渡帧）

---

## Phase 3：交互体验提升（第三刀）**（已完成 ✓）**

### 已完成改动

| 文件 | 改动 | 说明 |
|------|------|------|
| `pet_mixins/interaction_mixin.py` | 反馈文案池 | 摸头按连击数分层（1~4 连击不同文案），抚摸每 3 次弹低频气泡 |
| `ui/bubble.py` | 打字机 + 淡入提速 | 短文本 42→28ms/字符，淡入 250→170ms |
| `motion/behavior.py` | 物理参数 | 行走速度 4.0→3.2，惯性 0.90→0.86（更从容不滑头） |

### 方向

- 气泡动画：当前 `ChatBubble` 淡入动画，可加弹跳缓动
- 拖拽手感：`motion/physics.py` 的物理引擎参数调优（惯性/摩擦/弹跳）
- 摸头反应三段式：点击 → 表情变化 → 气泡文字 → 爱心粒子
- 打字机效果：当前已有，可调速度/节奏

### 涉及文件

- `ui/bubble.py` — 动画缓动曲线
- `motion/physics.py` — 物理参数
- `pet_mixins/interaction_mixin.py` — 摸头逻辑
- `ui/heart_particles.py` — 粒子效果

---

## Phase 4：接入 Hana 内测版（第四刀）**（部分完成）**

### 已完成 ✦ 桌宠调音台卡片

对话内交互卡（show_card）：读 config.json 展示 miku 情绪映射 + TTS 音量滑杆写回。首次调用需在卡片上授权 file.read/resource.write 访问桌宠目录。

### 已确认 ✦ 记忆链路物理共享（无需桥接代码）

`core/memory_snapshot.py` 直接读写 `~/.hanako/agents/<agent_id>/memory/`（memory.md/today.md/longterm.md/week.md/facts.md + identity.md + pinned-memory.json）。**桌宠记忆 = Hana Agent 记忆，同一文件系统**，跨会话上下文天然共享，P4 记忆线已通。

### 已完成 ✦ 通用外部触发接收器（不含个人任务绑定）

新建 `core/external_trigger_receiver.py` + `config.external_trigger` 块 + pet.py 接线：

- POST /trigger（默认 127.0.0.1:8988，X-Auth-Token 可选）
- Body: `{"action": "remind|say|praise|custom", "text": "...", "emotion": "happy"}`
- 回调经 QTimer 转主线程 → 气泡 + 情绪动画
- 默认关（config.external_trigger.enabled=false），零行为不占端口
- **通用机制**：不绑定任何特定调度器或个人任务；桌宠本地提醒保持自包含，外部触发是纯可选附加入口
- 单测已验证：POST 200 + 回调触发 + 未知 action 400

| 文件 | 改动 |
|---|---|
| `core/external_trigger_receiver.py` | 新建（独立 HTTP 接收器，复用 status_http 范式） |
| `config.py` | DEFAULT_CONFIG 加 external_trigger 块（默认关） |
| `pet.py` | _init_external_trigger + 回调（QTimer 转主线程） |

---

## Phase 5：月薪喵优化（第五刀）**（已完成 ✓）**

### 根因

`_load_from_frames_dir` 之前不设 `_seq_fps` → `play_anim` 回退 330ms/250ms 硬编码 ≈ 3~4fps，帧动画看起来"钝"。口型帧（speak_*）audio_mixin 已接好无需新增；专属反应帧（angry/surprise/eat）存在但情绪映射没用上。

### 改动

| 文件 | 改动 |
|---|---|
| `avatar/sprite_renderer.py` | frames 模式读取 pet.json animations.fps（通用修复，所有 frames 角色受益） |
| `characters/yuexinmiao/pet.json` | fps 提升（idle 3→6、running 6→8/10、speak_* →8…）；补充 angry/surprise/eat/sleep/speak 系列 fps；情绪映射改专属帧（angry→angry、surprised→surprise） |
| `config.py` | EXPRESSION_MAP 同步：surprised→surprise、angry→angry |

验证：offscreen 单测通过（15 序列 fps 全部填充 + 专属帧映射生效）。

### 现状

- 精灵图 spritesheet.webp 切割 9 种动画
- 14 个帧目录（含 speak_closed/speak_half/speak_open 口型帧）
- 贴图仅 1 张（eat_moment.png）

### 方向

- 口型帧利用：TTS 播放时根据音频能量切换 speak_closed/speak_half/speak_open
- 帧率提升：pet.json 中 idle 3fps → 6fps 试效果
- 新表情/动作：补充更多贴图（摸头反应、开心、惊讶）

---

## Phase 6：功能深化（第六刀）**（已完成 ✓）**

### 深化方向
按「优先清理 + 架构归位 → 再联动 → 最后碰大模块」原则推进：

#### 1. 插件工具清理（config 开关）
- `plugin_tools.enabled` 默认 false，工具注册表 disabled 时直接返回
- plugins/ 目录保持空但文档化（可放自定义工具）

#### 2. 手机感知合并（EventBus 统一入口）
- `phone_receiver` 收到 MacroDroid 上报后额外 emit `EventBus.external_trigger` 事件
- `external_trigger_receiver` 也走同一事件总线
- pet.py 订阅一次「external_trigger」事件，所有外部来源统一处理

#### 3. Work 系统联动（公共接口）
- pet.py 新增 `trigger(text, action, emotion)` 公共方法（QTimer 转主线程）
- pet_manager `_on_work_finish` 调用 `window.trigger(...)` 触发桌宠反馈
- 工作完成 → 自动气泡 + 情绪动画，无需 HTTP 端口

### 验证
全部编译通过；EventBus 事件路由与现有 phone_event / chat_completed 等模式一致。

| 文件 | 改动 |
|---|---|
| `config.py` | DEFAULT_CONFIG 加 plugin_tools 块 |
| `core/tool_registry.py` | discover() 检查 config 开关 |
| `core/external_trigger_receiver.py` | 收到 POST 后额外 EventBus.emit |
| `core/phone_receiver.py` | 收到 /phone/activity 后额外 EventBus.emit |
| `pet.py` | 订阅 external_trigger 事件 + trigger() 公共方法 |
| `pet_manager.py` | _on_work_finish 调用 window.trigger() |

---

## 执行计划

| 阶段 | 内容 | 预计耗时 | 依赖 |
|------|------|---------|------|
| P1 | Miku Live2D 情绪映射 | 1 次对话 | 无 |
| P2 | 对话引擎情绪感知 | 1-2 次对话 | P1 |
| P3 | 交互体验 | 1-2 次对话 | P1 |
| P4 | 内测版接入 | 1 次对话 | 内测版稳定 |
| P5 | 月薪喵优化 | 1 次对话 | 无 |
| P6 | 功能深化评估 | 待定 | P1-P5 |

---

*—— 奥菲莉娅*