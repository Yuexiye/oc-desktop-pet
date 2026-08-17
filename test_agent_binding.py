"""F2/F3/F4/F5 测试：agent 绑定、切换、会话保留、零硬编码

覆盖：
  - adapter.switch_agent: 切换对话后端 agent，恢复该 agent 的 session
  - chat_via_hanako: per-agent session 复用（切回续聊不新建）
  - ConversationEngine.switch_agent: 引擎层切换
  - 零硬编码检查：新增配置/逻辑不硬编码 agent 名单

运行: python -m pytest test_agent_binding.py -v
"""
from unittest.mock import MagicMock, patch

import pytest
from pytest import MonkeyPatch

from core.harness_adapter import HanakoPetAdapter
from core.conversation_engine import ConversationEngine


class FakeSessionRef:
    def __init__(self, sid, path):
        self.session_id = sid
        self.session_path = path


class FakeSM:
    """伪造 SessionManager：记录 create/ensure 调用，返回可预测 session"""
    def __init__(self):
        self.created = []
        self.ensured = []
        self._counter = 0

    def create_session(self, agent_id=None, **kw):
        self._counter += 1
        sid = f"sess_{agent_id}_{self._counter}"
        s = FakeSessionRef(sid, f"/sessions/{sid}.jsonl")
        self.created.append((agent_id, sid))
        return s

    def ensure_session(self, agent_id=None, preferred_session_id=None, **kw):
        self.ensured.append((agent_id, preferred_session_id))
        # 模拟服务端：只有 preferred 存在且 agent 匹配才返回它，否则新建
        if preferred_session_id and preferred_session_id.startswith(f"sess_{agent_id}_"):
            return FakeSessionRef(preferred_session_id, f"/sessions/{preferred_session_id}.jsonl")
        return self.create_session(agent_id=agent_id)

    def send_and_wait(self, session, text, **kw):
        # 返回固定回复；测试通过属性可控
        return MagicMock(text=getattr(self, "reply_text", "ok"), error=None, aborted=False)


def _make_adapter(agent_id="ophelia"):
    # 用 MonkeyPatch.context() 逐项设置/恢复环境变量，避免 patch.dict 整体
    # 重建 os.environ（Windows 环境块 32767 上限，同进程串扰会触发超长错误）。
    # HANAKO_TRANSPORT_MODE 默认值即 prefer_hanako，此处只为确定性。
    with MonkeyPatch.context() as m:
        m.setenv("HANAKO_TRANSPORT_MODE", "prefer_hanako")
        adapter = HanakoPetAdapter(agent_id=agent_id, builtin=False)
    return adapter


# ── F2: switch_agent 切换对话后端 ──

def test_switch_agent_updates_agent_id():
    adapter = _make_adapter("ophelia")
    assert adapter.switch_agent("aimis") is True
    assert adapter.agent_id == "aimis"


def test_switch_agent_same_noop():
    adapter = _make_adapter("ophelia")
    assert adapter.switch_agent("ophelia") is True
    assert adapter.agent_id == "ophelia"


def test_switch_agent_rejects_empty():
    adapter = _make_adapter("ophelia")
    assert adapter.switch_agent("") is False
    assert adapter.switch_agent("   ") is False
    assert adapter.agent_id == "ophelia"


def test_switch_agent_clears_history():
    adapter = _make_adapter("ophelia")
    adapter._history.append({"role": "user", "content": "hi"})
    adapter.switch_agent("rebecca")
    # _history 改为有界 deque（B2-7 并发安全），用长度断言
    assert len(adapter._history) == 0


# ── F3: per-agent session 保留（切回续聊不新建）──

def test_chat_via_hanako_reuses_session_per_agent():
    adapter = _make_adapter("ophelia")
    sm = FakeSM()
    adapter.set_session_manager(sm)

    # 首次：ophelia 建 session
    adapter._current_session = None
    with patch.object(adapter, "parse_emotion", return_value=("你好", "happy")):
        adapter.chat_via_hanako("你好")
    assert len(sm.created) == 1
    first_session = adapter._agent_sessions["ophelia"]
    assert first_session is not None

    # 切到 aimis，再切回 ophelia：应复用，不新建
    adapter.switch_agent("aimis")
    adapter._current_session = None
    with patch.object(adapter, "parse_emotion", return_value=("在", "neutral")):
        adapter.chat_via_hanako("在吗")
    assert len(sm.created) == 2  # aimis 新建一个

    # 切回 ophelia
    adapter.switch_agent("ophelia")
    adapter._current_session = None
    with patch.object(adapter, "parse_emotion", return_value=("回来了", "happy")):
        adapter.chat_via_hanako("我回来了")
    # 关键：ophelia 的 session 被复用，没有第 3 次 create
    assert len(sm.created) == 2, f"期望 ophelia 复用，实际新建 {len(sm.created)} 次"


def test_per_agent_sessions_independent():
    """不同 agent 持有各自 session，互不覆盖"""
    adapter = _make_adapter("ophelia")
    sm = FakeSM()
    adapter.set_session_manager(sm)

    adapter._current_session = None
    with patch.object(adapter, "parse_emotion", return_value=("a", "neutral")):
        adapter.chat_via_hanako("a")
    ophelia_sid = adapter._agent_pinned["ophelia"]

    adapter.switch_agent("glados")
    adapter._current_session = None
    with patch.object(adapter, "parse_emotion", return_value=("b", "neutral")):
        adapter.chat_via_hanako("b")
    glados_sid = adapter._agent_pinned["glados"]

    assert ophelia_sid != glados_sid
    assert adapter._agent_pinned["ophelia"] == ophelia_sid  # 未被覆盖


# ── F2: 引擎层 switch_agent ──

def test_engine_switch_agent_updates_agent_id():
    with patch("core.conversation_engine.HanakoPetAdapter"):
        engine = ConversationEngine(character_id="yuexinmiao", agent_id="ophelia")
        assert engine.agent_id == "ophelia"
        assert engine.switch_agent("aimis") is True
        assert engine.agent_id == "aimis"


def test_engine_agent_id_falls_back_to_character():
    """未显式传 agent_id 时回退到 character（向后兼容）"""
    with patch("core.conversation_engine.HanakoPetAdapter"):
        engine = ConversationEngine(character_id="yuexinmiao")
        assert engine.agent_id == "yuexinmiao"


# ── 零硬编码检查 ──

def test_no_hardcoded_agent_names_in_new_code():
    """新增的 agent 绑定逻辑不应硬编码 agent 名单。

    检查 conversation_engine / harness_adapter 里是否出现
    具体 agent 名（ophelia/aimis/alice/glados/luoqixi/rebecca）作为字面量。
    """
    import re
    from pathlib import Path
    hardcoded = {"ophelia", "aimis", "alice", "glados", "luoqixi", "rebecca"}
    files = [
        Path("core/conversation_engine.py"),
        Path("core/harness_adapter.py"),
        Path("config.py"),
    ]
    offenders = []
    for f in files:
        if not f.exists():
            continue
        text = f.read_text("utf-8")
        # 忽略 import/注释里的提及，查字符串字面量赋值
        for m in re.finditer(r"['\"]([a-z_]+)['\"]", text):
            if m.group(1) in hardcoded:
                offenders.append((str(f), m.group(1)))
    # 允许：HanakoContext 默认参数 yuexinmiao（既有），以及注释
    assert not offenders, f"发现硬编码 agent 名: {offenders}"