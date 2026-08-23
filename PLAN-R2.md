# oc-pet 桌宠优化计划书 · Round 2（修复 + 整合）

> 承接 Round 1（P1~P6 已完成）。本轮：显示修复 ×2 + 动作智能 ×1 + 窗口识别 ×1 + Hanako 全联动方案 ×1
> 编制：奥菲莉娅 · 2026-08-21

---

## P7 · 显示修复（两个渲染器，方向相反）**（已完成 ✓）**

| 子项 | 问题 | 方案 | 状态 |
|------|------|------|------|
| P7-1 | 日志噪声：tool_registry 每 30s 刷一行 | 防抖 60s + 日志降频 | ✅ 已修 |
| P7-2 | miku 脚部被裁（Live2D 显示不完整） | pet.json live2d.offset[1] -0.15→0.0 居中，跑起来验证脚部 | ✅ 已改，需跑验证 |
| P7-3 | 精灵图窗口远超精灵图（458x520 默认 vs 帧尺寸+15%） | sprite_renderer 加 calc_ideal_window_size，pet.py _recalc_geometry 优先使用 | ✅ 已修 |

附加：dark.qss/light.qss 字体栈去掉 macOS 专属字体名（-apple-system/BlinkMacSystemFont）→ 消除启动时 Fixedsys 字体警告

## P8 · 随机动作智能匹配（桌宠自身状态优先）**（已完成 ✓）**

- 说话态（on_tts_start → speak 帧）→ P3 已实现
- 交互态（touch → touch.motion3.json）→ P1 已实现
- 情绪加权随机动作（排除 touch + 按当前情绪 50% 概率选匹配 motion）→ 本轮已修
- 动作通用性：**机制通用**（pet.json 配置），**资源不通用**（每个模型 motion 文件不同）

## P9 · 窗口识别强化（标题优先）**（已完成 ✓）**

`build_vision_prompt` 规则强化："窗口标题是最准确的信息，截图可能包含残留图标/菜单，窗口标题与截图内容矛盾时绝对以窗口标题为准"

## P10 · Hanako 全功能联动方案 **（已完成 ✓）**

文档已产出：`PLAN-P10.md`

**核心结论**：
- 已通（缺口 0）：对话 ✅ 记忆 ✅ 定时触发 ✅
- 需打通（有方案）：插件（P2）· 卡片双向（P2）· 任务同步（P1）
- 最推荐先做：**任务同步**（mission 完成→WS 推送 Hana，改动最小效果最明显）
---

## Round 3（2026-08-22 追加）· 运行期问题修复

### R3-1 语音生成中气泡残留 ✅
根因：conversation_engine._synth_and_reply 合成前 on_status("🔊 语音生成中…") 与 finally 的 on_status("") 跨线程信号**顺序反转**，气泡挂了没人收。
修法：去掉合成前 on_status 调用（文字先行已显示回复，TTS 完成直接播音频）。

### R3-2 ASR 无法识别用户语音 ✅（设备选择）
根因：sd.Stream() 未指定 device，用系统默认设备（可能不是麦克风）。
修法：config.asr.device 字段 + VoiceInput 支持指定设备 + pet.py 传入 + settings 面板「麦克风设备」下拉（sounddevice 枚举输入设备）。

### R3-3 随机动作无权重/抖动 ✅（通用机制）
根因：_tick_auto_motion 等概率随机；motion 全 Loop=true 循环抖动。
修法：pet.json animations 加 weight（0=不自动播，未配默认1.0）；renderer 加权随机选择 + _motion_weight 文件名关键词匹配。**通用**：每个模型在 pet.json 配自己的权重。miku 示例：happy/waving=3、thinking/surprised=2、angry/sad=1、idle/touch=0。

### R3-4 插件面板点击无反应 🔶（已加保护，待验证）
根因待确认：离屏构造正常（27 插件可扫），真实环境异常可能被 Qt 信号处理器吞。
修法：_open_plugin_panel 加 try/except + logger.exception + 气泡兜底——下次点击异常会写日志，可定位。

### R3-5 openless ASR 调研 ✅
结论：openless 用流式 ASR（火山引擎首选/本地 Whisper），依赖系统默认麦克风无设备选择 UI；我们的 sounddevice 方案能精确指定设备，反而更灵活。流式方案（边录边转）可作长期参考。
