# -*- coding: utf-8 -*-
"""回归测试：静态能力路由的中文子串碰撞误匹配。

验证对象：capability_registry.CapabilityRouter.route
  - 修复前：纯子串 `pattern in text` 对中文产生碰撞——
    "接管一下一个对话" 中的 "一下一个" ⊃ "下一个" → 被 next_track 劫持，
    自然语言"接管对话"请求被错当成切歌插件调用（实证日志 next_track 误命中）。
  - 修复后：关键词被中文字符前后夹住时视为嵌在大词里的误匹配，放过给 LLM；
    独立的"下一个/切到下一首/播放下一个"仍正常命中。
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


def test_collision_takeover_dialog_not_hijacked():
    """"接管一下一个对话(音频播放器那个" 不应被 next_track 劫持 → 回退 LLM(None)。"""
    out = _router().route("请接管一下一个对话(音频播放器那个")
    assert out is None


def test_standalone_next_still_matches():
    """独立的"下一个"仍命中 next_track。"""
    out = _router().route("下一个")
    assert out is not None
    assert out.capability == "next_track"


def test_cut_to_next_still_matches():
    """"切到下一首" 前后有边界 → 命中 next_track。"""
    out = _router().route("切到下一首")
    assert out is not None
    assert out.capability == "next_track"


def test_play_next_still_matches():
    """"播放下一个" 结尾为边界 → 命中 next_track。"""
    out = _router().route("播放下一个")
    assert out is not None
    assert out.capability == "next_track"


def test_pause_still_matches():
    """既有能力"暂停一下"不被回归。"""
    out = _router().route("暂停一下")
    assert out is not None
    assert out.capability == "pause_music"


def test_daily_report_still_matches():
    """"今天的日报" 不被回归（命中 daily 类能力）。"""
    out = _router().route("今天的日报")
    assert out is not None
    assert "daily" in out.capability


def test_sandwiched_false_positive_rejected_general():
    """通用性：被中文字符夹住的关键词视为误匹配（"看一下一个视频"不命中 next_track）。"""
    assert _is_valid_capability_match("看一下一个视频", "下一个") is False


def test_valid_match_helper_boundaries():
    """_is_valid_capability_match：仅当两端皆为中文才拒绝。"""
    assert _is_valid_capability_match("接管一下一个对话", "下一个") is False  # 两端中文
    assert _is_valid_capability_match("下一个", "下一个") is True            # 独立
    assert _is_valid_capability_match("切到下一首", "下一首") is True         # 尾端边界
    assert _is_valid_capability_match("我想听下一个", "下一个") is True      # 尾端边界
