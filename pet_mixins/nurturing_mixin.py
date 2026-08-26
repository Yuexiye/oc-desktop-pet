"""NurturingMixin — 桌宠养成（已弃用）。

原功能：喂食 / 工作 / 任务菜单 / 状态摘要。
因用户反馈未深入开发且显得臃肿，已整体移除。保留空 mixin 避免旧 import 报错。
"""
import logging

logger = logging.getLogger(__name__)


class NurturingMixin:
    """养成逻辑占位符（已移除）。"""

    def _build_nurturing_menu(self):
        """已弃用。"""
        pass

    def _build_mission_menu(self):
        """已弃用。"""
        pass

    def _feed_item(self, item):
        """已弃用。"""
        pass

    def _start_work(self, work):
        """已弃用。"""
        pass

    def _on_work_finish(self, info):
        """已弃用。"""
        pass

    def _on_mission_completed_bubble(self, mission_id="", name="", rewards=None):
        """已弃用。"""
        pass

    def _emit_level_up(self, old_level: int, new_level: int):
        """已弃用。"""
        pass

    def _fire_level_up(self, old_level: int, new_level: int):
        """已弃用。"""
        pass

    def _show_status_summary(self):
        """已弃用。"""
        pass
