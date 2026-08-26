# -*- coding: utf-8 -*-
"""回归测试：静态能力路由的中文子串碰撞与歧义短词降级。

验证对象：capability_registry.CapabilityRouter.route
  - 子串碰撞：纯子串 `pattern in text` 对中文产生碰撞——
    "接管一下一个对话" 中的 "一下一个" ⊃ "下一个" 曾被 next_track 劫持，
    自然语言"接管对话"请求被错当成切歌插件调用（实证日志 next_track 误命中）。
    修复：关键词被中文字符前后夹住时视为嵌在大词里的误匹配，放过给 LLM。
  - 歧义短词降级（治本）：静态路由只保留"明确组合词"，移除本身也是常用中文
    词片段的裸双字词（暂停 / 继续 / 截图 / 下一个），让自然语言走 LLM，
    避免"暂停一下手头活 / 截图工具在哪 / 继续聊刚才的话题"被错当音乐指令。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.capability_registry import CapabilityRouter, _is_valid_capability_match


def _router():
    # route() 仅做匹配；无 tool_registry/executor 时命中返回"工具系统未就绪"
    return CapabilityRouter()


# ---- 子串碰撞防护（中间被中文夹住）----

def test_collision_takeover_dialog_not_hijacked():
    """"接管一下一个对话(音频播放器那个" 不应被 next_track 劫持 → 回退 LLM(None)。"""
    out = _router().route("请接管一下一个对话(音频播放器那个")
    assert out is None


def test_sandwiched_false_positive_rejected_general():
    """通用性：被中文字符夹住的关键词视为误匹配（"看一下一个视频"不命中 next_track）。"""
    assert _is_valid_capability_match("看一下一个视频", "下一个") is False


# ---- 歧义裸双字词降级为走 LLM（不再静态命中）----

def test_bare_next_retired_to_llm():
    """"下一个" 作为下载/承接等歧义词已退役，不走静态路由 → None。"""
    assert _router().route("下一个") is None


def test_bare_pause_retired_to_llm():
    """"暂停" 单独出现（如"暂停一下手头活"）已退役 → None，交由 LLM 判断。"""
    assert _router().route("暂停一下手头活") is None


def test_bare_screenshot_retired_to_llm():
    """"截图工具在哪" 中的"截图"是名词，已退役 → None。"""
    assert _router().route("截图工具在哪") is None


def test_bare_resume_retired_to_llm():
    """"继续聊刚才的话题" 中的"继续"是聊天动作，已退役 → None。"""
    assert _router().route("继续聊刚才的话题") is None


# ---- 明确组合词仍正常命中（不回归）----

def test_cut_to_next_still_matches():
    """"切到下一首" 命中 next_track。"""
    out = _router().route("切到下一首")
    assert out is not None
    assert out.capability == "next_track"


def test_play_next_song_still_matches():
    """"播放下一首" 命中 next_track（"下一首"是保留组合词）。"""
    out = _router().route("播放下一首")
    assert out is not None
    assert out.capability == "next_track"


def test_pause_explicit_still_matches():
    """"暂停一下 / 暂停播放" 命中 pause_music（保留组合词）。"""
    assert _router().route("暂停一下").capability == "pause_music"
    assert _router().route("暂停播放").capability == "pause_music"


def test_screenshot_explicit_still_matches():
    """"截个图" 命中 screenshot_now（保留组合词）。"""
    assert _router().route("截个图").capability == "screenshot_now"


def test_resume_explicit_still_matches():
    """"继续播放" 命中 resume_music（保留组合词）。"""
    assert _router().route("继续播放").capability == "resume_music"


def test_daily_report_still_matches():
    """"今天的日报" 不被回归（命中 daily 类能力）。"""
    out = _router().route("今天的日报")
    assert out is not None
    assert "daily" in out.capability


def test_valid_match_helper_boundaries():
    """_is_valid_capability_match：仅当两端皆为中文才拒绝。"""
    assert _is_valid_capability_match("接管一下一个对话", "下一个") is False  # 两端中文
    assert _is_valid_capability_match("下一首", "下一首") is True            # 独立
    assert _is_valid_capability_match("切到下一首", "下一首") is True         # 尾端边界
    assert _is_valid_capability_match("我想听下一首", "下一首") is True      # 尾端边界
