# 小蕾米桌宠插件 · 设计拆解学习笔记

> 分析对象：`小蕾米桌宠插件-v0.1.0.zip`（remielle-xiaolemi，Hanako 插件）
> 分析日期：2026-08-07
> 目的：作为 oc-pet 桌宠项目的参考——重点学习「桌宠如何与 Host 事件总线联动」的状态机设计，及其与 oc-pet 对话引擎状态机的对比。

---

## 一、插件架构：两层分离

```
┌─────────────────────────────────────────────┐
│  HanaAgent（宿主）                            │
│  ┌───────────────────────────────────────┐  │
│  │ 插件 lifecycle (index.js)              │  │
│  │  ├─ 订阅 ctx.bus 事件总线               │  │
│  │  ├─ 归约成 petState（状态机）            │  │
│  │  └─ 暴露 bus handler                   │  │
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │ routes/ui.js                          │  │
│  │  └─ HTTP GET /api/pet-state           │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
              │ 1.5s 轮询
              ▼
┌─────────────────────────────────────────────┐
│  桌宠本体 (xiaolemi-pet.exe, Tauri)          │
│  读 pet-state → 播放对应动画                 │
└─────────────────────────────────────────────┘
```

**核心思想**：宿主状态归约（插件）与桌宠渲染（独立 exe）**完全解耦**。插件只负责"把事件变成状态"，桌宠只负责"把状态变成动画"。中间通过 HTTP 轮询松耦合。

**关键设计**：桌宠本体是独立 Tauri exe，部署到 `%LOCALAPPDATA%\XiaolemiPet\`——**脱离 Hana 也能单独运行**（只是没有状态联动）。这与 oc-pet 的"桌宠内嵌对话引擎"是不同的架构取向。

---

## 二、状态机设计（最值得学）

### 状态集合
```
IDLE / RUNNING(工作中) / WAITING(等待确认) / REVIEW(思考)
COMPLETE(jumping庆祝) / FAILED(翻车) / WAVING(挥手)
```

### 事件映射（EVENT_MAP）
| 宿主事件 | 桌宠状态 |
|---------|---------|
| `tool_execution_start/update` | RUNNING |
| `turn_start` / `llm_usage` / `message_start/update` | REVIEW（思考） |
| `turn_end` | IDLE（回待机） |
| `tool_execution_end` | 延迟 2s 确认 |

### 三个精妙设计（踩坑沉淀）

**1. 工具完成的 2 秒确认（debounce）**
```js
tool_execution_end → clearTimeout(pendingComplete)
    → setTimeout(2s)：
        - 2s 内无新工作事件 → COMPLETE（庆祝）
        - 2s 内有新工具 → 取消，回到 RUNNING
```
> **为什么**：密集工具流里，"这个工具结束"不代表"任务完成"——下一个工具立刻开始。直接跳 COMPLETE 会造成"一直工作中/频繁闪烁"。延迟确认避免误判。

**2. 庆祝冷却（10s）**
```js
if (now - _lastToolEndCelebration >= TOOL_END_COOLDOWN_MS) celebration
else → IDLE
```
> 防止连续工具完成时动画闪成"迪斯科"。

**3. 空闲看门狗（15s）**
```js
armIdleWatchdog() → 15s 后若仍在 RUNNING/REVIEW → 强制回 IDLE
```
> **兜底色**：`tool_execution_end` 事件可能缺失/延迟（turn 结束才 flush），或 `turn_end` 丢失。看门狗保证桌宠不会永远卡在某个状态。新事件会重置看门狗。

### 优先级
- **错误优先**：`event.isError` 直接 FAILED（最高）
- **完成后冷却**：celebrate 受冷却限制
- 事件日志节流：同类 30s 一条，防刷屏

---

## 三、与 oc-pet 对话引擎状态机的对比

小蕾米和 oc-pet 的对话引擎（`conversation_engine.py`）是**同一类问题**，但解法不同：

| 维度 | 小蕾米（插件） | oc-pet（对话引擎） |
|------|--------------|------------------|
| 状态来源 | 宿主事件总线（被动订阅） | 自身对话流程（LLM/TTS/工具） |
| 状态集合 | 工作/思考/等待/完成/失败 | 情绪（happy/sad/thinking...）+ 动作 |
| 完成判定 | 工具结束延迟 2s 确认 | LLM 回复就绪即回调 |
| 防卡死 | 15s 空闲看门狗 | turn 超时（30s/180s） |
| 防刷屏 | 庆祝冷却 + 日志节流 | 气泡节流（test_bubble_throttle）|
| 架构 | 状态归约与渲染解耦 | 引擎内嵌渲染回调 |

**可借鉴到 oc-pet 的点**：
1. **空闲看门狗**（小蕾米 15s → oc-pet 的 think_timeout 已有类似，但可更精细）
2. **完成 debounce**：oc-pet 工具调用完成 → 是否也该延迟确认"任务完成"再切庆祝动画，避免密集工具流闪烁
3. **事件日志节流**：oc-pet 的 `_do_tool_progress` 频繁调用时，是否也需节流防刷
4. **状态与渲染解耦**：oc-pet 目前引擎内嵌渲染，若未来要做"多端渲染"（桌面+网页+手机），可借鉴小蕾米的"状态归约 + HTTP 轮询"分层

---

## 四、其他可复用点

- **部署幂等**：`deployAndStartPet` 检查文件大小差异才覆盖 + `petIsRunning` 检测已运行则跳过——避免每次启动都重部署
- **诊断日志落盘**：`DEPLOY_LOG = .hanako/logs/xiaolemi-deploy.log`，绕过插件日志通道不可见时也能排查
- **环境变量开关**：`XIAOLEMI_STATE_LOG` 控制是否记录状态明细，默认关闭——诊断友好
- **版权/NOTICE 正规**：米哈游素材非商业同人，含使用边界

---

## 五、局限（供参考）

1. `trust: full-access` + 自动部署 exe + 开机自启——权限较大
2. 事件映射依赖宿主事件名，若 Hana 事件流改名会脱钩（有 inferState 兜底但有限）
3. `petIsRunning` 用 `tasklist` 固定进程名，多实例/改名会失效
4. 轮询式（1.5s）状态同步，实时性不如 oc-pet 的内存回调直连

---

## 六、结论

小蕾米的价值不在角色，而在**“宿主事件总线 → 状态机 → 桌宠动画”这条链路的工程化成熟度**。它的 debounce/冷却/看门狗三个设计，是“桌宠随 Agent 工作状态动”这个问题经过真实踩坑后的最佳实践，值得 oc-pet 借鉴进自己的对话引擎状态机。

---

## 七、落地到 oc-pet（2026-08-08）

### B：小蕾米设计反向应用到 oc-pet

| 小蕾米设计 | oc-pet 落地 | 结论 |
|-----------|------------|------|
| 空闲看门狗(15s) | 已有 `_think_timeout`(30s/180s) + P1 打断状态机 | **不重复加**（能力已具备） |
| 工具完成 debounce(2s) | oc-pet 是同步工具链（`_handle_tool_calls` 返回最终 reply） | **不适用**（架构无 async merge 空间） |
| 事件/日志节流 | `_handle_session_tool_progress` 新增节流（同 工具+phase 500ms 合并） | **已实现** |
| 庆祝冷却 | oc-pet 情绪动画无高频庆祝 | 暂不需要 |

### A：T3/T4 Live2D 修复

**T3（角色不够大）**：`_recompute_fit` 系数 0.92→0.98，统一初始缩放按高度。真机 `fit 0.359→0.382`，角色更大。

**T4（动效不明显）**：`GetMotionGroups()` 返回 `['']`（空字符串组名），过滤后 `_motion_groups=['']→[]`。但过滤后该模型无真实 motion 组，`_start_idle` 走 else 用 `MotionGroup.IDLE` 常量。

**⚠️ 遗留问题（真机发现）**：L2D 离屏截图 `l2d_diag.png` 仍为全黑(1046字节)，截图视角也看不到角色。**角色可能根本没画出来**（不只是动效不明显）。需进一步排查：grabFramebuffer 对 QOpenGLWidget 抓取、或 GL 渲染循环未驱动。headless 无法验证透，需真机确认。