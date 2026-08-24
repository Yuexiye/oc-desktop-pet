# -*- coding: utf-8 -*-
"""BugFix #1 单元测试：HanakoSessionManager 静默 turn 快速恢复。

背景：用户消息经 `chat_via_hanako → send_and_wait` 后，Hanako WS 回复事件没有
匹配到桌宠 turn（会话身份不匹配 / 事件丢失），`send_and_wait` 死等满
reply_timeout(180s)，引擎线程冻结 → 无回复气泡、无 TTS、UI 停在"思考中..."。

修复：`send_and_wait` 增加静默 turn 快速恢复——send 后 `silent_turn_grace`
（默认 20s）内一条 WS 事件都没收到（last_event_ts 未动），主动从会话历史
恢复最终回复（云端其实已生成，Hanako 主窗口可见）；历史暂无回复则不打断
turn，继续等 WS 事件或走正常超时（绝不提前判死仍在处理的 turn）。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hanako_session_manager import (
    HanakoSessionManager,
    HistoryPage,
    SessionRef,
    TurnAccumulator,
)


class _Sub:
    def __init__(self, fn, event_types=None):
        self.fn = fn
        self.event_types = event_types

    def close(self):
        pass


class _FakeWS:
    """最小 WS 客户端：永远就绪、可发 prompt、不推送任何事件（静默 turn）。"""

    def __init__(self):
        self.sent_prompts: list[dict] = []
        self.ready = True

    @property
    def is_ready(self):
        return self.ready

    def start(self):
        pass

    def wait_until_ready(self, timeout):
        return self.ready

    def subscribe(self, callback, event_types=None, session_id=None, session_path=None):
        return _Sub(callback, event_types)

    def subscribe_state(self, callback):
        return _Sub(callback, None)

    def send_prompt(self, **kwargs):
        self.sent_prompts.append(kwargs)

    def resume_stream(self, cursor):
        pass

    def abort_stream(self, cursor, reason="user_abort"):
        pass


def _make_sm(grace: float = 0.05, reply_timeout: float = 180.0):
    sm = HanakoSessionManager(
        ws_client=_FakeWS(),
        base_url="https://hanako.invalid",
        token="",
        reply_timeout=reply_timeout,
        activity_timeout=60.0,
        silent_turn_grace=grace,
    )
    return sm


def _history_with_reply(session, limit=30):
    return HistoryPage(
        session=session,
        messages=(
            {"role": "user", "displayText": "你好", "content": "你好"},
            {"role": "assistant", "content": "晚上好呀，月曦夜。", "thinking": ""},
        ),
        content_blocks=(),
        has_more=False,
    )


def _history_without_reply(session, limit=30):
    return HistoryPage(
        session=session,
        messages=(
            {"role": "user", "displayText": "你好", "content": "你好"},
        ),
        content_blocks=(),
        has_more=False,
    )


# ── 1. 静默 turn：历史有最终回复 → 快速恢复，不等满 reply_timeout ─────────


def test_silent_turn_recovers_reply_from_history_before_reply_timeout():
    """WS 事件一条没到，但云端历史已有回复 → send_and_wait 提前返回回复。"""
    sm = _make_sm(grace=0.05, reply_timeout=180.0)
    sm.get_history = _history_with_reply

    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    t0 = time.monotonic()
    result = sm.send_and_wait(session, "你好", display_text="你好", timeout=180.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, "静默恢复应远早于 reply_timeout(180s) 返回"
    assert result.error is None
    assert "晚上好呀" in result.text
    # prompt 确实已发出（消息进了 Hanako，只是事件没回来）
    assert len(sm.ws_client.sent_prompts) == 1
    assert sm.ws_client.sent_prompts[0]["text"] == "你好"


def test_silent_recovery_preserves_recovered_text():
    """历史恢复应带回完整回复文本（含情绪标签解析留给上层）。"""
    sm = _make_sm(grace=0.05)
    sm.get_history = _history_with_reply
    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    result = sm.send_and_wait(session, "你好", display_text="你好", timeout=180.0)
    assert result.error is None
    assert result.text == "晚上好呀，月曦夜。"


# ── 2. 静默 turn：历史还没有回复 → 不提前判死，继续等超时墙 ──────────────


def test_silent_turn_not_killed_when_history_has_no_reply_yet():
    """服务端仍在处理（历史暂无回复）→ 静默恢复不得提前判死 turn。"""
    sm = _make_sm(grace=0.05, reply_timeout=180.0)
    sm.get_history = _history_without_reply

    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    t0 = time.monotonic()
    # 调用方 timeout=0.3：deadline 墙下限 1.0s → 应在 ~1s 走"历史无可恢复→判死"，
    # 而不是 0.05s 静默宽限一到就把 turn 杀掉。
    result = sm.send_and_wait(session, "你好", display_text="你好", timeout=0.3)
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.5, "历史无回复时不应在静默宽限(0.05s)就提前判死 turn"
    assert result.error is not None
    assert "Stream ended before reply history was available" in result.error


# ── 3. 正常 WS 事件流：收到事件后不触发静默恢复 ───────────────────────────


def test_normal_events_prevent_silent_recovery():
    """turn 正常收到 WS 事件（last_event_ts 被刷新）→ 静默恢复不触发，正常完成。"""
    sm = _make_sm(grace=5.0, reply_timeout=180.0)  # 宽限设大，正常事件先到
    sm.get_history = _history_without_reply  # 即使历史没回复也不该被静默恢复

    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    # 先 send 拿到 turn，再模拟服务端正常推送 turn_end（绕过静默恢复路径）
    future = sm.send_message(session, "你好", display_text="你好")
    turn = sm._pending_by_session[session.session_id]
    # 模拟事件流：thinking_start(带 seq+streamId) → text_delta → turn_end
    for event in (
        {"type": "thinking_start", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "s1", "seq": 1},
        {"type": "text_delta", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "s1", "seq": 2, "delta": "晚上好呀"},
        {"type": "turn_end", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "s1", "seq": 3},
    ):
        sm._handle_event(event)

    result = future.result(timeout=5.0)
    assert result.error is None
    assert "晚上好呀" in result.text
    assert not getattr(turn, "_silent_recovery_tried", False), "正常事件流不应触发静默恢复"


# ── 4. BugFix #2：显式 received_any_event（不依赖 monotonic 严格递增）──────


def test_accept_event_sets_received_any_event_flag():
    """accept_event 命中本 turn 事件 → received_any_event 置位（显式布尔）。"""
    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    turn = TurnAccumulator(session=session, client_message_id="ocpet_x", origin="oc_pet")
    assert turn.received_any_event is False
    # 模拟 monotonic 同 tick：时间戳没有严格增大（旧判断 last>created 会误判静默）
    turn.last_event_ts = turn.created_at
    assert turn.accept_event({"seq": 1, "streamId": "s1"}) is True
    assert turn.received_any_event is True, "收到事件必须置位显式布尔，不依赖时间戳"


def test_same_tick_event_not_treated_as_silent():
    """同 tick 收到事件（last_event_ts==created_at）→ 按流式文本完成，
    绝不被历史旧回复提前完成/截断（Fix 2 回归点）。"""
    sm = _make_sm(grace=0.05)
    sm.get_history = _history_with_reply  # 若误判静默会用旧回复提前完成（回归点）
    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")

    future = sm.send_message(session, "你好", display_text="你好")
    turn = sm._pending_by_session[session.session_id]
    # 模拟同 tick：收到事件后 last_event_ts 与 created_at 相等
    turn.received_any_event = True
    turn.last_event_ts = turn.created_at
    # 正常流式继续（text_delta → turn_end）
    for event in (
        {"type": "text_delta", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "s1", "seq": 1, "delta": "实时回复"},
        {"type": "turn_end", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "s1", "seq": 2},
    ):
        sm._handle_event(event)

    result = future.result(timeout=5.0)
    assert result.error is None
    assert "实时回复" in result.text
    assert "晚上好呀" not in result.text, "同 tick 收到事件的 turn 不得被历史旧回复截断"


# ── 5. BugFix #1：静默恢复期间 get_history 异常不判死 turn ─────────────────


def test_silent_recovery_ignores_get_history_exception():
    """静默恢复路径 get_history 瞬时异常（REST 抖动）→ 不提前判死/不 abort，
    turn 保持存活，继续等至 deadline 墙。"""
    sm = _make_sm(grace=0.05, reply_timeout=180.0)

    def boom(session, limit=30):
        raise RuntimeError("REST 抖动")

    sm.get_history = boom
    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")
    t0 = time.monotonic()
    result = sm.send_and_wait(session, "你好", display_text="你好", timeout=0.3)
    elapsed = time.monotonic() - t0

    # 不应在静默宽限(0.05s)处因 get_history 异常提前判死；应走 deadline 墙(≥1s)
    assert elapsed >= 0.5, "get_history 瞬时异常不得在静默宽限就判死 turn"
    assert result.error is not None
    assert "Stream recovery failed" in result.error


# ── 6. BugFix #3：同 session 其他消息事件不污染本 turn ─────────────────────


def test_foreign_message_events_rejected_for_pending_turn():
    """用户在 Hanako 主窗口同时发消息（同 session、不同 clientMessageId）→
    其事件不得绑定 stream / 刷新 last_seq / 积累文本污染本 turn。"""
    sm = _make_sm(grace=0.05)
    sm.get_history = _history_without_reply
    session = SessionRef("sess-1", "/agents/aimis/sessions/sess-1", "aimis")

    future = sm.send_message(session, "你好", display_text="你好")
    turn = sm._pending_by_session[session.session_id]

    # 外国消息事件：clientMessageId 不匹配 → 必须被拒绝
    sm._handle_event({
        "type": "text_delta", "sessionId": session.session_id,
        "sessionPath": session.session_path, "streamId": "foreign-stream",
        "seq": 1, "delta": "这是别人的回复", "clientMessageId": "other_client",
    })
    assert turn.stream_id is None, "外国事件不得绑定 stream"
    assert turn.text_parts == [], "外国事件不得积累文本"
    assert not turn.received_any_event, "外国事件不得标记本 turn 为活跃"
    assert turn.last_seq == 0, "外国事件不得推进 last_seq"

    # 本 turn 自己的事件仍正常完成
    for event in (
        {"type": "text_delta", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "own-stream",
         "seq": 1, "delta": "本 turn 回复"},
        {"type": "turn_end", "sessionId": session.session_id,
         "sessionPath": session.session_path, "streamId": "own-stream", "seq": 2},
    ):
        sm._handle_event(event)

    result = future.result(timeout=5.0)
    assert result.error is None
    assert "本 turn 回复" in result.text
    assert "别人的回复" not in result.text
