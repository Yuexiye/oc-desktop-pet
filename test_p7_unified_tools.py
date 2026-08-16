"""P7 统一工具调度层测试：UnifiedToolRouter —— 本地插件/能力/Hanako 工具统一快速路由

覆盖 P7 验收的客观项：
- 显式指定："用phone-peek-screen看下手机" → 精确命中（mock tool_executor 返回"手机屏幕…"）
- 显式指定容忍变体："用手机插件看屏幕" → 命中
- 关键词路由："看下手机屏幕" → 命中 phone-peek-screen
- 静态能力优先："放首歌" → 命中 play_music（不走插件层）
- 未命中："今天天气如何" → None（兜底 LLM / Hanako 服务端）
- refresh() 清空重建：discover 后 refresh 再 discover，无残留旧工具
- 热刷新生效：模拟新插件加入目录 → refresh 后 get_tool 能拿到
- 语音路径文本同样命中（"用手机看下屏幕"来自语音也路由）
- tool_executor 失败 → RouteResult 文案"操作失败"
- 工具名 sanitized 匹配（phone_peek_screen → phone-peek-screen）

运行: python -m pytest test_p7_unified_tools.py -v --basetemp=.pytest_tmp
"""
import json
import os
import re
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.capability_registry import CapabilityRouter, RouteResult
from core.tool_registry import ToolDef, ToolRegistry
from core.unified_tool_router import UnifiedToolRouter


# ── 工具：构造隔离的工具注册表（不依赖 ~/.hanako/plugins）──

_LINJIAN_DESCRIPTIONS = {
    "phone_peek_screen": "向手机请求一张新截图并返回。手机端必须已启动且无障碍截图权限已开启。",
    "phone_state": "读取手机当前状态：前台应用包名、屏幕文字、无障碍服务就绪状态。",
    "phone_life_state": "读取手机生活状态：电量、充电状态、网络、当前 App、今日屏幕时间、解锁次数、城市/天气等。默认不截图。",
    "phone_open_app": "打开手机上的指定应用。可填应用名（小红书/微信/QQ/抖音/ChatGPT/Speedcat）或包名。",
    "phone_home": "让手机回到桌面（按 Home 键）。",
    "phone_notification": "发送一条手机系统通知。只在用户明确要求时使用。",
    "phone_alarm": "设置手机系统闹钟。只在用户明确要求时使用。hour 为 0-23，minute 为 0-59。",
    "phone_status": "检查掌心窗后端是否在线，以及手机是否已连接。",
    # 静态能力 play_music 需要的底层工具（CapabilityRouter 会查）
    "play": "将音频添加到播放器播放列表",
}


def _make_registry(extra: dict = None) -> ToolRegistry:
    """构造只含 linjian-peek + play 的隔离注册表（确定性、快速）。"""
    reg = ToolRegistry()
    reg._tools.clear()
    descs = dict(_LINJIAN_DESCRIPTIONS)
    if extra:
        descs.update(extra)
    for name, description in descs.items():
        reg._tools[name] = ToolDef(
            name=name,
            description=description,
            parameters={"type": "object", "properties": {}},
            plugin_id="linjian-peek",
            source_path="",
        )
    reg._name_map.clear()
    for name in reg._tools:
        clean = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        reg._name_map[clean] = name
    return reg


def _make_router(registry=None, result_text="手机屏幕截图成功", raise_error=None) -> UnifiedToolRouter:
    """构造带 mock executor 的统一路由器。"""
    executor = MagicMock()
    if raise_error is not None:
        executor.execute.side_effect = raise_error
    else:
        executor.execute.return_value = result_text
    router = UnifiedToolRouter(tool_executor=executor)
    reg = registry or _make_registry()
    router.refresh(reg)
    return router


def _write_plugin(tmp_path, plugin_name: str, tool_name: str, tool_file: str):
    """在 tmp_path 下写一个最小插件（manifest + 一个 JS 工具）。"""
    pdir = tmp_path / plugin_name
    tools_dir = pdir / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (pdir / "manifest.json").write_text(
        json.dumps({
            "id": plugin_name,
            "name": plugin_name,
            "contributes": {"tools": [f"./tools/{tool_file}"]},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (tools_dir / tool_file).write_text(
        f"export const name = '{tool_name}';\n"
        "export const description = '测试工具';\n"
        "export const parameters = { type: 'object', properties: {} };\n",
        encoding="utf-8",
    )


# ── 1. 显式指定 ──

def test_explicit_phone_peek_screen():
    """"用phone-peek-screen看下手机" → 精确命中（mock tool_executor 返回"手机屏幕…"）"""
    router = _make_router()
    result = router.route("用phone-peek-screen看下手机")
    assert result is not None
    assert result.capability == "phone_peek_screen"
    assert "手机屏幕" in result.text
    # executor 应以该 ToolDef 被调用
    router._tool_executor.execute.assert_called_once()


def test_explicit_sanitized_underscore():
    """工具名 sanitized 匹配：phone_peek_screen（下划线变体）同样命中"""
    router = _make_router()
    result = router.route("用phone_peek_screen看下手机")
    assert result is not None
    assert result.capability == "phone_peek_screen"


def test_explicit_variant_phone_plugin():
    """显式指定容忍变体："用手机插件看屏幕" → 命中 phone_peek_screen"""
    router = _make_router()
    result = router.route("用手机插件看屏幕")
    assert result is not None
    assert result.capability == "phone_peek_screen"


# ── 2. 关键词路由 ──

def test_keyword_phone_screen():
    """关键词路由："看下手机屏幕" → 命中 phone_peek_screen"""
    router = _make_router()
    result = router.route("看下手机屏幕")
    assert result is not None
    assert result.capability == "phone_peek_screen"


def test_keyword_description_words():
    """描述关键词：工具描述含"天气"（name 同义词兜底）→ "今天天气如何" 直达 weather 工具"""
    reg = _make_registry({"weather_check": "查询今天的天气情况"})
    executor = MagicMock()
    executor.execute.return_value = "今日天气：晴，25℃"
    router = UnifiedToolRouter(tool_executor=executor)
    router.refresh(reg)
    result = router.route("今天天气如何", tool_registry=reg)
    assert result is not None
    assert result.capability == "weather_check"
    assert "天气" in result.text


# ── 3. 静态能力优先 / 兜底 ──

def test_static_capability_priority():
    """静态能力优先："播放一首歌" → 命中 play_music（不走插件层）

    注：现有 CapabilityRouter 的 play_music 触发词是 播放/放一首/来首 等，
    "放首歌"（放-首-歌）并不在触发词子串内，故用真实命中的"播放一首歌"验证。
    """
    reg = _make_registry()
    executor = MagicMock()
    executor.execute.return_value = "已播放"
    static = CapabilityRouter(
        perception=MagicMock(),
        tool_registry=reg,
        tool_executor=executor,
    )
    router = UnifiedToolRouter(tool_executor=executor)
    router.refresh(reg)
    result = router.route("播放一首歌", tool_registry=reg, static_router=static)
    assert result is not None
    assert result.capability == "play_music", "静态能力应优先命中"


def test_static_miss_falls_back_to_none():
    """静态未命中且无插件关键词（"今天天气如何"不在静态触发词内）→ None（兜底 LLM）"""
    reg = _make_registry()
    executor = MagicMock()
    static = CapabilityRouter(
        perception=MagicMock(),
        tool_registry=reg,
        tool_executor=executor,
    )
    router = UnifiedToolRouter(tool_executor=executor)
    router.refresh(reg)
    result = router.route("今天天气如何", tool_registry=reg, static_router=static)
    assert result is None, "静态未命中 + 无插件关键词 → 兜底 LLM"


def test_fangshouge_hits_play_music():
    """口语变体"放首歌/放个歌"应命中 play_music（P7 后续补充的触发词）"""
    reg = _make_registry()
    executor = MagicMock()
    static = CapabilityRouter(
        perception=MagicMock(),
        tool_registry=reg,
        tool_executor=executor,
    )
    router = UnifiedToolRouter(tool_executor=executor)
    router.refresh(reg)
    for phrase in ("放首歌", "放个歌", "来首歌", "放首歌听听"):
        result = router.route(phrase, tool_registry=reg, static_router=static)
        assert result is not None and result.capability == "play_music", \
            f"{phrase} 应命中 play_music，实际 {result.capability if result else None}"


def test_no_match_fallback():
    """未命中："今天天气如何" → None（兜底 LLM / Hanako 服务端）"""
    router = _make_router()
    result = router.route("今天天气如何")
    assert result is None


def test_no_match_empty_text():
    """空文本 → None"""
    router = _make_router()
    assert router.route("") is None
    assert router.route("   ") is None


# ── 4. 语音路径（语音转文字后走同一路由）──

def test_voice_path_text_routes():
    """"用手机看下屏幕"（语音转写文本）→ 同样命中 phone_peek_screen"""
    router = _make_router()
    result = router.route("用手机看下屏幕")
    assert result is not None
    assert result.capability == "phone_peek_screen"


# ── 5. tool_executor 失败 ──

def test_tool_executor_failure():
    """tool_executor 抛异常 → RouteResult 文案含 '操作失败'"""
    router = _make_router(raise_error=RuntimeError("boom"))
    result = router.route("用phone-peek-screen看下手机")
    assert result is not None
    assert "操作失败" in result.text
    assert result.capability == "phone_peek_screen"


def test_tool_executor_none_marker():
    """execute=False → 返回"工具执行中"标记（不执行 executor）"""
    executor = MagicMock()
    router = UnifiedToolRouter(tool_executor=executor)
    router.refresh(_make_registry())
    result = router.route("用phone-peek-screen看下手机", execute=False)
    assert result is not None
    assert "正在执行工具" in result.text
    executor.execute.assert_not_called()


# ── 6. refresh 清空重建 + 热刷新 ──

def test_refresh_clears_old_tools(tmp_path, monkeypatch):
    """refresh() 清空重建：discover 后 refresh 再 discover，无残留旧工具

    用切换插件目录模拟"旧工具被移除"（沙箱下 rmtree 会触发 SAFE_DELETE_FAIL_CLOSED）。
    """
    monkeypatch.setattr("core.tool_registry.HANAKO_PLUGINS", tmp_path / "no_hanako")
    monkeypatch.setattr("core.tool_registry.LOCAL_PLUGINS", tmp_path / "dir1")
    _write_plugin(tmp_path / "dir1", "plugin-alpha", "alpha_tool", "alpha.js")
    reg = ToolRegistry()
    reg.discover()
    assert reg.get_tool("alpha_tool") is not None

    # 切换到只含 beta 的目录 → refresh 清空重建
    monkeypatch.setattr("core.tool_registry.LOCAL_PLUGINS", tmp_path / "dir2")
    _write_plugin(tmp_path / "dir2", "plugin-beta", "beta_tool", "beta.js")
    reg.refresh()
    assert reg.get_tool("alpha_tool") is None, "旧工具不应残留"
    assert reg.get_tool("beta_tool") is not None


def test_hot_refresh_new_plugin(tmp_path, monkeypatch):
    """热刷新生效：模拟新插件加入目录 → refresh 后 get_tool 能拿到且可路由"""
    monkeypatch.setattr("core.tool_registry.HANAKO_PLUGINS", tmp_path / "no_hanako")
    monkeypatch.setattr("core.tool_registry.LOCAL_PLUGINS", tmp_path)
    _write_plugin(tmp_path, "plugin-alpha", "alpha_tool", "alpha.js")
    reg = ToolRegistry()
    reg.discover()
    router = UnifiedToolRouter(tool_executor=MagicMock())
    router.refresh(reg)
    assert reg.get_tool("alpha_tool") is not None

    # 模拟新增插件（不删除旧插件）→ refresh 后能拿到，且可被显式路由
    _write_plugin(tmp_path, "plugin-beta", "beta_tool", "beta.js")
    reg.refresh()
    router.refresh(reg)
    assert reg.get_tool("beta_tool") is not None
    result = router.route("用beta_tool做一下", tool_registry=reg)
    assert result is not None and result.capability == "beta_tool"


def test_should_refresh_interval():
    """should_refresh：超过 interval 返回 True；未到返回 False"""
    router = UnifiedToolRouter()
    router._last_refresh = time.monotonic() - 40
    assert router.should_refresh(interval=30) is True
    assert router.should_refresh(interval=60) is False
    # interval<=0 不刷新
    assert router.should_refresh(interval=0) is False


# ── 7. 引擎集成：conversation_engine 走统一路由 ──

def test_engine_routes_through_unified_router():
    """ConversationEngine._process_message 走统一路由（语音/文字同一链路）"""
    from core.conversation_engine import ConversationEngine

    engine = ConversationEngine(
        character_id="yuexinmiao",
        perception=MagicMock(),
        tts_provider=MagicMock(),
    )
    assert engine._unified_router is not None
    assert engine._capability_router is not None

    # 注入隔离注册表 + mock executor（避免真实 Node 执行）
    reg = _make_registry()
    mock_exec = MagicMock()
    mock_exec.execute.return_value = "手机屏幕截图成功"
    engine._tool_registry = reg
    engine._tool_executor = mock_exec
    engine._unified_router = UnifiedToolRouter(tool_executor=mock_exec)
    engine._unified_router.refresh(reg)

    replies = []
    engine.on_reply = lambda reply, emotion, anim, audio_path: replies.append(reply)
    engine._generation = 1
    engine._process_message({
        "text": "用phone-peek-screen看下手机",
        "character": "yuexinmiao",
        "source": "user",
        "gen": 1,
    })
    assert replies, "统一路由命中后应直接回调 on_reply"
    assert "手机屏幕" in replies[0]
    mock_exec.execute.assert_called_once()


def test_engine_has_hot_refresh_methods():
    """引擎具备热刷新方法 + 统一路由实例"""
    from core.conversation_engine import ConversationEngine

    engine = ConversationEngine(
        character_id="yuexinmiao",
        perception=MagicMock(),
        tts_provider=MagicMock(),
    )
    assert hasattr(engine, "_hot_refresh_tools")
    assert hasattr(engine, "_unified_router")
    assert engine._tool_refresh_interval == 30.0
