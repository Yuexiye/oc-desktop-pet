"""NurturingMixin — 桌宠养成（喂食 / 工作 / 任务菜单 / 状态摘要）。

由 PetWindow 多重继承。访问 self._item_registry / self._work_registry /
self._save_mgr / self._work_timer / self._mission_mgr / self._mission_submenu /
self._menu / self._show_bubble / self._set_anim_seq 等，均由 PetWindow 提供（鸭子类型）。

跨 mixin 依赖（鸭子类型）：_open_gacha_ui / _open_gacha_multi_ui / _open_collection_book
（GachaMixin）、_toggle_status_hud_from_menu（StatusHudMixin）。

拆分自 pet.py 的养成区块（原 982-1303 行），降低 PetWindow 体积。
"""
import logging

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from config import get_transition_style
from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class NurturingMixin:
    """养成逻辑：喂食、工作、任务菜单、状态摘要。"""

    # ── 养成菜单注入 ──

    def _build_nurturing_menu(self):
        """在右键菜单里注入"喂食 / 工作 / 状态"子菜单

        设计点：
        - 仅在 _save_mgr / _item_registry / _work_registry 都已注入时执行
        - 子菜单插到"❌ 退出"前一项
        - 失败不摊牌——hasattr / try 已在调用处守护
        """
        if not hasattr(self, '_item_registry') or self._item_registry is None:
            return
        if not hasattr(self, '_work_registry') or self._work_registry is None:
            return
        if not hasattr(self, '_save_mgr') or self._save_mgr is None:
            return

        try:
            # 复用现有 styleSheet，保持视觉一致
            menu_style = self._menu.styleSheet()

            # 找到退出 action 作为锚点
            quit_action = None
            for act in self._menu.actions():
                txt = act.text() or ""
                if txt.startswith("❌"):
                    quit_action = act
                    break

            # 状态摘要（可勾选，明确当前是否 pinned 常驻）
            status_act = QAction("📊 状态", self._menu)
            status_act.setCheckable(True)
            status_act.toggled.connect(self._toggle_status_hud_from_menu)
            self._status_menu_action = status_act
            if quit_action:
                self._menu.insertAction(quit_action, status_act)
            else:
                self._menu.addAction(status_act)

            # 喂食子菜单
            self._feed_menu = QMenu(self._menu)
            self._feed_menu.setTitle("🍎 喂食")
            if menu_style:
                self._feed_menu.setStyleSheet(menu_style)
            for item in self._item_registry.all():
                act = self._feed_menu.addAction(
                    f"{item.icon} {item.name} ({item.price:.0f}G)"
                )
                act.triggered.connect(lambda checked=False, it=item: self._feed_item(it))
            if quit_action:
                self._menu.insertMenu(quit_action, self._feed_menu)
            else:
                self._menu.addMenu(self._feed_menu)

            # 工作子菜单
            self._work_menu = QMenu(self._menu)
            self._work_menu.setTitle("💼 工作")
            if menu_style:
                self._work_menu.setStyleSheet(menu_style)
            available = self._work_registry.available(self._save_mgr.save.level)
            if available:
                for work in available:
                    act = self._work_menu.addAction(
                        f"{work.icon} {work.name}"
                    )
                    act.triggered.connect(lambda checked=False, w=work: self._start_work(w))
            else:
                empty = self._work_menu.addAction("（暂无可用工作）")
                empty.setEnabled(False)
            if quit_action:
                self._menu.insertMenu(quit_action, self._work_menu)
            else:
                self._menu.addMenu(self._work_menu)
        except Exception:
            logger.exception("_build_nurturing_menu failed")

    # ── 喂食 / 工作 ──

    def _feed_item(self, item):
        """使用一个物品——写挂起池 + 切动画 + 飘字

        物品效果走挂起池（core.items.item.use_item），回流由 state_mgr.tick 完成。
        """
        if not hasattr(self, '_save_mgr') or not self._save_mgr:
            return
        try:
            from core.items.item import use_item
            result = use_item(self._save_mgr.save, item)
            EventBus.emit("item_used", target=item.id)
        except Exception:
            logger.exception("_feed_item use_item failed")
            self._show_bubble("吃不动...", emotion="sad")
            return
        try:
            self._show_bubble(f"{item.icon} {item.name}", emotion="happy")
            self._set_anim_seq(
                result["graph"], emotion="happy",
                style=get_transition_style("happy"),
            )
        except Exception:
            logger.exception("_feed_item UI update failed")

    def _start_work(self, work):
        """开始一项工作——切到工作动画

        WorkTimer 在自己后台线程里跑，UI 只管通知。
        结算回调由 _work_timer._on_finish 在构造函数已绑定时接管；
        这里再覆盖一遍保险（双绑定按最新为准）。
        """
        if not hasattr(self, '_work_timer') or not self._work_timer:
            return
        try:
            success = self._work_timer.start_work(work)
        except Exception:
            logger.exception("_start_work failed")
            success = False
            self._show_bubble("出错了...", emotion="sad")
            return

        # 覆盖 callback 为本窗口的响应（多 agent 共享 work_timer 时，
        # 每个窗口都会重绑——这里用闭包锚定 self）
        try:
            self._work_timer._on_finish = self._on_work_finish
        except Exception:
            pass

        if success:
            try:
                self._show_bubble(f"{work.icon} 开始{work.name}...", emotion="thinking")
                self._set_anim_seq(
                    work.working_graph, emotion="thinking",
                    style=get_transition_style("thinking"),
                )
            except Exception:
                logger.exception("_start_work UI update failed")
        else:
            self._show_bubble("现在没法工作...", emotion="sad")

    def _on_work_finish(self, info):
        """WorkTimer 完成回调（后台线程）→ 切到主线程"""
        try:
            QTimer.singleShot(0, lambda: self._do_work_finish(info))
        except Exception:
            logger.exception("_on_work_finish schedule failed")

    def _do_work_finish(self, info):
        """工作完成主线程 UI 更新"""
        try:
            reason = getattr(info, "reason", "")
            # 任务系统：工作完成事件（仅成功完成计入）
            if reason == "complete" and getattr(self, "_mission_mgr", None) is not None:
                try:
                    from core.event_bus import EventBus
                    EventBus.emit(
                        "work_completed",
                        work_id=getattr(getattr(info, "work", None), "id", ""),
                        duration=getattr(info, "duration", 0),
                    )
                except Exception:
                    logger.debug("work_completed emit failed", exc_info=True)
            if reason == "complete":
                self._show_bubble(
                    f"完成啦！+{info.money:.0f}💰 +{info.exp:.0f}⭐",
                    emotion="happy",
                )
                self._set_anim_seq(
                    info.work.complete_graph, emotion="happy",
                    style=get_transition_style("happy"),
                )
            elif getattr(info, "reason", "") == "state_fail":
                self._show_bubble("太累了，干不动了...", emotion="sad")
                try:
                    self._set_anim_seq("failed", emotion="sad")
                except Exception:
                    pass
            else:
                # manual_stop 等其他原因——不飘字
                pass
        except Exception:
            logger.exception("_do_work_finish failed")

    def _on_mission_completed_bubble(self, mission_id="", name="", rewards=None):
        """任务完成通知（事件总线回调，主线程）"""
        try:
            if name:
                self._show_bubble(f"任务完成！{name} 🎉", emotion="happy", priority=0)
        except Exception:
            logger.debug("mission_completed bubble failed", exc_info=True)

    # ── 任务系统 UI（03 成长计划） ──

    def _emit_level_up(self, old_level: int, new_level: int):
        """升级事件发射（由 PetSaveManager.on_level_up 回调，可能处于任意线程）

        用 QTimer 延迟到事件循环空闲时发射，彻底切断"奖励结算 -> add_exp -> 升级 ->
        再发射"的同步递归链；非 GUI 环境（冒烟测试）降级为立即发射。
        """
        try:
            from core.event_bus import EventBus
        except Exception:
            return
        payload = {"level": new_level, "old_level": old_level}

        def _fire():
            EventBus.emit("level_up", **payload)

        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, _fire)
        except Exception:
            _fire()

    def _build_mission_menu(self):
        """创建「📋 任务」子菜单（只在任务系统可用时）"""
        if not getattr(self, '_mission_mgr', None):
            return
        if not hasattr(self, '_menu') or self._menu is None:
            return
        try:
            from PySide6.QtWidgets import QMenu
            self._mission_submenu = QMenu("📋 任务", self._menu)
            self._mission_submenu.setStyleSheet(self._menu.styleSheet())
            quit_action = None
            for act in self._menu.actions():
                if (act.text() or "").startswith("❌"):
                    quit_action = act
                    break
            if quit_action:
                self._menu.insertMenu(quit_action, self._mission_submenu)
            else:
                self._menu.addMenu(self._mission_submenu)
            self._refresh_mission_menu()
        except Exception:
            logger.exception("_build_mission_menu failed")

    def _refresh_mission_menu(self):
        """每次右键菜单弹出前刷新任务子菜单（进度/盲盒资源实时）"""
        mm = getattr(self, '_mission_mgr', None)
        sub = getattr(self, '_mission_submenu', None)
        if mm is None or sub is None or not hasattr(self, '_save_mgr') or not self._save_mgr:
            return
        try:
            sub.clear()
            s = self._save_mgr.save
            energy = float(getattr(s, 'gacha_energy', 0) or 0)
            tickets = int(getattr(s, 'gacha_tickets', 0) or 0)
            status = mm.get_gacha_status()
            pity_left = status.get("pity_left", 0)
            pity_total = status.get("pity_total", 10)
            can_ten = (energy >= status["cost_energy"] * 10) or (tickets >= status["cost_tickets"] * 10)

            # 盲盒入口（单抽 + 十连 + 保底进度）
            gacha_act = sub.addAction(
                f"🎁 开盲盒（能量 {energy:.0f} / 票 {tickets}）保底 再{pity_left}抽")
            gacha_act.triggered.connect(self._open_gacha_ui)
            ten_act = sub.addAction("🎰 十连抽（×10）")
            ten_act.setEnabled(can_ten)
            ten_act.triggered.connect(self._open_gacha_multi_ui)
            book_act = sub.addAction("📖 图鉴")
            book_act.triggered.connect(self._open_collection_book)
            sub.addSeparator()

            active = mm.get_active()
            if not active:
                tip = sub.addAction("（暂无任务）")
                tip.setEnabled(False)
            for m, p in active:
                parts = []
                for i, c in enumerate(m.conditions):
                    got = p.condition_progress[i] if i < len(p.condition_progress) else 0
                    parts.append(f"{min(got, c.count)}/{c.count}")
                prog_str = "  ".join(parts)
                mark = "✅" if p.completed else "▫️"
                act = sub.addAction(f"{mark} {m.name}  [{prog_str}]")
                act.setEnabled(not p.completed)
                act.triggered.connect(
                    lambda checked=False, mid=m.id: self._show_mission_detail(mid)
                )
        except Exception:
            logger.exception("_refresh_mission_menu failed")

    def _show_mission_detail(self, mission_id: str):
        """点击某任务 -> 气泡展示描述与进度"""
        mm = getattr(self, '_mission_mgr', None)
        if mm is None:
            return
        try:
            m = mm._pool.get_mission(mission_id)
            p = mm._pool.get_progress(mission_id)
            if m is None:
                return
            parts = []
            for i, c in enumerate(m.conditions):
                got = p.condition_progress[i] if i < len(p.condition_progress) else 0
                parts.append(f"{min(got, c.count)}/{c.count}")
            status = "已完成 ✅" if p.completed else "进行中"
            self._show_bubble(f"{m.name} · {status}\n{m.description}  [{ '  '.join(parts) }]", emotion="neutral")
        except Exception:
            logger.debug("show mission detail failed", exc_info=True)

    def _show_status_summary(self):
        """在气泡里显示养成属性概要"""
        if not hasattr(self, '_save_mgr') or not self._save_mgr:
            return
        try:
            s = self._save_mgr.save
            text = (
                f"❤{s.health:.0f} 💪{s.stamina:.0f} "
                f"🍖{s.hunger:.0f} 💧{s.thirst:.0f} "
                f"😊{s.mood:.0f} Lv.{s.level} 💰{s.money:.0f}"
            )
            self._show_bubble(text, emotion="neutral")
        except Exception:
            logger.exception("_show_status_summary failed")