"""ActivityTracker — 键盘/鼠标全局活动感知（Windows）

补上桌宠感知层最廉价的一环：用户在打字、在点鼠标、还是完全离开。
零 API 成本，全部用 Windows 原生 API。

信号:
    - last_input_time  全局最后一次输入（键盘/鼠标）时间戳
    - idle_seconds     完全空闲时长（秒）
    - typing           最近 WINDOW 秒内有过键盘输入（新按下，非持续按住）
    - mouse_active     最近 WINDOW 秒内有过鼠标输入（移动/点击/滚轮）
    - state            'typing' / 'mouse' / 'idle' / 'unknown'(非 Windows)

用法:
    tracker = ActivityTracker()
    tracker.tick()            # 每 200~500ms 调用
    st = tracker.state        # 当前活动状态
    if st.idle_seconds > 300: # 用户离开很久
        ...

注意:
    - GetAsyncKeyState 只对"当前是否有新按下"有意义；连续 tick 时
      通过上次状态对比检测新按下，避免持续按住也被算成打字。
    - 排除修饰键（Shift/Ctrl/Alt/Win）与功能键（F1..F24、Esc 等），
      聚焦实际打字/操作键。
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────
WINDOW = 5.0            # 活动判定窗口（秒）：窗口内有输入即算 active
TYPING_MIN_INPUTS = 2   # 窗口内最少新按键数才算"打字"（防单键误判）

_IS_WINDOWS = sys.platform == "win32"

# ── Windows 常量 ──────────────────────────────────────────
_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02
_VK_MBUTTON = 0x04
_VK_XBUTTON1 = 0x05
_VK_XBUTTON2 = 0x06

# 忽略的键：修饰键 + 功能键 + 控制键（避免长期按住 Shift 被算作打字）
_IGNORED_KEYS = {
    # 修饰键
    0x10, 0x11, 0x12, 0x5B, 0x5C, 0x5D,   # Shift Ctrl Alt WinL WinR Menu
    # 功能键
    *range(0x70, 0x88),                   # F1..F24 (0x70-0x87)
    0x1B,                                 # Esc
    # 控制/编辑键
    0x08, 0x09, 0x0D, 0x13, 0x14, 0x20,  # Backspace Tab Enter Pause CapsLock Space
    0x2D, 0x2E,                           # Insert Delete
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PgUp PgDn End Home Arrows
    0x90, 0x91,                           # NumLock ScrollLock
    *range(0x2A, 0x2C),                   # PrintScreen SysRq
}
_KEY_RANGE = range(0x01, 0xFF)  # 全部虚拟键


@dataclass
class ActivityState:
    """全局活动状态快照"""
    last_input_time: float = 0.0   # 全局最后输入时间（Unix 时间戳）
    idle_seconds: float = 0.0      # 完全空闲时长
    typing: bool = False           # 窗口内是否有键盘输入
    mouse_active: bool = False     # 窗口内是否有鼠标输入
    input_count: int = 0           # 窗口内新输入总数（键盘+鼠标）

    @property
    def state(self) -> str:
        """'typing' / 'mouse' / 'idle' / 'unknown'"""
        if self.typing:
            return "typing"
        if self.mouse_active:
            return "mouse"
        if self.idle_seconds > 0:
            return "idle"
        return "unknown"


class ActivityTracker:
    """全局键盘/鼠标活动追踪器（每 tick 轮询 Windows API）"""

    def __init__(self):
        self._state = ActivityState()
        self._prev_key_held: set[int] = set()  # 上次检测到按住的键
        self._input_times: list[float] = []    # 窗口内的输入时间戳（去抖）
        self._last_tick: float = 0.0

    @property
    def state(self) -> ActivityState:
        return self._state

    def tick(self):
        """轮询一次。建议每 200~500ms 调用。"""
        now = time.time()
        self._last_tick = now

        if not _IS_WINDOWS:
            self._state = ActivityState(last_input_time=now, idle_seconds=0,
                                        typing=False, mouse_active=False)
            return

        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # ── 全局最后输入时间 ──
            last_input = self._last_input_info(user32, kernel32)
            idle_seconds = max(0.0, now - last_input)

            # ── 窗口内新输入检测 ──
            new_inputs, new_typing, new_mouse = self._poll_inputs(user32, now)
            self._input_times = [t for t in self._input_times if now - t <= WINDOW]
            if new_inputs:
                self._input_times.append(now)
                # 抖动合并：同一瞬间的大量输入只算一次时间点
                self._input_times = sorted(set(self._input_times))

            # 窗口内总输入数（用于打字判定）
            total_inputs = len(self._input_times)

            self._state = ActivityState(
                last_input_time=last_input,
                idle_seconds=idle_seconds,
                typing=total_inputs >= TYPING_MIN_INPUTS and new_typing,
                mouse_active=new_mouse,
                input_count=total_inputs,
            )
        except Exception as e:
            # API 调用失败时退化为 idle，不影响主流程
            logger.debug("ActivityTracker tick error: %s", e)
            self._state = ActivityState(idle_seconds=9999)

    # ── Windows API 封装 ──

    def _last_input_info(self, user32, kernel32) -> float:
        """返回全局最后一次输入的时间戳（Unix）"""
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_ulong)]

        # 声明签名：64 位下 byref 指针必须显式声明，否则被截断
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        user32.GetLastInputInfo.restype = ctypes.c_bool

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return time.time()
        # dwTime 是系统启动后的毫秒数；GetTickCount 在 kernel32，同步对比
        tick_count = kernel32.GetTickCount()
        now = time.time()
        elapsed_ms = (tick_count - lii.dwTime) & 0xFFFFFFFF  # 处理回绕
        return now - elapsed_ms / 1000.0

    def _poll_inputs(self, user32, now: float) -> tuple[int, bool, bool]:
        """轮询虚拟键，返回 (新输入数, 是否打字, 是否鼠标活动)"""
        pressed_now: set[int] = set()
        overall_count = 0
        typing_flag = False
        mouse_flag = False

        for key in _KEY_RANGE:
            if key in _IGNORED_KEYS and key not in (0x01, 0x02, 0x04):
                continue
            # GetAsyncKeyState: 高位为1表示现在按下；低位为1表示按下后未读（新按下）
            res = user32.GetAsyncKeyState(key)
            if res & 0x8000:  # 当前按住
                pressed_now.add(key)
                if res & 0x0001:  # 且是新按下
                    overall_count += 1
                    if key in (_VK_LBUTTON, _VK_RBUTTON, _VK_MBUTTON, _VK_XBUTTON1, _VK_XBUTTON2):
                        mouse_flag = True
                    else:
                        typing_flag = True

        # 检测松开动作（上次按住 → 现在松开也算一次输入）
        released = self._prev_key_held - pressed_now
        prev_held = self._prev_key_held
        self._prev_key_held = pressed_now
        for key in released:
            if key in (_VK_LBUTTON, _VK_RBUTTON, _VK_MBUTTON, _VK_XBUTTON1, _VK_XBUTTON2):
                mouse_flag = True
            elif key not in _IGNORED_KEYS:
                typing_flag = True

        return overall_count, typing_flag, mouse_flag


# 模块级便捷入口（供 ProactiveScheduler 等注入）
_default = None


def get_default() -> ActivityTracker:
    global _default
    if _default is None:
        _default = ActivityTracker()
    return _default