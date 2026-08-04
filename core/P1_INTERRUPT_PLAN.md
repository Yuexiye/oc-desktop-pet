# P1 打断状态机 · 执行计划（实时更新）

> 位于 core/ 下，供多 Agent 持续读取。每个任务完成即更新本文件 + todo。

## 目标
实现"全链路打断"：用户插话/发消息/点停止时，同时中断 LLM 生成、TTS 合成、音频播放，并区分打断状态（interrupted / cancelled / completed），不粗暴丢弃。

## 已确认决策
- 打断条件：用户发文字 / 语音输入开始 / 用户点停止（三种都支持）
- 语音：按键说话 + 持续监听双模式（开关）
- 打断后行为：标记状态，不直接丢弃，看实际场景决定
- 多级渲染：简单形态 + 可切换 Live2D（后续 P4）
- 范围：P1 打断状态机 → P2 架构文档 → P3 测试

## 进度（✅ 完成 / 🔄 进行中 / ⬜ 待办）

| 任务 | 状态 | 说明 |
|------|------|------|
| P1-0 制定计划 | ✅ | 本文件 |
| P1-1 消息代际机制 | ✅ | `sent` 代际 + `interrupt()` 已实现（gen 计数 + 队列清理 + Hanako abort） |
| P1-2 TTS 层打断 | ✅ | LLM后/TTS前/TTS后 三处代际检查，过期丢弃不播 |
| P1-3 LLM 层打断 | ✅ | Hanako WS abort 闭环（chat_via_hanako 返回 aborted → 代际检查丢弃）；直连用代际检查丢弃结果 |
| P1-4 打断状态机 | ✅ | interrupt(reason) 区分状态；pet.py 三入口接入（new_message/voice_start）；4 个测试固化，36 全绿 |
| P2 架构文档 | ⬜ | ARCHITECTURE.md 更新 |
| P3 测试固化 | ⬜ | 测试 + 编译 + 回归 |

## 关键文件
- `core/conversation_engine.py`（主战场）
- `core/hanako_session_manager.py`（abort 能力，已确认存在）
- `pet.py`（播放层打断入口）