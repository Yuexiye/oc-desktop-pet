# OC 桌宠优化计划

> 基于代码审计和运行时日志分析，2026-08-18

---

## 🔴 高优先级

### 1. 补全 `ishiki.md` 配置文件
**现状**：启动告警 `配置不完整,缺失: ishiki.md`，对话引擎缺少行为约束文件。
**影响**：LLM 生成的回复缺乏角色行为准则指导，可能产生不符合角色设定的回复。
**方案**：在 `~/.hanako/agents/miku/` 下创建 `ishiki.md`，定义 miku 的说话风格、行为边界、禁忌话题。

### 2. 修复 config.window 200x300 异常值
**现状**：config.json 中 `window: {width: 200, height: 300}` 被自动修正为 458x520。
**影响**：每次启动都会触发告警，且修正后的尺寸可能不是用户期望的。
**方案**：清除 config.json 中的 `window.width` 和 `window.height` 字段，或设为正确值。

### 3. 代理中断保护（代际打断 + TTS 竞态）
**现状**：conversation_engine.py 已有代际打断机制，但 TTS 线程池在引擎关闭时仍有 `use-after-cleanup` 风险。
**影响**：频繁打断/快速连续对话可能触发 TTS 回调打到已关闭的引擎实例。
**方案**：在 `_tts_executor.shutdown(wait=False)` 前增加引用计数等待，确保所有在途合成完成。

---

## 🟡 中优先级

### 4. OC_TRACE 导入追踪性能开销
**现状**：main.py 启用了对 `faster_whisper/ctranslate2/live2d` 的导入追踪，每次 import 触发 `traceback.print_stack`。
**影响**：启动时日志刷屏（约 40+ 条 OC_TRACE），且 traceback 获取有性能开销。
**方案**：首次启动后关闭追踪，或仅在 `OC_DEBUG=1` 环境变量下启用。

### 5. 插件热刷新间隔可配置
**现状**：`conversation_engine.py` 硬编码 `_tool_refresh_interval = 30.0` 秒。
**影响**：频繁刷新增加不必要的文件扫描开销。
**方案**：移到 config.json 中可配置，默认 60s。

### 6. 屏幕感知的截图压缩质量可调
**现状**：`JPEG_QUALITY` 硬编码，OPT 调用固定使用 `compress=True`。
**影响**：低配机器上截图+压缩+vision LLM 可能造成卡顿。
**方案**：移到 config.json 中可配置，并提供 `interval_min/interval_max` 随机范围（已实现）。

---

## 🟢 低优先级

### 7. 多宠桥接社交事件池扩展
**现状**：SocialEventGenerator 只有 6 种场景，冷却 30 分钟。
**影响**：多宠模式下社交互动种类有限。
**方案**：增加场景模板（如"争宠"、"吐槽用户"、"联合催更"）。

### 8. 养成系统平衡性调整
**现状**：属性衰减速率硬编码（DECAY_RATES），ItemRegistry 只有 7 件默认物品。
**影响**：长期运行后属性衰减模式单一，物品选择有限。
**方案**：DECAY_RATES 移到 config.json 可配置，物品系统支持动态加载。

### 9. 启动画面加载时间优化
**现状**：Live2D 模型加载 + fit 检测耗时约 8.5 秒（从日志可见）。
**影响**：用户等待时间较长。
**方案**：fit 检测结果缓存，下次启动直接读取；或支持异步加载骨架。

### 10. 模块化拆分 pet.py
**现状**：pet.py 仍有约 1500+ 行，10 个 mixin 只搬了方法，`__init__` 约 300 行集中接线。
**影响**：每次修改 PetWindow 需要理解整个初始化流程。
**方案**：将 `_init_*` 方法也拆到各 mixin 中，PetWindow 只做注册和接线。

---

## 用户需求：模型动作展示菜单

### 分析
- Live2D 模型加载时已收集 `_motion_files`（motion 文件名列表）和 `_expression_names`（表情名列表）
- 当前渲染器已实现 `_play_motion_kw(*groups)` 按关键词播放 motion、`_start_motion_at(idx)` 按索引播放
- 右键菜单已有「互动」「玩法」「管理」三个分组，可在此添加「模型动作」子菜单

### 实现方案
在右键菜单「互动」组内增加「模型动作」子菜单，动态列举：
- **动作列表**：从 `renderer._motion_files` 读取 motion 文件名，每个文件生成一个菜单项
- **表情列表**：从 `renderer._expression_names` 读取表情名，每个表情生成一个菜单项
- 点击动作 → 调用 `renderer._play_motion_kw(关键词)` 或 `renderer._start_motion_at(idx)`
- 点击表情 → 调用 `renderer.set_emotion_expression_only(emotion_name)`
- 点击「重置表情」→ 调用 `renderer._model.ResetExpressions()`