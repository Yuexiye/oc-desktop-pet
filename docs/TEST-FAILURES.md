# 测试失败 Bug 列表

> P0/P1 验收时发现 14 个预先存在的测试失败
> 日期：2026-09-05
> 状态：待修复

---

## 汇总

- **总数**：14 个失败
- **总数**：435 个测试
- **通过率**：421/435 = 96.8%

---

## Bug 分类

### 1. CharacterCard 相关（4 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 1 | test_character_card_renders_info | test_character_card_hud_offscreen.py | CharacterCard 渲染问题 |
| 2 | test_character_card_empty_placeholder | test_character_card_hud_offscreen.py | 同上 |
| 3 | test_character_card_theme_switch | test_character_card_hud_offscreen.py | 主题切换问题 |
| 4 | test_character_card_set_agent_refresh | test_character_card_hud_offscreen.py | agent 刷新问题 |

**优先级**：P2（不影响核心功能）

---

### 2. MemoryPanel 相关（5 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 5 | test_memory_panel_empty_placeholder | test_chat_ui_offscreen.py | NameError: QWidget 未定义 |
| 6 | test_memory_panel_real_data | test_chat_ui_offscreen.py | 同上 |
| 7 | test_memory_panel_theme_switch | test_chat_ui_offscreen.py | 同上 |
| 8 | test_panel_empty_placeholder_offscreen | test_memory_facts_reflection.py | 同上 |
| 9 | test_panel_reads_fact_and_reflection_files | test_memory_facts_reflection.py | 同上 |

**原因**：`ui/memory_panel.py:284` 的 `NameError: name 'QWidget' is not defined`

**修复**：在 `memory_panel.py` 添加 `from PySide6.QtWidgets import QWidget`

**优先级**：P1（影响记忆面板功能）

---

### 3. 资源检查相关（1 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 10 | test_resource_available_marks_missing_models | test_package_switch_persistence.py | 模型资源检查逻辑 |

**优先级**：P2

---

### 4. 心跳相关（1 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 11 | test_heartbeat_pushes_during_blocked_chat_and_stops_after_return | test_qa_bugfix4_edge.py | 心跳逻辑 |

**优先级**：P2

---

### 5. 启动冒烟测试（1 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 12 | test_window_constructed_with_all_neko_wiring | test_real_startup_smoke.py | 启动时 N.E.K.O. 线路检查 |

**优先级**：P1（影响启动）

---

### 6. 回复丢失 BugFix（2 个）

| # | 测试 | 文件 | 可能原因 |
|---|---|---|---|
| 13 | test_interrupt_then_new_message_llm_ok_triggers_on_reply | test_reply_missing_bugfix.py | 打断后回复丢失 |
| 14 | test_reply_preserved_when_no_interrupt_during_llm | test_reply_missing_bugfix.py | 无打断时回复丢失 |

**错误信息**：
```
AssertionError: assert '你好呀～ [emotion:neutral]' == '你好呀～'
- 你好呀～
+ 你好呀～ [emotion:neutral]
```

**原因**：T1-4 改动引入——有 `[emotion:xxx]` 标签时不再做语义分析，但测试期望标签被剥离。

**修复**：检查 `conversation_engine.py` 的标签剥离逻辑。

**优先级**：P0（影响回复显示）

---

## 修复优先级

| 优先级 | Bug 数 | 说明 |
|---|---|---|
| P0 | 2 | 回复丢失（T1-4 引入） |
| P1 | 6 | MemoryPanel（5）+ 启动冒烟（1） |
| P2 | 6 | CharacterCard（4）+ 资源检查（1）+ 心跳（1） |

---

## 下一步

1. 修复 P0（回复丢失）
2. 修复 P1（MemoryPanel + 启动冒烟）
3. 修复 P2（CharacterCard + 资源检查 + 心跳）

---

## 参考

- `tests/` 目录 — 测试文件
- `ui/memory_panel.py` — MemoryPanel 实现
- `ui/character_card.py` — CharacterCard 实现
- `core/conversation_engine.py` — 对话引擎
