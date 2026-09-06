"""主动能力可解释面板 — 统一展示开关 + 运行状态 + 最近错误 + 费用边界

竞品参考（Mio）：主动联系/屏幕观察/自动日记默认关闭，设置页统一展示
「开关 + 运行状态 + 最近错误 + 费用边界」，并提供一个统一隐私暂停入口。

验收：用户能在 10 秒内知道"它现在在偷偷做什么"，并能一键全停。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AutonomyCapability:
    """主动能力定义"""
    id: str                      # 能力 ID（如 "screen_observe"）
    name: str                    # 显示名（如 "屏幕观察"）
    description: str = ""        # 描述
    enabled_by_default: bool = False  # 默认是否开启
    enabled: bool = False        # 当前状态
    running: bool = False        # 是否运行中
    ready: bool = False          # 是否就绪
    last_error: str = ""         # 最近错误
    last_error_time: float = 0.0  # 错误时间戳
    cost_per_day: float = 0.0    # 每日费用（元）
    token_per_day: int = 0       # 每日 Token
    privacy_sensitive: bool = False  # 是否涉及隐私（截图/录音等）


class AutonomyPanel:
    """主动能力可解释面板"""
    
    def __init__(self):
        self._capabilities: dict[str, AutonomyCapability] = {}
        self._lock = __import__("threading").Lock()
        self._privacy_paused = False  # 统一隐私暂停
        
        # 注册默认能力
        self._register_defaults()
    
    def _register_defaults(self):
        """注册默认的主动能力"""
        defaults = [
            AutonomyCapability(
                id="screen_observe",
                name="屏幕观察",
                description="定时截屏并分析屏幕内容，了解用户在做什么",
                enabled_by_default=False,
                privacy_sensitive=True,
            ),
            AutonomyCapability(
                id="media_smtc",
                name="媒体感知",
                description="读取正在播放的媒体信息（歌曲名、艺术家等）",
                enabled_by_default=True,
                privacy_sensitive=False,
            ),
            AutonomyCapability(
                id="proactive_chat",
                name="主动搭话",
                description="在合适时机主动开口，发起对话",
                enabled_by_default=False,
                privacy_sensitive=False,
            ),
            AutonomyCapability(
                id="phone_receive",
                name="手机接收",
                description="接收手机活动事件（MacroDroid 上报）",
                enabled_by_default=False,
                privacy_sensitive=True,
            ),
            AutonomyCapability(
                id="memory_write",
                name="记忆写入",
                description="自动记录对话内容到长期记忆",
                enabled_by_default=True,
                privacy_sensitive=False,
            ),
        ]
        
        with self._lock:
            for cap in defaults:
                cap.enabled = cap.enabled_by_default
                self._capabilities[cap.id] = cap
    
    def get_capability(self, capability_id: str) -> Optional[AutonomyCapability]:
        """获取能力定义"""
        with self._lock:
            return self._capabilities.get(capability_id)
    
    def is_enabled(self, capability_id: str) -> bool:
        """检查能力是否启用"""
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                return False
            if self._privacy_paused and cap.privacy_sensitive:
                return False
            return cap.enabled
    
    def set_enabled(self, capability_id: str, enabled: bool) -> bool:
        """设置能力启用状态"""
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                return False
            cap.enabled = enabled
            if not enabled:
                cap.running = False
                cap.ready = False
            logger.info("Capability %s set to %s", capability_id, enabled)
            return True
    
    def set_running(self, capability_id: str, running: bool) -> bool:
        """设置能力运行状态"""
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                return False
            cap.running = running
            if running:
                cap.ready = True
            return True
    
    def set_error(self, capability_id: str, error: str) -> bool:
        """设置能力错误信息"""
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                return False
            cap.last_error = error
            cap.last_error_time = time.time()
            cap.ready = False
            return True
    
    def set_usage(self, capability_id: str, token_per_day: int = 0, cost_per_day: float = 0.0) -> bool:
        """设置能力使用量"""
        with self._lock:
            cap = self._capabilities.get(capability_id)
            if not cap:
                return False
            cap.token_per_day = token_per_day
            cap.cost_per_day = cost_per_day
            return True
    
    def pause_privacy(self) -> bool:
        """统一隐私暂停（一键停掉所有隐私敏感能力）"""
        with self._lock:
            self._privacy_paused = True
            # 停用所有隐私敏感能力
            for cap in self._capabilities.values():
                if cap.privacy_sensitive:
                    cap.enabled = False
                    cap.running = False
                    cap.ready = False
            logger.warning("Privacy paused: all privacy-sensitive capabilities disabled")
            return True
    
    def resume_privacy(self) -> bool:
        """恢复隐私能力（按默认设置恢复）"""
        with self._lock:
            self._privacy_paused = False
            # 按默认设置恢复
            for cap in self._capabilities.values():
                if cap.privacy_sensitive:
                    cap.enabled = cap.enabled_by_default
            logger.info("Privacy resumed: privacy-sensitive capabilities restored")
            return True
    
    @property
    def privacy_paused(self) -> bool:
        """隐私是否暂停"""
        with self._lock:
            return self._privacy_paused
    
    def get_summary(self) -> dict:
        """获取所有能力的摘要"""
        with self._lock:
            return {
                "privacy_paused": self._privacy_paused,
                "capabilities": {
                    cap.id: {
                        "name": cap.name,
                        "description": cap.description,
                        "enabled": cap.enabled,
                        "running": cap.running,
                        "ready": cap.ready,
                        "last_error": cap.last_error,
                        "cost_per_day": cap.cost_per_day,
                        "token_per_day": cap.token_per_day,
                        "privacy_sensitive": cap.privacy_sensitive,
                    }
                    for cap in self._capabilities.values()
                }
            }
    
    def format_for_prompt(self) -> str:
        """格式化能力状态为 prompt 上下文"""
        summary = self.get_summary()
        lines = ["[主动能力状态]"]
        
        if summary["privacy_paused"]:
            lines.append("⚠️ 隐私已暂停")
        
        for cap_id, cap in summary["capabilities"].items():
            status = "✅" if cap["ready"] else "❌"
            if not cap["enabled"]:
                status = "⏸️"  # 已停用
            line = f"- {cap['name']}: {status} (enabled={cap['enabled']}, running={cap['running']}"
            if cap["last_error"]:
                line += f", error={cap['last_error'][:50]}"
            if cap["cost_per_day"] > 0:
                line += f", cost={cap['cost_per_day']:.2f}元/天"
            if cap["token_per_day"] > 0:
                line += f", tokens={cap['token_per_day']}"
            if cap["privacy_sensitive"]:
                line += ", 🔒隐私"
            line += ")"
            lines.append(line)
        
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  全局面板
# ════════════════════════════════════════════════════════════

_global_panel: Optional[AutonomyPanel] = None


def get_autonomy_panel() -> AutonomyPanel:
    """获取全局主动能力面板（单例）"""
    global _global_panel
    if _global_panel is None:
        _global_panel = AutonomyPanel()
    return _global_panel


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "AutonomyCapability",
    "AutonomyPanel",
    "get_autonomy_panel",
]