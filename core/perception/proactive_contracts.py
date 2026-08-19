# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""主动搭话稳定契约 — reason_code / stage 词汇与结果构造。

参考 N.E.K.O. `main_logic/proactive_chat/contracts.py`（Apache 2.0）直接搬入的
纯词汇/纯数据结构部分，按 oc-pet（PySide6 单体 + threading）重写：去掉 HTTP
框架依赖与 asyncio，保留决策管线的可观测 reason_code/stage 词汇。

作用：
  - tick 管线的每个阶段产生一个 reason_code，可写日志 / 状态 HUD，便于定位
    "为什么这次没搭话 / 为什么这次搭话"。
  - ``proactive_stage_for_reason`` 把 reason_code 归一到 stage，供统计聚合。

本文件是纯词汇模块，不依赖 Qt / asyncio / 网络。
"""
from __future__ import annotations

from typing import Any

# ── 主动搭话结果 action ─────────────────────────────────────
PROACTIVE_ACTION_CHAT = "chat"      # 成功搭话（文案已投递）
PROACTIVE_ACTION_PASS = "pass"      # 有意跳过（有原因）
PROACTIVE_ACTION_ERROR = "error"    # 异常（应记录但不应视为用户被打扰）

# ── reason_code 词汇（P0-2 可观测性）────────────────────────
PROACTIVE_REASON_CHAT_DELIVERED = "CHAT_DELIVERED"                    # 成功投递
PROACTIVE_REASON_PASS_BUSY = "PASS_BUSY"                              # 对话引擎忙
PROACTIVE_REASON_PASS_ACTIVITY_BUSY = "PASS_ACTIVITY_BUSY"            # 活动状态打扰成本高
PROACTIVE_REASON_PASS_DISABLED = "PASS_DISABLED"                      # 总开关关闭
PROACTIVE_REASON_PASS_COOLDOWN = "PASS_COOLDOWN"                      # 冷却中
PROACTIVE_REASON_PASS_DAILY_LIMIT = "PASS_DAILY_LIMIT"                # 每日上限
PROACTIVE_REASON_PASS_FULLSCREEN = "PASS_FULLSCREEN"                  # 全屏抑制
PROACTIVE_REASON_PASS_TYPING_SUPPRESS = "PASS_TYPING_SUPPRESS"        # 持续打字抑制
PROACTIVE_REASON_PASS_INTENT_LOW = "PASS_INTENT_LOW"                  # 意图置信度不足
PROACTIVE_REASON_PASS_THROTTLED = "PASS_THROTTLED"                    # 半衰期节流命中
PROACTIVE_REASON_PASS_SOURCE_EMPTY = "PASS_SOURCE_EMPTY"              # 候选为空
PROACTIVE_REASON_PASS_MODEL_PASS = "PASS_MODEL_PASS"                  # LLM 决策不开口
PROACTIVE_REASON_PASS_GENERATION_EMPTY = "PASS_GENERATION_EMPTY"      # 生成结果为空
PROACTIVE_REASON_PASS_DUPLICATE = "PASS_DUPLICATE"                    # 与近期文案重复
PROACTIVE_REASON_DELIVERY_PREEMPTED = "DELIVERY_PREEMPTED"            # 生成期间用户接管
PROACTIVE_REASON_ERROR_TIMEOUT = "ERROR_TIMEOUT"                      # 生成超时
PROACTIVE_REASON_ERROR_INTERNAL = "ERROR_INTERNAL"                    # 内部异常
PROACTIVE_REASON_PASS_UNSPECIFIED = "PASS_UNSPECIFIED"                # 未归类跳过

# ── stage 词汇 ──────────────────────────────────────────────
PROACTIVE_STAGE_ENTRY_GUARD = "entry_guard"        # 入口守卫（开关/冷却/每日/全屏）
PROACTIVE_STAGE_ACTIVITY_GATE = "activity_gate"    # 活动打扰成本门
PROACTIVE_STAGE_SOURCE_SELECTION = "source_selection"  # 候选选择
PROACTIVE_STAGE_MODEL_DECISION = "model_decision"      # LLM 是否开口
PROACTIVE_STAGE_GENERATION = "generation"              # 文案生成
PROACTIVE_STAGE_DEDUP = "dedup"                        # 去重
PROACTIVE_STAGE_DELIVERY = "delivery"                  # 投递
PROACTIVE_STAGE_RUNTIME_ERROR = "runtime_error"        # 运行时异常
PROACTIVE_STAGE_UNKNOWN = "unknown"                    # 未知

# reason_code → stage 归一表
_PROACTIVE_REASON_STAGE: dict[str, str] = {
    PROACTIVE_REASON_CHAT_DELIVERED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_BUSY: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_DISABLED: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_COOLDOWN: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_DAILY_LIMIT: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_FULLSCREEN: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_TYPING_SUPPRESS: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_INTENT_LOW: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_THROTTLED: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_MODEL_PASS: PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY: PROACTIVE_STAGE_GENERATION,
    PROACTIVE_REASON_PASS_DUPLICATE: PROACTIVE_STAGE_DEDUP,
    PROACTIVE_REASON_DELIVERY_PREEMPTED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_ERROR_TIMEOUT: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_INTERNAL: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_PASS_UNSPECIFIED: PROACTIVE_STAGE_UNKNOWN,
}


def proactive_stage_for_reason(reason_code: str | None) -> str:
    """把 reason_code 归一到 stage（未知/空 → unknown）。"""
    if not reason_code:
        return PROACTIVE_STAGE_UNKNOWN
    return _PROACTIVE_REASON_STAGE.get(reason_code, PROACTIVE_STAGE_UNKNOWN)


def proactive_reason_body(
    action: str | None,
    reason_code: str,
    *,
    success: bool,
    **extra: Any,
) -> dict[str, Any]:
    """构造统一的可观测结果体。

    Args:
        action: PROACTIVE_ACTION_* 之一（chat/pass/error），None 表示异常。
        reason_code: PROACTIVE_REASON_* 之一。
        success: 是否算作"成功投递"（pass 为 False，chat 为 True）。
        **extra: 附加字段（如 cooldown/similarity 等），随结果体透传。

    Returns:
        {"action", "reason_code", "stage", "success", **extra}
    """
    body: dict[str, Any] = {
        "action": action,
        "reason_code": reason_code,
        "stage": proactive_stage_for_reason(reason_code),
        "success": bool(success),
    }
    body.update(extra)
    return body


def proactive_chat_body(**extra: Any) -> dict[str, Any]:
    """成功搭话的结果体。"""
    return proactive_reason_body(
        PROACTIVE_ACTION_CHAT,
        PROACTIVE_REASON_CHAT_DELIVERED,
        success=True,
        **extra,
    )


def proactive_pass_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    """有意跳过（pass）的结果体。"""
    return proactive_reason_body(
        PROACTIVE_ACTION_PASS,
        reason_code,
        success=False,
        **extra,
    )


def proactive_error_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    """异常结果体。"""
    return proactive_reason_body(
        PROACTIVE_ACTION_ERROR,
        reason_code,
        success=False,
        **extra,
    )
