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
import threading
import time

from PySide6.QtCore import QThread

from config import get_transition_style

logger = logging.getLogger(__name__)


class BubbleMixin:
    """气泡显示与 Hanako 状态呈现。"""

    def _show_bubble(self, text: str, emotion: str = "neutral", priority: int = 0, duration_ms: int = 0):
        """显示消息气泡 —— 线程安全入口。

        MultiPetBridge 的 dispatcher 线程（pet_enter 事件 -> mission_tracker
        判定任务完成）会直接调到这里。而实现里要 self._bubble_timer.start()，
        从非主线程调用时 Qt 会拒绝：
            QObject::startTimer: Timers cannot be started from another thread
        比警告更糟的是定时器压根没起来，气泡就再也不会自动隐藏。
        所以这里先做线程判定，跨线程一律走信号绕回主线程。

        duration_ms > 0 时覆盖默认时长（如缩放提示 1500ms 短气泡）。
        """
        if not text or not hasattr(self, 'bubble'):
            return
        if QThread.currentThread() is not self.thread():
            self.bubble_signal.emit(str(text), str(emotion), int(priority))
            return
        self._show_bubble_impl(text, emotion, priority, duration_ms)

    def _bubble_duration(self, text: str) -> int:
        """根据文本长度计算气泡显示时长（base 10s + 字数系数，封顶 30s）。"""
        base = 10000
        n = len(text or '')
        extra = int(n / 8) * 1000   # 每 8 字加 1s
        return min(base + extra, 30000)

    def _show_bubble_impl(self, text: str, emotion: str = "neutral", priority: int = 0, duration_ms: int = 0):
        """气泡实现体（仅限主线程调用；相同内容不重复刷新，高优先级不被低优先级覆盖）

        duration_ms > 0 时覆盖默认时长（如缩放提示短气泡）。
        """
        if not text or not hasattr(self, 'bubble'):
            return
        # P0-2: 去重逻辑——2 秒内相同文本只打印一次日志
        now = time.time()
        if text == getattr(self, '_last_bubble_text', '') and (now - getattr(self, '_last_bubble_time', 0)) < 2.0:
            logger.debug("Bubble dedupe: same text within 2s, skipping log")
            self._bubble_timer.start(duration_ms if duration_ms > 0 else self._bubble_duration(text))  # 只续期
            return
        self._last_bubble_text = text
        self._last_bubble_time = now
        # 节流：相同内容且气泡可见时不重复设置
        if text == self._bubble_message and self.bubble.isVisible():
            logger.debug("Bubble throttle: same text still visible")
            self._bubble_timer.start(duration_ms if duration_ms > 0 else self._bubble_duration(text))  # 只续期
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
            self._bubble_timer.start(duration_ms if duration_ms > 0 else self._bubble_duration(text))
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
        # 模型动作子菜单：每次弹出前按当前模型动态重建
        if hasattr(self, '_motion_submenu'):
            self._rebuild_motion_menu()
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
        # G：celebrating 分支（在 safe_anims 收窄之前 return；开关关闭则降级旧 happy）。
        # 关键约束：只走 AvatarRenderer 统一接口（_status_mapper.render_for），
        # 绝不 import/触碰渲染器内部实现，不新增渲染线程。
        # P2 节流：避免短时间内多次 tool_end 并发触发 celebrating
        if state == "celebrating":
            now = time.time()
            last_celeb = getattr(self, "_last_celebrating_at", 0.0)
            if now - last_celeb < 5.0:  # 5s 内只触发一次 celebrating
                logger.debug("celebrating 节流：距离上次 %.1fs < 5s，跳过", now - last_celeb)
                return
            self._last_celebrating_at = now
            
            celeb_cfg = self.config.get("celebrating", {}) or {}
            if celeb_cfg.get("enabled", True):
                try:
                    self._update_status_indicator("celebrating")
                except Exception:
                    pass
                self._do_celebrating()
                return
            # 开关关闭 → 恢复旧 happy 行为（mood=happy, anim=waving）
            state = "happy"
            anim_name = "waving"
            emotion = "happy"
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
        # P1 修复：turn_end 走 push_event 直通 idle，若 _current_anim 已是 idle 会跳过
        # 下方 _set_anim_seq，导致前倾/脸红等贴图表情残留。state==idle 统一走
        # renderer._force_idle()（ResetExpressions + FORCE 优先级强制回 idle）。
        try:
            if state == "idle":
                renderer = getattr(self, "_renderer", None)
                if renderer is not None and hasattr(renderer, "_force_idle"):
                    renderer._force_idle()
                self._current_anim = "idle"
            elif anim_name != self._current_anim:
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

    # ── G：celebrating（庆祝态）主线程实现 ──

    def _do_celebrating(self):
        """G celebrating：撒花动作 + 3s 情绪表情 + 气泡 + 完工音 + 3s 后回 idle。

        硬约束：只通过 AvatarRenderer 统一接口（PetStatusMapper.render_for）驱动，
        不 import/触碰渲染器内部实现（Live2D C 层/渲染线程），不新增渲染线程。
        TTS 合成在后台线程，播放经 tts_celebration_signal 回主线程（绝不直接碰 Qt）。
        """
        # 并发防护：上一次庆祝未结束（3s revert 计时器未到/合成线程未收尾）时
        # 跳过重复触发，避免同一批 tool_end 产生多条庆祝序列和多个合成线程。
        if getattr(self, "_celebration_in_progress", False):
            logger.debug("celebrating 进行中，跳过重复触发")
            return
        # 时间节流兜底（_do_hanako_state 已节流，这里防御其他入口直调）
        _now = time.time()
        if _now - getattr(self, "_last_celebrating_at", 0.0) < 5.0:
            logger.debug("celebrating 节流：5s 内已触发，跳过")
            return
        self._last_celebrating_at = _now
        self._celebration_in_progress = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: setattr(self, "_celebration_in_progress", False))
        # 1. 双形态撒花动作（统一接口）
        try:
            mapper = getattr(self, "_status_mapper", None)
            if mapper is not None and hasattr(self, "_renderer"):
                mapper.render_for("celebrating", self._renderer)
        except Exception:
            pass
        # 2. 情绪/表情脸（3s 过期，复用现有机制）
        try:
            self._set_surface_emotion("happy", duration_ms=3000, source="celebrating")
        except Exception:
            pass
        # 3. 气泡（完工反馈）
        try:
            self._show_bubble("完成啦！", emotion="happy", priority=1)
        except Exception:
            pass
        # 4. TTS 完工音：走现有 tts provider 管道，非阻塞（后台合成 → 信号回主线程）
        try:
            celeb_cfg = self.config.get("celebrating", {}) or {}
            tts_cfg = self.config.get("tts", {}) or {}
            if celeb_cfg.get("tts_enabled", True) and tts_cfg.get("enabled", True):
                threading.Thread(target=self._synth_celebration_tts, daemon=True).start()
        except Exception:
            pass
        # 5. 3s 后回 idle（复用 _pet_revert_timer，现有防御已覆盖 Live2D 手势超时）
        try:
            self._pet_revert_timer.stop()
            self._pet_revert_timer.start(3000)
        except Exception:
            pass

    def _synth_celebration_tts(self):
        """后台线程：合成完工音 → 信号回主线程播放（绝不直接碰 Qt/渲染）。

        合成不在主线程执行（cosyvoice 等 provider 的重型链路会冻住事件循环）；
        播放经 tts_celebration_signal（Qt Signal）自动转主线程。
        """
        try:
            provider = None
            engine = getattr(self, "_engine", None)
            if engine is not None:
                provider = getattr(engine, "_tts", None) or getattr(engine, "_tts_provider", None)
            if provider is None:
                provider = getattr(self, "_tts_provider", None)
            if provider is None or not hasattr(provider, "synthesize"):
                return
            # P2-7: 完工音也按角色音色解析（空 = provider 默认；失败同样回退）
            _voice = ""
            _resolver = getattr(self, "_resolve_tts_voice", None)
            if callable(_resolver):
                try:
                    _voice = _resolver(getattr(self, "_current_char", ""), "happy") or ""
                except Exception:
                    _voice = ""
            audio = provider.synthesize(
                "完成啦！", character_id=getattr(self, "_current_char", ""), voice=_voice,
            )
            if audio and os.path.exists(audio):
                self.tts_celebration_signal.emit(audio)
        except Exception as e:
            logger.debug("完工音合成失败（忽略）: %s", e)

    def _do_tts_celebration(self, audio_path: str):
        """主线程槽：播放完工音（TTS 合成已在后台线程完成）。"""
        try:
            if not audio_path or not os.path.exists(audio_path):
                return
            tts_cfg = self.config.get("tts", {}) or {}
            if not tts_cfg.get("enabled", True):
                return
            self._tts_player.stop()
            self._last_tts_emotion = "happy"
            self._tts_player.play(audio_path)
        except Exception as e:
            logger.debug("完工音播放失败（忽略）: %s", e)