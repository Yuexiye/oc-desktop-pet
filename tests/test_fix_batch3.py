"""评审批次3：2 项微修的回归测试（offscreen Qt）

覆盖：
- B3-1 launcher _on_signal 退出前清理就绪哨兵（防 pid 复用"瞬时就绪"误判）
- B3-2 chat_mixin _do_asr 后台线程状态写入挪主线程（chat_state_signal）

运行: QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fix_batch3.py -v
"""
import os
import re
import sys
import threading
import time
import types
from pathlib import Path

# 项目根加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────── B3-1：launcher 信号退出清理哨兵 ─────────────────────────
def test_launcher_signal_cleans_ready_flag():
    """_on_signal 在 sys.exit(0) 前调用 _remove_ready_flag(child.pid)（源码级防回归）"""
    src = (ROOT / "launcher.py").read_text(encoding="utf-8")
    m = re.search(r"def _on_signal\(signum, _frame\):\n(.*?)(?=\n    signal\.signal)", src, re.S)
    assert m, "未找到 _on_signal"
    body = m.group(1)
    assert "_remove_ready_flag(child.pid)" in body, "信号退出必须清理就绪哨兵"
    assert "if child is not None:" in body, "清理前必须判 child 存在"
    assert body.index("_remove_ready_flag(child.pid)") < body.index("sys.exit(0)"), \
        "清理哨兵必须在 sys.exit 之前"


# ───────────────────────── B3-2：_do_asr 状态写入挪主线程 ─────────────────────────
def test_do_asr_no_direct_state_write():
    """_do_asr（后台线程）不再直写 _is_thinking/_pending_*/_record_topic"""
    src = (ROOT / "pet_mixins" / "chat_mixin.py").read_text(encoding="utf-8")
    m = re.search(r"def _toggle_voice\(self\):\n(.*?)(?=\n    def )", src, re.S)
    assert m, "未找到 _toggle_voice"
    body = m.group(1)
    asr_block = body.split("def _do_asr()", 1)[1].split("t = threading.Thread", 1)[0]
    for attr in ("self._is_thinking = True", "self._pending_chat = True",
                 "self._pending_user_msg =", "self._record_topic("):
        assert attr not in asr_block, f"后台线程 _do_asr 不应直写 {attr}"
    assert "self.chat_state_signal.emit(text)" in asr_block, "状态更新应经 chat_state_signal"


def test_do_chat_state_updates_state_and_records_topic():
    """_do_chat_state 主线程槽：更新状态 + 记录话题"""
    import pet
    w = pet.PetWindow.__new__(pet.PetWindow)
    topics = []
    w._record_topic = lambda t: topics.append(t)
    pet.PetWindow._do_chat_state(w, "你好世界")
    assert w._is_thinking is True
    assert w._pending_chat is True
    assert w._pending_user_msg == "你好世界"
    assert topics == ["你好世界"]
    # 空文本直接返回，不污染状态
    pet.PetWindow._do_chat_state(w, "")
    assert topics == ["你好世界"]
    assert w._pending_user_msg == "你好世界"


def test_chat_state_signal_cross_thread_delivery():
    """后台线程 emit chat_state_signal → 队列送达主线程 → 更新状态+记录话题"""
    from PySide6.QtCore import QObject, Signal

    import pet

    class _Host(QObject):
        chat_state_signal = Signal(str)

    host = _Host()
    w = pet.PetWindow.__new__(pet.PetWindow)
    topics = []
    w._record_topic = lambda t: topics.append(t)
    host.chat_state_signal.connect(pet.PetWindow._do_chat_state.__get__(w))
    threading.Thread(target=lambda: host.chat_state_signal.emit("跨线程语音")).start()
    deadline = time.time() + 3
    while time.time() < deadline and not getattr(w, "_pending_user_msg", None):
        _app.processEvents()
        time.sleep(0.01)
    assert w._pending_user_msg == "跨线程语音", "后台线程 emit 后主线程应更新状态"
    assert w._is_thinking is True
    assert w._pending_chat is True
    assert topics == ["跨线程语音"]


def test_chat_state_signal_present_and_connected():
    """PetWindow 提供 chat_state_signal + _init_engine 接线"""
    import pet
    assert hasattr(pet.PetWindow, "chat_state_signal")
    assert hasattr(pet.PetWindow, "_do_chat_state")
    pet_src = (ROOT / "pet.py").read_text(encoding="utf-8")
    assert "self.chat_state_signal.connect(self._do_chat_state)" in pet_src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nALL FIX-BATCH-3 CHECKS PASSED ({len(fns)} tests)")
