"""GachaMixin — 抽卡与图鉴（已正常接入 mission_mgr，右键菜单入口可用）。

由 PetWindow 多重继承。方法体内访问 self._mission_mgr / self._show_bubble 等均由
PetWindow 提供。涉及的 UI 类从 ui.* 导入（无循环依赖）。
"""
import logging

from ui.gacha_reveal import GachaReveal, GachaRevealMulti
from ui.collection_book import CollectionBook
from ui.gacha_sound import play_reveal

logger = logging.getLogger(__name__)


class GachaMixin:
    """抽卡（单抽/十连）+ 图鉴入口。"""

    def _open_gacha_ui(self):
        """右键菜单「开盲盒」（单抽）"""
        self._do_open_gacha(1)

    def _open_gacha_multi_ui(self):
        """右键菜单「十连抽」（×10）"""
        self._do_open_gacha(10)

    def _do_open_gacha(self, count: int):
        mm = getattr(self, '_mission_mgr', None)
        if mm is None:
            return
        try:
            items = mm.open_gacha(count=count)
        except Exception:
            logger.exception("open_gacha failed")
            items = None
        if not items:
            self._show_bubble("能量或盲盒票不足 🎰", emotion="sad")
            return
        if count <= 1:
            self._show_gacha_reveal(items[0])
        else:
            self._show_gacha_reveal_multi(items)

    def _gacha_pity_text(self) -> str | None:
        mm = getattr(self, '_mission_mgr', None)
        if not mm:
            return None
        try:
            st = mm.get_gacha_status()
            return f"保底进度 {st['pity_total'] - st['pity_left']}/{st['pity_total']}"
        except Exception:
            return None

    def _show_gacha_reveal(self, item):
        """开盲盒揭晓演出（稀有度分级、缩放淡入、自动消失）"""
        try:
            reveal = GachaReveal(
                icon=getattr(item, "icon", "🎁"),
                name=getattr(item, "name", "神秘物品"),
                rarity_value=getattr(item.rarity, "value", "common"),
                pity_text=self._gacha_pity_text(),
            )
            # 保持引用，防止局部变量释放导致动画/窗口被 GC
            active = getattr(self, "_active_reveals", None)
            if active is None:
                active = []
                self._active_reveals = active
            active.append(reveal)
            reveal.show()
            try:
                play_reveal(getattr(item.rarity, "value", "common"))
            except Exception:
                logger.exception("gacha sound failed")
            # 动画结束后释放引用
            reveal._op_out.finished.connect(lambda: self._release_reveal(reveal))
        except Exception:
            logger.exception("gacha reveal failed")
            self._show_bubble(f"抽中：{item.name} {item.icon}", emotion="happy")

    def _release_reveal(self, reveal):
        """揭晓动画关闭后从活跃列表移除引用"""
        active = getattr(self, "_active_reveals", None)
        if active and reveal in active:
            try:
                active.remove(reveal)
            except ValueError:
                pass

    def _show_gacha_reveal_multi(self, items):
        """十连抽揭晓（网格演出）"""
        try:
            reveal = GachaRevealMulti(items, pity_text=self._gacha_pity_text())
            active = getattr(self, "_active_reveals", None)
            if active is None:
                active = []
                self._active_reveals = active
            active.append(reveal)
            reveal.show()
            reveal._op_out.finished.connect(lambda: self._release_reveal(reveal))
        except Exception:
            logger.exception("gacha multi reveal failed")
            names = "、".join(getattr(it, "name", "?") for it in items)
            self._show_bubble(f"十连抽中：{names}", emotion="happy")

    def _open_collection_book(self):
        """右键菜单「图鉴」— 物品收集册"""
        mm = getattr(self, '_mission_mgr', None)
        if mm is None:
            return
        try:
            data = mm.get_collection()
            book = CollectionBook(data["all"], data["collected"])
            book.show()
        except Exception:
            logger.exception("open collection book failed")
