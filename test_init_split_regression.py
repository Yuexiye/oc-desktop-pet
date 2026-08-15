"""回归测试：__init__ 拆分后无残留局部变量引用（NameError 防护）

背景：d4285b9 拆分 __init__ 为 _init_* 方法时，_init_visual_startup 里残留了
局部变量 proactive_cfg（原 __init__ 中段定义），启动即 NameError（用户实测
"没有桌宠出现"）。此测试确保拆出的方法内不引用未定义的裸变量。

运行: python -m pytest test_init_split_regression.py -v
"""
import ast
import os
import sys
import re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

PET_PATH = Path(__file__).parent / "pet.py"
SRC = PET_PATH.read_text(encoding="utf-8")

# 已知的合法裸变量（builtin/模块级 import/参数/lambda 参数）
BUILTIN_OK = {
    # builtins
    "int", "float", "str", "bool", "len", "max", "min", "sum", "abs", "round",
    "any", "all", "getattr", "hasattr", "setattr", "isinstance", "issubclass",
    "type", "repr", "sorted", "enumerate", "zip", "map", "filter", "list",
    "dict", "set", "tuple", "range", "reversed", "next", "iter", "open", "print",
    "Exception", "ValueError", "TypeError", "KeyError", "AttributeError",
    "RuntimeError", "NameError", "ImportError", "OSError", "None", "True", "False",
    # 模块级 import（pet.py 顶部）
    "os", "sys", "time", "random", "json", "math", "re", "threading", "Path",
    "QPoint", "QTimer", "QIcon", "QPixmap", "QPainter", "QColor", "QMenu",
    "QCursor", "QWheelEvent", "QSystemTrayIcon", "Signal", "QApplication",
    "Qt", "QFont", "QSize", "QRect", "QTransform",
    # 常用类/函数
    "logger", "load_config", "save_config", "async_config_saver",
    "HanakoMonitor", "ActionLinker", "ForegroundWatcher", "ProactiveScheduler",
    "PerceptionController", "MouseTracker", "MOUSE_REACTIONS", "ConversationEngine",
    "VoiceInput", "TTSTtsPlayer", "PetAudioBridge", "TransitionEngine",
    "PhysicsEngine", "MotionStateMachine", "WindowInteraction", "IdleChatter",
    "_voice_available", "preload_whisper", "get_transition_style",
    "CHARACTER_INFO", "default_marker_path", "maybe_greet",
    # 信号（类属性，SafeExpression 只查方法内，类属性名需放行）
    "tool_progress_signal", "bubble_signal", "mission_bubble_signal",
    "level_up_signal", "engine_reply_signal", "engine_status_signal",
    "voice_status_signal", "screen_emotion_signal", "screen_proactive_signal",
    "screen_update_signal", "hanako_state_signal", "idle_chatter_signal",
    # 枚举/常量
    "ModeType", "GachaRarity", "ItemType",
}

# 应检查的方法（__init__ 拆出的 7 个 + 本身）
INIT_METHODS = [
    "_init_diag_switches", "_init_states", "_init_schedulers",
    "_init_interaction", "_init_engine", "_init_voice_audio",
    "_init_visual_startup",
]


def _method_body(name: str) -> str:
    """提取方法体源码"""
    m = re.search(rf"    def {name}\(self.*?\):\n(.*?)(?=\n    def |\nclass )", SRC, re.S)
    assert m, f"未找到方法 {name}"
    return m.group(1)


def _free_names(body: str) -> set[str]:
    """收集方法体内'裸变量引用'（非 self.x、非关键字）"""
    tree = ast.parse("def _f():\n" + body)
    func = tree.body[0]
    # 收集所有 Name(Load)
    loads = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    # 收集函数内赋值的名字（Store）——局部定义
    stores = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores.add(node.id)
    # lambda 参数
    lambda_args = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Lambda):
            for a in node.args.args:
                lambda_args.add(a.arg)
    # 收集函数内 def 的参数（含嵌套）
    def_args = set()
    for node in ast.walk(func):
        if isinstance(node, ast.FunctionDef):
            for a in node.args.args:
                def_args.add(a.arg)
    # except Exception as e → e 已定义
    except_names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.name:
            except_names.add(node.name)
    # from X import Y → Y 已定义（局部导入，如 ActivityTracker）
    import_names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.ImportFrom) and node.names:
            import_names.update(a.asname or a.name for a in node.names)
    return (loads - stores - lambda_args - def_args - except_names - import_names
            - {"self"}), stores


@pytest.mark.parametrize("method", INIT_METHODS)
def test_init_method_no_undefined_names(method):
    """拆出的 _init_* 方法不应引用未定义裸变量（防 NameError 回归）"""
    body = _method_body(method)
    free, _ = _free_names(body)
    # 排除已知合法名
    unknown = free - BUILTIN_OK
    # 排除 self.X 形式（ast 已拆分，self 本身被排除；self.x 是 Attribute 不算 Name）
    assert not unknown, (
        f"{method} 引用了可能未定义的裸变量: {sorted(unknown)}\n"
        f"（这会导致启动 NameError，如 proactive_cfg）"
    )


def test_proactive_cfg_is_self_attr():
    """proactive_cfg 必须以 self._proactive_cfg 形式访问（已知回归点）"""
    body = _method_body("_init_visual_startup")
    # 不应出现裸 proactive_cfg（非 self. 前缀）
    for line in body.splitlines():
        s = line.strip()
        if "proactive_cfg" in s and not s.startswith("#"):
            assert "self._proactive_cfg" in s, (
                f"发现裸 proactive_cfg 引用（应改 self._proactive_cfg）: {s}"
            )
