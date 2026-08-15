"""P2 关系：CompanionMemory + 陪伴钩子测试

覆盖 P2 验收的客观项：
- 记忆持久化（记录活动/话题 → 保存 → 重载不丢）
- 跨天滚动（今日统计归档进 history，连续天数 streak 计算）
- 昨日摘要生成（"昨天你在做什么/聊到哪"）
- 每日首启问候（跨天 → 接话题文案；非跨天 → 不打扰）
- 关电脑离别语（streak 高时强调陪伴关系）

运行: python -m pytest test_p2_companion.py -v
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from core.companion_memory import CompanionMemory
from core.companion_hooks import build_morning_greeting, build_farewell, CATEGORY_LABELS


@pytest.fixture
def mem(tmp_path):
    """临时目录的 CompanionMemory（不影响真实记忆）"""
    return CompanionMemory("test_agent", memory_dir=tmp_path)


# ── 1. 持久化 ──

def test_record_and_reload(tmp_path):
    """记录活动/话题 → 保存 → 新实例重载不丢"""
    m1 = CompanionMemory("test_agent", memory_dir=tmp_path)
    m1.record_activity("development")
    m1.record_activity("development")
    m1.record_topic("你昨天说的那个项目后来怎么样了")
    m1.save()

    m2 = CompanionMemory("test_agent", memory_dir=tmp_path)
    assert m2._today["development"] == 2, "活动计数未持久化"
    assert m2.last_topic == "你昨天说的那个项目后来怎么样了", "话题未持久化"


def test_record_topic_truncated(tmp_path):
    """话题超长截断"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m.record_topic("这是一个非常非常长的用户消息用来测试话题截断逻辑是否正常工作超过六十个字符的部分应该被截掉")
    assert len(m.last_topic) <= 60, f"话题应截断到 60 字, 实际 {len(m.last_topic)}"


# ── 2. 跨天滚动 ──

def test_new_day_rollover(tmp_path):
    """跨天后今日统计归档进 history，今日重置"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m.record_activity("development")  # 第一次记录 → 今天
    # 强制伪造上次活跃日期为昨天，模拟跨天
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    m._last_active_date = yesterday
    m.record_activity("browsing")  # 触发 rollover

    # rollover 后上次活跃已是今天 → 不再是"新的一天"
    assert not m.is_new_day(), "rollover 后应已归为今天"
    assert len(m._history) == 1, "昨日应归档进 history"
    assert m._history[0]["top_categories"][0]["category"] == "development"
    assert m._today["browsing"] == 1, "今日计数应重置"


def test_streak_calculation(tmp_path):
    """连续活跃天数：昨天有记录 → streak 递增"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    m._last_active_date = yesterday
    m._streak_days = 3
    m._total_days = 5
    m.record_activity("development")  # 今天活跃，昨天有记录 → streak 4
    assert m.streak == 4, f"streak 应为 4, 实际 {m.streak}"
    assert m.total_days == 6


# ── 3. 昨日摘要 ──

def test_yesterday_summary(tmp_path):
    """昨日摘要：分类 + 话题"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._history = [{
        "date": "2026-08-14",
        "top_categories": [{"category": "development", "count": 20}],
        "last_topic": "做了个重构",
        "minutes": 180,
    }]
    summary = m.yesterday_summary()
    assert "2026-08-14" in summary
    assert "development" in summary or "重构" in summary


def test_yesterday_summary_empty(tmp_path):
    """无历史 → 空摘要"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    assert m.yesterday_summary() == ""


# ── 4. 每日首启问候 ──

def test_morning_greeting_cross_day_with_topic(tmp_path):
    """跨天 + 有昨日话题 → 接话题（P2 验收核心）"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._last_active_date = "2026-08-14"  # 昨天
    m._last_topic = "你昨天说的那个项目后来怎么样了？"
    m._history = [{"date": "2026-08-14", "top_categories": [], "last_topic": m._last_topic}]
    greet = build_morning_greeting(m)
    assert greet, "跨天应有问候"
    assert "项目" in greet or "后来" in greet, f"应接昨日话题, 实际 {greet}"


def test_morning_greeting_same_day_none(tmp_path):
    """非跨天 → 不打扰（返回 None）"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._last_active_date = m._today_str()  # 今天已活跃过
    assert build_morning_greeting(m) is None, "非跨天不应问候"


def test_morning_greeting_new_user(tmp_path):
    """全新用户 → 基础问候"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._last_active_date = ""  # 首次
    m._last_topic = ""
    m._history = []
    greet = build_morning_greeting(m)
    assert greet, "首次启动应有问候"


# ── 5. 关电脑离别语 ──

def test_farewell_mentions_streak_when_high(tmp_path):
    """陪伴 streak ≥ 3 → 离别语强调陪伴关系"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._streak_days = 5
    farewell = build_farewell(m)
    assert "5" in farewell or "第" in farewell, f"应提到陪伴天数, 实际 {farewell}"


def test_farewell_normal(tmp_path):
    """普通离别语（不报错，从文案池随机）"""
    m = CompanionMemory("test_agent", memory_dir=tmp_path)
    m._streak_days = 1
    farewell = build_farewell(m)
    # 普通离别语文案池（无 streak 强调）
    assert farewell in (
        "今天也辛苦了，明天见！",
        "晚安，要好好休息呀～",
        "我在这边等你回来。",
    ), f"普通离别语意外文案: {farewell}"


# ── 6. 类别中文标签 ──

def test_category_labels_complete():
    """所有常用分类都有中文标签"""
    for cat in ("writing", "development", "browsing", "gaming", "communication", "entertainment", "other"):
        assert cat in CATEGORY_LABELS, f"缺少 {cat} 标签"
