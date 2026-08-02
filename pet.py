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
from config import CHARACTER_INFO, EXPRESSION_MAP, get_transition_style, load_config, save_config
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

logger = logging.getLogger(__name__)

# 延迟导入语音输入（依赖 sounddevice + whisper）
try:
    from voice_input import VoiceInput, preload_whisper
    _voice_available = True
except ImportError:
    _voice_available = False
    logger.info("VoiceInput not available (install sounddevice + whisper)")

# ─── 设置对话框 ─────────────────────────────────────────

class PetWindow(AudioMixin, GachaMixin, StatusHudMixin, QWidget):
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

    def _bob_tick(self):
        self._bob_frame += 1
        amp = 2.5 * self._emotion_bob_factor()
        if time.time() < getattr(self, '_stretch_until', 0.0):
            amp *= 2.2  # 伸懒腰：临时增强呼吸幅度
        self._bob_offset = int(math.sin(self._bob_frame * 0.06) * amp)
        if not self._is_dragging:
            ox = self._renderer._base_label_pos.x() + int(self._renderer._gaze_offset_x)
            oy = self._renderer._base_label_pos.y() + int(self._renderer._gaze_offset_y) + self._bob_offset
            # 只在位置变化时 move，避免不必要的重绘
            cur = self.char_label.pos()
            if cur.x() != ox or cur.y() != oy:
                self.char_label.move(ox, oy)

    def _gaze_tick(self):
        """视线跟随平滑更新"""
        self._renderer.update_gaze()

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
            if not hasattr(self, '_last_body_emotion'):
                self._last_body_emotion = 'neutral'
            if emo != self._last_body_emotion and hasattr(self, '_renderer'):
                calm = (not getattr(self, '_physics', None) or not self._physics.is_active) \
                    and not getattr(self, '_chasing', False) \
                    and not getattr(self, '_is_thinking', False) \
                    and not getattr(self, '_is_dragging', False)
                if calm:
                    self._renderer.set_emotion(emo)
                self._last_body_emotion = emo
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

    def _set_anim_seq(self, seq_name, emotion=None, style="snap"):
        """切换动画序列，可选弹性/缓动过渡（去简陋感）。

        style:
            snap  - 瞬切（向后兼容，不透明度不变）
            fade  - ease-out 缓出淡入
            spring- 欠阻尼弹簧（惊讶/生气等弹一下）

        全程 try/except 兜底：过渡若异常，降级为 snap 瞬切，绝不崩溃。
        """
        try:
            self._renderer.play_anim(seq_name, emotion=emotion)
            self._anim_seq = self._renderer._anim_seq
            self._anim_idx = self._renderer._anim_idx
            self._anim_range = self._renderer._anim_range

            tr = getattr(self, '_transition', None)
            if tr is None or style == "snap":
                if tr is not None:
                    tr.reset(1.0)  # 确保全亮（snap 不做过渡）
                return

            # fade / spring：先压暗再弹性淡入，表现"旧动作收尾、新动作登场"
            tr.reset(0.0)
            tr.go(1.0, style=style)
        except Exception:
            logger.exception("情绪过渡异常，降级 snap: %s", seq_name)
            try:
                self._renderer.play_anim(seq_name, emotion=emotion)
            except Exception:
                pass

    def _anim_tick(self):
        """帧推进 - 委托给 SpriteRenderer"""
        logger.debug("_anim_tick called")
        self._renderer._anim_tick()
        self._anim_idx = self._renderer._anim_idx

    def _show_anim_frame(self):
        """渲染当前帧 - 委托给 SpriteRenderer"""
        self._renderer._show_frame()

    def _get_char_top_y(self):
        """获取角色头顶 Y 坐标 - 委托给 SpriteRenderer"""
        return self._renderer.get_char_top_y()

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

    # ── 抽卡/图鉴已迁移至 pet_mixins/gacha_mixin.py（GachaMixin）──

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

    def _toggle_input(self):
        """切换输入框显示"""
        self._mark_user_interaction()
        if self.input_widget.isVisible():
            self.input_widget.hide()
        else:
            self.input_widget.show()
            self.input_field.setFocus()

    def _toggle_voice(self):
        """切换语音录音"""
        self._mark_user_interaction()
        if not self._voice_input:
            self._show_bubble("语音输入不可用", emotion="neutral")
            return

        if not self._voice_recording:
            # 开始录音
            if self._voice_input.start():
                self._voice_recording = True
                self._voice_action.setText("⏹ 停止")
            else:
                self._show_bubble("录音启动失败", emotion="neutral")
        else:
            # 停止录音 -> 识别 -> 发送
            self._voice_action.setText("🎤 说话")
            self._voice_recording = False

            # 在后台线程识别，避免阻塞 UI
            import threading
            def _do_asr():
                text = self._voice_input.stop()
                if text:
                    # 通过引擎发送
                    self._engine.send(text, character=self._current_char)
                    logger.info("Voice input sent: %s", text[:30])
                    # 不显示输入文字，隐藏气泡
                    self.voice_status_signal.emit("")
                    # 截停 TTS
                    self._tts_player.stop()
                    self._is_thinking = True
                    self._pending_chat = True
                    self._pending_user_msg = text
                else:
                    self.voice_status_signal.emit("没听清...")

            t = threading.Thread(target=_do_asr, daemon=True)
            t.start()

    def _on_voice_status(self, msg: str):
        """语音输入状态 - 从后台线程，通过信号转主线程"""
        self.voice_status_signal.emit(msg)

    def _do_voice_status(self, msg: str):
        """在主线程处理语音状态"""
        if msg:
            self._show_bubble(msg, emotion="thinking")
        else:
            try:
                self.bubble.hide_bubble()
            except Exception:
                pass

    def _create_tts_provider(self):
        """根据配置创建 TTS provider，失败返回 None"""
        provider = self.config.get("tts", {}).get("provider", "cosyvoice")
        try:
            if provider == "mimo":
                from tts_provider.mimo_tts import MimoTtsProvider
                from env_config import get_tts_api_config
                cfg = get_tts_api_config()
                if not cfg.get("base_url") or not cfg.get("api_key"):
                    logger.warning(
                        "TTS 引擎设为 MIMO 但 API 配置为空，回退到本地 CosyVoice"
                    )
                    from tts_provider.cosyvoice import CosyVoiceProvider
                    return CosyVoiceProvider()
                mimo = MimoTtsProvider()
                mimo.configure(
                    base_url=cfg["base_url"],
                    api_key=cfg["api_key"],
                    model=cfg.get("model", ""),
                    voice=cfg.get("voice", "default_zh"),
                )
                return mimo
            elif provider == "api":
                from tts_provider.api_tts import ApiTtsProvider
                return ApiTtsProvider()
            else:
                from tts_provider.cosyvoice import CosyVoiceProvider
                return CosyVoiceProvider()
        except Exception as e:
            logger.warning("TTS provider 创建失败 (%s): %s", provider, e)
            return None

    def _tts_provider_signature(self) -> tuple:
        """TTS provider 的身份签名——只有它变了才需要重建实例。

        只纳入决定「创建出哪个 provider 实例」的字段。volume / enabled
        这类运行期开关不算，它们由 _tts_player 直接生效，无需重建。
        """
        tts_cfg = self.config.get("tts", {}) or {}
        provider = tts_cfg.get("provider", "cosyvoice")
        api_sig: tuple = ()
        if provider in ("mimo", "api"):
            try:
                from env_config import get_tts_api_config
                c = get_tts_api_config() or {}
                api_sig = (
                    c.get("base_url", ""), c.get("api_key", ""),
                    c.get("model", ""), c.get("voice", ""),
                )
            except Exception:
                api_sig = ()
        return (provider, api_sig)

    def _maybe_reload_tts_provider(self):
        """按需重建 TTS provider —— 构造与预热全部在后台线程完成。

        绝不能在 Qt 主线程调 _create_tts_provider()：cosyvoice 分支的
        import 链（funasr → torch/lightning/diffusers → onnxruntime → wetext
        → ModelScope 下载）会把事件循环冻住几十秒。
        """
        if not self._engine:
            return

        sig = self._tts_provider_signature()
        if sig == getattr(self, "_tts_provider_sig", None):
            return  # 配置没变：跳过，避免重复拉起重型依赖
        self._tts_provider_sig = sig

        # 代际号：连续保存时只有最后一次的结果会被采纳
        self._tts_reload_gen = getattr(self, "_tts_reload_gen", 0) + 1
        gen = self._tts_reload_gen
        old = getattr(self._engine, "_tts", None)

        def _discard(p):
            if p is None:
                return
            try:
                p.cleanup()
            except Exception:
                pass

        def _rebuild():
            provider = None
            try:
                provider = self._create_tts_provider()   # 重型构造，后台执行
                if gen != self._tts_reload_gen:
                    _discard(provider)                   # 期间又保存过，本次作废
                    return
                if provider is not None:
                    provider.preload()
                if gen != self._tts_reload_gen:
                    _discard(provider)
                    return
                self._engine._tts = provider
                self._engine._tts_ready = bool(provider is not None and provider.is_ready)
                logger.info(
                    "TTS provider 已切换: %s (ready=%s)",
                    getattr(provider, "name", None), self._engine._tts_ready,
                )
            except Exception as e:
                logger.warning("TTS provider 重建失败: %s", e)
                _discard(provider)
            finally:
                # 旧实例只有确实被换下来时才释放
                if old is not None and old is not getattr(self._engine, "_tts", None):
                    _discard(old)

        threading.Thread(target=_rebuild, name="TTSReload", daemon=True).start()

    def _create_asr_provider(self):
        """根据配置创建 ASR provider，失败返回 None"""
        provider = self.config.get("asr", {}).get("provider", "whisper_local")
        try:
            if provider == "mimo":
                from asr_provider.mimo_asr import MimoAsrProvider
                from env_config import get_asr_api_config
                mimo = MimoAsrProvider()
                cfg = get_asr_api_config()
                mimo.configure(
                    base_url=cfg.get("base_url", ""),
                    api_key=cfg.get("api_key", ""),
                    model=cfg.get("model", ""),
                )
                return mimo
            elif provider == "api":
                from asr_provider.api_asr import ApiAsrProvider
                return ApiAsrProvider()
            else:
                from asr_provider.whisper_local import WhisperLocalProvider
                return WhisperLocalProvider()
        except Exception as e:
            logger.warning("ASR provider 创建失败 (%s): %s", provider, e)
            return None

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

    # ── 事件过滤器:统一处理点按/拖拽 ──

    def eventFilter(self, obj, event):
        if obj is self.char_label:
            t = event.type()
            import time as _time
            _t0 = _time.perf_counter()

            if t == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self._mark_user_interaction()
                    # 退出坐下状态
                    if self._is_sitting:
                        self._exit_sitting()
                    self._drag_start_cursor = QCursor.pos()
                    self._drag_start_window = self.pos()
                    self._is_dragging = False
                    self._was_click = True
                    # 抚摸手势：记录按下时刻与位置
                    self._pet_press_time = _time.perf_counter()
                    self._pet_press_pos = QCursor.pos()
                    self._pet_cuddle = False
                    self._pet_stroke_count = 0
                _elapsed = (_time.perf_counter() - _t0) * 1000
                if _elapsed > 16:
                    logger.warning("eventFilter[press] slow: %.1fms", _elapsed)
                return True

            elif t == QEvent.MouseMove:
                if (event.buttons() & Qt.LeftButton) and self._was_click:
                    self._stop_walking()
                    cursor = QCursor.pos()
                    delta = cursor - self._drag_start_cursor
                    if delta.manhattanLength() > 5 and not self._is_dragging:
                        self._is_dragging = True
                        sfx_play("pickup")
                        self._was_click = False
                        self._cancel_pending_click()
                        self.char_label.setCursor(QCursor(Qt.ClosedHandCursor))
                        self._drag_poll_timer.start(16)
                    if self._is_dragging:
                        self.move(self._drag_start_window + delta)
                    elif not self._is_dragging:
                        # 抚摸：按住不动 → 进入撸猫模式，小幅移动记一次抚摸
                        held = _time.perf_counter() - self._pet_press_time
                        moved = (cursor - self._pet_press_pos).manhattanLength()
                        if held > 0.3 and moved < 16:
                            if not self._pet_cuddle:
                                self._pet_cuddle = True
                                self._cancel_pending_click()
                            now = _time.perf_counter()
                            if now - self._pet_last_stroke > 0.22:
                                self._pet_last_stroke = now
                                self._on_pet_stroke()
                return True

            elif t == QEvent.MouseButtonRelease:
                if event.button() == Qt.LeftButton:
                    self._drag_poll_timer.stop()
                    if self._is_dragging:
                        self.char_label.setCursor(QCursor(Qt.ArrowCursor))
                        self._is_dragging = False
                        self._store_label_pos()

                        # ── 弹跳:释放时计算拖拽速度 ──
                        now = time.time()
                        cursor = QCursor.pos()
                        dt = now - self._drag_last_time
                        if dt > 0 and dt < 0.2:
                            dx = cursor.x() - self._drag_last_pos.x()
                            dy = cursor.y() - self._drag_last_pos.y()
                            vx = dx / dt * 0.02
                            vy = dy / dt * 0.02
                            speed = math.sqrt(vx ** 2 + vy ** 2)
                            if speed > 1.5:
                                self._bounce_active = True
                                self._is_walking = False
                                self._motion_state = "bounce"
                                self._set_anim_seq('walk')
                                self._physics.start_bounce(vx, vy)
                                sfx_play("bounce")
                            else:
                                self._bounce_active = False
                                sfx_play("drop")
                        else:
                            self._bounce_active = False

                        # ── 边缘吸附坐下 ──
                        edge = self._check_edge_sitting()
                        if edge and not self._bounce_active:
                            self._enter_sitting(edge)
                            self._was_click = False
                            return True

                        # 如果不在边缘，退出坐下状态
                        if self._is_sitting:
                            self._exit_sitting()

                        pos = self.pos()
                        self.config.setdefault("window", {})["x"] = pos.x()
                        self.config.setdefault("window", {})["y"] = pos.y()
                        save_config(self.config)
                        if self._on_position_change:
                            self._on_position_change(pos.x(), pos.y())
                    elif self._pet_cuddle:
                        # 撸猫会话结束（抚摸已在移动中累计）
                        self._pet_cuddle = False
                        self._cancel_pending_click()
                    elif self._was_click:
                        # 延迟触发聊天，留给双击(抚摸)取消
                        self._schedule_pending_click()
                        self._motion_state = "idle"
                    self._was_click = False
                _elapsed = (_time.perf_counter() - _t0) * 1000
                if _elapsed > 16:  # 超过一帧的时间才告警
                    logger.warning("eventFilter[release] slow: %.1fms", _elapsed)
                return True

            elif t == QEvent.MouseButtonDblClick:
                # 双击 = 抚摸（摸一下）；取消尚未触发的单击聊天
                self._cancel_pending_click()
                self._on_pet_pat()
                return True

        return super().eventFilter(obj, event)

    # ── 拖拽轮询 ──

    def _drag_poll_tick(self):
        """拖拽时每 16ms 轮询鼠标位置(不掉事件)"""
        if self._is_dragging:
            cursor = QCursor.pos()
            delta = cursor - self._drag_start_cursor
            self.move(self._drag_start_window + delta)
            # 记录用于释放后速度估算
            self._drag_last_pos = cursor
            self._drag_last_time = time.time()

    # ── 窗口边缘吸附坐下 ──

    SIT_THRESHOLD = 30   # 距离边缘多少 px 触发吸附
    SIT_ROTATE = 12      # 坐下时旋转角度

    def _check_edge_sitting(self) -> str | None:
        """检查是否靠近屏幕边缘，返回边缘方向或 None"""
        sg = self._current_screen_geometry()
        pos = self.pos()
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        # 检查四条边
        if y <= sg.top() + self.SIT_THRESHOLD:
            return "top"
        if y + h >= sg.bottom() - self.SIT_THRESHOLD:
            return "bottom"
        if x <= sg.left() + self.SIT_THRESHOLD:
            return "left"
        if x + w >= sg.right() - self.SIT_THRESHOLD:
            return "right"
        return None

    def _enter_sitting(self, edge: str):
        """吸附到窗口边缘并进入坐下状态"""
        sg = self._current_screen_geometry()
        pos = self.pos()
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        # 吸附到对应边缘
        if edge == "bottom":
            y = sg.bottom() - h
        elif edge == "top":
            y = sg.top()
        elif edge == "left":
            x = sg.left()
        elif edge == "right":
            x = sg.right() - w

        self.move(x, y)
        self._is_sitting = True
        self._sitting_edge = edge
        self._stop_walking()
        self._motion_state = "sitting"

        # 应用旋转效果（朝边缘方向倾斜）
        self._apply_sitting_rotation(edge)

        # 保存位置
        self.config.setdefault("window", {})["x"] = x
        self.config.setdefault("window", {})["y"] = y
        save_config(self.config)
        if self._on_position_change:
            self._on_position_change(x, y)

        logger.info("Sitting on %s edge", edge)

    def _exit_sitting(self):
        """退出坐下状态"""
        if not self._is_sitting:
            return
        self._is_sitting = False
        self._sitting_edge = ""
        self._motion_state = "idle"

        # 移除旋转
        self.char_label.setGraphicsEffect(None)
        # 恢复帧渲染
        self._renderer._show_frame()

        logger.info("Stopped sitting")

    def _apply_sitting_rotation(self, edge: str):
        """坐下时应用视觉旋转效果"""
        from PySide6.QtWidgets import QGraphicsRotation, QGraphicsProxyWidget
        # 简单方案：用 transform 旋转 char_label 的 pixmap
        frames = self._renderer._frames.get(self._renderer._anim_seq, [])
        if not frames:
            return
        pix = frames[self._renderer._anim_idx % len(frames)]
        ls = self.char_label.size()
        if ls.width() > 0 and ls.height() > 0:
            pix = pix.scaled(ls.width(), ls.height(),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)

        # 根据边缘方向旋转
        angle = {
            "bottom": self.SIT_ROTATE,     # 底部：向右倾
            "top": -self.SIT_ROTATE,       # 顶部：向左倾
            "left": self.SIT_ROTATE,       # 左边：向右倾
            "right": -self.SIT_ROTATE,     # 右边：向左倾
        }.get(edge, 0)

        transform = QTransform()
        cx = pix.width() // 2
        cy = pix.height() // 2
        transform.translate(cx, cy)
        transform.rotate(angle)
        transform.translate(-cx, -cy)
        rotated = pix.transformed(transform, Qt.SmoothTransformation)
        if not self._renderer._facing_right:
            rotated = rotated.transformed(QTransform().scale(-1, 1))
        self.char_label.setPixmap(rotated)

    # ── 聊天交互 ──

    def _stop_walking(self):
        self._is_walking = False
        self._bounce_active = False
        self._physics.stop()
        # _unified_timer 保持运行（idle 时 tick 直接 return）
        self._motion.reset()
        self._set_anim_seq('idle')

    # ── 抚摸 / 喂食 / 状态 HUD ──────────────────────────────

    def _schedule_pending_click(self):
        """延迟触发单击=聊天，给双击(抚摸)留取消窗口"""
        self._pending_click = True
        self._click_timer.start(250)

    def _fire_pending_click(self):
        if self._pending_click:
            self._pending_click = False
            self._toggle_chat()
            self._motion_state = "idle"

    def _cancel_pending_click(self):
        if self._pending_click:
            self._pending_click = False
            self._click_timer.stop()

    def _head_local_point(self) -> QPoint:
        """宠物头顶的窗口局部坐标（爱心/表情的原点）"""
        top = self._get_char_top_y()
        return QPoint(self.width() // 2, top)

    def _reposition_overlays(self):
        """把 HUD / 情绪脸定位到头顶，避免在屏幕边缘重叠"""
        if not hasattr(self, '_status_hud'):
            return
        top = self._get_char_top_y()
        cx = self.width() // 2
        face_h = self._emotion_face.height()
        hud_h = self._status_hud.height()

        # 情绪脸：头顶正上方
        face_y = max(top - face_h - 6, 2)
        self._emotion_face.move(cx - self._emotion_face.width() // 2, face_y)

        # 状态 HUD：
        #   - pinned（用户右键勾选常驻）→ 放头顶，方便随时查看
        #   - 临时闪现（喂食/抚摸）→ 放窗口右下角，不遮挡桌宠主体
        if getattr(self, '_hud_pinned', False):
            hud_y = top - hud_h - face_h - 12
            if hud_y <= face_h + 8:
                hud_x = cx + 10
                hud_y = max(top + 8, 2)
                if hud_x + self._status_hud.width() > self.width():
                    hud_x = max(cx - self._status_hud.width() // 2, 2)
            else:
                hud_x = cx - self._status_hud.width() // 2
            self._status_hud.move(hud_x, max(hud_y, 2))
        else:
            hud_x = max(self.width() - self._status_hud.width() - 6, 2)
            hud_y = max(self.height() - self._status_hud.height() - 6, 2)
            self._status_hud.move(hud_x, hud_y)

    def _on_pet_pat(self):
        """双击 = 摸一下：开心反应 + 连击累计"""
        self._mark_user_interaction()
        sfx_play("pat")
        self._pet_combo += 1
        self._pet_combo_timer.start(2500)  # 2.5s 内继续摸算连击
        big = self._pet_combo >= 5
        if hasattr(self, '_state_mgr') and self._state_mgr:
            if big:
                self._state_mgr.apply_item_effect({"mood": 6, "likability": 3})
            else:
                self._state_mgr.apply_item_effect({"mood": 2, "likability": 1})
        self._pet_play_happy(big=big)
        head = self._head_local_point()
        self._heart_overlay.burst(count=6 if big else 2, x=head.x(), y=head.y())
        if big:
            self._show_sticker("💕", "最喜欢主人了！")
        elif self._pet_combo == 1:
            import random
            self._show_bubble(random.choice(["喵~", "呼噜呼噜~", "好舒服~"]), emotion="happy")

    def _on_pet_stroke(self):
        """按住不动 = 连续撸：涓流心情 + 单颗爱心"""
        if hasattr(self, '_state_mgr') and self._state_mgr:
            self._state_mgr.apply_item_effect({"mood": 1, "likability": 0.5})
        self._pet_stroke_count += 1
        sfx_play("pet")
        self._pet_play_happy(big=False, revert=400, style="snap")
        head = self._head_local_point()
        self._heart_overlay.burst(count=1, x=head.x(), y=head.y())

    def _pet_play_happy(self, big=False, revert=650, seq=None, style="spring", surface=True):
        if seq is None:
            seq = "jumping" if big else "waving"
        self._set_anim_seq(seq, emotion="happy", style=style)
        # happy 比常规交互更突出，使用相对增益，母线音量由设置页 sfx 滑块控制
        sfx_play("happy", gain=0.6 if big else 0.42)
        # surface=False：只做动画肢体反应，不弹情绪脸（用于 idle 自发摇摇，避免机械感）
        if surface:
            self._set_surface_emotion("happy", duration_ms=revert)
        self._pet_revert_timer.stop()
        self._pet_revert_timer.start(revert)

    def _pet_revert(self):
        self._set_anim_seq("idle", emotion="neutral", style="snap")

    def _reset_pet_combo(self):
        self._pet_combo = 0

    def _quick_feed(self):
        """免费基础投喂：直接回流饥饿/口渴/心情，不依赖抽卡与钱"""
        self._mark_user_interaction()
        if hasattr(self, '_state_mgr') and self._state_mgr:
            self._state_mgr.apply_item_effect({"hunger": 35, "thirst": 12, "mood": 4})
        # 喂食时弹出「吃饭糰」插画贴纸（B：场景图转气泡贴图）
        base = os.path.dirname(os.path.abspath(__file__))
        sticker = os.path.join(base, "characters", "yuexinmiao", "stickers", "eat_moment.png")
        if os.path.exists(sticker) and hasattr(self, '_bubble'):
            self._bubble.set_sticker_image(sticker, "🍙 好吃~")
        else:
            self._show_bubble("🍙 好吃~", emotion="happy")
        # 喂食专属吃动作（route B 反应帧）；无 eat 帧时安全回退到挥手
        self._pet_play_happy(big=False, revert=700, seq="eat")
        self._flash_status_hud()

    # ── 状态 HUD / 主题跟随已迁移至 pet_mixins/status_hud_mixin.py（StatusHudMixin）──

    def _toggle_chat(self):
        self._stop_walking()
        if self.input_widget.isVisible():
            self.input_widget.hide()
            self.input_field.clear()
        else:
            self.input_widget.show()
            self.input_widget.raise_()
            self.input_field.setFocus()

    def _send_message(self):
        text = self.input_field.text().strip()
        if not text or self._is_thinking:
            return

        self._mark_user_interaction()
        self.input_field.clear()
        self.input_widget.hide()

        # P2: 用户交互 -> 重置情绪状态机
        try:
            self._perception.reset_emotion()
        except Exception:
            pass

        # 标记对话时间（主动对话用）
        if self._perception.proactive:
            self._perception.proactive.mark_conversation()

        # ── 用户发新消息 → 立即截停旧 TTS(P2 可中断管线)──
        self._tts_player.stop()

        # 通过对话引擎发送（异步）
        if self._engine:
            self._engine.send(text, character=self._current_char)

        self.bubble.set_text("⏳ 思考中...")
        self._reposition_bubble()
        self.bubble.show()
        self.bubble.raise_()
        self._is_thinking = True
        self._pending_user_msg = text
        self._pending_emotion = "neutral"
        self._pending_chat = True

        # 立即切换到思考动画（视觉反馈）
        try:
            self._set_anim_seq("working", emotion="thinking", style=get_transition_style("thinking"))
        except Exception:
            pass

        # 超时保护：30 秒无回复自动恢复
        if not hasattr(self, '_think_timeout'):
            from PySide6.QtCore import QTimer as _QTimer
            self._think_timeout = _QTimer()
            self._think_timeout.setSingleShot(True)
            self._think_timeout.timeout.connect(self._on_think_timeout)
        # M4: Hanako 模式下默认 180 秒（长任务支持）；直连模式保持 30 秒
        think_timeout_ms = 30000
        try:
            if hasattr(self._engine, '_adapter') and self._engine._adapter:
                if getattr(self._engine._adapter, 'transport_mode', 'direct') != 'direct':
                    think_timeout_ms = int(
                        getattr(self._engine._adapter, '_reply_timeout', 180) * 1000
                    )
        except Exception:
            pass
        self._think_timeout.start(think_timeout_ms)

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

        # 动画
        try:
            self._set_anim_seq(anim, emotion=emotion, style=get_transition_style(emotion))
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

    def _create_new_session(self):
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
        save_config(self.config)
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
        save_config(self.config)

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

    def _mark_user_interaction(self):
        """记录用户活动，并让未送达的自言自语失效。"""
        self._last_interaction = time.time()
        self._last_user_interaction_mono = time.monotonic()
        self._idle_stage = None
        idle_chatter = getattr(self, "_idle_chatter", None)
        if idle_chatter:
            idle_chatter.reset()

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

    def _foreground_tick(self):
        """每 2 秒检测前台窗口"""
        logger.debug("_foreground_tick called")
        try:
            self._foreground_watcher.tick()
        except Exception as e:
            logger.error("_foreground_tick error: %s", e)

    def _on_foreground_change(self, app_name: str, app_category: str, title: str):
        """前台窗口变化 → 重置 idle 计时器 + 窗口互动 + 事件触发截图"""
        going = self._idle_stage
        self._mark_user_interaction()
        if going is not None:
            self._show_bubble("你回来啦~", emotion="happy")
        
        # 窗口互动：桌宠靠近当前窗口（带冷却）
        if hasattr(self, '_window_interaction'):
            wi_config = self.config.get('window_interaction', {})
            if wi_config.get('enabled', True):
                cooldown = wi_config.get('cooldown_seconds', 30)
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

    def _on_proactive_trigger(self, prompt_text: str):
        """Proactive 调度器触发 -> 发送给模型生成回复 + TTS"""
        logger.info("Proactive trigger: %s", prompt_text)
        EventBus.emit("proactive_triggered", target="scheduler")
        
        # 将触发条件发送给对话引擎，让模型生成符合人格的回复
        if self._engine:
            # 包装成用户输入，让模型生成回复
            proactive_prompt = f"[主动对话触发] {prompt_text}\n\n请根据你的人格设定，生成一段简短的、有个性的回应。不要复述触发消息，而是自然地表达你的想法。"
            self._engine.send(proactive_prompt, character=self._current_char, source="proactive")
            
            # 显示思考中气泡
            self._show_bubble("⏳ 思考中...", emotion="thinking")
            self._is_thinking = True
        
        # 触发动画
        self._set_anim_seq("waving", emotion="happy", style=get_transition_style("happy"))



    # ── 鼠标交互反应 ──

    _mouse_reaction_cooldown: float = 0.0  # 上次反应时间

    def _get_window_rect(self) -> tuple[int, int, int, int] | None:
        """返回角色窗口 (x, y, w, h)，供 MouseTracker 使用"""
        p = self.pos()
        s = self.size()
        return (p.x(), p.y(), s.width(), s.height())

    def _gaze_tick(self):
        """每 50ms 更新视线跟随（平滑偏移）"""
        if not hasattr(self, '_renderer'):
            return
        params = self._mouse_reaction_params
        if params.gaze_enabled and self._mouse_tracker.is_nearby:
            state = self._mouse_tracker.state
            self._renderer.look_at(state.x, state.y)
        else:
            self._renderer.update_gaze()

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
        """鼠标在角色附近静止 - 只切动画"""
        params = self._mouse_reaction_params
        if not params.react_hover:
            return
        if self._is_thinking:
            return
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
        """待机时偶发微动作 + 有性格的表情节奏。

        表情脸性格化：不再每次 idle 都机械弹 happy，而是
        - 安静时极低概率自发微表情(眨眼/偷笑)，体现「活的」
        - 摇摇用害羞脸而非机械 happy，且受静默冷却约束
        - 用户强交互(摸/夸/找到你)仍走 _current_emotion 立即弹脸
        """
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
        self._idle_action_cd = random.uniform(14, 32)

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
            anim_map = {
                'happy': 'waving', 'surprised': 'jumping',
                'thinking': 'running', 'sad': 'failed',
            }
            anim = anim_map.get(emotion, 'idle')
            if anim in self._renderer._frames:
                self._set_anim_seq(anim, emotion=emotion, style=get_transition_style(emotion))
                self._set_surface_emotion(emotion, duration_ms=3000)
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

    def _show_bubble_impl(self, text: str, emotion: str = "neutral", priority: int = 0):
        """气泡实现体（仅限主线程调用；相同内容不重复刷新，高优先级不被低优先级覆盖）"""
        if not text or not hasattr(self, 'bubble'):
            return
        # 节流：相同内容且气泡可见时不重复设置
        if text == self._bubble_message and self.bubble.isVisible():
            logger.debug("Bubble throttle: same text still visible")
            self._bubble_timer.start(6000)  # 只续期
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
            self._bubble_timer.start(6000)
        except Exception:
            logger.exception("Show bubble failed")

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
        try:
            if anim_name != self._current_anim:
                safe_anims = ['idle', 'walk', 'extra']
                if anim_name not in safe_anims:
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


    # ── 窗口关闭清理 ──

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



