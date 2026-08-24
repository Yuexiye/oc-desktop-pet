"""日程感知 — 读取 Hanako 自动化任务

数据源（BugFix #5-C：原实现读 ~/.hanako/.ephemeral/automation*.json，该文件
不存在 → 感知永远空转，主动生成拿不到 Hanako 任务）：
  1. ~/.hanako/agents/<agent_id>/desk/cron-jobs.json   — Hanako 定时任务 jobs[]
     （id/type/schedule(cron表达式)/label/enabled/consecutiveErrors/lastRunAt/
       nextRunAt）
  2. ~/.hanako/.ephemeral/deferred-tasks.json           — 延迟任务 dict
     （key=subagent id，value 含 status/delivered/sessionId/sessionPath）
  3. ~/.hanako/.ephemeral/plugin-tasks.json             — 插件任务 {tasks, schedules}

refresh() 把三处数据归一化为单列表（每项带 kind 字段），
format_for_prompt() 只输出 enabled 的 cron + pending 的 deferred + 非空
plugin schedules。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HANAKO_HOME = Path.home() / ".hanako"


def _parse_iso(ts: str) -> float | None:
    """把 ISO8601 时间字符串转 epoch 秒（供本地时区格式化/比较；失败返回 None）。"""
    if not ts:
        return None
    try:
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.timestamp()
        return dt.timestamp()
    except Exception:
        return None


class SchedulePerception:
    """日程感知 - 读取 Hanako 自动化任务（cron / deferred / plugin-schedule）"""

    def __init__(self, agent_id: str = ""):
        self._agent_id = (agent_id or "").strip()
        self._automations: list[dict] = []

    # ── 刷新 ──────────────────────────────────────────────

    def refresh(self) -> None:
        """重读三处数据源，归一化为 _automations 列表。"""
        self._automations = []
        try:
            self._automations.extend(self._read_cron_jobs())
        except Exception as e:
            logger.debug("Schedule cron refresh failed: %s", e)
        try:
            self._automations.extend(self._read_deferred_tasks())
        except Exception as e:
            logger.debug("Schedule deferred refresh failed: %s", e)
        try:
            self._automations.extend(self._read_plugin_tasks())
        except Exception as e:
            logger.debug("Schedule plugin refresh failed: %s", e)

    # ── 数据源 ────────────────────────────────────────────

    def _read_cron_jobs(self) -> list[dict]:
        """读 ~/.hanako/agents/<agent_id>/desk/cron-jobs.json 的 jobs[]。"""
        if not self._agent_id:
            return []
        path = HANAKO_HOME / "agents" / self._agent_id / "desk" / "cron-jobs.json"
        data = self._read_json(path)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            return []
        items: list[dict] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            label = str(job.get("label") or job.get("prompt") or "未知任务").strip()
            items.append({
                "kind": "cron",
                "id": str(job.get("id") or ""),
                "label": label[:40],
                "schedule": str(job.get("schedule") or ""),
                "enabled": bool(job.get("enabled", True)),
                "last_run_at": str(job.get("lastRunAt") or ""),
                "next_run_at": str(job.get("nextRunAt") or ""),
                "consecutive_errors": int(job.get("consecutiveErrors") or 0),
            })
        return items

    def _read_deferred_tasks(self) -> list[dict]:
        """读 ~/.hanako/.ephemeral/deferred-tasks.json（dict key=subagent id）。"""
        path = HANAKO_HOME / ".ephemeral" / "deferred-tasks.json"
        data = self._read_json(path)
        if not isinstance(data, dict):
            return []
        items: list[dict] = []
        for key, task in data.items():
            if not isinstance(task, dict):
                continue
            items.append({
                "kind": "deferred",
                "key": str(key),
                "status": str(task.get("status") or ""),
                "delivered": bool(task.get("delivered", False)),
                "session_id": str(task.get("sessionId") or ""),
            })
        return items

    def _read_plugin_tasks(self) -> list[dict]:
        """读 ~/.hanako/.ephemeral/plugin-tasks.json 的 schedules[]。"""
        path = HANAKO_HOME / ".ephemeral" / "plugin-tasks.json"
        data = self._read_json(path)
        if not isinstance(data, dict):
            return []
        schedules = data.get("schedules")
        if not isinstance(schedules, list):
            return []
        items: list[dict] = []
        for sched in schedules:
            if not isinstance(sched, dict):
                continue
            items.append({
                "kind": "plugin_schedule",
                "id": str(sched.get("id") or sched.get("taskId") or ""),
                "label": str(sched.get("label") or sched.get("name") or "插件任务"),
                "schedule": str(sched.get("schedule") or sched.get("cron") or ""),
            })
        return items

    @staticmethod
    def _read_json(path: Path):
        """安全读 JSON；文件缺失/损坏返回 None。"""
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception as e:
            logger.debug("读取 %s 失败: %s", path, e)
            return None

    # ── 查询 ──────────────────────────────────────────────

    def get_cron_jobs(self) -> list[dict]:
        """全部 cron 任务（含 disabled；巡检用）。"""
        return [i for i in self._automations if i.get("kind") == "cron"]

    def get_upcoming(self, max_items: int = 3) -> list[dict]:
        """按 next_run_at 升序取前 N 条 enabled cron 任务（兼容旧调用方）。"""
        crons = [i for i in self._automations
                 if i.get("kind") == "cron" and i.get("enabled")]
        crons.sort(key=lambda i: _parse_iso(i.get("next_run_at")) or 0)
        return crons[:max_items]

    def get_pending_deferred(self) -> list[dict]:
        """status == pending 的延迟任务。"""
        return [i for i in self._automations
                if i.get("kind") == "deferred" and i.get("status") == "pending"]

    def get_plugin_schedules(self) -> list[dict]:
        """插件 schedules（非空时列入 prompt）。"""
        return [i for i in self._automations if i.get("kind") == "plugin_schedule"]

    # ── prompt 注入 ───────────────────────────────────────

    def format_for_prompt(self) -> str:
        """输出注入 LLM prompt 的日程上下文（空则返回空串）。"""
        parts: list[str] = []

        crons = self.get_upcoming(max_items=10)
        if crons:
            lines = ["[即将到来的定时任务]"]
            for item in crons:
                label = item.get("label", "未知")
                schedule = item.get("schedule", "")
                next_ts = _parse_iso(item.get("next_run_at"))
                hhmm = datetime.fromtimestamp(next_ts).strftime("%H:%M") if next_ts else "待定"
                lines.append(f"- {label}（{schedule}，下次 {hhmm}）")
            parts.append("\n".join(lines))

        pending = self.get_pending_deferred()
        if pending:
            lines = ["[延迟任务]"]
            for item in pending:
                sid = item.get("session_id", "")
                tail = sid[-8:] if sid else item.get("key", "")[-8:]
                lines.append(f"- [延迟任务] {tail}（pending）")
            parts.append("\n".join(lines))

        plugin_scheds = self.get_plugin_schedules()
        if plugin_scheds:
            lines = ["[插件定时]"]
            for item in plugin_scheds:
                lines.append(f"- {item.get('label')}（{item.get('schedule')}）")
            parts.append("\n".join(lines))

        return "\n".join(parts)
