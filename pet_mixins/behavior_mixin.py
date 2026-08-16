"""BehaviorMixin — 桌宠行为（用户交互标记 / 空闲/自言自语 / 前台 / 鼠标反应 / 屏幕感知）。

由 PetWindow 多重继承。访问 self._engine / self._perception / self._renderer /
self._physics / self._mouse_tracker / self._hanako_monitor / self._emotion_face /
self._state_mgr / self._pet_revert_timer / self.bubble / self._mouse_reaction_params /
self._show_bubble / self._set_anim_seq / self._set_surface_emotion / self._reposition_bubble
等，均由 PetWindow 提供（鸭子类型，无需 import pet）。

依赖方法（同样由 PetWindow / 其他 mixin 提供）：
  _mark_user_interaction / _show_bubble / _set_anim_seq / _set_surface_emotion /
  _pet_play_happy / _reposition_bubble / get_pos / _current_screen_geometry

拆分自 pet.py 的行为/感知区块（原 1965-2370 行），降低 PetWindow 体积。
"""
import logging
import random
import time

from PySide6.QtCore import QTimer

from config import EXPRESSION_MAP, get_transition_style
from core.event_bus import EventBus
from core.perception.scenarios import get_bubble_emotion_for_prompt

logger = logging.getLogger(__name__)


class BehaviorMixin:
    """行为逻辑：用户交互标记、空闲/自言自语、前台切换、鼠标反应、屏幕感知。"""

    # 鼠标反应冷却
    _mouse_reaction_cooldown: float = 0.0

    # ── 用户交互标记 ──

    def _mark_user_interaction(self):
        """记录用户活动，并让未送达的自言自语失效。"""
        self._last_interaction = time.time()
        self._last_user_interaction_mono = time.monotonic()
        self._idle_stage = None
        idle_chatter = getattr(self, "_idle_chatter", None)
        if idle_chatter:
            idle_chatter.reset()
        # 轻存在感：用户交互（拖拽/点击/对话/右键）重置空闲计时
        presence = getattr(self, "_presence", None)
        if presence is not None:
            try:
                presence.mark_interaction()
            except Exception:
                pass

    # ── 空闲自言自语 ──

    def _can_idle_chatter(self) -> bool:
        """仅在桌宠和对话链都空闲时允许生成自言自语。"""
        idle_chatter = getattr(self, "_idle_chatter", None)
        if not idle_chatter or not idle_chatter.enabled:
            return False
        if time.monotonic() - self._last_user_interaction_mono < idle_chatter.min_interval_sec:
            return False
        if not self.isVisible() or self._is_thinking or self._pending_chat:
            return False
        if getattr(self, "_voice_recording", False):
            return False
        if hasattr(self, "input_widget") and self.input_widget.isVisible():
            return False
        if hasattr(self, "_tts_player") and self._tts_player.is_playing():
            return False
        hanako_state = getattr(self._hanako_monitor, "current_state_name", "idle")
        if hanako_state in {"listening", "thinking", "working", "speaking"}:
            return False
        return True

    def _do_idle_chatter(self, text: str, emotion: str):
        """在 Qt 主线程显示自言自语，并应用情绪动画。"""
        if not text or not self._can_idle_chatter():
            logger.debug("Discarded stale idle chatter")
            return

        emotion = emotion or "neutral"
        self._show_bubble(text, emotion=emotion)
        anim = EXPRESSION_MAP.get(emotion, EXPRESSION_MAP["neutral"])[0]
        # 收窄：surprised/angry 不切瞪眼帧，避免空闲自言自语高频瞪眼
        if emotion in ("surprised", "angry"):
            anim = "idle"
        self._set_anim_seq(anim, emotion=emotion, style=get_transition_style(emotion))

        self._current_emotion = emotion
        if emotion != "neutral":
            self._emotion_expiry_timer.start(3000)
        else:
            self._emotion_expiry_timer.stop()
        logger.info("Idle chatter: %s [emotion:%s]", text, emotion)

    # ── 闲置检测 + 关怀提醒 ──

    def _break_check(self):
        """每 30 秒检查: idle 感知 + proactive 主动对话"""
        logger.debug("_break_check called")
        try:
            self._break_check_inner()
        except Exception as e:
            logger.error("_break_check error: %s", e)

    def _break_check_inner(self):
        now = time.time()
        idle_secs = now - self._last_interaction

        # idle 回归检测（用户回来时打招呼）
        if self._idle_stage is not None and idle_secs < 10:
            going = self._idle_stage
            self._idle_stage = None
            if going is not None:
                self._show_bubble("你回来啦~", emotion="happy")
        elif self._idle_stage is None and idle_secs >= 300:
            self._idle_stage = "idle"

        # Proactive 主动对话
        try:
            if time.time() > self._proactive_grace:
                self._proactive.tick()
        except Exception:
            pass

        # 感知系统 tick(情绪衰减 + 主动对话 + 日程刷新)
        try:
            self._perception.tick()
        except Exception:
            pass

    # ── 前台窗口 ──

    def _foreground_tick(self):
        """每 2 秒检测前台窗口 + 活动感知"""
        logger.debug("_foreground_tick called")
        try:
            self._foreground_watcher.tick()
        except Exception as e:
            logger.error("_foreground_tick error: %s", e)
        # 活动感知：打字/划水/空闲（零成本，喂给 ProactiveScheduler）
        tracker = getattr(self, "_activity_tracker", None)
        if tracker is not None:
            try:
                tracker.tick()
            except Exception:
                pass

    def _on_foreground_change(self, app_name: str, app_category: str, title: str):
        """前台窗口变化 → 重置 idle 计时器 + 窗口互动 + 事件触发截图"""
        going = self._idle_stage
        self._mark_user_interaction()
        if going is not None:
            self._show_bubble("你回来啦~", emotion="happy")

        # 窗口互动：桌宠靠近当前窗口（带冷却）——仅当显式开启 auto_walk 时触发。
        # 默认关闭：用户不希望在每次切换前台窗口时桌宠自动跳过去（位置漂移、
        # 像“偏左偏右”的困扰来源）。要恢复旧行为：设置里开“自动跟随窗口”。
        if hasattr(self, '_window_interaction'):
            wi_config = self.config.get('window_interaction', {})
            if wi_config.get('enabled', True) and wi_config.get('auto_walk', False):
                cooldown = wi_config.get('cooldown_seconds', 600)
                now = time.time()
                if not hasattr(self, '_last_move_near'):
                    self._last_move_near = 0
                if now - self._last_move_near >= cooldown:
                    try:
                        self._window_interaction.move_near_window()
                        self._last_move_near = now
                        EventBus.emit("window_interacted", target="window")
                    except Exception as e:
                        logger.debug("Window interaction failed: %s", e)

        # 事件触发截图：前台切换时触发一次屏幕感知（后台线程执行，不阻塞主线程）
        try:
            if hasattr(self, '_perception') and self._perception._screen:
                import threading as _threading
                _threading.Thread(
                    target=self._perception._screen.on_foreground_change,
                    args=(app_name, app_category, title),
                    daemon=True
                ).start()
        except Exception as e:
            logger.debug("Foreground screenshot trigger failed: %s", e)

    # ── 主动对话 ──

    def _on_proactive_trigger(self, prompt_text: str):
        """Proactive 调度器触发 -> 直接弹场景文案气泡 + 动作（P5 即时化）。

        P5 优化：proactive 文案本身就是桌宠要说的话，直接显示即可获得"即时感"，
        不再走 engine.send 等 2-5 秒 LLM 往返，也不把"[主动对话触发]…"指令包装
        以 user 身份写进 Hanako 会话历史（长期污染记忆）。LLM 不再参与本路径。
        """
        logger.info("Proactive trigger: %s", prompt_text)
        EventBus.emit("proactive_triggered", target="scheduler")

        # 场景文案 → 气泡情绪（不依赖 LLM 返回的 emotion 标签）
        emotion = get_bubble_emotion_for_prompt(prompt_text)

        # 直接显示场景文案气泡（prompt_text 即要显示的话，无需 LLM）
        self._show_bubble(prompt_text, emotion=emotion)

        # 记录对话空闲计时：主动对话也算一次"对话"，让 proactive 冷却正常
        try:
            if getattr(self, "_perception", None) is not None:
                proactive = getattr(self._perception, "proactive", None)
                if proactive is not None:
                    proactive.mark_conversation()
        except Exception:
            pass

        # 触发动画：主动动作是用户的明确意图（proactive 调度器判定后触发），
        # 必须【无视 emotion 冷却】强制播放挥手/比心——否则屏幕感知反复推 happy
        # 进入冷却后，proactive 触发只会"闪过思考气泡"但角色继续 idle 摇摆，
        # 用户感受"没动作"（实际是手势被冷却抑制了）。
        # 做法：先 force_idle 清掉可能占用优先级的旧 motion，再用 NORMAL 优先级播放 waving。
        try:
            renderer = getattr(self, "_renderer", None)
            if renderer is not None:
                # 清掉当前 gesture（不调用 _force_idle，避免和 emotion 冷却打架）
                if hasattr(renderer, "_model") and renderer._model is not None:
                    try:
                        if hasattr(renderer._model, "StopAllMotions"):
                            renderer._model.StopAllMotions()
                    except Exception:
                        pass
                # 重置 emotion 冷却记录，让 _play_motion_kw 内部能正常触发
                if hasattr(renderer, "_emotion_motion_cooldown"):
                    renderer._emotion_motion_cooldown.clear()
                if hasattr(renderer, "_last_gesture_at"):
                    renderer._last_gesture_at = 0.0
                # 强制播放 waving（绕过 _set_anim_seq 的情绪同步路径，避免再触发表情 → 表情同步是次要的）
                if hasattr(renderer, "_play_motion_kw"):
                    # miku 的 motion 有 waving.motion3.json；其它模型走 happy 兜底
                    if not renderer._play_motion_kw("waving"):
                        renderer._play_motion_kw("happy")
                # 触发"动作正在播"超时计时（3s 后自动回 idle）
                if hasattr(renderer, "_note_motion_started"):
                    renderer._note_motion_started("proactive", is_idle=False)
                # 标记表情同步（让人物表情也跟着场景情绪变化）
                if hasattr(renderer, "set_emotion_expression_only"):
                    renderer.set_emotion_expression_only(emotion)
        except Exception as e:
            logger.debug("Proactive 主动动作触发失败: %s", e)
        # 同时记录 sprite 路径的 _anim_seq（保持向后兼容；动作沿用 waving）
        self._set_anim_seq("waving", emotion=emotion, style=get_transition_style(emotion))

    # ── 鼠标交互反应 ──

    def _get_window_rect(self) -> tuple[int, int, int, int] | None:
        """返回角色窗口 (x, y, w, h)，供 MouseTracker 使用"""
        p = self.pos()
        s = self.size()
        return (p.x(), p.y(), s.width(), s.height())

    def _check_reaction_cooldown(self) -> bool:
        """检查是否在反应冷却中（3 秒内不重复）"""
        now = time.time()
        if now - self._mouse_reaction_cooldown < 5.0:
            return True  # 冷却中
        self._mouse_reaction_cooldown = now
        return False

    def _on_mouse_nearby(self):
        """鼠标进入角色附近 - 只切动画，不弹气泡（用温和的好奇/开心，避免频繁惊讶）"""
        params = self._mouse_reaction_params
        if not params.react_nearby:
            return
        if self._is_thinking or self._check_reaction_cooldown():
            return
        self._set_anim_seq(params.nearby_anim, emotion="happy", style=get_transition_style("happy"))

    def _on_mouse_hover(self):
        """鼠标在角色附近静止（1.5s）→ 转向 + 气泡回应（“在看我吗~”之类）"""
        params = self._mouse_reaction_params
        if not params.react_hover:
            return
        if self._is_thinking or self._check_reaction_cooldown():
            return
        try:
            import random as _random
            lines = [
                "嗯？", "在看我吗~", "有什么事吗？", "摸我一下试试？",
                "发什么呆呢~", "要陪我玩吗？", "在看什么呢？",
            ]
            self._show_bubble(_random.choice(lines), emotion="thinking")
        except Exception:
            pass
        self._set_anim_seq("idle", emotion="thinking", style=get_transition_style("thinking"))

    def _on_mouse_chase(self, target_x: int):
        """鼠标长时间不动，走过去并持续跟随光标"""
        params = self._mouse_reaction_params
        if not params.chase_enabled:
            return
        if self._is_thinking or self._physics.is_active:
            return
        x, _ = self.get_pos()
        sg = self._current_screen_geometry()
        target = max(10, min(target_x, sg.width() - self.width() - 10))
        self._motion_state = "chase"
        self._chasing = True
        self._chase_last_target = target
        self._physics.start_walk(target, facing_right=(target > x))
        # _unified_timer 已在初始化时启动，_update_chase 负责持续跟随

    # ── 待机微动作 / 随机散步活力 ──

    def _tick_idle_life(self):
        """待机时偶发微动作 + 有性格的表情节奏。"""
        if getattr(self, '_is_dragging', False) or self._is_thinking:
            return
        if getattr(self, '_chasing', False) or self._physics.is_active:
            return
        if getattr(self, '_pet_cuddle', False):
            return
        if getattr(self, '_motion_state', 'idle') not in ('idle', 'rest'):
            return
        if getattr(self, 'bubble', None) and self.bubble.isVisible() and self.bubble.is_typing():
            return

        ef = getattr(self, "_emotion_face", None)
        # 表情脸静默冷却（秒），避免连续高频弹出
        self._idle_face_cd = getattr(self, "_idle_face_cd", 0.0) - 1.0

        self._idle_action_cd -= 1.0
        if self._idle_action_cd > 0:
            # 动作冷却中：极低概率自发一个微表情，让安静也「有生命」
            if ef is not None and self._idle_face_cd <= 0 and random.random() < 0.03:
                ef.flash("blink", 900)
                self._idle_face_cd = random.uniform(12, 22)
            return
        self._idle_action_cd = random.uniform(8, 20)  # 待机微动作节奏：约 8-20 秒一次

        # 低精力时偶尔打盹（先于其他微动作）
        emgr = getattr(self, '_state_mgr', None)
        energy = 100.0
        if emgr is not None:
            try:
                energy = float(emgr.save.energy)
            except Exception:
                energy = 100.0
        if energy < 25 and random.random() < 0.5:
            self._set_anim_seq("sleep", emotion="neutral", style="snap")
            self._pet_revert_timer.stop()
            self._pet_revert_timer.start(2600)
            return

        roll = random.random()
        if roll < 0.45:
            self._do_look_around()
            if ef is not None and self._idle_face_cd <= 0 and random.random() < 0.3:
                ef.flash("blink", 1000)
                self._idle_face_cd = random.uniform(10, 18)
        elif roll < 0.8:
            self._do_stretch()
            if ef is not None and self._idle_face_cd <= 0 and random.random() < 0.25:
                ef.flash("heh", 1100)  # 伸懒腰后的小得意
                self._idle_face_cd = random.uniform(10, 18)
        elif roll < 0.88:
            # Live2D 微动作：待机时偶发播放低优先级 motion（比心/挥手/唱歌等）
            self._do_live2d_mini_action()
        else:
            mood = 50
            mgr = getattr(self, '_state_mgr', None)
            if mgr is not None:
                try:
                    mood = float(mgr.save.mood)
                except Exception:
                    mood = 50
            if mood >= 55:
                # 心情好：摇摇但不弹机械 happy，改用害羞脸（更有人格）
                self._pet_play_happy(big=False, revert=1100, surface=False)
                if ef is not None and self._idle_face_cd <= 0:
                    ef.flash("shy", 1300)
                    self._idle_face_cd = random.uniform(14, 24)
            else:
                self._do_look_around()

    def _do_live2d_mini_action(self):
        """Live2D 待机微动作：偶发播放弱优先级 motion（比心/挥手/唱歌/思考）。

        合理性约束（避免“神经质”）:
        - 仅 idle/rest 且非说话/非思考/非拖拽时触发
        - 行为模式调制概率: quiet 0.4 / normal 0.7 / active 1.0 / cling 1.0
        - 触发后由 revert 定时器在动作结束后回 idle
        """
        if getattr(self, '_is_dragging', False) or self._is_thinking:
            return
        if getattr(self, '_chasing', False) or self._physics.is_active:
            return
        if getattr(self, '_motion_state', 'idle') not in ('idle', 'rest'):
            return
        # 说话/思考中不播（避免打断口型与气泡）
        if getattr(self, '_tts_player', None) is not None:
            try:
                if self._tts_player.is_playing():
                    return
            except Exception:
                pass
        if getattr(self, 'bubble', None) and self.bubble.isVisible():
            return

        mode = getattr(self, '_behavior_mode', 'normal')
        mode_mult = {"quiet": 0.4, "normal": 0.7, "active": 1.0, "cling": 1.0}.get(mode, 0.7)
        if random.random() > mode_mult:
            return

        # 候选微动作（对应 renderer._ANIM_TO_MOTION_KW 的键；happy/touch 在 miku
        # 语义名模型下都有效，mail/complete/special 只在 lafei 风格模型有）
        acts = ["waving", "thinking", "happy", "touch", "mail", "special"]
        act = random.choice(acts)
        renderer = getattr(self, '_renderer', None)
        if renderer is not None and hasattr(renderer, 'play_anim'):
            try:
                renderer.play_anim(act, emotion="")
                self._pet_revert_timer.stop()
                self._pet_revert_timer.start(2200)
                logger.debug("Live2D 微动作: %s", act)
            except Exception:
                pass

    def _do_look_around(self):
        """张望：先左后右再回正（复用视线平滑）"""
        if getattr(self, '_chasing', False):
            return
        renderer = getattr(self, '_renderer', None)
        if renderer is None or not getattr(renderer, '_gaze_enabled', False):
            return
        if self._looking_around:
            return
        self._looking_around = True
        petx, pety = self.get_pos()
        try:
            renderer.look_at(petx - 280, pety)
            QTimer.singleShot(450, lambda: renderer.look_at(petx + 280, pety))
            QTimer.singleShot(950, self._end_look_around)
        except Exception:
            self._looking_around = False

    def _end_look_around(self):
        self._looking_around = False
        renderer = getattr(self, '_renderer', None)
        if renderer is not None:
            try:
                renderer.reset_gaze()
            except Exception:
                pass

    def _do_stretch(self):
        """伸懒腰：临时增强呼吸 bob 幅度"""
        self._stretch_until = time.time() + 1.4

    def _update_chase(self):
        """追逐中：持续跟随光标 X；贴脸或光标跑开则结束"""
        if self._is_thinking or getattr(self, '_is_dragging', False):
            self._chasing = False
            return
        tracker = self._mouse_tracker
        petx, _ = self.get_pos()
        # 光标跑远或快速移动 -> 放弃追逐
        if not tracker.is_nearby or tracker.state.speed > 1700:
            self._end_chase(happy=False)
            return
        cx = tracker.state.x
        if abs(cx - petx) <= 38:
            if not self._physics.is_active:
                self._end_chase(happy=True)
            return
        if abs(cx - self._chase_last_target) > 14:
            self._chase_last_target = cx
            self._physics.start_walk(cx, facing_right=(cx > petx))

    def _end_chase(self, happy=False):
        self._chasing = False
        self._motion_state = "idle"  # 复位，避免永久阻塞待机微动作
        if happy and not getattr(self, '_is_dragging', False):
            self._set_anim_seq('idle', emotion='happy', style='spring')
            self._set_surface_emotion('happy', duration_ms=900)
            self._show_bubble('找到你啦~', emotion='happy')
            self._pet_revert_timer.stop()
            self._pet_revert_timer.start(900)

    def _show_sticker(self, emoji: str, caption: str = ""):
        """显示大表情贴图（如摸头大反应的 💕）"""
        if not hasattr(self, 'bubble'):
            return
        self._is_thinking = False
        self._bubble_message = f"__sticker__{emoji}{caption}"
        try:
            self.bubble.set_sticker(emoji, caption)
            self._reposition_bubble()
            self.bubble.show()
            self.bubble.raise_()
            self._bubble_timer.start(6000)
        except Exception:
            pass

    def _on_mouse_startled(self, speed: float):
        """鼠标快速掠过 - 只切动画"""
        params = self._mouse_reaction_params
        if not params.react_startle:
            return
        if self._is_thinking or self._check_reaction_cooldown():
            return
        self._set_anim_seq(params.startle_anim, emotion="surprised", style=get_transition_style("surprised"))

    def _on_mouse_leave(self):
        """鼠标离开角色附近"""
        self._renderer.reset_gaze()

    # ── 屏幕感知 ──

    def _on_screen_emotion(self, emotion: str, intensity: float):
        """屏幕内容触发的情绪（从后台线程调用，通过信号转主线程）"""
        self.screen_emotion_signal.emit(emotion, intensity)

    def _on_screen_proactive(self, prompt: str):
        """屏幕内容触发主动对话（从后台线程调用，通过信号转主线程）"""
        self.screen_proactive_signal.emit(prompt)

    def _do_screen_emotion(self, emotion: str, intensity: float):
        """在主线程处理屏幕情绪（带应用层冷却）"""
        try:
            now = time.time()
            if now - self._last_screen_emotion_at < self._screen_emotion_cooldown:
                return
            self._last_screen_emotion_at = now

            self._perception.trigger_emotion(emotion, intensity)
            EventBus.emit("screen_analyzed", emotion=emotion, intensity=intensity)
            # 统一从 config.EXPRESSION_MAP 取动画（权威映射，避免分叉）
            mapped = EXPRESSION_MAP.get(emotion)
            anim = mapped[0] if mapped else 'idle'
            # 收窄：surprised/angry 不切瞪眼帧，避免高频瞪眼
            if emotion in ('surprised', 'angry'):
                anim = 'idle'
            if anim in self._renderer._frames:
                self._set_anim_seq(anim, emotion=emotion, style=get_transition_style(emotion))
                self._set_surface_emotion(emotion, duration_ms=3000, source="screen")
        except Exception:
            pass

    def _do_screen_proactive(self, prompt: str):
        """在主线程处理屏幕内容主动对话"""
        try:
            # 不显示原始提示词（那是内部 prompt，不是给用户看的）
            # 只显示思考状态
            self._show_bubble("⏳ 思考中...", emotion="thinking")
            self._is_thinking = True
            # 发送给对话引擎生成回复（会触发 TTS）
            if hasattr(self, '_engine') and self._engine:
                self._engine.send(prompt, source="proactive")
            elif hasattr(self, '_conversation_engine') and self._conversation_engine:
                self._conversation_engine.send(prompt)
        except Exception as e:
            logger.debug("Screen proactive failed: %s", e)

    def _on_screen_update(self, description: str):
        """屏幕分析结果更新（后台线程回调，通过信号绕回主线程）"""
        self.screen_update_signal.emit(description)

    def _do_screen_update(self, description: str):
        """在主线程处理屏幕更新（记录日志等）"""
        logger.debug("Screen update: %s", description[:50])