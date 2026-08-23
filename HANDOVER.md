# 桌宠项目转交文档

> 会话结束时间：2026-08-23 00:00+  
> 原因：Hana API 429 限流，用户转交任务  
> 项目路径：`W:\Games\Hanako\Work\projects\oc-pet`

---

## 一、当前状态

**全链路插件调用已打通 ✅**

23:59:04 日志确认：
```
统一路由 → 显式指定: play (plugin=hanako-audio-player)
统一路由(explicit) 执行工具 play: 请指定要播放的音频文件路径或在线 URL
```

插件面板弹出 ✅（用户 23:58 三次打开均正常）  
108 个工具在线 ✅（27 个 Hanako 插件全部扫描成功）

---

## 二、已完成

| 模块 | 内容 | 状态 |
|---|---|---|
| P1 情绪映射 | miku pet.json emotions + live2d_renderer 读取 | ✅ |
| P2 对话引擎 | parse_emotion 完备 | ✅ |
| P3 交互体验 | 文案池、气泡打字机提速、物理参数 | ✅ |
| P4 内测接入 | 调音台卡片、记忆链路、通用外部触发接收器 | ✅ |
| P5 月薪喵 | 帧率修复、帧率提升、专属反应帧 | ✅ |
| P6 功能深化 | plugin_tools 开关、手机感知并入 EventBus、work 完成触发 | ✅ |
| R2 修复 | miku 脚部、精灵图窗口贴合、Fixedsys 字体、窗口识别 prompt、随机动作 | ✅ |
| R3 修复 | 语音气泡、ASR 设备选择、动作权重、插件面板可弹出 | ✅ |
| **插件面板** | parent=None + 显式 show/raise/activateWindow + Qt.Window | ✅ |
| **插件工具启用** | config.json plugin_tools.enabled=true（修复重复 key 覆盖） | ✅ |
| **路由顺序** | 显式插件优先 → 静态能力 → 关键词 | ✅ |
| **成对匹配** | "用[插件名]的[工具名]" 正则支持 | ✅ |
| **正则修复** | Python 3 `\w` 匹配中文导致分组错误 → 改为 `[a-zA-Z]` | ✅ |
| **空参数保护** | play.js 缺 source 时返回友好提示 | ✅ |
| **ToolExecutor** | Node.js 子进程执行 Hanako 插件工具已验证可行 | ✅ |

---

## 三、用户明确关心的待办

### 3.1 动作优化（优先级：高）

用户说"只有 7 个 motion 文件不代表不能做别的动作"、"改参数是可行的"。

**方向：** 不依赖新 motion 文件，通过 Live2D 参数控制实现微动作：
- 动作 + 表情叠加：播放 motion 时同时叠加表情（脸红、眨眼、视线偏移）
- 参数随机化：motion 播完后，身体保持微小的随机摆动
- 表情序列：不播 motion，只做表情切换（脸红 2 秒 → 眨眼 3 次 → 视线游移）
- 用官方 Live2D 模型测试（用户要求）

**涉及文件：** `avatar/live2d_renderer.py`、`pet_mixins/animation_mixin.py`、`motion/behavior.py`

### 3.2 插件"整体调用"（优先级：高）

用户认为"插件是整体，拆成 108 个工具指定"用X的Y"不合理"，要求像 Hana 原界面一样自然。

**现状：** hanako-audio-player 没有 widget/main/pages 入口（只有 5 个工具），"整体调用"在 Hana 里也是通过 LLM 自动选择工具实现的。

**方案：** 
- 降低或移除静态能力 `play_music`（其 pattern 太宽，拦截了自然语言表达）
- 让"播首歌"这类自然语言走 LLM 工具调用路径，LLM 自动选择 play 工具并传参
- 或让 play_music 内部调用真正的插件工具 play，而不是乱传参数

**涉及文件：** `core/capability_registry.py`

### 3.3 configuration 问题

- 用户提到 429 限流，可能跟桌宠主动感知（screen/perception）频繁请求 LLM 有关
- 桌宠的 config.json 已被修改很多次，建议整理一份干净的配置模板

---

## 四、关键决策/踩坑记录

### 踩坑 1：config.json 重复 key
`config.json` 里有两个 `plugin_tools` 块（一个我插入的 true，一个原有的 false），JSON 重复 key 取最后一个 → false 覆盖 true。  
**教训：** 改 config.json 前先 grep 确认是否有重复 key。

### 踩坑 2：plugin_panel 不显示
`parent=self`（PetWindow）导致无边框透明窗口被 PetWindow 的 WS_EX_LAYERED/TRANSPARENT 约束，exec 在跑但窗口不显示。  
**修复：** `parent=None` + 显式 show/raise/activateWindow。

### 踩坑 3：Python 3 `\w` 匹配中文
`_EXPLICIT_PAIR_RE` 正则 `[\w.-]+` 在 Python 3 默认匹配中文汉字，导致"用crystal-speech-emote的render-speech"中 `\w+` 贪婪吞了"的render"，正则分组错误。  
**修复：** `\w` → `[a-zA-Z0-9_]`。

### 踩坑 4：play.js 空参数
快速路由不带参数执行 play.js，`source=undefined` 导致 `source.split('/')` 崩溃。  
**修复：** 在 play.js 里加空参数保护。

### 踩坑 5：静态能力 play_music 抢路由
pattern='play' 匹配所有含"play"的文本，拦截了"用X的Y"显式调用。  
**修复：** 路由顺序改为显式插件优先。

---

## 五、修改文件清单

```
oc-pet/
├── pet.py                           # _open_plugin_panel parent=None + 显式显示
├── config.json                      # plugin_tools.enabled=true（修复重复 key）
├── core/
│   ├── unified_tool_router.py       # 路由顺序调整 + 成对匹配 + 正则修复
│   └── tool_executor.py             # 未改（已验证可用）
├── ui/
│   └── plugin_panel.py              # flags 加 Qt.Window
└── (外部) ~/.hanako/plugins/hanako-audio-player/tools/play.js  # 空参数保护
```

---

## 六、快速测试方法

```bash
# 1. 重启桌宠
cd W:\Games\Hanako\Work\projects\oc-pet
.\start_pet.bat

# 2. 测试插件面板
右键 → 管理 → 插件（应该弹出）

# 3. 测试显式调用
"帮我用hanako-audio-player的play功能 播放 C:/音乐/歌.mp3"

# 4. 测试其他插件
"用biaoqingbao的express 开心一下"
"用crystal-speech-emote的render-speech"

# 5. 查看日志
Get-Content logs\oc_pet.log -Tail 20
```

---

## 七、未解决问题

- ~~动作优化（Live2D 参数随机化）~~ ✅ 已完成 2026-08-23：三层程序化微动作（动作+表情叠加 / idle 随机摆动 / 表情序列），官方 Haru 模型已下载到 characters/sample_live2d/
- ~~插件"整体调用"（去除 play_music 拦截）~~ ✅ 已完成 2026-08-23：移除 play_music/search_music 静态能力，播歌走 LLM 工具路径；暂停/下一首本地直达
- ~~配置模板整理~~ ✅ 已完成 2026-08-23：config.template.json + docs/CONFIG_GUIDE.md
- ~~429 限流排查~~ ✅ 已完成 2026-08-23：主嫌疑为 screen.llm_enrich 每次截图发一次 LLM 请求，已加 300s 冷却（场景变化立即补），预计降 1/4~1/8
- 桌面应用控制（computer 工具整合）——未开始
- GL 真机渲染观感（idle sway 幅度/眨眼节奏/blush 模拟）需实机确认，可调 sway weight 与 _EMOTE_PRESETS 时长
- ~~若"放歌"要秒回反馈，可后续给 play_music 配真正调用 play 工具的 extractor（方案 b）~~ ✅ 已完成：play_music 收窄恢复（随机意图词）+ _pick_random_music 从 1183 首曲库随机选在线曲目
- 若仍 429，按 CONFIG_GUIDE 调大 interval_min/max 或关 llm_enrich
- Bug A 真实 WS 链路（play→tts→tts_bus 工具链 >180s 场景）需实机验证气泡/TTS 回灌
- ~~"查一下最近的邮件"被 web_search 拦截、关键词误触 list_stickers 弹 JSON~~ ✅ R3 已修：删除 8 个静态绑定 Hanako 插件能力 + 关键词路由白名单化 + 疑问句不路由 + JSON 结果友好化
- R3 观察项：疑问句检测仅作用于关键词路径（静态能力如"可以帮我暂停吗"仍直接暂停，设计权衡）；tests/ 未覆盖路由三模块（建议补 tests/test_router_r3.py）；WS 模式下播放控制本地直达 vs Hanako 服务端执行的边界需实机确认

## 八、本会话（2026-08-23）变更记录

### 第三轮（架构收敛）：删除静态绑定 Hanako 插件能力 / 白名单路由 / 工具扫描修复
架构原则：**Hanako 插件工具由 Hanako 服务端执行、LLM 语义选工具**；oc-pet 收敛为"消息转发 + WS 回灌"薄客户端，不替 Hanako 插件写静态 pattern/关键词路由。

| 文件 | 变更 |
|---|---|
| core/capability_registry.py | 删除 8 个静态绑定 Hanako 插件的 CAPABILITIES（web_search/fetch_webpage/save_webpage/search_images/check_feeds/search_bilibili/list_todos/time_stats）+ _extract_search_query/_extract_url；保留 13 个（播放控制 5 + play_music + 本地内部能力 7） |
| core/unified_tool_router.py | 关键词路由白名单化（audio_bus + linjian-peek 9 工具）；疑问句不路由（_is_question_text）；_COMMON_ZH_WORDS 收敛；docstring 更新 |
| core/conversation_engine.py | _friendly_tool_text()：JSON 工具结果转自然语言（不再原始 JSON 直出气泡）；内部能力不截断 |
| core/tool_registry.py | _scan_dir 支持无 source 声明工具（name→tools/ 同名文件映射：直接名→kebab-case→token 子序列）；tavily 4 工具全扫到参数完整 |

git：R3 commit 647a927 + 前两轮遗留功能代码/文档 commit（见 git log）

测试：`python -m pytest tests/ -q` → 276 passed（用 Python 3.12，PATH 默认 python 是 3.13 无 pytest）
QA 三轮验证：276 passed 可复现 + 91/48 项专项断言全过，判定 NoOne。

### 第一轮：动作优化 / 插件整体调用 / 配置+429

| 文件 | 变更 |
|---|---|
| avatar/live2d_renderer.py | 程序化微动作层：_ACTION_OVERLAYS / _update_idle_sway / play_emote_sequence（blush/blink3/gaze_shift 预设，blink 按 times 计数） |
| core/capability_registry.py | 移除 play_music/search_music 静态能力 + _extract_song_name；docstring 对齐 |
| core/unified_tool_router.py | docstring 对齐「显式插件优先 → 静态 → 关键词 → LLM」 |
| core/perception/screen.py | llm_enrich 冷却：_enrich_cooldown=300（下限 30，场景变化立即补） |
| core/conversation_engine.py | 过期注释清理（L104/L590） |
| pet.py | set_enrich_cooldown hasattr 兜底接线 |
| config.py | DEFAULT_CONFIG screen 块加 llm_enrich_cooldown: 300 |
| config.template.json（新增） | 规范配置模板，无重复 key |
| docs/CONFIG_GUIDE.md（新增） | 配置说明 + 重复 key 坑 + 429 调优 |
| characters/sample_live2d/（新增） | 官方 Haru 模型（gitignore 排除） |

### 第二轮（用户实机反馈后）：Bug A / Bug B / 预设池 / 工具解析

| 文件 | 变更 |
|---|---|
| core/hanako_session_manager.py | Bug A：TurnAccumulator 加 last_event_ts；send_and_wait 活跃窗口顺延（activity_timeout=60）；_expire_turn 活跃顺延；超时先 _recover_from_history（REST 拉最终回复）；_finish_with_error 加诊断日志 |
| core/harness_adapter.py | Bug A：_synthesize_reply_from_tools 兜底合成（文本空时从 tool_end details 合成），正常完成/turn 失败均覆盖 |
| core/capability_registry.py | Bug B：play_music 收窄恢复（随机放/随便放/来一首等随机意图词，不含"播放/暂停"）；_pick_random_music 从 playlist.json 只选 http/https 曲目（309 首）随机播放；_has_specific_music_request 有歌名让位 LLM |
| core/tool_registry.py | **隐藏大坑修复**：_extract_json_block 从不解析单引号 → 108 工具参数几乎全空；修复后 72/109 工具有完整参数 |
| avatar/live2d_renderer.py | 预设池 3→32 个（wink/blush_shy/sneak_peek/pout/yawn/nod/head_shake/giggle 等）；_EMOTE_PRESET_WEIGHTS 加权随机；blink 支持单眼 side；头部参数序列生效；序列期间暂停 idle sway |
| ~/.hanako/plugins/hanako-audio-player/{play.js,list_music.js(新),generate_speech.js,manifest.json} | Bug B 方向 c：play.js 明确 source 必须是真实路径/URL 不支持歌名随机；新增 list_music 工具；generate_speech 名实对齐；manifest v0.4.1 |

测试：`python -m pytest tests/ -q` → 276 passed（用 Python 3.12，PATH 默认 python 是 3.13 无 pytest）
QA 两轮验证：276 passed 可复现 + 91 项专项断言全过，判定 NoOne。

---

*转交人：奥菲莉娅 | 2026-08-23 00:00*