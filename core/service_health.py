"""子服务健康四态 — 管理子服务生命周期 + 恢复窗口限流

竞品参考（Mio）：四态 `{enabled, running, ready, last_error}` + 恢复窗口限流。
替代静默异常（except: pass），用状态机管理子服务生命周期。

四态：
- enabled: 服务是否启用（配置层）
- running: 服务是否运行中（进程层）
- ready: 服务是否就绪（功能层）
- last_error: 最后一次错误信息

恢复窗口限流：
- 服务失败后，在冷却期内不重试
- 避免频繁重试导致资源浪费
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ServiceState(Enum):
    """服务状态"""
    DISABLED = "disabled"      # 未启用
    STARTING = "starting"      # 启动中
    READY = "ready"            # 就绪
    RUNNING = "running"        # 运行中
    FAILED = "failed"          # 失败
    RECOVERING = "recovering"  # 恢复中


@dataclass
class ServiceHealth:
    """子服务健康状态"""
    name: str                          # 服务名
    enabled: bool = True               # 是否启用
    running: bool = False              # 是否运行中
    ready: bool = False                # 是否就绪
    state: ServiceState = ServiceState.DISABLED
    last_error: str = ""               # 最后一次错误
    last_error_time: float = 0.0       # 错误时间戳
    start_time: float = 0.0            # 启动时间戳
    ready_time: float = 0.0            # 就绪时间戳
    restart_count: int = 0             # 重启次数
    last_restart_time: float = 0.0     # 最后一次重启时间

    # 恢复窗口限流
    recovery_window: float = 30.0      # 恢复冷却期（秒）
    max_retries: int = 5               # 最大重试次数
    retry_backoff: float = 2.0         # 重试退避因子


class HealthMonitor:
    """健康监控器 — 管理多个子服务的生命周期"""
    
    def __init__(self, name: str = "default", recovery_window: float = 30.0, max_retries: int = 5):
        """初始化健康监控器
        
        Args:
            name: 监控器名称
            recovery_window: 恢复冷却期（秒）
            max_retries: 最大重试次数
        """
        self._name = name
        self._services: dict[str, ServiceHealth] = {}
        self._lock = threading.Lock()
        self._recovery_window = recovery_window
        self._max_retries = max_retries
    
    def register(self, name: str, enabled: bool = True) -> ServiceHealth:
        """注册子服务
        
        Args:
            name: 服务名
            enabled: 是否启用
        
        Returns:
            ServiceHealth 实例
        """
        with self._lock:
            if name in self._services:
                return self._services[name]
            
            health = ServiceHealth(
                name=name,
                enabled=enabled,
                state=ServiceState.DISABLED if not enabled else ServiceState.READY,
                recovery_window=self._recovery_window,
                max_retries=self._max_retries,
            )
            self._services[name] = health
            logger.debug("Service registered: %s (enabled=%s)", name, enabled)
            return health
    
    def get(self, name: str) -> Optional[ServiceHealth]:
        """获取服务健康状态"""
        with self._lock:
            return self._services.get(name)
    
    def mark_running(self, name: str) -> bool:
        """标记服务运行中"""
        with self._lock:
            health = self._services.get(name)
            if not health:
                return False
            health.running = True
            health.state = ServiceState.RUNNING
            health.start_time = time.time()
            logger.debug("Service running: %s", name)
            return True
    
    def mark_ready(self, name: str) -> bool:
        """标记服务就绪"""
        with self._lock:
            health = self._services.get(name)
            if not health:
                return False
            health.ready = True
            health.state = ServiceState.READY
            health.ready_time = time.time()
            logger.debug("Service ready: %s", name)
            return True
    
    def mark_failed(self, name: str, error: str) -> bool:
        """标记服务失败"""
        with self._lock:
            health = self._services.get(name)
            if not health:
                return False
            health.running = False
            health.ready = False
            health.state = ServiceState.FAILED
            health.last_error = error
            health.last_error_time = time.time()
            logger.warning("Service failed: %s - %s", name, error)
            return True
    
    def can_restart(self, name: str) -> bool:
        """检查是否可以重启（恢复窗口限流）"""
        with self._lock:
            health = self._services.get(name)
            if not health:
                return False
            
            if not health.enabled:
                return False
            
            # 检查重试次数
            if health.restart_count >= health.max_retries:
                return False
            
            # 检查恢复窗口
            now = time.time()
            if now - health.last_error_time < health.recovery_window:
                return False
            
            return True
    
    def restart(self, name: str, on_restart: Callable[[], bool]) -> bool:
        """重启服务（带恢复窗口限流）
        
        Args:
            name: 服务名
            on_restart: 重启回调（返回是否成功）
        
        Returns:
            是否成功
        """
        if not self.can_restart(name):
            logger.debug("Restart denied: %s (recovery window)", name)
            return False
        
        health = self.get(name)
        if not health:
            return False
        
        health.state = ServiceState.RECOVERING
        health.restart_count += 1
        health.last_restart_time = time.time()
        
        logger.info("Restarting service: %s (attempt %d/%d)", 
                   name, health.restart_count, health.max_retries)
        
        try:
            success = on_restart()
            if success:
                self.mark_running(name)
                self.mark_ready(name)
                logger.info("Service restarted: %s", name)
                return True
            else:
                self.mark_failed(name, "Restart callback returned False")
                return False
        except Exception as e:
            self.mark_failed(name, str(e))
            return False
    
    def get_summary(self) -> dict:
        """获取所有服务的健康摘要"""
        with self._lock:
            return {
                name: {
                    "enabled": health.enabled,
                    "running": health.running,
                    "ready": health.ready,
                    "state": health.state.value,
                    "last_error": health.last_error,
                    "restart_count": health.restart_count,
                }
                for name, health in self._services.items()
            }
    
    def format_for_prompt(self) -> str:
        """格式化健康状态为 prompt 上下文"""
        summary = self.get_summary()
        if not summary:
            return ""
        
        lines = ["[子服务健康]"]
        for name, health in summary.items():
            status = "✅" if health["ready"] else "❌"
            line = f"- {name}: {status} (state={health['state']}"
            if health["last_error"]:
                line += f", error={health['last_error'][:50]}"
            if health["restart_count"] > 0:
                line += f", restarts={health['restart_count']}"
            line += ")"
            lines.append(line)
        
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  全局监控器
# ════════════════════════════════════════════════════════════

_global_monitor: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    """获取全局健康监控器（单例）"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = HealthMonitor()
    return _global_monitor


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "ServiceHealth",
    "ServiceState",
    "HealthMonitor",
    "get_health_monitor",
]