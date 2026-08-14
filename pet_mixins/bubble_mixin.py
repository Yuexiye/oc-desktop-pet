"""BubbleMixin — 桌宠气泡 / Hanako 状态呈现。

由 PetWindow 多重继承。访问 self.bubble / self._bubble_timer / self._bubble_message /
self._bubble_priority / self._pending_bubbles / self._reposition_bubble /
self._tts_player / self._perception / self._update_status_indicator / self._set_anim_seq /
self._current_anim / self._current_emotion 等，均由 PetWindow 提供（鸭子类型）。

线程安全：_show_bubble 是线程安全入口，跨线程调用自动走信号绕回主线程
（MultiPetBridge 的 dispatcher 线程会直接调它）。

拆分自 pet.py 的气泡/Hanako 区块（原 1512-1640 行），降低 PetWindow 体积。
"""
import logging
import os
import time

from PySide6.QtCore import QThread

from config import get_transition_style

logger = logging.getLogger(__name__)


class BubbleMixin:
    """气泡显示与 Hanako 状态呈现。"""

    def _show_bubble(self, text: str, emotion: str = "neutral", priority: int = 0):
        """显示消息气泡 —— 线程安全入口。

        MultiPetBridge 的 dispatcher 线程（pet_enter 事件 -> mission_tracker
        判定任务完成）会直接调到这里。而实现里要 self._bubble_timer.start()，
        从非主线程调用时 Qt 会拒绝：
            QObject::startTimer: Timers cannot be started from another thread
        比警告更糟的是定时器压根没起来，气泡就再也不会自动隐藏。
        所以这里先做线程判定，跨线程一律走信号绕回主线程。
        """
        if not text or not hasattr(self, 'bubble'):
            return
        if QThread.currentThread() is not self.thread():
            self.bubble_signal.emit(str(text), str(emotion), int(priority))
            return
        self._show_bubble_impl(text, emotion, priority)

    def _bubble_duration(self, text: str) -> int:
        """根据文本长度计算气泡显示时长（base 10s + 字数系数，封顶 30s）。"""
        base = 10000
        n = len(text or '')
        extra = int(n / 8) * 1000   # 每 8 字加 1s
        return min(base + extra, 30000)

    def _show_bubble_impl(self, text: str, emotion: str = "neutral", priority: int = 0):
        """气泡实现体（仅限主线程调用；相同内容不重复刷新，高优先级不被低优先级覆盖）"""
        if not text or not hasattr(self, 'bubble'):
            return
        # P0-2: 去重逻辑——2 秒内相同文本只打印一次日志
        now = time.time()
        if text == getattr(self, '_last_bubble_text', '') and (now - getattr(self, '_last_bubble_time', 0)) < 2.0:
            logger.debug("Bubble dedupe: same text within 2s, skipping log")
            self._bubble_timer.start(self._bubble_duration(text))  # 只续期
            return
        self._last_bubble_text = text
        self._last_bubble_time = now
        # 节流：相同内容且气泡可见时不重复设置
        if text == self._bubble_message and self.bubble.isVisible():
            logger.debug("Bubble throttle: same text still visible")
            self._bubble_timer.start(self._bubble_duration(text))  # 只续期
            return
        # 高优先级正在显示时，低优先级先排队
        if self.bubble.isVisible() and self._bubble_priority > priority:
            logger.debug("Bubble queued (priority %d < current %d): %s", priority, self._bubble_priority, text[:40])
            self._pending_bubbles.append((text, emotion, priority))
            return
        try:
            self._is_thinking = False
            self._bubble_message = text
            self._bubble_priority = priority
            log_level = "debug" if text == "⏳ 思考中..." else "info"
            getattr(logger, log_level)("Showing bubble: %s [emotion=%s]", text[:80], emotion)
            self.bubble.set_text(text, bright=(emotion == "happy"))
            self._reposition_bubble()
            self.bubble.show()
            self.bubble.raise_()
            self._bubble_timer.start(self._bubble_duration(text))
        except Exception:
            logger.exception("Show bubble failed")

    # ── 右键菜单 ──

    def _show_context_menu(self, pos):
        """右键菜单"""
        self._mark_user_interaction()
        if not hasattr(self, '_menu'):
            return
        # 更新动态部分
        if hasattr(self, '_behavior_actions'):
            for mode, a in self._behavior_actions.items():
                a.setChecked(mode == self._behavior_mode)
        if hasattr(self, '_action_menu_items') and hasattr(self, '_action_linker'):
            highlighted = self._action_linker.highlighted_actions
            for aid, a in self._action_menu_items.items():
                a.setVisible(aid in highlighted)
        if hasattr(self, '_passthrough_action'):
            self._passthrough_action.setChecked(self._mousePassthrough)
        # M4: 根据 transport_mode 决定是否启用"新对话"入口
        if hasattr(self, '_new_session_action'):
            hanako_mode = False
            try:
                if self._engine and self._engine._adapter:
                    hanako_mode = getattr(self._engine._adapter, 'transport_mode', 'direct') != 'direct'
            except Exception:
                hanako_mode = False
            self._new_session_action.setVisible(hanako_mode)
        # 任务系统：每次弹出前刷新进度 / 盲盒资源
        if hasattr(self, '_refresh_mission_menu'):
            self._refresh_mission_menu()
        try:
            self._menu.popup(self.mapToGlobal(pos))
        except Exception:
            pass

    # ── Hanako 状态呈现 ──

    def _on_hanako_state(self, anim_name: str, message: str, emotion: str = "neutral", state: str = "idle", audio_path: str = ""):
        """Hanako 状态变化回调 — 从 WS 后台线程调用，通过信号切主线程"""
        self.hanako_state_signal.emit(anim_name, message, emotion, state, audio_path)

    def _do_hanako_state(self, anim_name: str, message: str, emotion: str, state: str, audio_path: str):
        """在主线程处理 Hanako 状态变化"""
        try:
            self._update_status_indicator(state)
        except Exception:
            pass

        # P2: 触发情绪状态机
        if emotion and emotion != "neutral":
            try:
                self._perception.trigger_emotion(emotion)
            except Exception:
                pass

        # 1. 消息气泡
        show_text = message.strip()
        if show_text:
            try:
                tts_cfg = self.config.get("tts", {})
                if tts_cfg.get("enabled", True) and audio_path:
                    if os.path.exists(audio_path):
                        logger.info("Playing TTS: %s", audio_path)
                        self._last_tts_emotion = emotion or "neutral"
                        self._tts_player.play(audio_path)
                    else:
                        logger.warning("TTS audio not found: %s", audio_path)
                else:
                    if not audio_path:
                        logger.debug("No audio_path in response")
                bubble_priority = 1 if state == "speaking" and show_text else 0
                self._show_bubble(show_text, emotion=emotion, priority=bubble_priority)
            except Exception as e:
                logger.warning("TTS/bubble error: %s", e)

        # 2. 动画(P3: 传递 emotion,支持帧区间)
        # 收窄：surprised/angry 不切瞪眼帧，避免高频瞪眼（只保留气泡情绪）
        try:
            if anim_name != self._current_anim:
                safe_anims = ['idle', 'walk', 'extra']
                if anim_name not in safe_anims:
                    anim_name = 'idle'
                if emotion in ('surprised', 'angry'):
                    anim_name = 'idle'
                self._current_anim = anim_name
                self._set_anim_seq(anim_name, emotion=emotion, style=get_transition_style(emotion))
        except Exception:
            pass

        # A2: 情绪过期 — 重置计时器
        self._current_emotion = emotion or "neutral"
        if self._current_emotion != "neutral":
            self._emotion_expiry_timer.start(3000)
        else:
            self._emotion_expiry_timer.stop()

        # 3. 动作联动
        if state in ("working", "listening") and self._action_linker.enabled:
            try:
                self._action_linker.check()
            except Exception:
                pass

        # 4. 重置状态(当收到 Agent 回复时)
        if state == "speaking" and message and self._pending_chat:
            # 重置跟踪
            self._pending_user_msg = ""
            self._pending_emotion = "neutral"
            self._pending_chat = False

        # 5. Hanako 自身状态变化不算用户活动；否则会取消正在生成的闲聊。
        idle_chatter = getattr(self, "_idle_chatter", None)
        if not idle_chatter or not idle_chatter.is_running:
            self._idle_stage = None
            self._last_interaction = time.time()