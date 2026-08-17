"""事件流 — 桌宠记忆层 A/B 的 JSONL 持久化存储

把"感知信号（前台分类/视觉活动/话题/情绪）"持续写入带时间轴的流，
补齐"感知→记忆"断链。与 `<agent_id>.json`（CompanionMemory）平级共存，
互不破坏：事件流是**新文件**，只做 append-only 追加。

文件：`~/.oc-pet/memory/<agent_id>_events.jsonl`

特性：
- 单行 JSON，append-only；异常不外抛（防御式编程，调用方可放心埋点）
- 行级损坏隔离：读取时逐行 try/except，坏行跳过并告警，不影响其余行
- 线程安全：append/prune 用锁保护，跨线程调用安全
- 裁剪：`prune(max_days, max_entries)` 按时间/条数双上限裁剪

事件字段（示例）：
    {"ts": 1755417600.0, "start_ts": 1755416400.0, "end_ts": 1755417600.0,
     "category": "development", "scenario": "late_night_work", "intent": "deep_work",
     "emotion": "happy", "intensity": 0.8, "topic": "重构事件流模块（截断60字）",
     "source": "foreground|vision|topic"}

隐私约定：
- `topic` 入库前截断 60 字（复用 CompanionMemory.TOPIC_MAX_CHARS）
- source="vision" 的视觉 API summary/detail **不进流**（只在写入方省略字段）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".oc-pet" / "memory"

TOPIC_MAX_CHARS = 60          # 话题文本截断长度（与 CompanionMemory 一致）
DEFAULT_MAX_DAYS = 30         # 事件保留天数上限
DEFAULT_MAX_ENTRIES = 5000    # 事件条数上限


class EventStream:
    """事件流 JSONL 存储（append-only，行级损坏隔离，线程安全）"""

    def __init__(self, agent_id: str, memory_dir: str | Path | None = None):
        """
        Args:
            agent_id: 桌宠/角色 id（如 "yuexinmiao"）
            memory_dir: 记忆目录；缺省 ~/.oc-pet/memory
        """
        self._agent_id = agent_id
        self._dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self._path = self._dir / f"{agent_id}_events.jsonl"
        self._lock = threading.Lock()

    # ── 属性 ──

    @property
    def path(self) -> Path:
        return self._path

    # ── 写入 ──

    def append(self, record: dict) -> None:
        """单行 JSON append + "\\n"，锁保护；异常不外抛（埋点方安全）。

        Args:
            record: 事件字段 dict（ts/start_ts/end_ts/category/scenario/...）。
                    缺省字段会自动补齐：ts 取 end_ts 兜底 now。
        """
        if not record or not isinstance(record, dict):
            return
        try:
            now = time.time()
            entry = dict(record)
            # ts：事件记录时间（=end_ts 兜底）
            if not entry.get("ts"):
                entry["ts"] = entry.get("end_ts") or now
            # topic 隐私截断（≤60 字）
            topic = entry.get("topic")
            if topic:
                cleaned = " ".join(str(topic).split())[:TOPIC_MAX_CHARS]
                entry["topic"] = cleaned
            # 旧事件无 emotion/intensity → 读取时 .get("emotion","neutral")
            line = json.dumps(entry, ensure_ascii=False)
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            logger.warning("EventStream append failed: %s", e)

    # ── 读取 ──

    def _read_lines(self) -> list[dict]:
        """读取全部行；坏行跳过并告警（行级损坏隔离）。"""
        try:
            if not self._path.exists():
                return []
            with self._path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning("EventStream read failed: %s", e)
            return []
        records: list[dict] = []
        for i, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
                else:
                    logger.warning("EventStream 坏行跳过（第 %d 行，非对象）", i)
            except Exception as e:
                logger.warning("EventStream 坏行跳过（第 %d 行）: %s", i, e)
        return records

    @staticmethod
    def _record_ts(rec: dict) -> float:
        """取事件时间戳（ts 优先，回退 end_ts/start_ts/0）。"""
        ts = rec.get("ts")
        if ts is None:
            ts = rec.get("end_ts")
        if ts is None:
            ts = rec.get("start_ts")
        try:
            return float(ts or 0.0)
        except Exception:
            return 0.0

    def read_all(self) -> list[dict]:
        """读取全部事件，按 ts 排序；坏行跳过。"""
        records = self._read_lines()
        records.sort(key=self._record_ts)
        return records

    def read_since(self, ts: float) -> list[dict]:
        """读取 ts 之后（含）的事件，按 ts 排序。"""
        try:
            ts = float(ts)
        except Exception:
            ts = 0.0
        return [r for r in self.read_all() if self._record_ts(r) >= ts]

    def read_range(self, start: float, end: float) -> list[dict]:
        """读取 [start, end] 时间区间内的事件，按 ts 排序。"""
        try:
            start = float(start)
            end = float(end)
        except Exception:
            return []
        if start > end:
            start, end = end, start
        return [r for r in self.read_all() if start <= self._record_ts(r) <= end]

    # ── 裁剪 ──

    def prune(self, max_days: int = DEFAULT_MAX_DAYS,
              max_entries: int = DEFAULT_MAX_ENTRIES) -> int:
        """裁剪超限事件：超过 max_days 天或超过 max_entries 条的部分删除。

        Returns:
            删除条数（int）
        """
        records = self._read_lines()
        if not records:
            return 0
        cutoff = time.time() - float(max_days) * 86400.0
        kept: list[dict] = []
        removed = 0
        for r in records:
            if self._record_ts(r) < cutoff:
                removed += 1
                continue
            kept.append(r)
        # 条数上限：只保留最新 max_entries 条
        if len(kept) > max_entries:
            kept = kept[-max_entries:]
            removed += len(records) - len(kept)
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with self._path.open("w", encoding="utf-8") as f:
                    for r in kept:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("EventStream prune failed: %s", e)
            return 0
        if removed > 0:
            logger.info("EventStream prune: removed=%d kept=%d (%s)",
                        removed, len(kept), self._path.name)
        return removed


__all__ = ["EventStream", "DEFAULT_MEMORY_DIR", "TOPIC_MAX_CHARS"]
