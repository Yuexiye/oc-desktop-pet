# -*- coding: utf-8 -*-
"""BugFix #5-C/D 单测：SchedulePerception 真实数据源 + InspectionPerception 巡检。

- C：SchedulePerception.refresh() 读三处真实数据源（原 automation*.json 不存在
  → 感知永远空转）：
    1. ~/.hanako/agents/<agent_id>/desk/cron-jobs.json（jobs[]，只列 enabled）
    2. ~/.hanako/.ephemeral/deferred-tasks.json（pending 延迟任务）
    3. ~/.hanako/.ephemeral/plugin-tasks.json（schedules[] 非空时列出）
- D：InspectionPerception 每 5 分钟节流 + 订阅 Hanako 产出（cron-runs 运行结果 /
  deferred 延迟任务 / 可选 notifications）+ 同 key 去重（30 分钟）。

运行: python -m pytest tests/test_bugfix5_schedule_inspection.py -v
"""
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.perception import schedule as schedule_mod
from core.perception.schedule import SchedulePerception
from core.perception.inspection import (
    InspectionPerception,
    INSPECTION_INTERVAL_SECONDS,
    REPORT_REPEAT_SECONDS,
)


def _write(home, rel, data):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _append_jsonl(home, rel, obj):
    """向 .jsonl 文件追加一行 JSON（cron-runs 运行结果用）。"""
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return p


def _cron_iso(epoch_seconds: float) -> str:
    """把 epoch 秒格式化为 UTC ISO8601（Z 结尾，与 Hanako cron-jobs 一致）。"""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


@pytest.fixture()
def hanako_home(tmp_path, monkeypatch):
    """把 schedule 模块的 HANAKO_HOME 指向临时目录。"""
    monkeypatch.setattr(schedule_mod, "HANAKO_HOME", tmp_path)
    return tmp_path


# ── C：SchedulePerception 数据源 ──────────────────────────


def test_schedule_reads_cron_jobs(hanako_home):
    _write(hanako_home, "agents/aimis/desk/cron-jobs.json", {
        "jobs": [
            {"id": "job_1", "type": "cron", "schedule": "0 8 * * *",
             "label": "每日签到", "enabled": True, "consecutiveErrors": 0,
             "lastRunAt": "2026-05-22T00:01:17.718Z",
             "nextRunAt": "2026-05-23T00:00:00.000Z"},
            {"id": "job_2", "schedule": "0 9 * * *", "label": "已禁用任务",
             "enabled": False, "consecutiveErrors": 0,
             "lastRunAt": "", "nextRunAt": "2026-05-23T09:00:00.000Z"},
        ]
    })
    s = SchedulePerception(agent_id="aimis")
    s.refresh()
    assert len(s.get_cron_jobs()) == 2  # 含 disabled（巡检仍会读）
    up = s.get_upcoming()
    assert len(up) == 1  # 只列 enabled
    assert up[0]["label"] == "每日签到"
    ctx = s.format_for_prompt()
    assert "每日签到" in ctx
    assert "0 8 * * *" in ctx
    assert "下次 " in ctx
    assert "已禁用任务" not in ctx  # enabled=false 不列


def test_schedule_reads_deferred_tasks(hanako_home):
    _write(hanako_home, ".ephemeral/deferred-tasks.json", {
        "subagent-1": {"status": "pending", "delivered": False,
                       "sessionId": "sess_abc_12345678"},
        "subagent-2": {"status": "running", "delivered": False,
                       "sessionId": "sess_def_87654321"},
    })
    s = SchedulePerception(agent_id="aimis")
    s.refresh()
    pending = s.get_pending_deferred()
    assert len(pending) == 1
    assert pending[0]["session_id"] == "sess_abc_12345678"
    ctx = s.format_for_prompt()
    assert "345678" in ctx  # sessionId 尾 8 位
    assert "（pending）" in ctx
    assert "87654321" not in ctx  # 非 pending 不列


def test_schedule_reads_plugin_schedules(hanako_home):
    _write(hanako_home, ".ephemeral/plugin-tasks.json", {
        "tasks": [{"taskId": "t1"}],
        "schedules": [{"id": "s1", "label": "盘后复盘", "schedule": "0 15 * * *"}],
    })
    s = SchedulePerception(agent_id="aimis")
    s.refresh()
    assert len(s.get_plugin_schedules()) == 1
    ctx = s.format_for_prompt()
    assert "盘后复盘" in ctx
    assert "0 15 * * *" in ctx


def test_schedule_missing_files_empty(hanako_home):
    """三处数据源都缺失 → 空（不抛异常）。"""
    s = SchedulePerception(agent_id="aimis")
    s.refresh()
    assert s.get_cron_jobs() == []
    assert s.get_pending_deferred() == []
    assert s.format_for_prompt() == ""


def test_schedule_agent_id_path_is_used(hanako_home):
    """C：按 agent_id 拼 cron-jobs 路径（~/.hanako/agents/<id>/desk/...）。"""
    _write(hanako_home, "agents/aimis/desk/cron-jobs.json", {
        "jobs": [{"id": "j1", "label": "A 的任务", "enabled": True,
                  "schedule": "0 8 * * *", "consecutiveErrors": 0,
                  "lastRunAt": "", "nextRunAt": ""}],
    })
    _write(hanako_home, "agents/ophelia/desk/cron-jobs.json", {
        "jobs": [{"id": "j2", "label": "O 的任务", "enabled": True,
                  "schedule": "0 9 * * *", "consecutiveErrors": 0,
                  "lastRunAt": "", "nextRunAt": ""}],
    })
    s = SchedulePerception(agent_id="aimis")
    s.refresh()
    labels = [j["label"] for j in s.get_cron_jobs()]
    assert labels == ["A 的任务"]


# ── D：InspectionPerception 巡检 ──────────────────────────


def test_inspection_throttle_5min(hanako_home):
    """D：5 分钟节流——间隔内 tick 返回空（订阅 cron-runs 新增行）。"""
    now = 1_000_000.0
    _write(hanako_home, "agents/aimis/desk/cron-jobs.json", {
        "jobs": [{"id": "j1", "label": "签到", "enabled": True}],
    })
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "j1", "status": "success"})
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    hits = insp.tick(now=now)
    assert len(hits) == 1
    assert "签到" in hits[0] and "跑完了" in hits[0]
    # 5 分钟节流内：即便再追加新行也不重复扫描
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "j1", "status": "success"})
    assert insp.tick(now=now + INSPECTION_INTERVAL_SECONDS - 1) == []


def test_inspection_cron_run_reported(hanako_home):
    """D：cron-runs 新增 success/failed 行 → 通知完成/失败（label 来自 cron-jobs）。"""
    now = 2_000_000.0
    _write(hanako_home, "agents/aimis/desk/cron-jobs.json", {
        "jobs": [
            {"id": "sign", "label": "每日签到", "enabled": True},
            {"id": "sync", "label": "数据同步", "enabled": True},
        ],
    })
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "sign", "status": "success"})
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "sync", "status": "failed"})
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    hits = insp.tick(now=now)
    text = "\n".join(hits)
    assert "【任务】每日签到跑完了 ✅" in text
    assert "【任务】数据同步跑失败了 ❌" in text


def test_inspection_deferred_new_pending(hanako_home):
    """D：deferred-tasks 新增 pending → 通知「有 N 个延迟任务待处理」。"""
    now = 3_000_000.0
    _write(hanako_home, ".ephemeral/deferred-tasks.json", {
        "subagent-x": {"status": "pending", "delivered": False,
                       "sessionId": "sess_1234567890abcdef"},
    })
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    hits = insp.tick(now=now)
    assert "有 1 个延迟任务待处理" in "\n".join(hits)
    # 同一批 pending 未变 → 下一个巡检周期（过 5 分钟）不再重复
    assert insp.tick(now=now + INSPECTION_INTERVAL_SECONDS + 1) == []


def test_inspection_dedup_same_key(hanako_home):
    """D：同一 job+status 在 REPORT_REPEAT_SECONDS 内只通知一次。"""
    now = 5_000_000.0
    _write(hanako_home, "agents/aimis/desk/cron-jobs.json", {
        "jobs": [{"id": "j1", "label": "提醒", "enabled": True}],
    })
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "j1", "status": "success"})
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    assert len(insp.tick(now=now)) == 1
    # 5 分钟后再追加同 job 同 status → 同 key 未过 30 分钟冷却 → 不重复
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "j1", "status": "success"})
    assert insp.tick(now=now + INSPECTION_INTERVAL_SECONDS + 1) == []
    # 超过 REPORT_REPEAT 后再次允许
    _append_jsonl(hanako_home, "agents/aimis/desk/cron-runs/run.jsonl",
                  {"jobId": "j1", "status": "success"})
    later = now + REPORT_REPEAT_SECONDS + INSPECTION_INTERVAL_SECONDS + 1
    assert len(insp.tick(now=later)) == 1


def test_inspection_format_for_prompt(hanako_home):
    """D：format_for_prompt() 输出最近巡检命中（供 build_context 注入）。"""
    now = 6_000_000.0
    _write(hanako_home, ".ephemeral/deferred-tasks.json", {
        "subagent-x": {"status": "pending", "delivered": False,
                       "sessionId": "sess_1234567890abcdef"},
    })
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    assert insp.tick(now=now)
    ctx = insp.format_for_prompt()
    assert "[任务巡检]" in ctx
    assert "有 1 个延迟任务待处理" in ctx


def test_inspection_files_missing_tolerant(hanako_home):
    """D：cron-runs / deferred / notifications 文件均缺失 → 不抛异常，返回空。"""
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    # 既无 cron-runs 目录也无 deferred / notifications 文件
    assert insp.tick(now=1_000_000.0) == []
    assert insp.format_for_prompt() == ""


def test_inspection_notifications_optional(hanako_home):
    """D：（可选）notifications.json 新增条目播报；缺失则跳过不报错。"""
    now = 7_000_000.0
    _write(hanako_home, ".ephemeral/notifications.json", [
        {"id": "n1", "message": "记得喝水"},
        "直接文本条目",
    ])
    insp = InspectionPerception(SchedulePerception(agent_id="aimis"))
    hits = insp.tick(now=now)
    text = "\n".join(hits)
    assert "记得喝水" in text
    assert "直接文本条目" in text
    # 同条目已见 → 下一个周期不重复
    assert insp.tick(now=now + INSPECTION_INTERVAL_SECONDS + 1) == []


def test_controller_tick_inspection_callback():
    """D：controller._tick_inspection 命中 → 回调主动汇报（复用 proactive 链路）。"""
    from core.perception.controller import PerceptionController

    fake_inspection = MagicMock()
    fake_inspection.tick.return_value = ["快到 签到 时间了"]
    fake_self = SimpleNamespace(_inspection=fake_inspection, _inspection_callback=None)
    # 无回调不崩
    PerceptionController._tick_inspection(fake_self)
    cb = MagicMock()
    fake_self._inspection_callback = cb
    PerceptionController._tick_inspection(fake_self)
    cb.assert_called_once_with("快到 签到 时间了")
