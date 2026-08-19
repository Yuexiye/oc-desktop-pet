# THIRD-PARTY NOTICES

> oc-pet（AGPL v3）内包含/参考的第三方代码声明
> 更新日期：2026-08 ｜ 维护者：oc-pet 团队

本文件登记所有从 **N.E.K.O. 猫娘计划**（Apache License 2.0）搬入或参考的文件，
并附 Apache 2.0 版权声明，满足「AGPL 仓库内再分发 Apache 2.0 代码」的合规要求。

---

## 1. 上游项目信息

| 项 | 值 |
|---|---|
| 项目名 | N.E.K.O. 猫娘计划（Project N.E.K.O.） |
| 仓库 | https://github.com/Project-N-E-K.O/N.E.K.O |
| 许可 | Apache License 2.0（https://www.apache.org/licenses/LICENSE-2.0） |
| 版权 | Copyright 2025-2026 Project N.E.K.O. Team；Copyright (c) 2025 Hongzhi Wen |
| 上游 NOTICE | 见 N.E.K.O. 仓库根目录 `NOTICE`（本项目在 `third_party_reference/neko/` 中未复制该文件，仅在本声明引用） |
| 移植规划 | 见 `docs/migration-neko-port-plan.md` |

Apache License 2.0 完整文本：<https://www.apache.org/licenses/LICENSE-2.0>

---

## 2. 搬运/参考文件清单

所有文件以 **原样拷贝（verbatim）** 方式放入 `third_party_reference/neko/`（只读参考区），
**未改动任何内容**，原 Apache 2.0 版权头逐字保留。运行时代码为 oc-pet 重写实现，
**不会**从 `third_party_reference/` 导入（该目录仅作合规记录与移植参考）。

### 2.1 直接搬运文件（含内联 Apache 2.0 版权头）

| 参考区路径（相对 `third_party_reference/neko/`） | 上游原路径（N.E.K.O.） | 对应移植项 | 移植落点（oc-pet 重写） |
|---|---|---|---|
| `main_logic/proactive_chat/contracts.py` | `main_logic/proactive_chat/contracts.py` | P0-2 | `core/perception/proactive_contracts.py` |
| `main_logic/proactive_chat/state.py` | `main_logic/proactive_chat/state.py` | P0-2 | `core/perception/proactive_state.py` |
| `memory/hybrid_recall.py` | `memory/hybrid_recall.py` | P0-3 | `core/memory_hybrid.py` |
| `memory/script_fold.py` | `memory/script_fold.py` | P0-3 | `core/memory_keywords.py` |
| `memory/persona/_shared.py` | `memory/persona/_shared.py` | P0-3（`_extract_keywords`） | `core/memory_keywords.py` |
| `main_logic/activity/focus_scorer.py` | `main_logic/activity/focus_scorer.py` | P0-5 | `core/perception/focus.py` |
| `memory/embeddings.py` | `memory/embeddings.py` | P1-1 | `core/memory_embedding.py`（参考重写：fallback gate / 生命周期思路，threading 化） |
| `memory/anti_repeat.py` | `memory/anti_repeat.py` | P1-5 | `core/anti_repeat.py`（BM25 语义指纹 + 时间窗去重直接搬；去 asyncio，threading + 同步落盘） |
| `main_logic/activity/snapshot.py` | `main_logic/activity/snapshot.py` | P1-6 | `core/perception/screen_intent.py`（状态/倾向/口吻词汇 + 意图识别逻辑参考重写） |
| `main_logic/activity/llm_enrichment.py` | `main_logic/activity/llm_enrichment.py` | P1-6 | `core/perception/screen_intent.py`（LLM 语义增强 + 失败静默降级思路参考重写） |

上述 10 个文件均以 `# Copyright 2025-2026 Project N.E.K.O. Team` 开头，
Apache 2.0 许可文本随文件头保留。

### 2.1b 运行时直接搬入文件（含内联 Apache 2.0 版权头，位于 oc-pet 源码树内）

> 与 2.1 只读参考区不同：以下文件是 oc-pet **运行时代码**中按迁移文档
> T02/T03/T04 标记为"直接搬"的纯算法/纯词汇部分，原样搬入时保留 Apache 2.0
> 版权头，因此 `git grep -l "Project N.E.K.O"` 同样会命中它们，必须在本声明登记。

| oc-pet 运行路径 | 上游原路径（N.E.K.O.） | 对应移植项 | 说明 |
|---|---|---|---|
| `core/perception/proactive_contracts.py` | `main_logic/proactive_chat/contracts.py` | P0-2 | reason_code/stage 词汇（直接搬，去 HTTP/asyncio） |
| `core/perception/proactive_state.py` | `main_logic/proactive_chat/state.py` | P0-2 | `_half_life_for`/`_source_skip_probability` 半衰期节流（直接搬）+ oc-pet 每日预算/去重包装 |
| `core/memory_hybrid.py` | `memory/hybrid_recall.py` | P0-3 | BM25 + RRF 混合召回纯算法（直接搬，去 asyncio/文件池加载）；cosine 端留 `EmbeddingProvider` 接口等 P1-1 |
| `core/memory_keywords.py` | `memory/script_fold.py` + `memory/persona/_shared.py` | P0-3 | `fold_script` 繁简折叠 + `_extract_keywords`/`_tokenize` CJK 2/3-gram 分词（直接搬） |
| `core/perception/focus.py` | `main_logic/activity/focus_scorer.py` | P0-5 | `FocusScorer`/`FocusScore` 三信号加权直接搬；`FocusStateMachine` hysteresis 按 oc-pet threading 重写 |
| `core/anti_repeat.py` | `memory/anti_repeat.py` | P1-5 | `bm25_score`/`AntiRepeatCorpus`/`UnansweredProactiveRepeatSignal` 直接搬（保留 Apache 头）；去 asyncio → threading + 同步落盘 `~/.oc-pet/memory/anti_repeat.json`；ngram 复用 `core/memory_keywords.tokenize` |

### 2.1c P1-2/P1-3 参考拷贝文件（含内联 Apache 2.0 版权头，位于参考区）

> 与 2.1 相同的只读参考区拷贝（`third_party_reference/neko/`），按原样保留
> Apache 2.0 版权头；运行时 oc-pet 侧按迁移文档 P1-2/P1-3 标记为"参考重写"，
> 不直接 import 参考区。事实库/反思引擎在 oc-pet 的实现为
> `core/memory_facts.py`、`core/memory_reflection.py`（PySide6/threading 重写）。

| 参考区路径（相对 `third_party_reference/neko/`） | 上游原路径（N.E.K.O.） | 对应移植项 | 移植落点（oc-pet 重写） |
|---|---|---|---|
| `memory/facts.py` | `memory/facts.py` | P1-2 | `core/memory_facts.py` |
| `memory/evidence.py` | `memory/evidence.py` | P1-2 | `core/memory_facts.py`（evidence 数学） |
| `memory/fact_dedup.py` | `memory/fact_dedup.py` | P1-2 | `core/memory_facts.py`（去重思路） |
| `memory/refine.py` | `memory/refine.py` | P1-3 | `core/memory_reflection.py`（摘要压缩思路） |
| `memory/reflection/__init__.py` | `memory/reflection/__init__.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/_shared.py` | `memory/reflection/_shared.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/evidence_flow.py` | `memory/reflection/evidence_flow.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/manager.py` | `memory/reflection/manager.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/persistence.py` | `memory/reflection/persistence.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/promotion.py` | `memory/reflection/promotion.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/promotion_merge.py` | `memory/reflection/promotion_merge.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/refinement.py` | `memory/reflection/refinement.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/surfacing.py` | `memory/reflection/surfacing.py` | P1-3 | `core/memory_reflection.py` |
| `memory/reflection/synthesis.py` | `memory/reflection/synthesis.py` | P1-3 | `core/memory_reflection.py` |

### 2.1d P1-6 参考重写文件（AGPL，非直接搬，参考上游概念）

> `core/perception/screen_intent.py` 是 oc-pet **参考重写**（AGPL v3），非直接搬运：
> 场景词汇/倾向/口吻映射参考 `main_logic/activity/snapshot.py`，LLM 语义增强 +
> 失败静默降级思路参考 `main_logic/activity/llm_enrichment.py`；不复制上游代码，
> 不含内联 Apache 头，因此不命中 `git grep -l "Project N.E.K.O"`。上游参考文件
> 已在 2.1 表登记（`main_logic/activity/snapshot.py`、`main_logic/activity/llm_enrichment.py`）。

### 2.2 参考拷贝文件（原文件无内联版权头，属项目级 Apache 2.0 许可范围）

| 参考区路径（相对 `third_party_reference/neko/`） | 上游原路径（N.E.K.O.） | 对应移植项 | 说明 |
|---|---|---|---|
| `frontend/react-neko-chat/src/styles.css` | `frontend/react-neko-chat/src/styles.css` | P0-6 / P0-7 / P1-8 | 设计语言参考（气泡角色色/圆角/阴影/动画 token）。上游文件本身不含内联版权头，依据 N.E.K.O. 仓库级 Apache 2.0 许可整体覆盖；此处原样拷贝并在此声明归属 |

> 边界说明：`docs/playmate-direction-prd.md`、`docs/playmate-gap-analysis.md`、
> `docs/migration-neko-port-plan.md` 中出现 "Project N.E.K.O" 字样，系 oc-pet
> 自身撰写的竞品分析与移植规划文档（prose 提及），**不是**从上游搬入的代码文件，
> 故不列入上表。

---

## 3. Apache 2.0 版权声明（随搬入文件保留）

以下声明适用于 `third_party_reference/neko/` 内所有文件：

```
Copyright 2025-2026 Project N.E.K.O. Team

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 4. 合规边界与验证

1. **只读参考区**：`third_party_reference/neko/` 仅作合规记录与移植参考，
   运行时不得被 oc-pet 代码 import。实际功能代码为 oc-pet 按 AGPL v3 重写。
2. **头保留**：直接搬运文件保留原 `# Copyright 2025-2026 Project N.E.K.O. Team` 头，未改动内容。
   运行时直接搬入文件（2.1b）保留同样头部，且因算法直接源自上游，Apache 2.0
   声明随文件头保留；其外层封装/集成代码为 oc-pet 新增，整体按 AGPL v3 分发。
3. **一致性验证**：
   - `git grep -l "Project N.E.K.O"` 命中的**参考区搬入文件**（`third_party_reference/neko/` 下
     带版权头的 10 个 `.py` 文件）与上表 2.1 完全一致；
   - **P1-2/P1-3 参考拷贝**（2.1c，14 个 `.py` 文件：`memory/facts.py`、`memory/evidence.py`、
     `memory/fact_dedup.py`、`memory/refine.py`、`memory/reflection/` 下 10 个文件）同样带
     Apache 2.0 版权头，命中同一 grep，已在 2.1c 登记；
   - **运行时直接搬入文件**（6 个，均在 oc-pet 源码树内且带内联版权头）命中同一 grep，
     已在 2.1b 登记：
     `core/perception/proactive_contracts.py`、`core/perception/proactive_state.py`、
     `core/memory_hybrid.py`、`core/memory_keywords.py`、`core/perception/focus.py`、
     `core/anti_repeat.py`；
   - **P1-6 参考重写**（2.1d，`core/perception/screen_intent.py`）为 AGPL 重写、无内联头，
     不命中该 grep，已在 2.1d 登记归属；
   - `styles.css`（2.2）不含内联头，故不命中该 grep，但已在 2.2 登记归属；
   - oc-pet 自撰文档（playmate 分析/移植规划）属 prose 提及，不纳入搬运清单。
4. **新增搬运流程**：后续若再搬入任何 N.E.K.O. 文件（含运行时代码），必须同步更新本文件
   2.1/2.1b/2.2 表格，并保留上游版权头。

---

## 5. 免责声明

第三方代码按 Apache 2.0 许可 "AS IS" 提供，无任何明示或默示担保。
oc-pet 与本声明中引用的上游项目无隶属或背书关系。
