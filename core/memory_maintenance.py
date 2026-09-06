"""记忆自动维护代理 — 用户空闲时自动归纳/去重/修剪/重要性衰减

竞品参考（FaustBot）：独立后台代理，在用户空闲时自动运行，把近期记忆归纳成索引、
去重、修剪冗余关系、计算重要性衰减。

验收：连续用两周后记忆库不膨胀、检索命中率不下降。

功能：
- 空闲检测：检测用户是否空闲（基于屏幕观察/活动追踪）
- 记忆归纳：把近期事件归纳成索引
- 去重：检测并合并重复记忆
- 修剪：移除低重要性记忆
- 重要性衰减：计算记忆的重要性分数，随时间衰减
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".oc-pet" / "memory"


@dataclass
class MemoryRecord:
    """记忆记录"""
    id: str = ""                    # 记忆 ID
    content: str = ""               # 内容
    timestamp: float = 0.0          # 创建时间
    importance: float = 1.0         # 重要性分数（0-1）
    access_count: int = 0           # 访问次数
    last_accessed: float = 0.0      # 最后访问时间
    tags: list[str] = field(default_factory=list)  # 标签
    source: str = ""                # 来源
    status: str = "active"          # 状态（active/archived/deleted）


@dataclass
class MaintenanceReport:
    """维护报告"""
    timestamp: float = 0.0          # 维护时间
    duration_ms: int = 0            # 耗时（毫秒）
    total_memories: int = 0         # 总记忆数
    active_memories: int = 0        # 活跃记忆数
    archived_memories: int = 0      # 归档记忆数
    deleted_memories: int = 0       # 删除记忆数
    duplicates_merged: int = 0      # 合并的重复数
    importance_updates: int = 0     # 重要性更新数
    errors: list[str] = field(default_factory=list)  # 错误列表


class MemoryMaintenanceAgent:
    """记忆自动维护代理"""
    
    def __init__(
        self,
        agent_id: str = "default",
        memory_dir: str | Path | None = None,
        idle_timeout: int = 300,        # 空闲超时（秒，默认 5 分钟）
        maintenance_interval: int = 3600,  # 维护间隔（秒，默认 1 小时）
        importance_decay_rate: float = 0.01,  # 重要性衰减率（每天）
        min_importance_threshold: float = 0.1,  # 最低重要性阈值（低于此值归档）
        max_memories: int = 10000,      # 最大记忆数（超过此值强制修剪）
    ):
        self._agent_id = agent_id
        self._dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
        self._path = self._dir / f"{agent_id}_memories.json"
        
        self._idle_timeout = idle_timeout
        self._maintenance_interval = maintenance_interval
        self._importance_decay_rate = importance_decay_rate
        self._min_importance_threshold = min_importance_threshold
        self._max_memories = max_memories
        
        self._memories: list[MemoryRecord] = []
        self._last_maintenance = time.time()
        self._last_user_activity = time.time()
        self._running = False
        
        # 回调
        self.on_maintenance_complete: Callable[[MaintenanceReport], None] = lambda report: None
        
        # 加载现有数据
        self._load_memories()
    
    def _load_memories(self):
        """加载记忆"""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._memories = [MemoryRecord(**m) for m in data.get("memories", [])]
            logger.info("Loaded %d memories for %s", len(self._memories), self._agent_id)
        except Exception as e:
            logger.warning("Failed to load memories for %s: %s", self._agent_id, e)
            self._memories = []
    
    def _save_memories(self):
        """保存记忆"""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            data = {
                "agent_id": self._agent_id,
                "last_maintenance": self._last_maintenance,
                "memories": [vars(m) for m in self._memories],
            }
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save memories for %s: %s", self._agent_id, e)
    
    def add_memory(self, content: str, tags: list[str] = None, source: str = "") -> MemoryRecord:
        """添加记忆"""
        import uuid
        record = MemoryRecord(
            id=uuid.uuid4().hex[:8],
            content=content,
            timestamp=time.time(),
            importance=1.0,
            tags=tags or [],
            source=source,
        )
        self._memories.append(record)
        self._save_memories()
        return record
    
    def record_activity(self):
        """记录用户活动"""
        self._last_user_activity = time.time()
    
    def is_idle(self) -> bool:
        """检查用户是否空闲"""
        return (time.time() - self._last_user_activity) >= self._idle_timeout
    
    def should_maintain(self) -> bool:
        """检查是否应该维护"""
        return (time.time() - self._last_maintenance) >= self._maintenance_interval
    
    def calculate_importance(self, record: MemoryRecord) -> float:
        """计算记忆重要性（基于访问次数 + 时间衰减）"""
        # 基础分数：访问次数越多，重要性越高
        access_score = min(record.access_count * 0.1, 0.5)  # 最多 0.5
        
        # 时间衰减：越旧衰减越多
        days_since = (time.time() - record.timestamp) / 86400
        time_decay = 1.0 - (days_since * self._importance_decay_rate)
        time_decay = max(time_decay, 0.0)  # 不低于 0
        
        # 综合分数
        importance = record.importance * time_decay + access_score
        return min(importance, 1.0)  # 最高 1.0
    
    def find_duplicates(self) -> list[list[MemoryRecord]]:
        """查找重复记忆（基于内容相似度）"""
        duplicates = []
        active = [m for m in self._memories if m.status == "active"]
        
        # 简单去重：基于内容前 50 字符
        seen = {}
        for record in active:
            key = record.content[:50].lower()
            if key in seen:
                duplicates.append([seen[key], record])
            else:
                seen[key] = record
        
        return duplicates
    
    def merge_duplicates(self, group: list[MemoryRecord]) -> MemoryRecord:
        """合并重复记忆"""
        if not group:
            return None
        
        # 保留重要性最高的
        main = max(group, key=lambda m: m.importance)
        
        # 合并标签
        all_tags = set(main.tags)
        for other in group:
            all_tags.update(other.tags)
        
        main.tags = list(all_tags)
        main.access_count = sum(m.access_count for m in group)
        main.importance = min(1.0, main.importance + 0.1)  # 合并后重要性略增
        
        # 删除其他重复项
        for other in group:
            if other.id != main.id:
                other.status = "merged"
        
        return main
    
    def prune_low_importance(self) -> int:
        """修剪低重要性记忆"""
        pruned = 0
        for record in self._memories:
            if record.status != "active":
                continue
            
            importance = self.calculate_importance(record)
            record.importance = importance
            
            if importance < self._min_importance_threshold:
                record.status = "archived"
                pruned += 1
        
        return pruned
    
    def enforce_max_memories(self) -> int:
        """强制修剪到最大记忆数"""
        deleted = 0
        active = [m for m in self._memories if m.status == "active"]
        
        if len(active) <= self._max_memories:
            return 0
        
        # 按重要性排序，删除最低的
        active.sort(key=lambda m: m.importance)
        for record in active[:len(active) - self._max_memories]:
            record.status = "deleted"
            deleted += 1
        
        return deleted
    
    def maintain(self) -> MaintenanceReport:
        """执行维护"""
        start_time = time.time()
        report = MaintenanceReport(timestamp=start_time)
        
        logger.info("Memory maintenance started for %s", self._agent_id)
        
        try:
            # 统计
            report.total_memories = len(self._memories)
            report.active_memories = len([m for m in self._memories if m.status == "active"])
            
            # 1. 重要性更新 + 修剪低重要性
            report.importance_updates = self.prune_low_importance()
            report.archived_memories = len([m for m in self._memories if m.status == "archived"])
            
            # 2. 去重 + 合并
            duplicates = self.find_duplicates()
            for group in duplicates:
                self.merge_duplicates(group)
                report.duplicates_merged += 1
            
            # 3. 强制修剪
            report.deleted_memories = self.enforce_max_memories()
            
            # 保存
            self._save_memories()
            
            # 更新统计
            report.active_memories = len([m for m in self._memories if m.status == "active"])
            
        except Exception as e:
            logger.error("Memory maintenance failed: %s", e)
            report.errors.append(str(e))
        
        report.duration_ms = int((time.time() - start_time) * 1000)
        report.timestamp = time.time()
        
        self._last_maintenance = time.time()
        
        logger.info(
            "Memory maintenance completed: %d total, %d active, %d archived, %d merged, %d deleted",
            report.total_memories, report.active_memories, report.archived_memories,
            report.duplicates_merged, report.deleted_memories
        )
        
        self.on_maintenance_complete(report)
        
        return report
    
    def start(self):
        """启动维护代理"""
        self._running = True
        
        def _loop():
            while self._running:
                # 检查是否应该维护
                if self.is_idle() and self.should_maintain():
                    self.maintain()
                
                # 等待 1 分钟
                time.sleep(60)
        
        thread = __import__("threading").Thread(
            target=_loop,
            daemon=True,
            name="memory-maintenance",
        )
        thread.start()
        logger.info("Memory maintenance agent started for %s", self._agent_id)
    
    def stop(self):
        """停止维护代理"""
        self._running = False
        logger.info("Memory maintenance agent stopped for %s", self._agent_id)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total": len(self._memories),
            "active": len([m for m in self._memories if m.status == "active"]),
            "archived": len([m for m in self._memories if m.status == "archived"]),
            "deleted": len([m for m in self._memories if m.status == "deleted"]),
            "last_maintenance": self._last_maintenance,
            "is_idle": self.is_idle(),
            "should_maintain": self.should_maintain(),
        }


# ════════════════════════════════════════════════════════════
#  全局代理
# ════════════════════════════════════════════════════════════

_global_agents: dict[str, MemoryMaintenanceAgent] = {}


def get_maintenance_agent(agent_id: str = "default") -> MemoryMaintenanceAgent:
    """获取记忆维护代理（单例）"""
    global _global_agents
    if agent_id not in _global_agents:
        _global_agents[agent_id] = MemoryMaintenanceAgent(agent_id)
    return _global_agents[agent_id]


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "MemoryRecord",
    "MaintenanceReport",
    "MemoryMaintenanceAgent",
    "get_maintenance_agent",
]