"""StatusHudMixin — 状态 HUD / 左下角状态指示器（已弃用）。

原功能：状态 HUD 显隐、状态指示器、情绪调制呼吸、主题切换刷新。
因用户反馈未深入开发且显得臃肿，已整体移除。保留空 mixin 避免旧 import 报错。
"""
import logging

logger = logging.getLogger(__name__)


class StatusHudMixin:
    """状态 HUD 占位符（已移除）。"""

    def _status_idle_style(self) -> str:
        """已弃用。"""
        return ""

    def _on_theme_changed(self, theme: str):
        """已弃用。"""
        pass

    def _toggle_status_hud(self, force_show=None):
        """已弃用。"""
        pass

    def _toggle_status_hud_from_menu(self, checked: bool):
        """已弃用。"""
        pass

    def _refresh_status_hud(self):
        """已弃用。"""
        pass

    def _flash_status_hud(self):
        """已弃用。"""
        pass

    def _auto_hide_status_hud(self):
        """已弃用。"""
        pass

    def _emotion_bob_factor(self) -> float:
        """已弃用。"""
        return 1.0

    def _reposition_status_label(self):
        """已弃用。"""
        pass

    def _restore_status_label(self):
        """已弃用。"""
        pass

    def _update_status_indicator(self, state_name: str):
        """已弃用。"""
        pass
