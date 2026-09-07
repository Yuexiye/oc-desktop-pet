"""统一对话调度器 — 合并 proactive / idle / idle chatter，幂等去重

2026-09-06: P1 改动
- 单例模式，线程安全
- 触发请求入口：scheduler.request(trigger_source, priority, payload)
- 静默模式检查
- 打扰预算检查
- 并发互斥检查
- 幂等去重（5 秒内同源触发合并，同源 60 秒内只放行一次）
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class DialogueScheduler:
    """统一对话调度器（单例，线程安全）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._last_trigger: dict[str, float] = {}  # trigger_source -> last trigger time
        self._is_busy = False  # 用户是否正在忙
        
        # 配置
        self._daily_limit = 6
        self._period_limits = {
            "morning": 2,   # 08:00-12:00
            "afternoon": 2, # 12:00-18:00
            "evening": 2,   # 18:00-24:00
            "night": 0      # 00:00-08:00
        }
        self._daily_count = 0
        self._period_counts = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
        self._daily_date = ""
        
        # 冷却时间（同源 60 秒内只放行一次）
        self._cooldown_seconds = 60.0
        # 幂等合并窗口（5 秒内同源触发合并）
        self._dedupe_window = 5.0
        
        logger.info("DialogueScheduler initialized")
    
    def load_config(self, config: dict):
        """加载配置"""
        cfg = config.get("proactive", {}) or {}
        
        # 打扰预算
        budget = cfg.get("daily_budget", {}) or {}
        self._daily_limit = budget.get("daily_limit", 6)
        self._period_limits = budget.get("period_limits", self._period_limits) or self._period_limits
        
        with self._lock:
            logger.info("DialogueScheduler config loaded: daily_limit=%d, period_limits=%s", 
                       self._daily_limit, self._period_limits)
    
    def set_busy(self, busy: bool):
        """设置用户忙碌状态（由外部注入）"""
        with self._lock:
            self._is_busy = busy
    
    def _get_period(self) -> str:
        """获取当前时间段"""
        hour = time.localtime().tm_hour
        if hour < 8:
            return "night"
        elif hour < 12:
            return "morning"
        elif hour < 18:
            return "afternoon"
        else:
            return "evening"
    
    def _is_dnd_active(self) -> bool:
        """检查是否处于静默模式"""
        hour = time.localtime().tm_hour
        return hour < 8  # 00:00-08:00
    
    def _has_budget_remaining(self) -> bool:
        """检查是否有剩余打扰预算"""
        with self._lock:
            # 跨天重置
            today = time.strftime("%Y-%m-%d")
            if today != self._daily_date:
                self._daily_date = today
                self._daily_count = 0
                self._period_counts = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
            
            # 每日上限
            if self._daily_count >= self._daily_limit:
                return False
            
            # 时段上限
            period = self._get_period()
            if self._period_counts.get(period, 0) >= self._period_limits.get(period, 0):
                return False
            
            return True
    
    def _is_trigger_allowed(self, trigger_source: str, now: float) -> bool:
        """检查触发是否允许（幂等去重）"""
        with self._lock:
            last = self._last_trigger.get(trigger_source, 0)
            
            # 5 秒内同源触发合并
            if now - last < self._dedupe_window:
                return False
            
            # 同源 60 秒内只放行一次
            if now - last < self._cooldown_seconds:
                return False
            
            return True
    
    def request(self, trigger_source: str, priority: str, payload: dict) -> Optional[str]:
        """
        请求触发一次主动对话。
        
        Args:
            trigger_source: 触发源（proactive / idle / idle_chatter / screen）
            priority: 优先级（high / low）
            payload: 上下文（场景标签、记忆、用户状态等）
        
        Returns:
            生成的文本（如果允许触发），或 None（如果放弃触发）
        """
        now = time.time()
        
        # 1. 静默模式检查
        if self._is_dnd_active():
            logger.debug("Scheduler suppressed: DND active")
            return None
        
        # 2. 打扰预算检查
        if not self._has_budget_remaining():
            logger.debug("Scheduler suppressed: budget exhausted")
            return None
        
        # 3. 并发互斥检查
        if self._is_busy:
            logger.debug("Scheduler suppressed: user busy")
            return None
        
        # 4. 幂等去重
        if not self._is_trigger_allowed(trigger_source, now):
            logger.debug("Scheduler suppressed: dedupe (source=%s)", trigger_source)
            return None
        
        # 5. 记录触发时间
        with self._lock:
            self._last_trigger[trigger_source] = now
            self._daily_count += 1
            period = self._get_period()
            self._period_counts[period] = self._period_counts.get(period, 0) + 1
        
        # 6. 优先级判断
        if priority == "high":
            # 系统提示：模板直出，不走 LLM，不占预算
            return self._generate_template(payload)
        else:
            # 主动聊天：LLM 生成，注入记忆，占预算
            return self._generate_llm(payload)
    
    def _generate_template(self, payload: dict) -> Optional[str]:
        """模板直出（系统提示）"""
        # 从 payload 中获取模板文本
        return payload.get("template_text")
    
    def _generate_llm(self, payload: dict) -> Optional[str]:
        """LLM 生成（主动聊天）"""
        # 从 payload 中获取 LLM 生成的文本
        return payload.get("llm_text")
