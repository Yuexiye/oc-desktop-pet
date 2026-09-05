# oc-pet Embody 层改造价值论证

> 文档类型：改造价值论证（**不是 PRD，不是技术方案**）
> 撰写：许清楚（产品经理）
> 日期：2026-09-04
> 参考对象：[Soullink Emotion SDK](https://github.com/nanlingyin/soullink-emotion-sdk)（TypeScript / MIT / beta）
> 决策前提：**不集成该 SDK**（技术栈鸿沟：TS+PIXI vs Python+PySide6+live2d-py），仅**参照其设计思想在 oc-pet 内用 Python 重写核心抽象**
> 论证范围：D1 ModelProfile / D2 MotionMixer / D3 VAD 连续情绪

---

## 0. 一句话结论（给没时间读全文的人）

- **D2（MotionMixer）该做**，而且是唯一一项有硬数据支撑「不改会继续痛」的：Embody 层 82/233 次提交、60 fix : 9 feat、5 道互相叠加的互斥锁，其中一个调用方已经被逼到从外部手动清空渲染器私有冷却表才能播出一个挥手动作。
- **D1（ModelProfile）该做，但别抱幻想**：它买的是「接下一个新模型时不用改 Python」，**不是**「miku 能表达更多」。miku 只有 7 个表情 + 7 个 motion，这是资产上限，配置化造不出参数。
- **D3（VAD）建议缓做，或大幅缩小范围**。最强反面证据：`intensity` 参数在 `Live2DRenderer.set_emotion()` 和 `SpriteRenderer.set_emotion()` 里**从头到尾没有被读过一次**——也就是说，把一个已经算好的连续强度接进表现层的工程量，比"引入 VAD"这个概念本身大得多，而 oc-pet 63 处 `except Exception: pass` 意味着任何漏改都会**静默降级**而不报错。

---

## 一、现状盘点：改造前，Embody 层实际能干什么

### 1.1 Embody 层的物理边界

团队 brief 里提到的 `_apply_expression` / `_start_motion_at` / `set_emotion` / `_tick_auto_motion` **不在 `pet.py`（2950 行）里，全在 `avatar/live2d_renderer.py`（2561 行）**。这一点很重要——它说明 Embody 逻辑其实已经收敛在单一渲染器内，`pet.py` 只是转发。**改造面的中心是 `avatar/live2d_renderer.py`，不是 god-object。**

当前 Embody 层是**四层叠加**结构：

| 层 | 实现位置 | 驱动方式 | 规模（miku 实测） |
|---|---|---|---|
| ① Live2D Expression（贴图表情） | `_apply_expression` L2191 | 情绪名 → 模糊匹配模型自带表情名 | **7 个**（比心/唱歌/脸红/葱/圈圈/前倾/QQ人） |
| ② Live2D Motion | `_start_motion_at` L2053 | 情绪名/动画名 → motion 文件名关键词 | **7 个**（idle/happy/waving/angry/sad/thinking/touch） |
| ③ 程序化参数层 | `_update_procedural_emotion` L1309 | `master_emotion` → 12 个归一化抽象参数 → Cubism 标准参数 | 12 参数 × **7 档情绪** |
| ④ 表情序列层 | `_EMOTE_PRESETS` L159 + `play_emote_sequence` L1575 | 预设名 → 参数步进序列 | **38 个预设**，加权随机 |

资产盘点来源：`characters/miku/live2d/miku.model3.json`：`Expressions: ['比心','唱歌','脸红','葱','圈圈','前倾','QQ人','水印']`（第 8 个「水印」被 `_IGNORED_EXPRESSIONS` L54 过滤）、`Motions: {'': 7}`（全部在空组，所以组名匹配无效，只能按文件名匹配）。

### 1.2 已经做到的、且做得不错的部分（不要改坏）

必须诚实承认，③和④这两层是**真东西**，不是补丁堆：

- **程序化参数层的 12 个参数**（L84-120）：`eye_open / eye_smile / brow_angle / brow_form / mouth_form / mouth_open / eye_ball_x / eye_ball_y / head_angle_x / head_angle_y / breath_amp / breath_rate`（外加一个特例 `blush`）。全部归一化，经**帧率无关指数平滑**（`alpha = 1 - exp(-dt/tau)`，tau 可配，L1351）写入，以 weight < 1 叠加在 motion 之上。
- **38 个表情序列预设**（L159-310），带 `_EMOTE_PRESET_WEIGHTS`（L315）加权随机：日常微妙表情（眨眼 2.5 / 视线游移 2.2 / 微笑 2.4）权重高，戏剧性表情（打喷嚏 0.4 / 伸懒腰 0.5 / 惊讶 0.5）权重低。**这个设计思路本身就是 Soullink 的 "layered animation" 的简化版。**
- **情绪来源优先级仲裁**（`pet.py` L2509-2514）：`dialog(3) > screen(2) > timer(1) > neutral(0)`，低优先级不能覆盖高优先级。
- **帧区间映射**（`config.py` L255 `EXPRESSION_MAP`）：11 个情绪名 → 帧序列。
- **结构化动作意图**（`apply_action_intent` L2352）：`[action:{gesture, intensity, params}]` 直通 Cubism 参数，走同一套平滑。**这是目前最接近 Soullink "model profile" 的东西。**
- **已有 V/A 映射**：`core/perception/focus.py` L480 `EMOTION_VA_MAP`（11 情绪 → valence/arousal）+ `EmotionReading` 适配器。

**结论：oc-pet 的 Embody 层不是"没做"，而是"做了四层，但四层之间是互斥/打架的，靠 5 道补丁强行隔开"。这正是 Soullink 用 "layered animation + mixer" 解决的问题。**

### 1.3 一个必要的更正：现有情绪不是「5 档离散」

团队 brief 说「5 档离散情绪（happy/sad/thinking/surprised/neutral）」。代码事实是**三套不同的枚举，且互不对齐**：

| 枚举表 | 位置 | 条目数 |
|---|---|---|
| `EXPRESSION_MAP`（帧序列映射，权威表） | `config.py` L255 | **11**：happy/surprised/angry/sad/thinking/working/cute/missing/neutral/listening/speaking |
| `_EMOTION_FACIAL_TARGETS`（程序化面部参数） | `live2d_renderer.py` L84 | **7**：neutral/happy/sad/angry/surprised/thinking/cute |
| `_EMOTION_MOTION`（情绪→motion 组） | `live2d_renderer.py` L59 | **5**：happy/angry/sad/surprised/thinking |

**对齐缺口（已核实）**：
- `working / missing / listening / speaking` 在 `EXPRESSION_MAP` 里，但**没有面部参数目标** → 程序化层静默回退 `neutral`（L1332 的 `or ...["neutral"]`）。全项目 `working` 出现 33 次、`speaking` 14 次、`missing` 13 次、`listening` 10 次——**这 4 个情绪在 Live2D 角色上，面部是不动的。**
- `angry / cute` 有面部参数，但**不在 `_EMOTION_MOTION`** → 不会触发 motion。

而 `EmotionStateMachine`（`core/perception/emotion.py`）**本身已经是连续的**：`_intensity` 是 float，`DECAY_RATE = 0.08`（每分钟衰减 8%），`THRESHOLD_LOW = 0.15` 以下归 neutral，全程加锁。

**所以准确的表述是：情绪名是离散字符串，强度是连续 float，但强度从未被表现层读取。** 见下节缺口 #3。

---

## 二、能力缺口：现在做不到什么

每条都给代码证据，并标注是**架构限制**还是**只是没做**。

### 缺口 #1：表情与 motion 无法共存 —— 【架构限制】

**证据** `live2d_renderer.py` L2205-2209：
```python
# 非 idle motion 播放期间，不叠加新表情
if expr is not None and not getattr(self, "_motion_is_idle", True):
    return
```

**后果**：正在播 waving / happy / touch 时，情绪系统每秒推来的 `set_emotion('happy')` 会走到 `_apply_expression` 然后被这一行挡掉。**用户夸桌宠时它正好在挥手 → 脸上什么都不会变。** 这是 `b1f543c` 为修「比心+举葱叠加」引入的，代价是彻底放弃叠加能力。

### 缺口 #2：调用方必须从外部手动绕过冷却 —— 【架构限制，最硬的反面证据】

**证据** `pet_mixins/behavior_mixin.py` L272-299（主动对话触发动作）：
```python
if hasattr(renderer, "_model") and renderer._model is not None:
    renderer._model.StopAllMotions()          # 伸手进渲染器私有模型
if hasattr(renderer, "_emotion_motion_cooldown"):
    renderer._emotion_motion_cooldown.clear() # 手动清空冷却表
if hasattr(renderer, "_last_gesture_at"):
    renderer._last_gesture_at = 0.0           # 手动伪造时间戳
if not renderer._play_motion_kw("waving"):    # 调私有方法
    renderer._play_motion_kw("happy")
renderer._note_motion_started("proactive", is_idle=False)  # 手动伪造状态
```
注释写得很直白（L269-270）：*「否则屏幕感知反复推 happy 进入冷却后，proactive 触发只会"闪过思考气泡"但角色继续 idle 摇摆，用户感受"没动作"」*。

**这不是"代码写得糙"，这是架构信号**：渲染器的保护机制粗到连自己的调用方都受不了，调用方只能绕过封装去拆自己的锁。**一个 mixer 需要的正是显式的优先级仲裁（谁可以打断谁），而不是"全局冷却 + 外部 bypass"。**

同类越界还有：`bubble_mixin.py` L204 直接调 `renderer._force_idle()`。

### 缺口 #3：情绪强度被完整丢弃 —— 【只是没做，但暴露了 D3 的真实成本】

**证据链（完整闭环，逐环核实）**：

1. 屏幕感知算出强度：`core/perception/screen.py` L99-102 → `"错误": ("surprised", 0.45)` / `"崩溃": ("surprised", 0.55)`
2. 回调传出强度：`on_emotion(emotion, intensity)`（screen.py L839）
3. 应用层收到：`pet_mixins/behavior_mixin.py` L580 `_do_screen_emotion(self, emotion, intensity)`
4. 存入状态机：`self._perception.trigger_emotion(emotion, intensity)`（L588）
5. **下发时丢弃**：`self._set_surface_emotion(emotion, duration_ms=3000, source="screen")`（L614）—— **intensity 根本没传**
6. 就算传了也没用：`Live2DRenderer.set_emotion(self, emotion, intensity=1.0)` 的**整个函数体（L2259-2348）没有一次读取 `intensity`**；`SpriteRenderer.set_emotion`（L533）同样。
7. `EmotionStateMachine.intensity` 全项目只被读 2 处：`controller.py` L544（状态快照 dump）和 `focus.py` L520（专注度打分）。**从未进入渲染层。**

**后果（可验证的具体例子）**：屏幕上出现「错误」和「崩溃」，感知层分别给出 `surprised 0.45` 和 `surprised 0.55`——两个明显不同的强度——**最终桌宠表现完全一致**。

**这条对 D3 的判定至关重要**：不是"VAD 没引入"，而是"连续强度在管线里已经存在了三跳，然后在最后一跳被扔掉"。修这条**不需要 VAD 模型**，只需要把 intensity 透传下去（`_set_surface_emotion` 加个参数 + 程序化层用它缩放 targets 幅度）。**这是"用 20 行代码拿到 D3 六成收益"的路径，必须和 D3 的完整改造放在一起权衡。**

### 缺口 #4：40% 表情预设对默认角色完全无效 —— 【架构限制，被低估】

**证据**：`play_emote_sequence` **只存在于 `avatar/live2d_renderer.py` L1575**。`avatar/sprite_renderer.py`（817 行）和 `avatar/vrm_renderer.py`（157 行）**都没有这个方法**。

**而默认角色是 `yuexinmiao`（精灵图）**：`config.py` L10 `"character": "yuexinmiao"`。（当前 `config.json` 里是 `miku`，但那是本地配置覆盖了默认值；新装/重置用户拿到的是 sprite。）

**后果**：38 个表情预设（眨眼/脸红/视线游移/偷瞄/撇嘴/叹气/哈欠/点头/歪头/偷笑/打喷嚏/伸懒腰……）**对 sprite 角色 100% 不可用**。sprite 角色的全部表现力 = `EXPRESSION_MAP` 的 11 个情绪 → 静态帧序列切换。

**这条同时是 D1/D2 最大的隐藏收益，也是最大的隐藏工作量。** 任何"统一混流"若只做在 Live2D 渲染器里，就是把现状又固化一遍。

### 缺口 #5：接新模型必须改 Python —— 【架构限制】

**证据**：模型专属知识散在 `live2d_renderer.py` 的 4 张表 + 1 段硬编码里：

| 位置 | 内容 | 模型专属证据 |
|---|---|---|
| `_EMOTION_KEYWORDS` L44-52 | 情绪→表情名关键词 | 含中文表情名 `"唱歌"/"比心"`（happy）、`"圈圈"`（surprised）、`"脸红"`（cute）—— **miku 专属** |
| `_ANIM_TO_MOTION_KW` L2141-2162 | 动画名→motion 关键词 | 注释明写「兼容 lafei（main_1/2/3、home…）与 miku（waving/touch/thinking…）两种模型命名」，两套命名硬塞在一张表里 |
| `_EMOTION_FACIAL_TARGETS` L82-83 | 面部参数 | 注释「miku moc3 无手/臂/腿参数（双手固定祈祷），只驱动面部/眼神/呼吸」—— **为 miku 量身定做的约束** |
| `_suppress_watermark` L1215 / `_cache_watermark_index` L1169 | 水印抑制 | 硬编码关键词 `("水印","watermark","logo","版权")`，读 `cdi3.json` 做 Id→Name 映射 |
| L1892 注释 | | 「miku 的比心/葱等是 Param131-137 贴图开关」 |

**注（诚实更正）**：brief 里说的「Param131-137 硬编码」，实际代码里**只有 `Param137`（水印）出现过一次**（L684/1144，且已弃用，改用部件透明度方案）。Param131-136 并没有被硬编码——它们是通过 `SetExpression("比心")` 按名字间接驱动的。**所以 D1 要解决的不是"消除 Param131-137 硬编码"，而是"消除 4 张语义映射表 + 水印探测的硬编码"。** 这一点必须说清楚，否则会按错误的靶子设计。

### 缺口 #6：异常被静默吞掉，改造风险被放大 —— 【架构限制，放大所有其他风险】

- `avatar/live2d_renderer.py`：**63 处** `except Exception:` 紧跟 `pass`，共 110 处 `except Exception`。
- `_update_procedural_emotion` 一个函数 **16 处**，外加 L1488 一个**方法级** `except Exception: pass` 包住整个函数体——任何一条参数写入抛异常，整个 12 参数层从出错点开始全部停摆，**不报错、不打日志**。这正是 brief 提到的 `_override` 漏定义事故的根因机制。
- `pet.py` 48 处、`pet_mixins/behavior_mixin.py` 13 处。

**后果**：D1/D2/D3 都是跨多张枚举表的改造。在这个异常密度下，**漏改一处 = 静默降级 = 可能几周后才发现某个情绪不动了**。

---

## 三、改造后能干什么：逐条对应，给具体场景

### D1 ModelProfile —— 模型参数映射配置化

**做什么**：把上表 5 处模型专属知识抽到 `characters/<id>/profile.json`，Python 只读配置。

**新增能力**：

| 场景 | 现在 | 改造后 |
|---|---|---|
| 接入新 Live2D 模型（如一个新的 shizuku/原创模型） | 改 `live2d_renderer.py` 的 4 张表，其中 `_ANIM_TO_MOTION_KW` 要往 lafei/miku 并存的兼容分支里再塞第三套命名 | 写一份 profile.json，Python 零改动 |
| 微调某个角色的表情强度（如"miku 的开心笑得再收敛一点"） | 改 L90-94 的 `happy` 字典，全局生效、影响所有模型 | 改 miku 的 profile.json，只影响 miku |
| 水印/版权部件处理 | 硬编码 4 个关键词，遇到德文/日文水印名就漏 | profile 里显式列出部件 Id/Name |

**诚实边界（必须写进来）**：**D1 对 miku 的表达力零提升。** miku 的 7 个表情 + 7 个 motion 是 `miku.model3.json` 决定的，配置化不能凭空造出参数。`shizuku/pet.json` 就是活证据——它的 `emotions` 已经把 5 个情绪全部映射到 `"anim": "idle"`，配置化到极致也只是"正确地什么都不做"。

**D1 真正买的东西**：(a) 新模型接入成本从「改 2561 行的核心渲染器」降到「写一份 JSON」；(b) 为 D2 提供 mixer 必需的元数据（"这个动作占用哪些参数通道"）。

### D2 MotionMixer —— 表情/动作统一混流

**做什么**：用显式的层优先级仲裁（谁占哪条参数通道、谁能打断谁）替换现有的 5 道互斥补丁。

**新增能力（这是三项里唯一能立刻被用户看见的）**：

1. **复合表情**：
   - 现在：用户夸桌宠 → 只能切 `happy`；如果它正在播 waving，连 `happy` 都切不了（缺口 #1）→ 用户看到的是"夸了没反应"。
   - 改造后：`waving motion`（肢体层）+ `脸红`（贴图层）+ `eye_smile 0.75 / blush 0.65 / eye_ball_y -0.12 / head_angle_y -0.3`（参数层）三层同时生效 = **「一边挥手一边脸红、眯眼、微微低头目光躲闪」**。即 brief 里举的"开心但害羞"。
   - 这个组合其实**已经存在于代码里**（`_EMOTE_PRESETS["blush_shy"]` L177-180 就是这套参数），只是**不能被主动调用**——它只能在 idle 时靠 25% 随机概率撞上（L1813）。

2. **消除外部 bypass**：主动对话不再需要 `behavior_mixin.py` L283-286 的"清空冷却表 + 伪造时间戳"三连。改为向 mixer 提交一个带优先级的请求（`user_initiated > dialog > screen > idle`），由 mixer 决定打断还是排队。

3. **跨渲染后端对齐**：把 mixer 抽象提到 `avatar/base.py`（173 行，目前只有 `set_emotion` / `play_anim` / `apply_action_intent` 三个空壳），让 sprite 渲染器用帧序列实现同一批 emote 预设 → **38 个预设对默认角色 yuexinmiao 从 0% 可用变成可用**（缺口 #4）。

4. **让已有的 `[action:{...}]` 真正好用**：`apply_action_intent` 已支持 `params` 直通，但 sprite 渲染器直接 `忽略 params 字典`（`sprite_renderer.py` L539 注释：「精灵图只有帧序列、无参数概念，故 intent["params"] 字典直接忽略」）。mixer 抽象可以给 sprite 一条 gesture→帧序列的映射路径，让同一个 intent 在两种后端都有语义。

**诚实边界**：mixer 不会让 miku 多出任何一个表情资产。它让**已有的 7+7+38 个资产从"三选一"变成"可组合"**。组合数从 ~14 提升到理论上的笛卡尔积（实际受参数冲突约束，但量级上是 10²~10³）。

### D3 VAD 连续情绪 —— valence/arousal/dominance 三轴

**做什么**：用连续 VAD 状态替换离散情绪名，作为表现层的驱动源。

**新增能力**：

1. **情绪渐变而非硬切**：
   - 现在：用户连吐槽三句 → 第一句 `happy`、第二句 `thinking`、第三句 `sad`，三次硬切。程序化参数层有 0.28s 平滑（`PROCEDURAL_SMOOTH_TAU` L69），但**贴图表情层是瞬切**（`ResetExpressions` → `SetExpression`，L2230-2231）。
   - 改造后：valence 单调下滑，眉毛角度/嘴角/眼皮沿路径连续漂移，不出现"啪"一下换脸。

2. **修复缺口 #3**：屏幕感知的 `surprised 0.45` vs `surprised 0.55` 终于会有区别。

3. **强度语义**：`happy 0.2`（微微愉悦）和 `happy 0.9`（狂喜）不再是同一张脸。

**诚实边界（这是我要唱反调的地方）**：

- **D3 的收益上限被 miku 的 7 个表情资产封死。** VAD 算出的连续状态，最终下发给模型的仍是「7 选 1 的贴图表情 + 12 个抽象参数」。连续性能真正体现的地方只有程序化参数层（12 参数）——而那层现在已经由 `master_emotion` 驱动了。
- **V/A 在 oc-pet 里已经存在**：`core/perception/focus.py` L480 `EMOTION_VA_MAP`。D3 的真实工作不是"引入 VAD"，而是**反转数据流向**：从「标签 → VA（有损，下游只用于专注度打分）」变成「VA → 标签/参数（VA 为真相）」。
- **Dominance 轴在 oc-pet 里没有任何现有消费点**，也没有任何数据源能可靠地产生它。这是三轴里唯一需要凭空造的。

---

## 四、为什么要改：不改会怎样

### 4.1 打补丁的边际成本已经在恶化（硬数据）

| 指标 | 数值 | 来源 |
|---|---|---|
| 触及 `avatar/live2d_renderer.py` 的提交 | **82 / 233 = 35%** | `git log --oneline -- avatar/live2d_renderer.py` |
| 其中 fix : feat | **60 : 9 = 6.7 : 1** | 提交前缀统计 |
| 标题含「表情/动作/叠加/卡死/比心/手势/残留/idle」的提交 | **27 次** | 同上 |
| 时间分布 | **全部集中在 2026-08 一个月内** | `git log --date=format:%Y-%m` |
| `ResetExpressions()` 调用点 | **11 处** | 全项目 grep |
| `StopAllMotions()` 调用点 | **6 处**（其中 1 处在渲染器**外部**） | 全项目 grep |
| `except Exception: pass` | **63 处**（仅 live2d_renderer） | 脚本统计 |

**解读**：60 次 fix 里绝大多数是**同一类问题的不同表现**——「两个表现源撞在一起了」。每次修都是加一道新的锁：

1. `322a902` 同 emotion 重复调用判断
2. `b8283f9` emotion 冷却 + 兜底
3. `3be390a` 表情超时自动重置（"一直比心"）
4. `3527e76` SetExpression 前先 ResetExpressions
5. `b15abe5` 双重 StopAllMotions
6. `3c32840` `_force_idle` 改 FORCE 优先级
7. `78e7758` exclusive 清场补全
8. `b1f543c` 非 idle 禁止叠加表情
9. `6a25412` 手动设置表情时也清场
10. `6b55ef8` 多重 ResetExpressions 防贴图残留
11. `behavior_mixin.py` L283 从外部清空冷却表 ← **最新一道，也是最危险的一道**

**趋势判断**：前 10 道都还在渲染器内部（至少封装没破）。第 11 道**破了封装**——调用方绕过公开接口直接操作私有状态。这是"局部修补已到极限"的典型信号：内部的锁已经密到让合法调用方无法完成合法操作。

**继续打补丁的预测成本**：下一道补丁（比如"对话情绪和鼠标交互撞车"）要么再加一道更细的锁（互斥矩阵继续膨胀，组合爆炸），要么再开一个 bypass 口子（封装进一步失效）。**两种方式都在提高后续每次修改的认知负担。**

### 4.2 不改的另一种成本：新功能做不动

- 缺口 #4 意味着：如果想给**默认角色 yuexinmiao** 加任何一个微表情（眨眼/脸红/视线游移），现状下**无处可加**——sprite 渲染器没有 `play_emote_sequence`，没有参数层。要么在 sprite 渲染器里复制一遍（维护两份），要么先做 mixer 抽象。
- 缺口 #1 意味着：任何"复合情绪表达"的需求（复合表情是 AI 伴侣的差异化核心）在当前架构下**只能通过增加新的 emote preset + 靠 25% 随机撞上去**来实现。

### 4.3 但也要说清楚：不改不会死

- 现有 5 道锁**虽然丑，但能work**。82 次提交里 60 次 fix 说明痛，但也说明**每次都修好了**。
- 现有能力（7 motion + 7 expression + 38 preset + 12 参数平滑）对一个单人维护的桌宠来说**不算寒酸**，38 个表情预设的加权随机在观感上已经能提供"活的"感觉。
- **不改的代价是"每次新增/修改表现需求都要付出越来越高的认知税"，不是"产品不能用"。**

---

## 五、反面：不该改的理由与风险

用户需要权衡，所以这部分我写得比"好处"更重。

### 风险 1：改造会打碎 82 次提交攒下来的行为契约（**最高风险**）

那 60 次 fix 每一条都在守护一个具体的、用户肉眼可见的行为。举例：

- `_force_idle` 的"三重 `ResetExpressions` + 双重 `StopAllMotions` + FORCE 优先级"（L1894-1970）不是过度设计，是三次失败后的产物，注释里逐条记录了 v1/v2/v3 的失败原因。**mixer 若用"优雅的优先级仲裁"替换它，很可能重新引入"比心死锁"**——因为根因不是优先级设计得不好，而是 `live2d-py` wrapper 的行为不确定（同一优先级不打断、`StopAllMotions` 需要调两次、`ResetExpressions` 有延迟生效）。
- `_apply_expression` 的"非 idle 不叠加"（L2208）是 `b1f543c` 为修「比心+举葱」加的。**D2 要做的恰恰是撤销它。** 撤销后如果 mixer 的资源占用元数据不完整，会立刻复现两个贴图表情叠加。

**缓解建议**：D2 的 mixer 必须**保留这些"不优雅但有效"的兜底作为最后一道防线**，而不是替换它们。mixer 的仲裁在上层做，底层的多重清场保留为"强制重置"路径。

### 风险 2：`except: pass` 密度会让回归**静默**

63 处裸 `except Exception: pass` + `_update_procedural_emotion` 的方法级吞异常（L1488）。D1/D2/D3 都要动多张枚举表：

- D3 要动 8+ 张表：`EmotionStateMachine`（emotion.py）、`_EMOTION_PRIORITY`（pet.py L2509）、`_set_surface_emotion`（pet.py L2516）、`EXPRESSION_MAP`（config.py L255）、`EMOTION_VA_MAP`（focus.py L480）、`MOOD_EMOTION_PRIORITY`（hanako_monitor.py L268）、`SCREEN_EMOTION_MAP`（screen.py L85）、`_EMOTION_FACIAL_TARGETS`（renderer L84）、`_EMOTE_PRESETS`（renderer L159）。
- **漏改任何一处，表现为"某个情绪不动了"，不报错、不打日志。** 63 处 `pass` 保证你不会看到堆栈。

**缓解建议**：改造前先给 `_update_procedural_emotion` 这类方法级吞异常加**降级日志**（这是低风险、可独立进行的准备动作），否则整个改造在盲飞。

### 风险 3：miku 资产天花板 —— 投入产出比最硬的反面证据

`miku.model3.json`：**8 个表情（1 个是水印，实际 7 个）+ 7 个 motion，全部在空组**。

- D1 配置化后，miku 仍然是 7+7。
- D3 连续情绪后，miku 仍然只能在 7 个贴图表情里选。
- 唯一能放大的是程序化参数层（12 参数），而那层**现在已经能用**——只是由离散的 `master_emotion` 选择驱动。

**换句话说：D1+D3 在 miku 上的可感知收益，主要来自"情绪切换时的过渡更顺滑"，而不是"能表达更多东西"。** 用户如果期待"改造后桌宠表情丰富一倍"，会失望。

### 风险 4：D2 可能引入新的抖动

现有 5 道锁虽然粗暴，但它们共同保证了一件事：**桌宠不会抽搐**。每秒一次的 `set_emotion` 洪流（来自 `_unified_tick` L2040 + 屏幕感知 + 对话）被冷却层层削减。mixer 若按"每次请求都仲裁"的语义实现，可能在高频请求下产生**新的闪烁**——而这正是 `3be390a` 用"重置后 3 秒冷却防闪烁"（L2255）辛苦治掉的。

### 风险 5：单人维护的精力账

60,586 行自有代码 / 203 个 Python 文件 / 单人开发。D1+D2+D3 全上是**跨 `avatar/` + `core/perception/` + `config.py` + `pet.py` + `pet_mixins/` 五处的横切改造**。按 82 次提交里 60 fix 的历史节奏看，这类改造的返工率不低。

### 风险 6：sprite 对齐是被低估的工作量

缺口 #4 说"38 个预设对 sprite 不可用"。但要让它们可用，需要为 yuexinmiao 生成对应的帧资产——`scripts/procedural_emotion_frames.py`（307 行）目前**只生成 7 个动画**（angry/surprise/sleep/eat/speak_open/speak_half/speak_closed），且**每个只生成 1 帧**（`save_frame(img, anim, idx=0)`）。要覆盖 38 个预设，需要重新设计 sprite 的表情资产管线。**这不在 D1/D2/D3 的定义里，但会变成实际的阻塞项。**

---

## 六、建议的优先级与理由

### 推荐顺序：**D2（含 D1-lite）→ D1 完整 → D3（且先做"廉价版"）**

#### 第一批：**D2 MotionMixer + D1-lite（同批做）**

**理由**：
1. **痛感最强、证据最硬**：5 道互斥锁 + 82/233 提交集中度 + 唯一的破封装 bypass（`behavior_mixin.py` L283）。这是三项里唯一有量化证据显示"不改会继续恶化"的。
2. **收益可感知**：复合表情是用户肉眼能看见的差异化。而且**组合参数已经写在代码里了**（`_EMOTE_PRESETS` 的 38 个预设），D2 只是让它们变得"可被主动调用"，而不是"靠 25% 随机撞上"。这是**低垂果实**。
3. **D1-lite 是 D2 的硬依赖**：mixer 要知道"这个动作占用哪些参数通道"才能仲裁。这个元数据现在散在 `_EMOTE_PRESETS` 和 `_ACTION_OVERLAYS` 里，必须先抽成结构化的 ModelProfile。
4. **D1-lite 范围要克制**：只抽"资源占用元数据"（每个 motion/expression/preset 占用哪些参数通道、优先级、时长），**不做** Soullink 那种完整的 `profile-generator`（自动扫描模型生成 profile）。理由：oc-pet 只有 4 个角色（miku/shizuku/yuexinmiao/lafei），手工写 profile 的成本远低于写一个生成器。

**必做的准备动作**（低风险，可先于本体）：
- 给 `_update_procedural_emotion` 的方法级 `except Exception: pass`（L1488）加降级日志。**否则整个改造盲飞。**
- 给 `set_emotion` 的 `intensity` 参数补上透传（缺口 #3 的修复，约 20 行）。**这是"用最小成本拿到 D3 六成收益"的路径，应尽早做，不必等 D3。**

#### 第二批：**D1 完整（profile 配置化 + sprite 对齐）**

**理由**：
- D1 完整版（4 张语义映射表 + 水印探测全部外置）的价值是**保险费**，不是收益——它在"接入第 5、第 6 个模型"时才兑现。当前 4 个角色的情况下，优先级低于 D2。
- **但 sprite 对齐（缺口 #4）应该提前考虑**：默认角色是 yuexinmiao（sprite），38 个预设对它 100% 不可用。如果产品方向是"默认给用户的角色要有表现力"，这条的优先级会跳到第一批。这需要产品侧先回答一个问题：**默认角色到底是 miku（Live2D，能力强但资产少）还是 yuexinmiao（sprite，资产可控但表现层是空的）？**

#### 第三批：**D3 VAD（且强烈建议先只做"廉价版")**

**理由（这是我最想唱反调的一项）**：
1. **收益被资产封顶**（风险 3）：miku 7 个表情，连续情绪的"连续"只能体现在 12 个程序化参数上。
2. **改造面最大、表最多**（风险 2）：8+ 张离散枚举表，配合 63 处 `except: pass`，漏改静默。
3. **Dominance 轴无数据源**：valence/arousal 至少有 `EMOTION_VA_MAP` 作为起点，dominance 完全要凭空造。
4. **有更便宜的替代**：缺口 #3 显示，把已有的 `intensity` 透传下去就能拿到"强度语义"这一项收益，成本 20 行 vs D3 的全套改造。

**建议的"D3 廉价版"范围**：
- 透传 intensity 到程序化参数层（缩放 targets 幅度）→ 拿到"强度语义"
- 在程序化参数层内部，让 V/A 作为**中间表示**：离散情绪名 → (v, a) → 12 参数目标值（线性插值）。这样情绪切换变成 V/A 空间里的路径插值，**不需要改变任何调用方的接口，也不动 `EmotionStateMachine`**。
- **不做**：替换 `EmotionStateMachine`、不做 dominance 轴、不改上游 8 张枚举表。

这个廉价版能拿到 D3 宣称收益里**最可感知的那部分（渐变而非硬切 + 强度语义）**，而改造面局限在 `avatar/live2d_renderer.py` 一个文件内。

### 一句话总结优先级

> **D2 治"痛"，D1 治"险"，D3 治"奢"。先治痛，再治险，奢华的（D3）先做个便宜版看看够不够——很可能够。**

---

## 附录 A：证据索引（全部可复核）

| # | 断言 | 位置 |
|---|---|---|
| 1 | Embody 核心逻辑在渲染器不在 pet.py | `avatar/live2d_renderer.py` L2053/2191/2259/1783 |
| 2 | miku 资产上限 | `characters/miku/live2d/miku.model3.json`：8 Expressions / 7 Motions（空组） |
| 3 | 表情与 motion 互斥 | `avatar/live2d_renderer.py` L2205-2209 |
| 4 | 外部破封装 bypass | `pet_mixins/behavior_mixin.py` L272-299；`pet_mixins/bubble_mixin.py` L204 |
| 5 | intensity 被丢弃 | `screen.py` L99-102 → `behavior_mixin.py` L588 → L614（未传） → `live2d_renderer.py` L2259（未读）/ `sprite_renderer.py` L533（未读） |
| 6 | EmotionStateMachine 已连续 | `core/perception/emotion.py` L21-23, L43-52 |
| 7 | V/A 已存在（下游消费者） | `core/perception/focus.py` L480-492, L495-525 |
| 8 | 38 预设仅 Live2D 可用 | `grep -rn "def play_emote_sequence" avatar/*.py` → 仅 live2d_renderer.py L1575 |
| 9 | 默认角色是 sprite | `config.py` L10 `"character": "yuexinmiao"` |
| 10 | 三套情绪枚举不对齐 | `config.py` L255（11）/ `live2d_renderer.py` L84（7）/ L59（5） |
| 11 | 清场调用点 | `ResetExpressions()` × 11、`StopAllMotions()` × 6（全项目 grep） |
| 12 | 异常吞没密度 | live2d_renderer.py：63 处 `except Exception: pass` / 110 处 `except Exception`；`_update_procedural_emotion` 单函数 16 处 + 方法级 L1488 |
| 13 | 提交集中度 | `git log --oneline -- avatar/live2d_renderer.py` → 82/233；fix:feat = 60:9；全部 2026-08 |
| 14 | Param131-137 未被硬编码 | `grep -rn "Param13[0-9]"` → 仅 Param137（水印），见 L684/1144/1219 注释 |
| 15 | 表情序列不可主动调用（只能随机撞） | `live2d_renderer.py` L1813（`random.random() < 0.25`） |
| 16 | sprite 帧资产生成规模 | `scripts/procedural_emotion_frames.py` L288-301（7 个动画 × 1 帧） |

## 附录 B：Soullink Emotion SDK 概念对照（仅作思想参照，不集成）

| Soullink 包/概念 | 对应 oc-pet D 项 | oc-pet 现状 |
|---|---|---|
| `@soullink-emotion/profile-generator`（模型 profile 生成与校验） | **D1** | 无。4 张硬编码表 + 硬编码水印探测 |
| layered animation（分层动画） | **D2** | 有 4 层，但互斥；靠 5 道锁隔开 |
| VAD emotion state（连续 VAD） | **D3** | 有 V/A 映射但方向相反（标签→VA，仅下游消费）；无 dominance |
| FACS/AU synthesis | — | 无。oc-pet 用 12 个自定义归一化参数，非 FACS 标准 |
| `@soullink-emotion/devtools-vue`（模型标定工具） | — | 无。oc-pet 靠改 Python 字典 + 真机观察 |

**说明**：Soullink 是 monorepo + npm 多包 + beta 发布 + CI + Vue 标定工具，工程完整度高于"97 star"给人的印象。但它的 `live2d-pixi` 是 PIXI v7 渲染集成，与 `live2d-py` 无任何共享代码路径——**技术栈鸿沟是真实的，不集成的判断是对的。**
