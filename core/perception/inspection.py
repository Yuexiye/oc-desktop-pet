"""Hanako 任务巡检 — 作为观察者每 5 分钟轮询 cron / deferred 数据并主动汇报。

BugFix #5-D 需求：主动感知除了屏幕/窗口，新增 Hanako 任务巡检，每 5 分钟
检查一次（对齐 Hanako cron 设计，作为观察者轮询）。四种命中：

  1. 任务临近：某 enabled cron job 的 nextRunAt 在未来 10 分钟内（且
     lastRunAt 未更新到 >= nextRunAt，即还没跑）→ 主动说「快到 <label> 时间了」
  2. 该跑没跑：nextRunAt 已过但 lastRunAt < nextRunAt（没执行）→ 主动提醒
  3. 连续失败：consecutiveErrors > 0 → 主动说「<label> 连续失败 N 次」
  4. pending 任务：deferred-tasks 有 status=pending → 主动汇报
     「有 N 个延迟任务待处理」

去重：同一触发 key 在 REPORT_REPEAT_SECONDS（默认 30 分钟）内只汇报一次，
避免每 5 分钟对同一个"该跑没跑"任务反复轰炸。测试可注入 now 控制时间。
"""
from __future__ import annotations

import logging
import time

from .schedule import SchedulePerception, _parse_iso

logger = logging.getLogger(__name__)

# 巡检节流：每 5 分钟检查一次（对齐 Hanako cron 设计的观察者轮询粒度）
INSPECTION_INTERVAL_SECONDS = 300.0

# 任务临近窗口：nextRunAt 在未来 10 分钟内
NEAR_MINUTES = 10.0

# 同一触发 key 的重复汇报冷却（30 分钟；期间不再重复提醒）
REPORT_REPEAT_SECONDS = 1800.0


class InspectionPerception:
    """Hanako 任务巡检 — 每 5 分钟检查 cron/deferred 并产出触发文案。"""

    def __init__(self, schedule: SchedulePerception | None = None):
        self._schedule = schedule or SchedulePerception()
        self._last_tick_at: float = 0.0
        self._reported: dict[str, float] = {}  # key -> 上次汇报时间
        self._last_findings: list[str] = []    # 最近一次巡检命中（供 prompt 注入）

    # ── 对外入口 ──────────────────────────────────────────

    def tick(self, now: float | None = None) -> list[str]:
        """执行一次巡检（带 5 分钟节流）。

        Args:
            now: 注入的当前时间（测试用；缺省 time.time()）。

        Returns:
            命中的触发文案列表；节流内或未命中返回空列表。
        """
        now = time.time() if now is None else float(now)
        if now - self._last_tick_at < INSPECTION_INTERVAL_SECONDS:
            return []
        self._last_tick_at = now

        try:
            self._schedule.refresh()
        except Exception as e:
            logger.debug("Inspection schedule refresh failed: %s", e)

        findings: list[str] = []
        try:
            findings.extend(self._check_cron_jobs(now))
            findings.extend(self._check_pending_deferred(now))
        except Exception as e:
            logger.debug("Inspection check failed: %s", e)

        # 只保留通过去重的命中
        triggers = [t for t in findings if self._allow_report(t[0], now)]
        self._last_findings = [t[1] for t in triggers]
        if triggers:
            logger.info("Inspection triggers: %d 条（%s）", len(triggers),
                        "；".join(t[1] for t in triggers[:3]))
        return [t[1] for t in triggers]

    def format_for_prompt(self) -> str:
        """最近一次巡检命中注入 LLM prompt（无命中返回空串）。"""
        if not self._last_findings:
            return ""
        lines = ["[任务巡检]"]
        for text in self._last_findings:
            lines.append(f"- {text}")
        return "\n".join(lines)

    def reset(self) -> None:
        """清空节流与去重状态（测试/手动触发用）。"""
        self._last_tick_at = 0.0
        self._reported.clear()
        self._last_findings = []

    # ── 命中判定 ──────────────────────────────────────────

    def _check_cron_jobs(self, now: float) -> list[tuple[str, str]]:
        """cron 三种命中：任务临近 / 该跑没跑 / 连续失败。

        Returns:
            [(dedup_key, 触发文案), ...]
        """
        hits: list[tuple[str, str]] = []
        for job in self._schedule.get_cron_jobs():
            # 巡检只盯 enabled 任务（disabled 不会执行，不报临近/失败）
            if not job.get("enabled", True):
                continue
            job_id = job.get("id") or job.get("label", "unknown")
            label = job.get("label", "未知任务")
            next_ts = _parse_iso(job.get("next_run_at"))
            last_ts = _parse_iso(job.get("last_run_at"))
            errors = int(job.get("consecutive_errors") or 0)

            # 3. 连续失败（独立于是否临近，先报）
            if errors > 0:
                hits.append((f"error:{job_id}:{errors}",
                             f"{label} 连续失败 {errors} 次"))

            if next_ts is None:
                continue
            # lastRunAt >= nextRunAt 说明该轮已执行过，不再提醒
            if last_ts is not None and last_ts >= next_ts:
                continue
            if next_ts > now:
                # 1. 任务临近：未来 NEAR_MINUTES 分钟内
                if next_ts - now <= NEAR_MINUTES * 60.0:
                    hits.append((f"near:{job_id}",
                                 f"快到 {label} 时间了"))
            else:
                # 2. 该跑没跑：nextRunAt 已过但没执行
                hits.append((f"overdue:{job_id}",
                             f"{label} 该执行了，好像还没跑"))
        return hits

    def _check_pending_deferred(self, now: float) -> list[tuple[str, str]]:
        """4. pending 延迟任务（按数量聚合汇报）。"""
        pending = self._schedule.get_pending_deferred()
        if not pending:
            return []
        count = len(pending)
        return [(f"pending:{count}", f"有 {count} 个延迟任务待处理")]

    # ── 去重 ──────────────────────────────────────────────

    def _allow_report(self, key: str, now: float) -> bool:
        """同一 key 在 REPORT_REPEAT_SECONDS 内只汇报一次。"""
        last = self._reported.get(key)
        if last is not None and now - last < REPORT_REPEAT_SECONDS:
            return False
        self._reported[key] = now
        return True
