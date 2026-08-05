"""桌面宠物主窗口"""
import os
import json
import math
import random
import time
import logging
import threading
import contextlib
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QMenu, QDialog, QFormLayout,
    QSystemTrayIcon, QSlider, QStyle
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, QEvent, QThread,
    QPropertyAnimation, QEasingCurve, Signal
)
from PySide6.QtGui import (
    QPixmap, QPainter, QFont, QColor, QPen, QPainterPath,
    QFontMetrics, QAction, QIcon, QTransform, QImage,
    QCursor
)
from config import (CHARACTER_INFO, EXPRESSION_MAP, get_transition_style,
                    load_config, save_config, async_config_saver)
from core.hanako_monitor import HanakoMonitor, compact_bubble_text
from core.idle_chatter import IdleChatter

from motion.behavior import BehaviorParams, BEHAVIOR_MODES
from motion.behavior import MOUSE_REACTIONS, MouseReactionParams
from motion.behavior import (
    PHYSICS_INTERVAL, INERTIA_FACTOR, INTENT_FACTOR,
    ARRIVAL_DISTANCE, WALK_SPEED_BASE,
    BOUNCE_ELASTICITY, BOUNCE_FRICTION, BOUNCE_GRAVITY, BOUNCE_MIN_SPEED
)
from ui.bubble import ChatBubble
from ui.activity_feed import ActivityFeed
from ui.heart_particles import HeartBurst
from ui.status_hud import StatusHUD
from ui.theme.palette import rgb, rgba
from ui.emotion_face import EmotionFace
from ui.theme import get_default, rgb, rgba

from motion.action_linker import ActionLinker
from motion.foreground_watcher import ForegroundWatcher
from ui.tts_player import TTSTtsPlayer
from ui.startup_screen import StartupScreen
from core.perception import PerceptionController, ProactiveScheduler
from core.pet_audio_bridge import PetAudioBridge, PetAudioCallbacks, AudioType
from core.emotion_transitions import TransitionEngine
from motion.physics import PhysicsEngine, MotionStateMachine, PhysicsCallbacks
from avatar.factory import create_renderer
from ui.sfx import play as sfx_play

from core.conversation_engine import ConversationEngine
from motion.mouse_tracker import MouseTracker
from core.window_interaction import WindowInteraction
from pet_mixins.audio_mixin import AudioMixin
from pet_mixins.gacha_mixin import GachaMixin
from pet_mixins.status_hud_mixin import StatusHudMixin
from pet_mixins.animation_mixin import AnimationMixin
from pet_mixins.interaction_mixin import InteractionMixin
from pet_mixins.chat_mixin import ChatMixin
from pet_mixins.behavior_mixin import BehaviorMixin
from pet_mixins.voice_provider_mixin import VoiceProviderMixin
from pet_mixins.nurturing_mixin import NurturingMixin
from pet_mixins.bubble_mixin import BubbleMixin

logger = logging.getLogger(__name__)

# 延迟导入语音输入（依赖 sounddevice + whisper）
try:
    from voice_input import VoiceInput, preload_whisper
    _voice_available = True
except ImportError:
    _voice_available = False
    logger.info("VoiceInput not available (install sounddevice + whisper)")

# ─── 设置对话框 ─────────────────────────────────────────

class PetWindow(AudioMixin, GachaMixin, StatusHudMixin, AnimationMixin, InteractionMixin, ChatMixin, BehaviorMixin, VoiceProviderMixin, NurturingMixin, BubbleMixin, QWidget):
    """透明桌面宠物窗口"""

    # 跨线程信号：后台线程 -> 主线程
    engine_reply_signal = Signal(str, str, str, str)  # reply, emotion, anim, audio_path
    engine_status_signal = Signal(str)  # status message
    voice_status_signal = Signal(str)  # voice input status
    screen_emotion_signal = Signal(str, float)  # emotion, intensity
    screen_proactive_signal = Signal(str)  # prompt
    hanako_state_signal = Signal(str, str, str, str, str)  # anim, msg, emotion, state, audio_path
    idle_chatter_signal = Signal(str, str)  # text, emotion
    # M4: 工具进度（Hanako WS 模式下从 SessionManager.on_tool 转发过来）
    tool_progress_signal = Signal(str, str, str, object)  # tool_name, phase, display_text, success
    # 气泡：MultiPetBridge dispatcher / mission_tracker 等后台线程也会调 _show_bubble，
    # 而它内部要 start QTimer——必须先绕回主线程，否则 Qt 拒绝启动定时器。
    bubble_signal = Signal(str, str, int)  # text, emotion, priority

    def __init__(self, agent_id: str = "yuexinmiao", sprite_dir: str = None,
                 position: dict = None, scale: float = 1.0,
                 on_position_change: callable = None,
                 pet_manager=None):
        super().__init__()
        self.config = load_config()
        self._agent_id = agent_id
        self._sprite_dir = sprite_dir  # None = 用默认 characters/ 目录
        self._on_position_change = on_position_change  # 位置变化回调
        self._pet_manager = pet_manager  # 多桌宠管理器引用
        self._init_position = position  # 初始位置（供 _setup_window 使用）

        # ── 交互状态 ──
        self._drag_start_cursor = QPoint()
        self._drag_start_window = QPoint()
        self._is_dragging = False
        self._was_click = False
        self._drag_poll_timer = QTimer(self)
        self._drag_poll_timer.timeout.connect(self._drag_poll_tick)
        self._drag_last_pos = QPoint()   # 上一帧拖拽位置,用于速度计算
        self._drag_last_time = 0.0
        # 速度滑动平均窗口（拖拽甩动平滑）：保留最近 N 帧的速度，
        # 释放时取平均替代单帧，避免快速甩动时的初速度抖动。
        self._drag_vel_hist = []         # [(vx, vy), ...]
        self._drag_vel_hist_max = 5      # 窗口大小
        self._vy = 0.0                   # 垂直速度(弹跳用)
        self._bounce_active = False      # 弹跳模式中
        self._is_sitting = False         # 是否坐在窗口边缘
        self._sitting_edge = ""           # 坐在哪条边: top/bottom/left/right

        self._current_char = agent_id
        self._is_thinking = False

        self._pet_scale = scale
        self._pet_opacity = self.config.get("opacity", 1.0)
        self._behavior_mode = self.config.get("behavior", "normal")

        # ── 动画状态 ──
        self._bob_frame = 0
        self._bob_offset = 0
        self._label_base_pos = QPoint(0, 0)
        self._target_x = 0
        self._vx = 0.0
        # 合并 physics + bob + gaze 为单个定时器（减少事件循环压力）
        self._unified_timer = QTimer(self)
        self._unified_timer.timeout.connect(self._unified_tick)
        self._unified_timer.start(50)  # 50ms = 20fps，平衡流畅与性能
        self._motion_state = "idle"   # idle / wander / rest
        self._rest_counter = 0
        self._motion_timer = QTimer(self)
        # 守卫：与 _unified_tick 同因，MotionStateMachine 还没注入前 lambda 一调就崩。
        # 短路的 and 在 None 时直接返回 False，tick 不会执行。
        self._motion_timer.timeout.connect(
            lambda: getattr(self, '_motion', None) is not None
            and self._motion.tick(self._get_behavior_params())
        )
        self._motion_timer.start(500)
        self._is_walking = False

        # ── Hanako 状态监控 ──
        self._hanako_monitor = HanakoMonitor(on_state_change=self._on_hanako_state)

        # Hanako 状态轮询(文件桥接模式)
        self._hanako_poll_timer = QTimer(self)
        self._hanako_poll_timer.timeout.connect(self._hanako_monitor.tick)
        self._hanako_poll_timer.start(800)
        self._bubble_message = ""    # 当前气泡文字,用于超时隐藏
        self._bubble_priority = 0    # 当前气泡优先级（0=通知 1=对话回复）
        self._pending_bubbles = []   # 被高优先级顶掉的低优先级气泡队列
        self._bubble_timer = QTimer(self)
        self._bubble_timer.timeout.connect(self._clear_hanako_bubble)
        self._bubble_timer.setSingleShot(True)

        # ── 情绪过期定时器（A2: 3秒无新情绪回 idle）──
        self._emotion_expiry_timer = QTimer(self)
        self._emotion_expiry_timer.timeout.connect(self._on_emotion_expired)
        self._emotion_expiry_timer.setSingleShot(True)
        self._current_emotion = "neutral"
        # 屏幕情绪二次冷却，避免视觉模型反复输出同类关键词导致表情高频跳动
        self._screen_emotion_cooldown = 30.0  # 秒
        self._last_screen_emotion_at = 0.0

        # ── 空闲检查定时器 ──
        self._break_timer = QTimer(self)
        self._break_timer.timeout.connect(self._break_check)

        # ── 动作联动 ──
        al_cfg = self.config.get("action_linker", {})
        self._action_linker = ActionLinker(
            character_id=self._current_char,
            highlight_duration=al_cfg.get("highlight_duration", 30),
            enabled=al_cfg.get("enabled", True),
        )

        # ── 前景窗口检测 ──
        self._foreground_watcher = ForegroundWatcher()
        self._foreground_watcher.on_change = self._on_foreground_change
        self._foreground_watcher.start()
        self._foreground_timer = QTimer(self)
        self._foreground_timer.timeout.connect(self._foreground_tick)

        # ── Proactive 主动对话调度器(P1)──
        proactive_cfg = self.config.get("proactive", {})
        self._proactive = ProactiveScheduler(
            foreground_watcher=self._foreground_watcher,
            on_proactive=self._on_proactive_trigger,
        )
        self._proactive.load_config(proactive_cfg)
        self._proactive_grace = time.time() + 120  # 启动后 2 分钟内不触发主动对话

        # ── 感知控制器(P2: 时间 + 情绪状态机 + 日程)──
        self._perception = PerceptionController(self._current_char)
        # 屏幕内容→情绪回调
        self._perception.screen.on_emotion = self._on_screen_emotion
        self._perception.screen.on_screen_proactive = self._on_screen_proactive
        
        # ── 屏幕感知开关（从配置读取）──
        screen_cfg = self.config.get("screen", {})
        if not screen_cfg.get("enabled", True):
            self._perception.screen.disable()
            logger.info("Screen perception disabled by config")
        # 截图保护开关（默认全关，配置开启）
        if screen_cfg.get("blur", False):
            self._perception.screen.set_blur(True)
        if screen_cfg.get("blacklist", False):
            self._perception.screen.set_blacklist(True)
        if not screen_cfg.get("compress", True):
            self._perception.screen.set_compress(False)

        # ── 鼠标交互追踪器 ──
        self._mouse_tracker = MouseTracker(self._get_window_rect)
        self._mouse_reaction_params = MOUSE_REACTIONS.get(
            self._behavior_mode, MOUSE_REACTIONS["normal"]
        )
        self._mouse_tracker.on_nearby = self._on_mouse_nearby
        self._mouse_tracker.on_hover = self._on_mouse_hover
        self._mouse_tracker.on_chase = self._on_mouse_chase
        self._mouse_tracker.on_startled = self._on_mouse_startled
        self._mouse_tracker.on_leave = self._on_mouse_leave
        self._mouse_last_scene = "idle"  # 用于去重
        self._mouse_tracker_timer = QTimer(self)
        self._mouse_tracker_timer.timeout.connect(self._mouse_tracker.tick)
        self._mouse_tracker_timer.start(200)
        # 视线跟随由 unified_timer 驱动，不再单独开定时器

        # ── 抚摸 / 喂食 / HUD 状态 ──
        self._pet_combo = 0
        self._pet_combo_timer = QTimer(self)
        self._pet_combo_timer.setSingleShot(True)
        self._pet_combo_timer.timeout.connect(self._reset_pet_combo)
        self._pet_revert_timer = QTimer(self)
        self._pet_revert_timer.setSingleShot(True)
        self._pet_revert_timer.timeout.connect(self._pet_revert)
        # 单击延迟判定：避免与双击(抚摸)冲突
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._fire_pending_click)
        self._pending_click = False
        # 状态 HUD 显隐
        self._hud_pinned = False
        self._hud_auto_hide_timer = QTimer(self)
        self._hud_auto_hide_timer.setSingleShot(True)
        self._hud_auto_hide_timer.timeout.connect(self._auto_hide_status_hud)
        # 抚摸手势状态
        self._pet_press_time = 0.0
        self._pet_press_pos = QPoint()
        self._pet_cuddle = False
        self._pet_stroke_count = 0
        self._pet_last_stroke = 0.0
        # 待机微动作 + 随机散步活力
        self._idle_action_cd = random.uniform(10, 24)   # 距下次待机微动作的秒数
        self._stretch_until = 0.0                         # 伸懒腰：bob 增强截止时间
        self._looking_around = False
        # 鼠标追逐：持续跟随
        self._chasing = False
        self._chase_last_target = 0

        # ── TTS provider ──
        tts_provider = self._create_tts_provider()
        # 记下初始签名：设置页保存时若 TTS 配置未变，就不再重建 provider
        # （重建 cosyvoice 会在主线程拉起 torch/wetext 整条链，必须避免）
        self._tts_reload_gen = 0
        try:
            self._tts_provider_sig = self._tts_provider_signature()
        except Exception:
            self._tts_provider_sig = None

        # ── 检测是否是内置角色 ──
        is_builtin = False
        if self._pet_manager:
            for agent in self._pet_manager.agents:
                if agent["id"] == self._current_char:
                    is_builtin = agent.get("builtin", False)
                    break

        # ── 对话引擎（合并 bridge，单进程）──
        self._engine = ConversationEngine(
            self._current_char, perception=self._perception,
            tts_provider=tts_provider, builtin=is_builtin
        )
        self._engine.on_reply = self._on_engine_reply
        self._engine.on_status = self._on_engine_status
        self._engine.on_tts_ready = lambda: logger.info("Engine TTS ready")
        # M4: 桥接 on_tool_progress -> Qt Signal
        self._engine.on_tool_progress = lambda tool_name, phase, display_text, success: \
            self.tool_progress_signal.emit(tool_name, phase, display_text, success)
        self.tool_progress_signal.connect(self._do_tool_progress)
        # 后台线程调 _show_bubble 时经此信号绕回主线程（queued connection）
        self.bubble_signal.connect(self._show_bubble_impl)

        # 连接跨线程信号
        self.engine_reply_signal.connect(self._do_engine_reply)
        self.engine_status_signal.connect(self._do_engine_status)
        self.voice_status_signal.connect(self._do_voice_status)
        self.screen_emotion_signal.connect(self._do_screen_emotion)
        self.screen_proactive_signal.connect(self._do_screen_proactive)
        self.hanako_state_signal.connect(self._do_hanako_state)
        self._engine.start()

        # ── 语音输入（ASR）──
        asr_provider = self._create_asr_provider()
        self._voice_input = None
        self._voice_recording = False
        if _voice_available:
            self._voice_input = VoiceInput(asr_provider=asr_provider)
            self._voice_input._on_status = self._on_voice_status
            # 仅当 ASR 使用本地 Whisper 时才预加载本地模型；
            # 远程(mimo/api)走 API，不需要本地大模型，避免无意义地加载
            # torch/whisper 及下游依赖（funasr/wetext 等），造成启动卡顿与运行时下载
            if getattr(asr_provider, "name", "") == "whisper_local":
                preload_whisper()

        # ── TTS 播放器 ──
        tts_cfg = self.config.get("tts", {})
        self._tts_player = TTSTtsPlayer()
        self._tts_player.set_volume(tts_cfg.get("volume", 0.8))
        if not tts_cfg.get("enabled", True):
            self._tts_player.disable()

        # TTS 口型回调
        self._tts_player.on_start = self._on_tts_start
        self._tts_player.on_end = self._on_tts_end
        self._tts_player.on_error = lambda msg: self._on_tts_end()

        # ── AUDIO-07: 桌宠音频事件桥接器 ──
        self._audio_bridge = PetAudioBridge(self)
        try:
            self._audio_bridge.connect()
            logger.info("AUDIO-07: PetAudioBridge connected")
        except Exception as e:
            logger.warning("AUDIO-07: Failed to connect bridge: %s", e)

        # ── 帧动画状态(在 _setup_ui 后初始化)──
        self._anim_seq = 'idle'
        self._anim_idx = 0
        self._anim_range = (None, None)
        self._facing_right = True  # 当前朝向

        # 状态
        self._visible = True
        self._mousePassthrough = False

        self._setup_window()
        self._setup_ui()
        # ── 渲染器就绪后,同步动画状态别名 ──
        self._anim_frames = self._renderer._frames
        self._anim_frame_tops = self._renderer._frame_tops
        self._anim_timer = self._renderer._anim_timer
        # 守卫：部分渲染器（如 Live2D）不挂 QTimer 帧动画器，_anim_timer 为 None。
        # 直接 .timeout 会 AttributeError，这里与 _unified_tick/_motion_timer 同理短路。
        if self._anim_timer is not None:
            self._anim_timer.timeout.connect(self._anim_tick)
        self._setup_animation()

        # ── 情绪过渡引擎（依赖渲染器、不依赖动画帧动画计时器）──
        # 由 _unified_tick() 驱动 tick()，不新开 QTimer
        self._transition = TransitionEngine(self._renderer.set_alpha)

        # ── 物理引擎（委托）──
        self._physics = PhysicsEngine(self)
        self._motion = MotionStateMachine(self._physics, self)

        # ── 窗口互动模块 ──
        self._window_interaction = WindowInteraction(self)

        self._setup_menu()
        self._setup_tray()
        self.load_character(self._current_char)
        self._startup_screen.raise_()  # 确保启动画面在角色立绘之上
        self._break_timer.start(30000)  # 每 30 秒检查一次空闲
        self._foreground_timer.start(2000)  # 每 2 秒检测前台窗口

        # ── proactive 默认启用(由 config 控制)──
        if not proactive_cfg.get("enabled", True):
            self._proactive.disable()

        # ── 空闲时间追踪 ──
        self._current_anim = "idle"
        self._last_interaction = time.time()
        self._last_user_interaction_mono = time.monotonic()
        self._idle_stage = None

        # ── 恢复窗口状态 ──
        self._recalc_geometry()
        self.setWindowOpacity(self._pet_opacity)
        # ── 对话记忆跟踪 ──
        self._pending_user_msg = ""  # 等待配对的用户消息
        self._pending_emotion = "neutral"  # 等待配对的 emotion
        self._pending_chat = False  # 是否正在等待 Agent 回复

        # ── 空闲自言自语 ──
        self.idle_chatter_signal.connect(self._do_idle_chatter)
        self._idle_chatter = IdleChatter(
            llm_adapter=getattr(self._engine, "_adapter", None),
            on_chatter=lambda text, emotion: self.idle_chatter_signal.emit(text, emotion),
            min_interval_sec=120,
            max_interval_sec=600,
            character_id=self._current_char,
        )
        # 注入 agent 身份（异步，不阻塞启动）
        self._inject_agent_identity()

    def set_hanako_ws(self, ws_client, session_manager):
        """注入共享 Hanako WS 客户端（由 PetManager 调用）"""
        try:
            # 注入到对话引擎
            if hasattr(self, '_engine') and self._engine:
                if hasattr(self._engine, 'set_session_manager'):
                    self._engine.set_session_manager(session_manager)
            # 注入到 HanakoMonitor（共享 WS 订阅）
            if hasattr(self, '_hanako_monitor') and self._hanako_monitor:
                if hasattr(self._hanako_monitor, 'set_ws_client'):
                    self._hanako_monitor.set_ws_client(ws_client)
                # 绑定本桌宠对应的助手：只观测该助手的会话，
                # 不转播其他 agent 的活动（一个桌宠对应一个助手）
                if hasattr(self._hanako_monitor, 'set_agent_context'):
                    self._hanako_monitor.set_agent_context(self._agent_id, session_manager)
            logger.info("Hanako WS injected into PetWindow")
        except Exception as e:
            logger.warning("set_hanako_ws failed: %s", e)

    def set_nurturing(
        self,
        save_mgr,
        state_mgr,
        work_timer,
        item_registry,
        work_registry,
    ):
        """注入养成系统（由 PetManager 调用，可选）

        把 PetSaveManager / PetStateManager / WorkTimer 三个实例
        注入到 PetWindow，由 _unified_tick 驱动每秒 tick。
        全程 hasattr 守卫，未注入时主循环不崩。

        Args:
            save_mgr: 养成数据存档管理器
            state_mgr: 养成状态管理器（衰减 + 挂起池回流 + 模式）
            work_timer: 工作计时器
            item_registry: 物品注册表（菜单填充用）
            work_registry: 工作注册表（菜单填充用）
        """
        try:
            self._save_mgr = save_mgr
            self._state_mgr = state_mgr
            self._work_timer = work_timer
            self._item_registry = item_registry
            self._work_registry = work_registry

            # 在右键菜单里动态插入"喂食" / "工作" / "状态"入口
            if hasattr(self, '_menu') and self._menu is not None:
                self._build_nurturing_menu()

            # ── 任务系统（03 成长计划）──
            try:
                from core.event_bus import EventBus
                from core.mission.mission_manager import MissionManager
                self._mission_mgr = MissionManager(save_mgr=save_mgr, state_mgr=state_mgr)
                self._mission_mgr.start()
                EventBus.on("mission_completed", self._on_mission_completed_bubble)

                # 解耦升级事件：把"升级"钩子接到事件总线（延迟发射，避免奖励结算重入 add_exp 递归）
                save_mgr.on_level_up = self._emit_level_up
                self._build_mission_menu()
                logger.info("任务系统已注入并启动")
            except Exception as e:
                self._mission_mgr = None
                logger.warning("任务系统初始化失败（非致命）: %s", e)

            logger.info("Nurturing system injected into PetWindow")
        except Exception as e:
            logger.warning("set_nurturing failed: %s", e)


    def _inject_agent_identity(self):
        """异步读取 agent 身份，注入给 idle_chatter 和 screen perception"""
        def _load():
            try:
                from core.hanako_context import HanakoContext
                ctx = HanakoContext(self._current_char)
                identity = ctx.read_identity() or ctx.read_description() or ""
                if identity:
                    if hasattr(self, '_idle_chatter') and self._idle_chatter:
                        self._idle_chatter.set_agent_identity(identity)
                    if hasattr(self, '_perception') and self._perception:
                        screen = getattr(self._perception, '_screen', None)
                        if screen and hasattr(screen, 'set_agent_identity'):
                            screen.set_agent_identity(identity)
                    logger.info("Agent identity injected (%d chars)", len(identity))
            except Exception as e:
                logger.debug("Agent identity injection skipped: %s", e)
        threading.Thread(target=_load, daemon=True).start()

    # ── 屏幕查询 ──

    def _current_screen_geometry(self):
        """获取当前窗口所在屏幕的可用区域(支持多显示器)"""
        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1920, 1080)
        return screen.availableGeometry()

    # ── 窗口设置 ──

    def _setup_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._apply_penetration()
        self.setFixedSize(200, 360)

        win_cfg = self._init_position or self.config.get("window", {})
        if win_cfg.get("x", -1) >= 0 and win_cfg.get("y", -1) >= 0:
            self.move(win_cfg["x"], win_cfg["y"])
        else:
            sg = self._current_screen_geometry()
            self.move(sg.width() - 250, sg.height() - 350)

    def _apply_penetration(self):
        """应用当前鼠标穿透状态"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._mousePassthrough)
        if hasattr(self, 'char_label') and self.char_label:
            self.char_label.setAttribute(Qt.WA_TransparentForMouseEvents, self._mousePassthrough)
        if hasattr(self, 'status_label') and self.status_label:
            self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents, self._mousePassthrough)

    def _toggle_passthrough(self):
        """切换鼠标穿透"""
        self._mousePassthrough = not self._mousePassthrough
        self._apply_penetration()
        if self._mousePassthrough:
            self.input_widget.hide()
            self.bubble.hide_bubble()
            # 状态栏提示穿透已启用(3s后恢复)
            self._status_label.setText("🖱️ 穿透中")
            self._status_label.setStyleSheet(self._passthrough_qss())
            self._status_label.show()
            self._reposition_status_label()
            QTimer.singleShot(3000, self._restore_status_label)

    # ── 系统托盘 ──

    def _setup_tray(self):
        """初始化系统托盘图标"""
        self._tray = QSystemTrayIcon(self)
        # 用角色首帧做托盘图标
        px = self._make_tray_icon()
        self._tray.setIcon(QIcon(px))
        self._tray.setToolTip("OC Desktop Pet")
        tray_menu = QMenu()
        tray_menu.setStyleSheet(self._menu_qss())
        vis = tray_menu.addAction("显示/隐藏")
        vis.triggered.connect(self._toggle_visibility)
        passthrough = tray_menu.addAction("鼠标穿透")
        passthrough.setCheckable(True)
        passthrough.setChecked(self._mousePassthrough)
        passthrough.triggered.connect(self._toggle_passthrough)
        tray_menu.addSeparator()
        quit_a = tray_menu.addAction("退出")
        quit_a.triggered.connect(self.close)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self._tray_menu = tray_menu

    # ── 主题化样式（颜色统一来自 ui/theme/palette，随主题切换刷新）──

    def _menu_qss(self, theme=None):
        """右键菜单 / 托盘菜单 / 喂食·工作子菜单共用的主题化样式"""
        t = theme or getattr(self, "_ui_theme", "dark")
        return f"""
            QMenu {{ background: rgba({rgba(t, 'panel_bg')}); color: rgb({rgb(t, 'text_primary')});
                     border: 1px solid rgba({rgba(t, 'panel_border')}); border-radius: 8px; padding: 4px; }}
            QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
            QMenu::item:selected {{ background: rgba({rgba(t, 'tab_checked')}); }}
            QMenu::indicator {{ width: 0; }}
        """

    def _input_qss(self, theme=None):
        t = theme or getattr(self, "_ui_theme", "dark")
        return f"""
            QLineEdit {{
                background: rgba({rgba(t, 'input_bg')});
                color: rgb({rgb(t, 'text_primary')});
                border: 1px solid rgba({rgba(t, 'input_border')});
                border-radius: 12px; padding: 6px 12px; font-size: 11px;
            }}
            QLineEdit:focus {{ border-color: rgba({rgba(t, 'tab_checked')}); }}
        """

    def _send_btn_qss(self, theme=None):
        t = theme or getattr(self, "_ui_theme", "dark")
        return f"""
            QPushButton {{
                background: rgba({rgba(t, 'btn_primary')});
                color: rgb({rgb(t, 'text_primary')});
                border: none; border-radius: 15px; font-size: 14px;
            }}
            QPushButton:hover {{ background: rgba({rgba(t, 'btn_primary_hover')}); }}
        """

    def _passthrough_qss(self, theme=None):
        t = theme or getattr(self, "_ui_theme", "dark")
        return f"""
            QLabel {{
                background: rgba({rgba(t, 'panel_bg')});
                color: rgb({rgb(t, 'accent')});
                border: 1px solid rgba({rgba(t, 'accent_soft')});
                border-radius: 10px; font-size: 9px; padding: 2px 6px;
            }}
        """

    def _refresh_window_theme(self, theme: str):
        """主题切换时刷新主窗体所有写死样式（菜单 / 输入 / 发送键 / 穿透态）"""
        self._ui_theme = theme
        self.input_field.setStyleSheet(self._input_qss(theme))
        self.send_btn.setStyleSheet(self._send_btn_qss(theme))
        self._menu.setStyleSheet(self._menu_qss(theme))
        if hasattr(self, "_tray_menu"):
            self._tray_menu.setStyleSheet(self._menu_qss(theme))
        if hasattr(self, "_feed_menu"):
            self._feed_menu.setStyleSheet(self._menu_qss(theme))
        if hasattr(self, "_work_menu"):
            self._work_menu.setStyleSheet(self._menu_qss(theme))
        if self._status_label.isVisible() and "穿透" in (self._status_label.text() or ""):
            self._status_label.setStyleSheet(self._passthrough_qss(theme))

    def _maybe_show_onboarding(self):
        """首次启动弹出轻量引导；看过则不再现"""
        ui_cfg = self.config.setdefault("ui", {})
        if ui_cfg.get("onboarded"):
            return
        try:
            from ui.onboarding import OnboardingOverlay

            def _mark_done():
                self.config.setdefault("ui", {})["onboarded"] = True
                try:
                    save_config(self.config)
                except Exception:
                    pass

            ov = OnboardingOverlay(self, on_close=_mark_done)
            ov.show_relative()
        except Exception:
            logger.exception("onboarding failed")

    # ── 行为模式(占位) ──
    def _switch_behavior_mode(self, mode):
        """切换行为模式 - 通过 BehaviorParams 完全参数化"""
        self._behavior_mode = mode
        self.config["behavior"] = mode
        save_config(self.config)
        self._stop_walking()
        self._motion_state = "idle"
        self._rest_counter = 0
        # 更新鼠标交互参数
        self._mouse_reaction_params = MOUSE_REACTIONS.get(mode, MOUSE_REACTIONS["normal"])
        self._renderer.set_gaze_enabled(self._mouse_reaction_params.gaze_enabled)

    def _get_behavior_params(self) -> BehaviorParams:
        """获取当前行为模式的参数"""
        return BEHAVIOR_MODES.get(self._behavior_mode, BEHAVIOR_MODES["normal"])

    def _make_tray_icon(self):
        px = QPixmap(16, 16)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(140, 100, 200, 220))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()
        return px

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_visibility()

    def _toggle_visibility(self):
        """切换显示/隐藏"""
        self._mark_user_interaction()
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def _trigger_action(self, action_id: str):
        """用户点击动作联动项"""
        self._mark_user_interaction()
        basedir = Path(__file__).parent / "data"
        self._action_linker.trigger_action(basedir, action_id)
        self._show_bubble(f"{action_id}!", emotion="happy")

    def _recalc_geometry(self):
        """缩放后重算窗口和角色图片尺寸(不改变窗口位置)"""
        w = max(200, int(200 * self._pet_scale))
        h = max(360, int(360 * self._pet_scale))
        self.setFixedSize(w, h)
        # 委托给 SpriteRenderer 处理角色尺寸
        self._renderer.set_scale(self._pet_scale)
        self._renderer.recalc_geometry(w, h)
        QTimer.singleShot(50, self._store_label_pos)
        QTimer.singleShot(50, self._reposition_status_label)
        QTimer.singleShot(50, self._reposition_bubble)

    def _apply_scale(self):
        """应用缩放设置"""
        self._recalc_geometry()

    def _rescale_current_frame(self):
        """把当前帧缩放到 char_label 大小"""
        frames = self._anim_frames.get(self._anim_seq, [])
        if not frames:
            return
        pix = frames[self._anim_idx % len(frames)]
        ls = self.char_label.size()
        if ls.width() > 0 and ls.height() > 0:
            pix = pix.scaled(ls.width(), ls.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if not self._facing_right:
            pix = pix.transformed(QTransform().scale(-1, 1))
        self.char_label.setPixmap(pix)

    def _adjust_opacity(self):
        """降低透明度 0.1,钳制 0.2~1.0"""
        self._pet_opacity = max(0.2, self._pet_opacity - 0.1)
        self.setWindowOpacity(self._pet_opacity)

    def _opacity_up(self):
        """增加透明度"""
        self._pet_opacity = min(1.0, self._pet_opacity + 0.1)
        self.setWindowOpacity(self._pet_opacity)

    def _opacity_down(self):
        """降低透明度"""
        self._adjust_opacity()

    # ── UI ──

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.main_layout.setAlignment(Qt.AlignCenter)

        # 角色渲染器(帧精灵 / Live2D / 未来 VRM) — 按角色目录格式自动选择
        self._renderer = create_renderer(self._current_char, self)
        # 兼容别名(供 pet.py 其他部分使用)
        self.char_label = self._renderer.label
        self.char_label.installEventFilter(self)
        # 关键：必须把角色 widget 加进布局，否则 QOpenGLWidget 不显示（画了看不见）。
        # 透明测试已验证 addWidget 后 Live2D 能正常显示。精灵渲染用绝对定位 move()，
        # 但 QOpenGLWidget 需要进布局才能触发正确的显示/绘制。
        self.main_layout.addWidget(self.char_label, 0, Qt.AlignCenter)
        self.char_label.raise_()

        # 启动画面
        self._startup_screen = StartupScreen(self)
        self._startup_screen.show_for_character(self._current_char)

        # 气泡(顶层)
        self.bubble = ChatBubble(self)
        self.bubble.move(0, 0)
        self.bubble.raise_()

        # 宠物互动叠层：爱心粒子 / 状态 HUD / 情绪脸
        self._heart_overlay = HeartBurst(self)
        self._heart_overlay.resize_to_parent()
        self._heart_overlay.raise_()
        self._status_hud = StatusHUD(self)
        self._status_hud.hide()  # 默认不显示，避免遮挡桌宠主体
        self._emotion_face = EmotionFace(self)

        # 状态指示器(左下角悬浮)
        self._status_label = QLabel(self)
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setFixedSize(68, 20)
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        self._status_label.setStyleSheet(self._status_idle_style())
        if mgr is not None:
            mgr.theme_changed.connect(self._on_theme_changed)
            mgr.theme_changed.connect(self._refresh_window_theme)
        self._status_label.setText("⚪ 空闲")
        self._status_label.hide()

        # 底部输入区
        self.input_widget = QWidget(self)
        self.input_widget.setFixedSize(200, 40)
        self.input_widget.setStyleSheet("background: transparent;")
        input_layout = QHBoxLayout(self.input_widget)
        input_layout.setContentsMargins(4, 2, 4, 2)
        input_layout.setSpacing(4)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("说点什么...")
        self.input_field.setStyleSheet(self._input_qss())
        self.input_field.returnPressed.connect(self._send_message)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.setStyleSheet(self._send_btn_qss())
        self.send_btn.clicked.connect(self._send_message)

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_btn)
        self.input_widget.hide()

        self.main_layout.addStretch()
        self.main_layout.addWidget(self.input_widget, 0, Qt.AlignCenter)

        # 状态指示器移到右下角
        QTimer.singleShot(100, self._reposition_status_label)

        # 首次启动：延迟弹出引导（不挡启动流程）
        QTimer.singleShot(900, self._maybe_show_onboarding)

    # ── 动画 ──

    def _setup_animation(self):
        # 帧动画时钟(idle 默认 4fps,walk 6fps)
        # 渲染器不提供 QTimer 动画器（Live2D 自管循环）时跳过，避免 None.start()
        if self._anim_timer is not None:
            self._anim_timer.start(200)
        # 呼吸浮动由 unified_timer 驱动，不再单独开定时器

    def _unified_tick(self):
        """统一高频定时器回调（30ms）— 合并 physics + bob + gaze + transition"""
        # 守卫：渲染器/物理引擎尚未初始化完成时（PetWindow.__init__ 中段抛出）
        # 定时器已在更早的 start(50) 里启动，若不短路就会 AttributeError。
        if getattr(self, '_physics', None) is None:
            return
        # 1. 物理模拟
        self._physics.tick(self._get_behavior_params())
        # 2. 呼吸浮动
        self._bob_tick()
        # 3. 视线跟随（每 2 帧更新一次，~15fps 足够）
        if self._bob_frame % 2 == 0:
            self._gaze_tick()
        # 4. 空闲自言自语（每秒检查一次，实际触发间隔至少 120 秒）
        if self._bob_frame % 20 == 0:
            idle_chatter = getattr(self, "_idle_chatter", None)
            if idle_chatter and self._can_idle_chatter():
                idle_chatter.tick()
        # 5. 情绪过渡（无进行中过渡时 tick 内部直接 return，开销可忽略）
        self._transition.tick()
        # 5.5 情绪脸轮询 + HUD 刷新（每秒一次）
        if self._bob_frame % 20 == 0:
            emo = getattr(self, '_current_emotion', 'neutral')
            if hasattr(self, '_emotion_face'):
                self._emotion_face.set_emotion(emo)
            if getattr(self, '_status_hud', None):
                self._status_hud.set_emotion(emo)
                if self._status_hud.isVisible():
                    self._refresh_status_hud()
            # 情绪驱动本体动画（仅平静状态下，避免打断行走/追逐/对话）
            # 收窄：surprised/angry 不切瞪眼帧（用户反馈高频瞪眼），只保留表情脸表达
            if not hasattr(self, '_last_body_emotion'):
                self._last_body_emotion = 'neutral'
            body_emo = emo
            if body_emo in ('surprised', 'angry'):
                body_emo = 'neutral'  # 驱动本体动画时降级为中性，避免瞪眼
            if body_emo != self._last_body_emotion and hasattr(self, '_renderer'):
                calm = (not getattr(self, '_physics', None) or not self._physics.is_active) \
                    and not getattr(self, '_chasing', False) \
                    and not getattr(self, '_is_thinking', False) \
                    and not getattr(self, '_is_dragging', False)
                if calm:
                    self._renderer.set_emotion(body_emo)
                self._last_body_emotion = body_emo
        # 6. 养成 tick（每秒一次）+ 自动保存（自带 60s 节流）
        #    所有访问都靠 hasattr 守卫，没注入养成模块时跳过
        if self._bob_frame % 20 == 0:
            state_mgr = getattr(self, '_state_mgr', None)
            if state_mgr:
                try:
                    state_mgr.tick(1.0)
                except Exception:
                    logger.exception("pet state tick failed")
            save_mgr = getattr(self, '_save_mgr', None)
            if save_mgr:
                try:
                    save_mgr.auto_save()
                except Exception:
                    logger.exception("pet auto_save failed")

        # 7. 待机微动作（约每秒一次）
        if self._bob_frame % 33 == 0:
            self._tick_idle_life()
        # 8. 鼠标追逐：持续跟随光标
        if getattr(self, '_chasing', False):
            self._update_chase()

    # ── TTS 口型 ──

    # ── PetAudioCallbacks 实现（AUDIO-07）──

    # ── 音频回调已迁移至 pet_mixins/audio_mixin.py（AudioMixin）──

    def _reposition_bubble(self):
        """气泡置于角色头顶上方,根据实际角色内容定位"""
        top_y = self._get_char_top_y()
        bw = self.bubble.width()
        bh = self.bubble.height()
        bx = (self.width() - bw) // 2
        by = top_y - bh - 20  # 头顶上方 20px，避免遮挡
        self.bubble.move(max(bx, 2), max(by, 2))
        self._reposition_overlays()

    # ── 右键菜单 ──

    def _setup_menu(self):
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # 构建右键菜单
        self._menu = QMenu(self)
        self._menu.setStyleSheet(self._menu_qss())

        # 基础操作
        self._menu.addAction("💬 对话", self._toggle_input)
        self._voice_action = self._menu.addAction("🎤 说话", self._toggle_voice)
        self._menu.addAction("🍙 喂一口", self._quick_feed)

        # 行为模式子菜单
        self._behavior_submenu = self._menu.addMenu("🚶 行为")
        self._behavior_actions = {}
        for mode in ["quiet", "normal", "active", "cling"]:
            labels = {"quiet": "静默", "normal": "正常", "active": "活跃", "cling": "黏人"}
            a = self._behavior_submenu.addAction(labels.get(mode, mode))
            a.setCheckable(True)
            a.setChecked(mode == self._behavior_mode)
            a.triggered.connect(lambda checked, m=mode: self._switch_behavior_mode(m))
            self._behavior_actions[mode] = a

        # 动作联动(动态高亮)
        self._menu.addSeparator()
        self._action_menu_items = {}  # action_id -> QAction
        for action in self._action_linker.actions:
            a = self._menu.addAction(f"{action.emoji} {action.label}", lambda a_id=action.id: self._trigger_action(a_id))
            a.setVisible(False)  # 默认隐藏,匹配时高亮
            self._action_menu_items[action.id] = a

        self._menu.addSeparator()

        # 穿透 / 设置
        self._passthrough_action = self._menu.addAction("🔍 穿透", self._toggle_passthrough)
        self._passthrough_action.setCheckable(True)
        self._passthrough_action.setChecked(self._mousePassthrough)
        self._menu.addAction("📜 活动流", self._open_activity_feed)
        # M4: 新建对话入口（仅在 Hanako WS 模式下有意义）
        self._new_session_action = self._menu.addAction("🔄 新对话", self._create_new_session)

        # 主题子菜单
        self._theme_submenu = self._menu.addMenu("🎨 主题")
        self._theme_actions = {}
        for label, mode in [("自动（跟随时间）", "auto"), ("浅色", "light"), ("深色", "dark")]:
            a = self._theme_submenu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda checked, m=mode: self._set_theme_mode(m))
            self._theme_actions[mode] = a
        self._refresh_theme_menu()

        self._menu.addAction("⚙️ 设置", self._open_settings)
        self._menu.addAction("🔌 插件", self._open_plugin_panel)

        self._menu.addSeparator()
        self._menu.addAction("❌ 退出", self.close)

    # ── 养成接入（set_nurturing 后才填充） ──

    def _refresh_theme_menu(self):
        """根据 ThemeManager 当前 mode 同步右键菜单选中状态"""
        from ui.theme import get_default
        mgr = get_default()
        if mgr is None:
            return
        current_mode = mgr.mode
        for mode, action in self._theme_actions.items():
            action.setChecked(mode == current_mode)

    def _set_theme_mode(self, mode: str):
        """切换主题模式（auto / light / dark），持久化到 config.json"""
        from ui.theme import get_default
        mgr = get_default()
        if mgr is None:
            return
        mgr.set_mode(mode)
        self._refresh_theme_menu()
        mode_label = {"auto": "自动（跟随时间）", "light": "浅色", "dark": "深色"}.get(mode, mode)
        logger.info("主题模式切换：%s", mode_label)

        # 持久化到 config.json
        try:
            from config import load_config, save_config
            cfg = load_config()
            cfg["theme_mode"] = mode
            save_config(cfg)
        except Exception as e:
            logger.warning("保存主题模式到 config 失败：%s", e)

        # 轻量提示（在气泡显示）
        self._show_bubble(f"🎨 主题：{mode_label}", "neutral")

    def _open_activity_feed(self):
        """打开活动流窗口（浮动窗口，跟随宠宠位置）

        数据源：PerceptionController.get_recent_activity_events()
        主题感知：跟随 ThemeManager
        """
        events = []
        if hasattr(self, "_perception") and self._perception:
            try:
                events = self._perception.get_recent_activity_events(minutes=60)
            except Exception as e:
                logger.warning("获取活动事件失败: %s", e)

        if not hasattr(self, "_activity_feed") or self._activity_feed is None:
            self._activity_feed = ActivityFeed(events=events, parent=None)
            self._activity_feed.setWindowFlags(
                self._activity_feed.windowFlags() | Qt.Tool
            )
            # 注入定时刷新源——从 perception 拉最新事件
            self._activity_feed.set_refresh_source(
                lambda: self._perception.get_recent_activity_events(minutes=60)
                if self._perception else []
            )
        else:
            self._activity_feed.set_events(events)

        # 定位：宠宠右上角偏移 16px
        pet_geo = self.geometry()
        self._activity_feed.move(
            pet_geo.right() + 16,
            max(8, pet_geo.top() - 8)
        )
        self._activity_feed.show()
        self._activity_feed.raise_()

    def _open_settings(self):
        """打开配置面板"""
        from ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(parent=self, config=self.config, pet_manager=self._pet_manager)
        if dialog.exec():
            self.config = dialog.get_config()
            save_config(self.config)
            logger.info("配置已保存")
            # 应用即时生效的设置
            self._apply_settings()

    def _open_plugin_panel(self):
        """打开插件面板"""
        from ui.plugin_panel import PluginPanel
        panel = PluginPanel(on_send_command=self._send_plugin_command, parent=self)
        panel.exec()

    def _send_plugin_command(self, text: str):
        """从插件面板发送指令到对话引擎"""
        self._mark_user_interaction()
        if self._engine:
            self._engine.send(text, character=self._current_char)
            self._tts_player.stop()
            self.bubble.set_text("⏳ 思考中...")
            self._reposition_bubble()
            self.bubble.show()
            self.bubble.raise_()
            self._is_thinking = True
            self._pending_chat = True

    def _apply_settings(self):
        """应用配置变更"""
        # 窗口透明度和缩放
        new_opacity = self.config.get("opacity", 1.0)
        if hasattr(self, '_pet_opacity') and self._pet_opacity != new_opacity:
            self._pet_opacity = new_opacity
            self.setWindowOpacity(new_opacity)
        
        new_scale = self.config.get("scale", 1.0)
        if hasattr(self, '_pet_scale') and self._pet_scale != new_scale:
            self._pet_scale = new_scale
            self._apply_scale()
        
        # TTS
        tts_cfg = self.config.get("tts", {})
        if tts_cfg.get("enabled", True):
            self._tts_player.enable()
        else:
            self._tts_player.disable()
        self._tts_player.set_volume(tts_cfg.get("volume", 0.8))

        # TTS 引擎切换（重建 provider）
        # 关键：CosyVoiceProvider 的**构造函数**会同步 import
        # torch / lightning / diffusers / onnxruntime，并可能触发 ModelScope
        # 联网下载 wetext 模型，耗时数十秒。旧实现把构造留在主线程、只把
        # preload() 丢后台，等于没防住——每点一次「保存」就冻结一次 UI。
        # 现改为：① 签名未变直接跳过；② 构造+preload 整体后台化；
        #        ③ 代际号丢弃过期结果，防连点产生并发实例。
        self._maybe_reload_tts_provider()

        # 鼠标交互
        self._mouse_reaction_params = MOUSE_REACTIONS.get(
            self.config.get("behavior", "normal"), MOUSE_REACTIONS["normal"]
        )
        if hasattr(self, '_renderer'):
            self._renderer.set_gaze_enabled(
                self.config.get("mouse_interaction", True)
                and self._mouse_reaction_params.gaze_enabled
            )

        # 行为模式
        self._switch_behavior_mode(self.config.get("behavior", "normal"))

        # 主动对话
        pro_cfg = self.config.get("proactive", {})
        if pro_cfg.get("enabled", True):
            self._proactive.enable()
        else:
            self._proactive.disable()
        self._proactive.load_config(pro_cfg)

        # 屏幕感知
        if hasattr(self, '_perception') and self._perception:
            screen = self._perception._screen
            screen_cfg = self.config.get("screen", {})
            if screen_cfg.get("enabled", True):
                screen.enable()
            else:
                screen.disable()
            screen.set_blur(screen_cfg.get("blur", False))
            screen.set_blacklist(screen_cfg.get("blacklist", False))
            screen.set_compress(screen_cfg.get("compress", True))

    # ── 角色加载 ──

    def load_character(self, char_id: str):
        """加载角色 - 委托给 SpriteRenderer"""
        self._current_char = char_id

        # 委托给渲染器加载帧序列，优先使用 sprite_dir
        self._renderer.load(char_id, sprite_dir=self._sprite_dir)
        # 同步状态别名
        self._anim_frames = self._renderer._frames
        self._anim_frame_tops = self._renderer._frame_tops

        # 更新托盘图标
        self._tray.setIcon(QIcon(self._make_tray_icon()))

        # 启动画面
        self._startup_screen.show_for_character(char_id)

        # 重新定位气泡
        self._reposition_bubble()

        QTimer.singleShot(50, self._store_label_pos)

    def _store_label_pos(self):
        self._label_base_pos = self.char_label.pos()
        self._renderer.set_label_base_pos(self._label_base_pos)

    def _auto_hide_bubble(self):
        """发送中气泡超时隐藏"""
        self._is_thinking = False
        self._bubble_message = ""
        # 取消超时计时器
        if hasattr(self, '_think_timeout'):
            self._think_timeout.stop()
        if hasattr(self, 'bubble'):
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass

    def _on_think_timeout(self):
        """LLM 超时：自动恢复 idle 状态"""
        if self._is_thinking:
            logger.warning("LLM response timeout (30s), resetting to idle")
            self._is_thinking = False
            self._pending_chat = False
            self.bubble.hide_bubble()
            self._set_anim_seq('idle')
            self._show_bubble("…信号不太好", emotion="sad")

    def _clear_hanako_bubble(self):
        """清除气泡（超时回调）；如有排队的低优先级通知，依次弹出"""
        if hasattr(self, 'bubble'):
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass
            self._bubble_message = ""
            self._bubble_priority = 0
            # 弹出最早排队的通知
            while self._pending_bubbles:
                text, emotion, priority = self._pending_bubbles.pop(0)
                if text:
                    self._show_bubble(text, emotion=emotion, priority=priority)
                    return

    def _on_emotion_expired(self):
        """A2: 情绪过期 — 3秒无新情绪后回到 idle"""
        if self._current_emotion != "neutral":
            logger.debug("Emotion expired: %s -> neutral", self._current_emotion)
            self._current_emotion = "neutral"
            try:
                self._set_anim_seq("idle", emotion="neutral", style=get_transition_style("neutral"))
            except Exception:
                pass

    def _set_surface_emotion(self, emotion: str, duration_ms: int = 3000):
        """统一设置当前情绪并同步到情绪脸，启动过期计时器"""
        self._current_emotion = emotion or "neutral"
        if hasattr(self, '_emotion_face'):
            self._emotion_face.set_emotion(self._current_emotion)
        if self._current_emotion != "neutral":
            self._emotion_expiry_timer.stop()
            self._emotion_expiry_timer.start(duration_ms)
        else:
            self._emotion_expiry_timer.stop()

    def _on_engine_reply(self, reply: str, emotion: str, anim: str, audio_path: str):
        """对话引擎回复回调 - 从后台线程调用，通过信号转到主线程"""
        # 从 Python threading.Thread 调 QTimer.singleShot 不可靠
        # 用 Signal 发射，Qt 会自动跨线程投递到主线程
        self.engine_reply_signal.emit(reply, emotion, anim, audio_path)

    def _do_engine_reply(self, reply: str, emotion: str, anim: str, audio_path: str):
        """在主线程中处理引擎回复"""
        try:
            self._do_engine_reply_inner(reply, emotion, anim, audio_path)
        except Exception:
            logger.exception("_do_engine_reply crashed")
            # 确保至少恢复基本状态
            self._is_thinking = False
            self._pending_chat = False

    def _do_engine_reply_inner(self, reply: str, emotion: str, anim: str, audio_path: str):
        """在主线程中处理引擎回复（内部实现）"""
        # 取消超时计时器
        if hasattr(self, '_think_timeout'):
            self._think_timeout.stop()

        # 截停旧 TTS
        self._tts_player.stop()

        # 显示气泡
        if reply and reply.strip() and reply.strip() not in ("\u2026", "..."):
            try:
                compact = compact_bubble_text(reply)
            except Exception:
                compact = reply
            self._show_bubble(compact or reply, emotion=emotion, priority=1)
        else:
            # 空回复也要清除"思考中"气泡
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass

        # 任务系统：对话完成事件（有效回复才计入）
        if (getattr(self, "_mission_mgr", None) is not None
                and reply and reply.strip()
                and reply.strip() not in ("\u2026", "...")):
            try:
                from core.event_bus import EventBus
                EventBus.emit("chat_completed", count=1)
            except Exception:
                logger.debug("chat_completed emit failed", exc_info=True)

        # 播放音频（和文字一起）
        if audio_path and os.path.exists(audio_path):
            tts_cfg = self.config.get("tts", {})
            if tts_cfg.get("enabled", True):
                logger.info("Playing TTS: %s", audio_path)
                self._last_tts_emotion = emotion or "neutral"
                self._tts_player.play(audio_path)

        # 动画（收窄：surprised/angry 不切瞪眼帧，避免对话时高频瞪眼）
        try:
            body_anim = anim
            if emotion in ('surprised', 'angry'):
                body_anim = 'idle'
            self._set_anim_seq(body_anim, emotion=emotion, style=get_transition_style(emotion))
        except Exception:
            pass

        # A2: 情绪过期 — 3秒无新情绪自动回 idle
        self._current_emotion = emotion or "neutral"
        if self._current_emotion != "neutral":
            self._emotion_expiry_timer.start(3000)
        else:
            self._emotion_expiry_timer.stop()

        # 触发情绪状态机
        if emotion and emotion != "neutral":
            try:
                self._perception.trigger_emotion(emotion)
            except Exception:
                pass

        # 重置状态
        if self._pending_chat:
            self._pending_user_msg = ""
            self._pending_chat = False

        # 重置 idle
        self._is_thinking = False
        self._mark_user_interaction()

    def _on_engine_status(self, msg: str):
        """引擎状态提示 - 从后台线程调用，通过信号转到主线程"""
        self.engine_status_signal.emit(msg)

    def _do_engine_status(self, msg: str):
        """在主线程中处理引擎状态"""
        if msg:
            self._show_bubble(msg, emotion="thinking")
        else:
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass

    # ── M4: 工具进度 + 新对话入口 ──

    def _do_tool_progress(self, tool_name: str, phase: str, display_text: str, success):
        """Hanako WS 工具进度回调 - 在主线程中显示气泡

        phase: "start" / "progress" / "end"
        success: None / True / False
        """
        try:
            if phase == "start" or phase == "progress":
                self._show_bubble(display_text or f"⏳ {tool_name}…", emotion="thinking")
            elif phase == "end":
                if success is False:
                    self._show_bubble(f"⚠️ {display_text or tool_name} 出了问题", emotion="sad")
                # 成功时不覆盖后续的最终回复气泡
        except Exception as e:
            logger.warning("_do_tool_progress error: %s", e)

        """右键菜单：创建新 Session"""
        self._mark_user_interaction()
        if not hasattr(self, '_engine') or self._engine is None:
            self._show_bubble("引擎还没起来", emotion="thinking")
            return
        if not hasattr(self._engine, 'create_new_session'):
            self._show_bubble("当前模式不支持新建对话", emotion="neutral")
            return
        session = self._engine.create_new_session(agent_id=self._current_char)
        if session is not None:
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass
            self._show_bubble("🔄 新对话已创建", emotion="happy")
            logger.info("新 Session 创建成功: %s", getattr(session, 'session_id', '?'))
        else:
            self._show_bubble("新对话创建失败", emotion="sad")

    # ── 右键菜单 ──

    # ── PhysicsCallbacks 接口 ──

    def get_screen_geometry(self):
        return self._current_screen_geometry()

    def get_pos(self):
        return (self.x(), self.y())

    def get_size(self):
        return (self.width(), self.height())

    def move_to(self, x: int, y: int):
        self.move(x, y)

    def on_walk_finished(self):
        self._is_walking = False
        self._set_anim_seq('idle')
        self._store_label_pos()
        pos = self.pos()
        self.config.setdefault("window", {})["x"] = pos.x()
        self.config.setdefault("window", {})["y"] = pos.y()
        # 异步防抖保存：散步每次到达都写盘会周期性卡顿，改走后台
        async_config_saver.schedule(self.config)
        if self._on_position_change:
            self._on_position_change(pos.x(), pos.y())
        params = self._get_behavior_params()
        self._motion._start_rest(params)
        # 散步到达后张望一下，更有生气（追逐中不抢戏）
        if not (getattr(self, '_chasing', False) or getattr(self, '_is_dragging', False) or self._is_thinking):
            self._do_look_around()

    def on_bounce_finished(self, x: int, y: int):
        self._motion_state = "idle"
        self._bounce_active = False
        self.config.setdefault("window", {})["x"] = x
        self.config.setdefault("window", {})["y"] = y
        # 异步防抖保存
        async_config_saver.schedule(self.config)

    def on_facing_change(self, facing_right: bool):
        self._facing_right = facing_right
        # 同步渲染器朝向，确保 atlas 方向动画不被反向翻转
        self._renderer.set_facing(facing_right)

    def set_anim(self, anim: str):
        # atlas 格式：walk → running-right/left（根据朝向）
        if anim == 'walk':
            if 'running-right' in self._renderer._frames:
                anim = 'running-right' if self._facing_right else 'running-left'
        self._set_anim_seq(anim)

    # ── Hanako 状态回调 ──

    # ── 状态指示器已迁移至 pet_mixins/status_hud_mixin.py（StatusHudMixin）──

    # ── 空闲时间追踪(idle 超时递进)──

    def closeEvent(self, event):
        """关闭窗口时停止所有定时器 + 清理资源"""
        timers = [
            '_unified_timer', '_motion_timer',
            '_anim_timer', '_drag_poll_timer', '_hanako_poll_timer',
            '_break_timer', '_foreground_timer', '_bubble_timer',
            '_mouse_tracker_timer',
        ]
        for tname in timers:
            t = getattr(self, tname, None)
            if t:
                try:
                    t.stop()
                except Exception:
                    pass

        if hasattr(self, '_idle_chatter'):
            try:
                self._idle_chatter.disable()
            except Exception:
                pass

        if hasattr(self, '_foreground_watcher'):
            try:
                self._foreground_watcher.stop()
            except Exception:
                pass

        if hasattr(self, '_tts_player'):
            try:
                self._tts_player.stop()
            except Exception:
                pass

        # ── AUDIO-07: 断开桥接器 ──
        if hasattr(self, '_audio_bridge'):
            try:
                self._audio_bridge.disconnect()
            except Exception:
                pass

        # ── P0 养成：停止工作 + 落盘 ──
        work_timer = getattr(self, '_work_timer', None)
        if work_timer and getattr(work_timer, 'is_working', False):
            try:
                work_timer.stop_work(reason="close")
            except Exception:
                logger.exception("work_timer.stop_work failed on close")
        save_mgr = getattr(self, '_save_mgr', None)
        if save_mgr:
            try:
                save_mgr.save_to_disk()
            except Exception:
                logger.exception("save_to_disk failed on close")

        if hasattr(self, '_tray'):
            try:
                self._tray.hide()
            except Exception:
                pass

        if hasattr(self, '_engine'):
            try:
                self._engine.stop()
                # 等后台线程退出，避免 TTS 文件被截断
                if self._engine._thread and self._engine._thread.is_alive():
                    self._engine._thread.join(timeout=3)
            except Exception:
                pass

        super().closeEvent(event)



