# 当前状态

> 2026-08-12 · 奥菲莉娅接管

## 已完成

- **CosyVoice 链路打通**：executor.py 补丁生效（torchaudio→soundfile），speaker_refs 自动补全逻辑已实现，ophelia 语音真机验证通过
- **T1 角色↔agent 解耦**：agent 名不匹配根因修复，零硬编码
- **右键切换助手** + 每 agent 会话保留
- **滚轮缩放**（0.5~3.0，真机已验）
- **T3/T4 Live2D**：缩放 fit 0.359→0.382，motion 组过滤
- **小蕾米插件学习笔记**已记录

## 待处理（按优先级）

### 批次 1：测试恢复（当前进行中）
- [ ] 恢复被删的 4 个测试文件（git checkout）
- [ ] 跑 pytest 确认 61 绿
- [ ] 清理 main.py 的 Aqua 死代码（可选）

### 批次 2：音色参考文本补全 ✅
- [x] rebecca — whisper 转录日配，补全 ref_text，端到端合成验证通过（4.7s）
- [x] alice — whisper 转录日配，补全 ref_text
- [x] luoqixi — whisper 转录日配，补全 ref_text
- [x] 全部 7 个说话人 ref_audio+ref_text 完整

> 注：三个缺失文本均为日配音频，用 whisper base 转录。rebecca 已实测合成出声。

### 批次 3：视觉展示（Live2D 渲染）← 当前
- [ ] 确认 L2D 离屏截图全黑问题（l2d_diag.png 1046B 全黑）
- [ ] 角色大小调优
- [ ] 动效/UI 布局优化

### 批次 4：后续
- [ ] 服务端无 agent 的手动引导 UI
- [ ] 其他功能补充

## 关键文件位置
- CosyVoice 项目：`W:/Games/Hanako/Work/projects/cosyvoice-tts`
- 桌宠 worker：`oc-pet/tts_provider/cosyvoice_worker.py`
- 插件 executor：`~/.hanako/plugins/hanako-audio-player/executor.py`
- speaker_refs.json：`cosyvoice-tts/speaker_refs.json`
- 参考音频：`W:/Games/Hanako/Work/通用/助手/语音/`
- 学习笔记：`oc-pet/docs/xiaolemi-plugin-learning.md`