"""PresenceScheduler 单测 — 假时钟、纯逻辑、零 Qt 依赖。

运行：python -m pytest test_presence.py -q
"""
from __future__ import annotations

import sys
import os
import pytest

# 把项目根加入 sys.path，让 core.presence 能被直接 import
_ROOT = os.path.dirname(__file__)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.presence import PresenceScheduler, _ACTION_WHITELIST, _ACTION_TO_SEQ


@pytest.fixture
def fake_time():
    """共享的假时钟，默认从 t=0 开始，单位秒。

    返回 dict，避免 type() 把自由函数绑成 bound method。
    """
    _state = {"now": 0.0}

    def provider():
        return _state["now"]

    def advance(seconds: float):
        _state["now"] += seconds

    def set_to(t: float):
        _state["now"] = t

    return {"provider": provider, "advance": advance, "set": set_to}


@pytest.fixture
def scheduler(fake_time):
    return PresenceScheduler(time_provider=fake_time["provider"])


# ── 1. 刚 mark_interaction → tick 返回 None（空闲不足）──────────────────────

def test_mark_interaction_blocks_tick(fake_time, scheduler):
    scheduler.load_config({})
    scheduler.mark_interaction()
    # 还没到 min_idle（默认 5 分钟 = 300s）
    assert scheduler.tick() is None


# ── 2. 假时钟推进超过 min_idle → 第一个 tick 返回白名单动作 ────────────────

def test_first_trigger_after_min_idle(fake_time, scheduler):
    scheduler.load_config({})
    # 跳到 min_idle 之后
    fake_time["advance"](300 + 1)
    result = scheduler.tick()
    assert result is not None
    assert result in _ACTION_WHITELIST


# ── 3. 触发一次后推进 interval 再 tick → 再触发（周期性）───────────────────

def test_periodic_after_interval(fake_time, scheduler):
    scheduler.load_config({})
    # 进入可存在感（t=301，超过 min_idle=300）
    fake_time["advance"](301)
    first = scheduler.tick()
    assert first is not None

    # 还没到 interval（默认 8 分钟 = 480s），应再次 None
    fake_time["advance"](100)
    assert scheduler.tick() is None

    # 补满 interval：第一次触发在 t=301，_next_at=301+480=781
    # 当前 t=401，需再推 781-401=380 秒
    fake_time["advance"](380)
    second = scheduler.tick()
    assert second is not None
    assert second in _ACTION_WHITELIST


# ── 4. 动作名全部在白名单里 ────────────────────────────────────────────────

def test_all_tick_results_are_in_whitelist(fake_time, scheduler):
    scheduler.load_config({})
    # 连跑多次，确保每次都能触发且动作合法
    for _ in range(20):
        fake_time["advance"](scheduler._interval_seconds + 1)
        act = scheduler.tick()
        assert act is not None, "tick 应该返回动作而非 None"
        assert act in _ACTION_WHITELIST, f"{act!r} 不在白名单 {set(_ACTION_WHITELIST)}"


# ── 5. load_config 缺省值生效（不传 config 用默认）──────────────────────────

def test_defaults_when_no_config(fake_time, scheduler):
    scheduler.load_config({})
    assert scheduler.enabled is True
    assert scheduler._min_idle_seconds == 5 * 60
    assert scheduler._interval_seconds == 8 * 60

    # 传入空 dict 等价于不传
    scheduler2 = PresenceScheduler(time_provider=fake_time["provider"])
    scheduler2.load_config({})
    assert scheduler2._min_idle_seconds == 300
    assert scheduler2._interval_seconds == 480


# ── 6. mark_interaction 后重新计数（tick 暂时回 None）───────────────────────

def test_mark_interaction_resets_counter(fake_time, scheduler):
    scheduler.load_config({})
    # 第一次触发
    fake_time["advance"](301)
    assert scheduler.tick() is not None

    # 用户交互：重置计时
    scheduler.mark_interaction()
    # 立刻再 tick —— 因为刚重置，未到 min_idle，应该 None
    assert scheduler.tick() is None

    # 再推够 min_idle，又能触发
    fake_time["advance"](301)
    assert scheduler.tick() is not None


# ── 补充：enabled=False 时永远返回 None ─────────────────────────────────────

def test_disabled_scheduler_returns_none(fake_time, scheduler):
    scheduler.load_config({"enabled": False})
    fake_time["advance"](1000)
    assert scheduler.tick() is None


# ── 补充：自定义 config 值生效 ──────────────────────────────────────────────

def test_custom_config(fake_time, scheduler):
    scheduler.load_config({"min_idle_minutes": 1, "interval_minutes": 2})
    assert scheduler._min_idle_seconds == 60
    assert scheduler._interval_seconds == 120

    fake_time["advance"](61)
    assert scheduler.tick() is not None

    # interval 没到
    fake_time["advance"](50)
    assert scheduler.tick() is None

    # 补满
    fake_time["advance"](70)
    assert scheduler.tick() is not None


# ── 补充：回调收到的是真实帧序列名（经过 _ACTION_TO_SEQ 映射）───────────────

def test_callback_receives_real_seq(fake_time):
    received = []

    def on_presence(action_seq: str, bubble: str):
        received.append((action_seq, bubble))

    scheduler = PresenceScheduler(on_presence=on_presence, time_provider=fake_time["provider"])
    scheduler.load_config({})
    fake_time["advance"](301)
    scheduler.tick()

    assert len(received) == 1
    seq, bubble = received[0]
    # seq 必须是 _ACTION_TO_SEQ 里的值（即实际帧目录名）
    assert seq in set(_ACTION_TO_SEQ.values()), f"帧序列 {seq!r} 不在映射目标里"
    # bubble 是 str
    assert isinstance(bubble, str)
