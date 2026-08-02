"""轻量级事件总线（解耦事件发布者与订阅者）

设计参考 03-成长计划 §7.2，按 oc-pet 现状做了两点调整：
- 注册表用一把锁保护（订阅/退订偶发，简单锁足够）
- emit 时复制 handler 列表，允许 handler 内部安全退订

约定：
- handler 在 emit 调用方线程中同步执行；调用方需保证 handler 自身线程安全。
- 单个 handler 抛异常不影响其他 handler（已兜底）。
- 没有任何订阅者时 emit 是 O(1) 空操作，可安全在任意模块随意埋点。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class EventBus:
    """进程级事件总线。类方法风格，全局共享一个注册表。"""

    _handlers: dict[str, list[Callable]] = {}
    _lock = threading.Lock()

    # ------------------------------------------------------------------ 注册
    @classmethod
    def on(cls, event: str, handler: Callable) -> Callable:
        """订阅事件，返回 handler 便于后续 off()。"""
        with cls._lock:
            cls._handlers.setdefault(event, []).append(handler)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("EventBus.on: %s <- %s",
                         event, getattr(handler, "__qualname__", handler))
        return handler

    @classmethod
    def off(cls, event: str, handler: Callable) -> None:
        """取消订阅。"""
        with cls._lock:
            handlers = cls._handlers.get(event)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    cls._handlers.pop(event, None)

    # ------------------------------------------------------------------ 发布
    @classmethod
    def emit(cls, event: str, **data) -> None:
        """发布事件。同步调用所有订阅者，单个异常不影响其余。"""
        with cls._lock:
            handlers = list(cls._handlers.get(event, []))
        for handler in handlers:
            try:
                handler(**data)
            except Exception:
                logger.exception(
                    "EventBus handler error [%s] -> %s",
                    event, getattr(handler, "__qualname__", handler),
                )

    # ------------------------------------------------------------------ 调试
    @classmethod
    def clear(cls) -> None:
        """清空所有订阅（测试用）。"""
        with cls._lock:
            cls._handlers.clear()

    @classmethod
    def subscriber_count(cls, event: str | None = None) -> int | dict[str, int]:
        """调试用：查询订阅数。"""
        with cls._lock:
            if event is not None:
                return len(cls._handlers.get(event, []))
            return {k: len(v) for k, v in cls._handlers.items()}


__all__ = ["EventBus"]
