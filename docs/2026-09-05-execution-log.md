# 2026-09-05 oc-pet 优化执行

## 操作

按调度中心流程执行 oc-pet 优化任务：
- P0 止血 6 项（README 修复、依赖、auth_token、模型脚本、默认脸、版本固定）
- P1 体验+文档 8 项（自检、体检报告、触摸冷却、情绪标记、WHY.md、SETUP-FOR-AI.md、模型适配、FAQ、日志分级）
- P2 架构 5 项（管线化、_IGNORED_EXPRESSIONS、Blend 折算、BodyAPI、依赖分层、静默异常审计）
- spike 借壳路线调研（VTS/Wallpaper）

## 重点

- **P0/P1/P2 全部完成**，共 19 项
- **spike 结论**：借壳路线不推荐，维持现状
- **P3 调整**：T3-1 外部大脑接入取消，T3-2 本地语音栈独立新对话
- **测试结果**：421/435 通过（14 失败是预先存在的 bug，已记录到 docs/TEST-FAILURES.md）
- **静默异常审计**：268 处，报告在 docs/SILENT-EXCEPTIONS.md

## 文件变更

### 新增
- `docs/WHY.md` — oc-pet 的 7 条护城河
- `docs/SETUP-FOR-AI.md` — 整段复制给 AI 装
- `docs/MODEL-ADAPTATION.md` — 模型适配三步指南
- `docs/FAQ.md` — 已知坑前置
- `docs/TEST-FAILURES.md` — 14 个测试失败 bug 列表
- `docs/SILENT-EXCEPTIONS.md` — 268 处静默异常审计
- `docs/SPIKE-BORROW-SHELL.md` — 借壳路线可行性调研
- `core/startup_check.py` — 首启自检工具
- `core/body_api.py` — BodyAPI 门面
- `avatar/model_health.py` — 模型体检报告
- `avatar/frame_pipeline.py` — 每帧管线化框架
- `scripts/audit_silent_exceptions.py` — 静默异常审计脚本
- `requirements-core.txt` / `requirements-audio.txt` / `requirements-live2d.txt` / `requirements-tts.txt` / `requirements-asr.txt` / `requirements-optional.txt` — 分层依赖

### 修改
- `README.md` — 补"跑起来你会看到什么" + 免费模型脚本
- `requirements.txt` — 版本锁定 + live2d-py 补进
- `core/status_http_server.py` — auth_token 空值拒绝 + 自动生成
- `core/external_trigger_receiver.py` — auth_token 空值拒绝 + 自动生成
- `avatar/live2d_renderer.py` — neutral 表情加微笑 + ImportError 提示 + _IGNORED_EXPRESSIONS 可配置 + Blend 折算
- `core/conversation_engine.py` — 显式情绪标记压制
- `pet_mixins/interaction_mixin.py` — 触摸分层冷却
- `main.py` — 日志分级

## git commits

- `0590cf3` — P0 止血 6 项
- `91216dc` — P1 文档类 5 项
- `f988553` — P1 代码类 3 项
- `089aa2a` — P1 触摸冷却
- `fec62ec` — 记录 14 个测试失败 bug
- `8cf43b4` — P2 架构改造 3 项
- `cc1b2c0` — 静默异常审计
- `bafb8ea` — exp3 Blend 折算
- `a64ac8a` — 每帧管线化框架
- `89b91b1` — spike 借壳路线调研

## 下一步

- 修复 14 个测试失败 bug（P0 回复丢失 + P1 MemoryPanel）
- T3-2 本地语音栈（独立新对话）
