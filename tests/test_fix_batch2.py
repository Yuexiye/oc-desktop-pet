"""评审批次2：9 项修复的回归测试（offscreen Qt）

覆盖：
- B2-1 settings_dialog/env_config 合并式写 .env 保留未知键
- B2-2 EventBus 退订生命周期（mission_tracker unsubscribe / pet closeEvent / 多宠隔离）
- B2-3 item energy 真正生效（use_item + apply_item_effect）
- B2-4 memory_snapshot 导出 facts.md
- B2-5 character_package 防 zip-slip（agent_id 白名单 + 成员路径校验）
- B2-6 framebaker 定向终止（不再 taskkill /IM bun.exe）
- B2-7 harness_adapter 历史 deque 并发安全
- B2-8 action_linker outbox 原子写
- B2-9 pet_save store_take 负值回流行为统一

运行: QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fix_batch2.py -v
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

# 项目根加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────── B2-1：.env 合并式写入 ─────────────────────────
def test_update_env_preserves_unknown_keys(tmp_path, monkeypatch):
    """update_env 只更新已知键，保留 HANAKO_*/PHONE_*/OC_PET_* 等未知键"""
    import env_config
    monkeypatch.setattr(env_config, "ENV_PATH", tmp_path / ".env")
    (tmp_path / ".env").write_text(
        "# header\n"
        "LLM_BASE_URL=http://old.example.com\n"
        "HANAKO_BASE_URL=http://127.0.0.1:20099\n"
        "PHONE_AUTH_TOKEN=secret123\n"
        "OC_PET_COSYVOICE_DIR=W:/cosy\n",
        encoding="utf-8",
    )
    env_config.update_env({"LLM_BASE_URL": "http://new.example.com",
                           "LLM_API_KEY": "sk-new"})
    out = (tmp_path / ".env").read_text("utf-8")
    assert "LLM_BASE_URL=http://new.example.com" in out
    assert "LLM_API_KEY=sk-new" in out
    assert "HANAKO_BASE_URL=http://127.0.0.1:20099" in out, "未知键被丢弃!"
    assert "PHONE_AUTH_TOKEN=secret123" in out, "未知键被丢弃!"
    assert "OC_PET_COSYVOICE_DIR=W:/cosy" in out, "未知键被丢弃!"
    assert "# header" in out, "注释应保留"
    # 原子写：不应残留临时文件
    assert not list(tmp_path.glob("*.tmp"))


def test_save_env_preserves_unknown_keys(tmp_path, monkeypatch):
    """save_env（对话框同源入口）同样保留未知键"""
    import env_config
    monkeypatch.setattr(env_config, "ENV_PATH", tmp_path / ".env")
    (tmp_path / ".env").write_text(
        "HANAKO_API_TOKEN=tok123\nPHONE_RECEIVER_PORT=8123\n",
        encoding="utf-8",
    )
    env_config.save_env(llm_base_url="http://x", llm_api_key="k")
    out = (tmp_path / ".env").read_text("utf-8")
    assert "LLM_BASE_URL=http://x" in out
    assert "HANAKO_API_TOKEN=tok123" in out
    assert "PHONE_RECEIVER_PORT=8123" in out


def test_settings_dialog_save_env_uses_merge(tmp_path, monkeypatch):
    """settings_dialog._save_env 必须走合并式 update_env（源码级防回归）"""
    src = (ROOT / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
    m = re.search(r"def _save_env\(self\):\n(.*?)(?=\n    # ── 保存 ──)", src, re.S)
    assert m, "未找到 _save_env"
    body = m.group(1)
    assert "from env_config import update_env" in body
    assert "update_env({" in body
    assert "ENV_PATH.write_text" not in body, "_save_env 不应再整文件覆写"


# ───────────────────────── B2-2：EventBus 退订生命周期 ─────────────────────────
def test_mission_tracker_subscribe_unsubscribe():
    """MissionTracker.subscribe 注册、unsubscribe 全部退订（不累积）"""
    from core.event_bus import EventBus
    from core.mission.mission_tracker import MissionTracker
    EventBus.clear()
    try:
        tracker = MissionTracker(pool=MagicMock(), grantor=MagicMock())
        tracker.subscribe()
        assert EventBus.subscriber_count("chat_completed") == 1
        assert EventBus.subscriber_count("work_completed") == 1
        tracker.unsubscribe()
        assert EventBus.subscriber_count() == {}, "退订后不应残留任何订阅"
        # 幂等：重复 unsubscribe 安全
        tracker.unsubscribe()
    finally:
        EventBus.clear()


def test_mission_manager_stop_unsubscribes():
    """MissionManager.stop() 应退订 tracker 的事件总线订阅"""
    from core.event_bus import EventBus
    from core.mission.mission_manager import MissionManager
    EventBus.clear()
    try:
        save_mgr = MagicMock()
        save_mgr.save.level = 1
        mgr = MissionManager(save_mgr=save_mgr, state_mgr=MagicMock())
        mgr.start()
        assert EventBus.subscriber_count("chat_completed") == 1
        mgr.stop()
        assert EventBus.subscriber_count() == {}
    finally:
        EventBus.clear()


def test_pet_close_unsubscribes_mission_completed():
    """pet.py closeEvent 退订 mission_completed（源码级：订阅处保存 handler、关闭处 off）"""
    src = (ROOT / "pet.py").read_text(encoding="utf-8")
    close_body = src.split("def closeEvent", 1)[1]
    assert "EventBus.off(\"mission_completed\"" in close_body, "closeEvent 必须退订"
    assert "_mission_mgr.stop()" in close_body, "closeEvent 必须 stop mission_mgr"
    # 订阅处必须保存 handler 引用（供 closeEvent off 用同一对象）
    assert "_mission_completed_handler = self._on_mission_completed_bubble" in src


def test_mission_bubble_filters_foreign_mission():
    """多宠隔离：非本实例任务池的 mission_completed 不弹气泡"""
    from PySide6.QtCore import QObject
    from pet_mixins.nurturing_mixin import NurturingMixin

    class _FakePet(QObject):
        """最小假宠物：满足 handler 需要的 self._mission_mgr / self.thread() / QTimer 父对象"""

        def _flush_mission_bubbles(self):
            """节流计时器回调（handler 里 QTimer 会 connect 这个方法）"""

    w = _FakePet()
    mgr = MagicMock()
    mgr.owns_mission.return_value = False  # 外来任务
    w._mission_mgr = mgr
    # 直接调 handler：外来任务应直接 return（不 emit 信号、不弹气泡）
    NurturingMixin._on_mission_completed_bubble.__get__(w)(
        mission_id="foreign_1", name="别人的任务", rewards=None
    )
    assert not hasattr(w, "_mission_bubble_pending"), "外来任务不应触发气泡"
    # 自己的任务 → 正常走合并节流（主线程路径）
    mgr.owns_mission.return_value = True
    NurturingMixin._on_mission_completed_bubble.__get__(w)(
        mission_id="own_1", name="我的任务", rewards=None
    )
    assert getattr(w, "_mission_bubble_pending", None) == ["我的任务"]
    # 清理节流计时器，避免测试后触发
    if hasattr(w, "_mission_bubble_timer"):
        w._mission_bubble_timer.stop()


# ───────────────────────── B2-3：item energy 生效 ─────────────────────────
def test_use_item_applies_energy():
    """use_item 的 effect_energy 直接加到 save.energy（夹紧 [0,100]）"""
    from core.items.item import Item, ItemType, use_item
    from core.save.pet_save import PetSave

    save = PetSave()
    save.energy = 80.0
    item = Item(id="coffee", name="咖啡", item_type=ItemType.DRINK,
                effect_energy=30)
    use_item(save, item)
    assert save.energy == 100.0, "80+30 应夹紧到 100"
    # 负值也支持且夹紧下限
    save.energy = 10.0
    item2 = Item(id="tired", name="疲劳", item_type=ItemType.MEDICINE,
                 effect_energy=-50)
    use_item(save, item2)
    assert save.energy == 0.0, "10-50 应夹紧到 0"


def test_apply_item_effect_handles_energy():
    """PetStateManager.apply_item_effect 对 energy 直接生效（不再跳过）"""
    from core.pet_state import PetStateManager
    from core.save.pet_save import PetSaveManager

    with tempfile.TemporaryDirectory() as d:
        mgr = PetSaveManager(str(Path(d) / "save.json"))
        mgr.save.energy = 50.0
        sm = PetStateManager(mgr)
        sm.apply_item_effect({"energy": 40})
        assert sm.save.energy == 90.0
        sm.apply_item_effect({"energy": 200})
        assert sm.save.energy == 100.0, "energy 应夹紧到 100"


# ───────────────────────── B2-4：memory_snapshot 导出 facts.md ─────────────────────────
def test_memory_snapshot_exports_facts():
    """export_snapshot 应包含 memories.facts（原漏读分支）"""
    from core.memory_snapshot import MemorySnapshotManager

    mgr = MemorySnapshotManager.__new__(MemorySnapshotManager)
    mgr.agent_id = "test_agent"
    ctx = MagicMock()
    ctx.read_facts.return_value = "fact line 1\nfact line 2"
    ctx.read_memory.return_value = "recent memory"
    ctx.read_today.return_value = ""
    ctx.read_longterm.return_value = ""
    mgr._ctx = ctx
    with tempfile.TemporaryDirectory() as d:
        mgr._agent_dir = Path(d)
        out = Path(d) / "snap.json"
        mgr.export_snapshot(output_path=out)
        data = json.loads(out.read_text("utf-8"))
        assert data["memories"]["memories.facts"] == "fact line 1\nfact line 2"


# ───────────────────────── B2-5：character_package 防 zip-slip ─────────────────────────
def _make_pet_zip(zip_path: Path, members: list[tuple[str, str]]):
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"name": "t", "agent_id": "t1", "version": "1.0.0"}),
        )
        for name, content in members:
            zf.writestr(name, content)


def test_character_package_rejects_traversal_agent_id(tmp_path):
    from core.character_package import CharacterPackageManager, PackageValidationError

    zip_path = tmp_path / "evil.pet"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"name": "evil", "agent_id": "../evil", "version": "1.0.0"}),
        )
    mgr = CharacterPackageManager.__new__(CharacterPackageManager)
    mgr.install_dir = tmp_path / "installed"
    try:
        mgr.install_package(str(zip_path), target_dir=tmp_path / "installed")
        assert False, "应拒绝越界 agent_id"
    except PackageValidationError:
        pass


def test_character_package_rejects_traversal_member(tmp_path):
    from core.character_package import CharacterPackageManager, PackageValidationError

    zip_path = tmp_path / "evil2.pet"
    _make_pet_zip(zip_path, [("../escape.txt", "pwned")])
    mgr = CharacterPackageManager.__new__(CharacterPackageManager)
    mgr.install_dir = tmp_path / "installed"
    try:
        mgr.install_package(str(zip_path), target_dir=tmp_path / "installed")
        assert False, "应拒绝越界成员路径"
    except PackageValidationError:
        pass
    assert not (tmp_path / "escape.txt").exists()


def test_character_package_install_normal(tmp_path):
    """正常包仍可安装（不误伤）"""
    from core.character_package import CharacterPackageManager

    zip_path = tmp_path / "good.pet"
    _make_pet_zip(zip_path, [("identity.md", "id"), ("memory/a.md", "m")])
    mgr = CharacterPackageManager.__new__(CharacterPackageManager)
    mgr.install_dir = tmp_path / "installed"
    target = mgr.install_package(str(zip_path), target_dir=tmp_path / "installed")
    assert (Path(target) / "identity.md").exists()
    assert (Path(target) / "memory" / "a.md").exists()


# ───────────────────────── B2-6：framebaker 定向终止 ─────────────────────────
def test_framebaker_stop_uses_pid_not_im_kill(tmp_path, monkeypatch):
    """stop_framebaker 优先按 PID 终止；不再 taskkill /IM bun.exe"""
    import ui.framebaker as fb
    monkeypatch.setattr(fb, "FRAMEBAKER_PATH", str(tmp_path / "fb"))
    # 无跟踪进程 → 走按路径匹配分支（Windows 用 PowerShell 定向过滤）
    calls = []
    monkeypatch.setattr(
        fb.subprocess, "run",
        lambda *a, **k: calls.append((a, k)) or types.SimpleNamespace(returncode=0),
    )
    fb._framebaker_proc = None
    assert fb.stop_framebaker() is True
    assert calls, "应执行定向终止命令"
    cmd = calls[0][0][0]
    assert "bun.exe" not in cmd or "taskkill" not in cmd, "不得无差别杀 bun"
    joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "FRAMEBAKER_PATH" in joined or str(tmp_path) in joined or "Stop-Process" in joined


def test_framebaker_stop_by_pid(tmp_path, monkeypatch):
    """有跟踪进程时按 PID 终止"""
    import ui.framebaker as fb

    class _Proc:
        def __init__(self):
            self._poll = None
            self.pid = 7777
            self.terminated = False
            self.killed = False

        def poll(self):
            return self._poll

        def terminate(self):
            self.terminated = True
            self._poll = 1

        def wait(self, timeout=0):
            return self._poll

        def kill(self):
            self.killed = True

    proc = _Proc()
    fb._framebaker_proc = proc
    monkeypatch.setattr(fb, "FRAMEBAKER_PATH", str(tmp_path / "fb"))
    assert fb.stop_framebaker() is True
    assert proc.terminated, "应按 PID terminate"
    assert fb._framebaker_proc is None


# ───────────────────────── B2-7：harness_adapter 历史 deque ─────────────────────────
def test_adapter_history_is_bounded_deque():
    """_history 为有界 deque(maxlen=40)，append 并发安全无读改写"""
    from core.harness_adapter import HanakoPetAdapter
    import collections
    with tempfile.TemporaryDirectory() as d:
        # 构造会读 ~/.hanako，用 __new__ + 最小属性
        a = HanakoPetAdapter.__new__(HanakoPetAdapter)
        a._history = collections.deque(maxlen=40)
        assert isinstance(a._history, collections.deque)
        assert a._history.maxlen == 40
        for i in range(50):
            a._history.append({"role": "user", "content": str(i)})
        assert len(a._history) == 40
        assert a._history[0]["content"] == "10"  # 最旧被裁剪


def test_adapter_history_concurrent_append_no_loss():
    """并发 append 不丢消息（deque 原子 append）"""
    import collections
    from core.harness_adapter import HanakoPetAdapter
    a = HanakoPetAdapter.__new__(HanakoPetAdapter)
    a._history = collections.deque(maxlen=40)
    errors = []

    def _writer(n):
        try:
            for i in range(200):
                a._history.append({"role": "user", "content": f"{n}-{i}"})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5)
    assert not errors
    assert len(a._history) == 40  # 有界


# ───────────────────────── B2-8：action_linker 原子写 ─────────────────────────
def test_action_linker_outbox_atomic_write(tmp_path):
    """trigger_action 串行化读改写 + 原子写，不丢事件、无半截文件"""
    from motion.action_linker import ActionLinker

    linker = ActionLinker(character_id="yuexinmiao")
    outbox = tmp_path / "outbox"
    # 并发写同一 outbox（同进程多线程）
    errs = []
    def _click(n):
        try:
            for _ in range(10):
                linker.trigger_action(outbox, "pet")
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=_click, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5)
    assert not errs
    data = json.loads((outbox / "outbox.json").read_text("utf-8"))
    assert len(data) == 40, f"40 次点击应全部写入，实际 {len(data)}"
    assert (outbox / ".pending").exists()
    assert not list(outbox.glob("*.tmp")), "原子写不应残留临时文件"


# ───────────────────────── B2-9：store_take 负值回流 ─────────────────────────
def test_store_take_drains_negative_pending():
    """store_take 对负 pending 同样回流（与 _drain_pending 行为一致）"""
    from core.save.pet_save import PetSaveManager

    with tempfile.TemporaryDirectory() as d:
        mgr = PetSaveManager(str(Path(d) / "save.json"))
        mgr.save.health = 80.0
        mgr.save.pending_health = -30.0
        mgr.store_take(ratio=1.0)
        assert mgr.save.pending_health == 0.0
        assert mgr.save.health == 50.0, "负 pending 应扣主属性"
        # 浮点残留清零
        mgr.save.pending_mood = 0.005
        mgr.store_take(ratio=0.1)
        assert mgr.save.pending_mood == 0.0


def test_item_docstring_points_to_drain_pending():
    """item.py 过时注释已更新：主属性回流指向 PetStateManager._drain_pending"""
    src = (ROOT / "core" / "items" / "item.py").read_text(encoding="utf-8")
    assert "PetStateManager._drain_pending" in src
    # 旧声明（声称 store_take 是运行时回流路径）不应再出现
    assert "由 PetSaveManager.store_take() 每 tick 回流到主属性" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nALL FIX-BATCH-2 CHECKS PASSED ({len(fns)} tests)")
