"""T1 无头复现测试：验证「服务端列表失联 → ensure_session fallback 反复 create_session」

背景
----
oc-pet 对话走 Hanako WS。harness_adapter.chat_via_hanako 里：
    if self._current_session is None:
        pinned = self._pinned_session_id
        if pinned:
            self._current_session = sm.ensure_session(preferred_session_id=pinned)
        else:
            self._current_session = sm.create_session(...)

ensure_session 的隐患（hanako_session_manager.py）：
    if preferred_session_id:
        for summary in sessions:
            if summary.session_id == preferred_session_id:
                return summary.ref   # 命中就走这里
    if sessions:
        return sessions[0].ref
    return self.create_session(...)   # 列表查不到 → 静默新建

本测试不连真实服务端，用 mock 模拟 REST，验证：
  A) 当 list_sessions 返回空（服务端 session 列表失联/重置）时，
     连续 chat 会反复 create_session —— 复现「5 秒 6 个 Session」。
  B) 当 list_sessions 能查到 pinned session 时，不会新建 —— 对照。

运行: python -m pytest test_session_loop_repro.py -v
"""
from unittest.mock import MagicMock, patch

import pytest

from core.hanako_session_manager import HanakoSessionManager
from core.harness_adapter import HanakoPetAdapter


class FakeWSClient:
    """最小伪造 WS client：只提供 manager 构造需要的接口"""
    base_url = "http://127.0.0.1:9000"

    def subscribe(self, callback, event_types=None):
        return MagicMock()

    def subscribe_state(self, callback):
        return MagicMock()


def _make_manager(rest_handler):
    """构造 HanakoSessionManager，monkeypatch _request 走 rest_handler"""
    ws = FakeWSClient()
    mgr = HanakoSessionManager(ws, base_url="http://127.0.0.1:9000")
    mgr._request = rest_handler  # 替换 REST 层
    return mgr


def _make_adapter(mgr):
    """构造 adapter，注入 manager，模拟 Hanako 传输模式"""
    with patch.dict("os.environ", {"HANAKO_TRANSPORT_MODE": "prefer_hanako"}):
        adapter = HanakoPetAdapter(agent_id="yuexinmiao", builtin=True)
        adapter.set_session_manager(mgr)
        return adapter


def test_ensure_session_falls_back_to_create_when_list_empty():
    """服务端列表为空 → ensure_session fallback 到 create_session（根因复现）"""
    created = {"n": 0}

    def rest(method, path, **kw):
        if path == "/api/sessions":
            # 服务端 session 列表返回空 —— 模拟列表失联/重置
            return {"sessions": []}
        if path == "/api/sessions/new":
            created["n"] += 1
            return {
                "sessionId": f"sess_{created['n']}",
                "path": f"/sessions/sess_{created['n']}.jsonl",
                "agentId": "yuexinmiao",
            }
        raise AssertionError(f"unexpected {method} {path}")

    mgr = _make_manager(rest)
    adapter = _make_adapter(mgr)

    # 模拟连续 6 次用户消息（每次重置 _current_session，模拟"每次新 turn"）
    for i in range(6):
        adapter._current_session = None
        adapter._pinned_session_id = f"sess_{i}"  # pin 了，但列表查不到
        session = mgr.ensure_session(
            agent_id="yuexinmiao",
            preferred_session_id=adapter._pinned_session_id,
        )
        assert session is not None

    # 关键断言：列表查不到 pinned → 6 次请求全部 fallback 到 create
    assert created["n"] == 6, f"期望 6 次 create，实际 {created['n']}"


def test_ensure_session_reuses_when_pinned_found():
    """服务端列表能查到 pinned session → 不新建（对照）"""
    created = {"n": 0}

    def rest(method, path, **kw):
        if path == "/api/sessions":
            # 服务端能查到 pinned 的 session
            return {
                "sessions": [{
                    "sessionId": "sess_keep",
                    "path": "/sessions/sess_keep.jsonl",
                    "agentId": "yuexinmiao",
                    "modified": "2026-08-07T00:00:00",
                }]
            }
        if path == "/api/sessions/new":
            created["n"] += 1
            return {
                "sessionId": f"new_{created['n']}",
                "path": f"/sessions/new_{created['n']}.jsonl",
                "agentId": "yuexinmiao",
            }
        raise AssertionError(f"unexpected {method} {path}")

    mgr = _make_manager(rest)
    adapter = _make_adapter(mgr)

    for i in range(6):
        adapter._current_session = None
        adapter._pinned_session_id = "sess_keep"  # 列表能查到
        session = mgr.ensure_session(
            agent_id="yuexinmiao",
            preferred_session_id=adapter._pinned_session_id,
        )
        assert session.session_id == "sess_keep"

    # 对照：列表能查到 → 0 次新建
    assert created["n"] == 0, f"期望 0 次 create，实际 {created['n']}"


def test_chat_via_hanako_creates_when_current_session_none():
    """verification: chat_via_hanako 在 _current_session is None 时走新建路径"""
    created = {"n": 0}

    def rest(method, path, **kw):
        if path == "/api/sessions":
            return {"sessions": []}  # 失联
        if path == "/api/sessions/new":
            created["n"] += 1
            return {
                "sessionId": f"sess_{created['n']}",
                "path": f"/sessions/sess_{created['n']}.jsonl",
                "agentId": "yuexinmiao",
            }
        raise AssertionError(f"unexpected {method} {path}")

    mgr = _make_manager(rest)
    adapter = _make_adapter(mgr)

    # 直接调用底层新建入口（模拟 chat_via_hanako 的 session 准备逻辑）
    adapter._current_session = None
    adapter._pinned_session_id = None
    session = mgr.create_session(agent_id="yuexinmiao")
    assert session is not None
    assert created["n"] == 1