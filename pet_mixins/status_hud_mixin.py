"""StatusHudMixin — 状态 HUD / 左下角状态指示器 / 主题跟随。

由 PetWindow 多重继承。访问 self._status_label / self._status_hud / self._ui_theme /
self._hud_pinned / self._hud_auto_hide_timer / self._save_mgr / self._current_emotion /
self._reposition_overlays 等，均由 PetWindow 提供（鸭子类型）。
"""
import contextlib
import logging

from ui.theme import rgb, rgba

logger = logging.getLogger(__name__)


class StatusHudMixin:
    """状态 HUD 显隐、状态指示器、情绪调制呼吸、主题切换刷新。"""

    def _status_idle_style(self) -> str:
        """左下角状态条的默认（idle）样式 — 跟随主题调色板"""
        t = getattr(self, "_ui_theme", "dark")
        return f"""
            QLabel {{
                background: rgba({rgba(t, 'panel_bg')});
                color: rgb({rgb(t, 'text_secondary')});
                border: 1px solid rgba({rgba(t, 'panel_border')});
                border-radius: 10px;
                font-size: 9px;
                padding: 2px 6px;
            }}
        """

    def _on_theme_changed(self, theme: str):
        """主题切换 — 同步刷新状态条默认样式（不打断正在显示的语义色）"""
        self._ui_theme = theme
        if not self._status_label.isVisible():
            self._status_label.setStyleSheet(self._status_idle_style())

    def _toggle_status_hud(self, force_show: bool | None = None):
        """切换状态 HUD 显隐；无养成数据时回退文字摘要

        force_show:
          - True:  强制显示并 pinned
          - False: 强制隐藏
          - None:  按当前显隐切换
        """
        if not hasattr(self, '_status_hud'):
            return
        visible = self._status_hud.isVisible()
        if force_show is True:
            do_show = True
        elif force_show is False:
            do_show = False
        else:
            do_show = not visible

        if do_show:
            if not hasattr(self, '_save_mgr') or not self._save_mgr:
                self._show_status_summary()
                return
            self._hud_pinned = True
            self._refresh_status_hud()
            self._status_hud.set_emotion(getattr(self, '_current_emotion', 'neutral'))
            self._status_hud.show()
            self._reposition_overlays()
        else:
            self._hud_pinned = False
            self._status_hud.hide()
            self._hud_auto_hide_timer.stop()

        # 同步右键菜单勾选状态
        act = getattr(self, '_status_menu_action', None)
        if act is not None:
            with contextlib.suppress(Exception):
                act.setChecked(do_show)

    def _toggle_status_hud_from_menu(self, checked: bool):
        """右键菜单「状态」可勾选项的回调"""
        self._toggle_status_hud(force_show=checked)

    def _refresh_status_hud(self):
        if hasattr(self, '_save_mgr') and self._save_mgr and hasattr(self, '_status_hud'):
            self._status_hud.set_stats(self._save_mgr.save)

    def _flash_status_hud(self):
        """喂食/抚摸后闪现 HUD 几秒；未 pinned 时自动隐藏"""
        if not hasattr(self, '_status_hud'):
            return
        if not hasattr(self, '_save_mgr') or not self._save_mgr:
            return
        self._refresh_status_hud()
        self._status_hud.show()
        self._reposition_overlays()
        if not self._hud_pinned and not self._hud_auto_hide_timer.isActive():
            self._hud_auto_hide_timer.start(4000)

    def _auto_hide_status_hud(self):
        if not self._hud_pinned and hasattr(self, '_status_hud'):
            self._status_hud.hide()

    def _emotion_bob_factor(self) -> float:
        """按情绪调制呼吸浮动幅度（happy 更弹、sad 更稳）"""
        return {
            "happy": 1.8,
            "surprised": 1.3,
            "sad": 0.3,
            "thinking": 1.0,
            "angry": 1.1,
            "neutral": 1.0,
        }.get(getattr(self, '_current_emotion', 'neutral'), 1.0)

    def _reposition_status_label(self):
        """将状态指示器放在窗口右下角"""
        sw = self._status_label.width()
        sh = self._status_label.height()
        self._status_label.move(self.width() - sw - 6, self.height() - sh - 6)

    def _restore_status_label(self):
        """穿透提示后恢复为状态指示，然后淡出隐藏"""
        self._update_status_indicator(self._hanako_monitor.current_state_name)
        # 3 秒后淡出隐藏
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, self._status_label.hide)

    def _update_status_indicator(self, state_name: str):
        """更新持久化状态指示器"""
        from core.hanako_monitor import STATE_LABELS
        label = STATE_LABELS.get(state_name, f"⚪ {state_name}")

        # ── P3 换装：已装备外观的图标拼到状态栏（🧣 等）──
        try:
            equip = getattr(self, "_equipped_costume_icons", None)
            if equip is None:
                # 从 save 管理器读取一次并缓存
                save_mgr = getattr(self, "_save_mgr", None)
                icons = ""
                if save_mgr is not None:
                    try:
                        s = save_mgr.save
                        costumes = dict(getattr(s, "equipped_costumes", {}) or {})
                        # 图标映射：costume_id -> emoji（缺省用 🎽）
                        icon_map = {
                            "scarf": "🧣",
                            "hat": "🎩",
                            "glasses": "👓",
                            "crown": "👑",
                            "wings": "🪽",
                            "tail": "🐾",
                        }
                        icons = " ".join(
                            icon_map.get(cid, "🎽") for cid in costumes if costumes[cid]
                        )
                    except Exception:
                        icons = ""
                self._equipped_costume_icons = icons
            if self._equipped_costume_icons:
                label = f"{self._equipped_costume_icons} {label}"
        except Exception:
            pass

        self._status_label.setText(label)

        # 状态颜色映射
        colors = {
            "idle": ("#aaaacc", "rgba(30,30,50,200)"),
            "listening": ("#88dd88", "rgba(30,60,30,200)"),
            "thinking": ("#ddcc66", "rgba(60,50,20,200)"),
            "working": ("#6699ff", "rgba(20,40,80,200)"),
            "speaking": ("#88bbff", "rgba(20,50,80,200)"),
        }
        tc, bg = colors.get(state_name, colors["idle"])
        self._status_label.setStyleSheet(f"""
            QLabel {{
                background: {bg};
                color: {tc};
                border: 1px solid {tc}40;
                border-radius: 10px;
                font-size: 9px;
                padding: 2px 6px;
            }}
        """)
        self._status_label.show()
        self._reposition_status_label()
        # idle 状态：标签仅短暂提示，3 秒后淡出，避免与养成面板在角落重复常驻
        if state_name == "idle":
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self._status_label.hide)
