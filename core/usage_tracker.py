"""Token/费用统计与预算边界 — 按会话/按天统计，可配预算上限

功能：
- 统计 Token 使用量（prompt_tokens + completion_tokens）
- 按会话/按天汇总
- 可配预算上限（Token 或费用）
- 检查是否超限（通知回调）

注意：不做降级（无模板池兜底），只统计 + 通知。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    """单次 API 调用记录"""
    timestamp: float = 0.0          # 调用时间戳
    source: str = ""                # 调用来源（如 "chat", "proactive", "screen_enrich"）
    prompt_tokens: int = 0          # 提示词 Token
    completion_tokens: int = 0      # 回复 Token
    total_tokens: int = 0           # 总 Token（prompt + completion）
    model: str = ""                 # 模型名
    cost: float = 0.0               # 费用（元）


@dataclass
class UsageSummary:
    """使用量摘要"""
    total_tokens: int = 0           # 总 Token
    prompt_tokens: int = 0          # 总提示词 Token
    completion_tokens: int = 0      # 总回复 Token
    total_cost: float = 0.0         # 总费用（元）
    call_count: int = 0             # 调用次数
    today_tokens: int = 0           # 今天 Token
    today_cost: float = 0.0         # 今天费用（元）
    today_calls: int = 0            # 今天调用次数


class UsageTracker:
    """Token/费用统计器"""
    
    def __init__(
        self,
        storage_path: str = "",
        daily_token_limit: int = 0,       # 每日 Token 上限（0=无限制）
        daily_cost_limit: float = 0.0,    # 每日费用上限（元，0=无限制）
        session_token_limit: int = 0,     # 会话 Token 上限（0=无限制）
        session_cost_limit: float = 0.0,  # 会话费用上限（元，0=无限制）
        price_per_1k_tokens: float = 0.002,  # 每千 Token 价格（元）
    ):
        self._storage_path = Path(storage_path) if storage_path else None
        self._records: list[UsageRecord] = []
        self._lock = __import__("threading").Lock()
        
        # 预算限制
        self._daily_token_limit = daily_token_limit
        self._daily_cost_limit = daily_cost_limit
        self._session_token_limit = session_token_limit
        self._session_cost_limit = session_cost_limit
        self._price_per_1k = price_per_1k_tokens
        
        # 回调
        self.on_limit_exceeded: Callable[[str], None] = lambda msg: None
        
        # 加载历史记录
        self._load_history()
    
    def _load_history(self):
        """加载历史记录"""
        if not self._storage_path or not self._storage_path.exists():
            return
        
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._records = [UsageRecord(**r) for r in data]
            logger.info("Loaded %d usage records", len(self._records))
        except Exception as e:
            logger.warning("Failed to load usage history: %s", e)
    
    def _save_history(self):
        """保存历史记录"""
        if not self._storage_path:
            return
        
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [vars(r) for r in self._records]
            self._storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save usage history: %s", e)
    
    def record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        source: str = "",
        model: str = "",
    ) -> UsageRecord:
        """记录一次 API 调用的 Token 使用量
        
        Args:
            prompt_tokens: 提示词 Token
            completion_tokens: 回复 Token
            source: 调用来源
            model: 模型名
        
        Returns:
            UsageRecord 实例
        """
        total_tokens = prompt_tokens + completion_tokens
        cost = (total_tokens / 1000) * self._price_per_1k
        
        record = UsageRecord(
            timestamp=time.time(),
            source=source,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
            cost=cost,
        )
        
        with self._lock:
            self._records.append(record)
        
        # 检查预算
        self._check_limits(source)
        
        # 异步保存（避免阻塞）
        __import__("threading").Thread(
            target=self._save_history,
            daemon=True,
            name="usage-save",
        ).start()
        
        logger.debug(
            "Usage recorded: source=%s prompt=%d completion=%d total=%d cost=%.4f",
            source, prompt_tokens, completion_tokens, total_tokens, cost
        )
        
        return record
    
    def _check_limits(self, source: str = ""):
        """检查预算限制"""
        with self._lock:
            summary = self._get_summary()
        
        # 检查每日限制
        if self._daily_token_limit > 0 and summary.today_tokens >= self._daily_token_limit:
            msg = f"今日 Token 已达上限：{summary.today_tokens}/{self._daily_token_limit}"
            logger.warning(msg)
            self.on_limit_exceeded(msg)
            return
        
        if self._daily_cost_limit > 0 and summary.today_cost >= self._daily_cost_limit:
            msg = f"今日费用已达上限：{summary.today_cost:.2f}/{self._daily_cost_limit:.2f} 元"
            logger.warning(msg)
            self.on_limit_exceeded(msg)
            return
        
        # 检查会话限制
        if self._session_token_limit > 0 and summary.total_tokens >= self._session_token_limit:
            msg = f"会话 Token 已达上限：{summary.total_tokens}/{self._session_token_limit}"
            logger.warning(msg)
            self.on_limit_exceeded(msg)
            return
        
        if self._session_cost_limit > 0 and summary.total_cost >= self._session_cost_limit:
            msg = f"会话费用已达上限：{summary.total_cost:.2f}/{self._session_cost_limit:.2f} 元"
            logger.warning(msg)
            self.on_limit_exceeded(msg)
            return
    
    def _get_summary(self) -> UsageSummary:
        """获取使用量摘要"""
        summary = UsageSummary()
        
        # 计算今天的时间范围
        today_start = time.time() - (time.time() % 86400)  # 今天 00:00
        
        with self._lock:
            for r in self._records:
                summary.total_tokens += r.total_tokens
                summary.prompt_tokens += r.prompt_tokens
                summary.completion_tokens += r.completion_tokens
                summary.total_cost += r.cost
                summary.call_count += 1
                
                if r.timestamp >= today_start:
                    summary.today_tokens += r.total_tokens
                    summary.today_cost += r.cost
                    summary.today_calls += 1
        
        return summary
    
    def get_summary(self) -> dict:
        """获取使用量摘要（字典格式）"""
        summary = self._get_summary()
        return {
            "total_tokens": summary.total_tokens,
            "prompt_tokens": summary.prompt_tokens,
            "completion_tokens": summary.completion_tokens,
            "total_cost": round(summary.total_cost, 4),
            "call_count": summary.call_count,
            "today_tokens": summary.today_tokens,
            "today_cost": round(summary.today_cost, 4),
            "today_calls": summary.today_calls,
            "limits": {
                "daily_token": self._daily_token_limit,
                "daily_cost": self._daily_cost_limit,
                "session_token": self._session_token_limit,
                "session_cost": self._session_cost_limit,
            },
        }
    
    def get_records(self, limit: int = 50) -> list[dict]:
        """获取最近的调用记录"""
        with self._lock:
            records = self._records[-limit:]
            return [vars(r) for r in records]
    
    def format_for_prompt(self) -> str:
        """格式化使用量为 prompt 上下文"""
        summary = self.get_summary()
        lines = [
            "[Token 使用统计]",
            f"今日：{summary['today_tokens']} tokens, {summary['today_cost']:.2f} 元 ({summary['today_calls']} 次)",
            f"总计：{summary['total_tokens']} tokens, {summary['total_cost']:.2f} 元 ({summary['call_count']} 次)",
        ]
        
        # 显示预算状态
        limits = summary.get("limits", {})
        if limits.get("daily_token"):
            pct = min(100, int(summary["today_tokens"] / limits["daily_token"] * 100))
            lines.append(f"今日 Token 预算：{pct}%")
        if limits.get("daily_cost"):
            pct = min(100, int(summary["today_cost"] / limits["daily_cost"] * 100))
            lines.append(f"今日费用预算：{pct}%")
        
        return "\n".join(lines)
    
    def reset(self):
        """重置统计（清空记录）"""
        with self._lock:
            self._records = []
        self._save_history()
        logger.info("Usage tracker reset")


# ════════════════════════════════════════════════════════════
#  全局统计器
# ════════════════════════════════════════════════════════════

_global_tracker: Optional[UsageTracker] = None


def get_usage_tracker() -> UsageTracker:
    """获取全局使用量统计器（单例）"""
    global _global_tracker
    if _global_tracker is None:
        # 从环境变量读取配置
        storage_path = os.environ.get("OC_PET_USAGE_STORAGE", "")
        daily_token_limit = int(os.environ.get("OC_PET_DAILY_TOKEN_LIMIT", "0"))
        daily_cost_limit = float(os.environ.get("OC_PET_DAILY_COST_LIMIT", "0.0"))
        session_token_limit = int(os.environ.get("OC_PET_SESSION_TOKEN_LIMIT", "0"))
        session_cost_limit = float(os.environ.get("OC_PET_SESSION_COST_LIMIT", "0.0"))
        price_per_1k = float(os.environ.get("OC_PET_PRICE_PER_1K_TOKENS", "0.002"))
        
        _global_tracker = UsageTracker(
            storage_path=storage_path,
            daily_token_limit=daily_token_limit,
            daily_cost_limit=daily_cost_limit,
            session_token_limit=session_token_limit,
            session_cost_limit=session_cost_limit,
            price_per_1k_tokens=price_per_1k,
        )
    return _global_tracker


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "UsageRecord",
    "UsageSummary",
    "UsageTracker",
    "get_usage_tracker",
]