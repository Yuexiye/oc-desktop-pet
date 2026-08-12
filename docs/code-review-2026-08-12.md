# OC-Pet 代码评审报告（codebuddy）

> 评审日期：2026-08-12 · 评审工具：codebuddy CLI v2.127.0
> 范围：main.py / pet.py(1730行) / 10 mixin / core/ / avatar/ / tts,asr_provider/ / pet_manager.py / config.py

---

## P0（必须修）— 已修复 ✅

### pet.py:1484-1502 孤儿代码
`_do_tool_progress` 方法体内错误嵌入了 `_create_new_session` 的内容（方法头丢失）。
**后果**：每次工具进度事件都会误建新 Session，反复破坏会话上下文。

**已修复**：删除孤儿代码，`_create_new_session` 恢复为 chat_mixin.py:255 的唯一实现。
验证：语法 OK + 61 测试全绿。

---

## P1（应该修）

1. **多宠 Live2D glRelease() 全局污染**（live2d_renderer.py:619）
   关一个 Live2D 宠释放进程级 GL 状态 → 其他 Live2D 宠渲染崩坏。
   → 不要释放全局状态，或按 GL 上下文隔离。

2. **TTS 缓存 WAV 永不清理**（cosyvoice.py:34/421/428）
   缓存键含完整文本，动态回复几乎每次都 miss → 每句一个新 wav 永不删除，磁盘无限增长。
   → 加 TTL/LRU 清理或播放即删。

3. **TTS provider 跨线程替换竞态**（voice_provider_mixin:116-117 vs conversation_engine worker）
   无锁读写 `self._tts`/`self._tts_ready`，可能在合成中途替换成 None。
   → 用 `self._lock` 保护。

4. **TTS 同步合成阻塞消息队列**（conversation_engine.py:472）
   CPU 版 CosyVoice 单句 60-150s 会卡住整个消息队列。
   → TTS 移独立线程/线程池。

5. **`_user_turn_active` 逻辑断裂**（conversation_engine.py:374/443/483/264）
   只写不读（死代码），且打断后不复位会永久屏蔽主动消息。
   → 删字段或补读取点 + interrupt 时复位。

---

## P2（建议修）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| 1 | tool_executor 临时 .mjs 超时/异常泄漏 | tool_executor.py:63-78 | try/finally 删除 |
| 2 | `_global_inited` 应为模块级 | live2d_renderer.py:164 | 避免重复 l2d.init() |
| 3 | Live2D 调试残留：首帧写图 + idle 刷日志 | live2d_renderer.py:319-326,349-350 | L2D_DEBUG 门控 |
| 4 | VAD 缓冲跨线程无锁 | chat_mixin.py:132-164 | queue.Queue/锁 |
| 5 | 持续监听 ASR 线程无并发上限 | chat_mixin.py:160 | 限流/合并 |
| 6 | ARCHITECTURE.md 全面过期 | 文档 | 对照真实结构重写 |
| 7 | VRMRenderer 静默空白 | factory.py:106 | 给用户明确提示 |
| 8 | .env 明文 + 硬编码 W:/ 路径 | 配置 | 确认 gitignore + 文档化 |

---

## 架构要点

- **好的**：分层清晰（core 无 UI / avatar 渲染抽象 / provider 隔离）、事件总线解耦任务系统、对话打断代际机制完整、渲染抽象踩坑沉淀扎实。
- **欠债**：`pet.py` 仍是 god-object（mixin 只搬方法不搬接线，`__init__` 约 300 行集中接线）、多宠 Live2D 全局 GL 释放、TTS 缓存阻塞、ARCHITECTURE.md 与代码脱节。

## 安全
- ✅ 无硬编码密钥（API key 全在 .env / provider-catalog）
- ✅ 无命令注入（tool_executor 无 shell，参数 JSON 化）
- ⚠️ tool_executor JS 模板拼接用 f-string 而非 json.dumps（健壮性问题）
- ⚠️ 屏幕感知截图送视觉模型，已有黑名单/模糊开关

## 性能
- 主要瓶颈：TTS 同步合成阻塞消息队列（P1）
- Live2D idle 重触发刷日志 + 首帧写诊断 PNG（P2）
- 800ms 轮询文件桥 / 2s 前台轮询可为事件驱动（保守但可优化）