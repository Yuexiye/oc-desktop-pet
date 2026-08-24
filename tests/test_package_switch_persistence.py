# -*- coding: utf-8 -*-
"""任务 #3 回归测试：设置面板「角色包」切换在重启后真正生效。

背景（Problem B）：设置面板存在两套互不相通的"切换角色"机制——
  - 角色包管理 tab `_switch_pet` 写 config["character"] + agents[].enabled（启动生效）
  - 基础 tab 角色包下拉框 `_save` 只写 config["character_package"]（启动无人读取，死字段）
结果：用户在角色包管理 tab 切 Shizuku 后，基础 tab 下拉框仍显示"默认"；
重启后桌宠仍是 miku。

修复：统一以 agents[].enabled 为启动真相源，character/character_package 同步为
展示字段；下拉框初始值从"实际启用中的桌宠"推导；_save 与 _switch_pet 走同一套
_apply_package_selection 逻辑。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _StubPetManager:
    """仅用于让 SettingsDialog 构建"桌宠角色"分组（构造时只判真值）。"""

    def __init__(self):
        self.agents = []


def _build_dialog(config: dict):
    from PySide6.QtWidgets import QApplication
    from ui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    return SettingsDialog(config=config, pet_manager=_StubPetManager())


def _base_config():
    return {
        "agents": [
            {"id": "miku", "enabled": True, "position": {"x": 0, "y": 0}, "scale": 1.0, "builtin": True},
            {"id": "yuexinmiao", "enabled": False, "position": {"x": 0, "y": 0}, "scale": 1.0, "builtin": True},
        ],
        "character": "miku",
        "character_package": "default",
    }


def test_dropdown_initial_selection_reflects_active_agent_not_dead_field():
    """基础 tab 下拉框初始值应来自 agents[].enabled 中实际启用的桌宠，
    而不是无人读取的 character_package（否则 _switch_pet 后仍显示"默认"）。"""
    config = _base_config()
    config["character_package"] = "default"  # 死字段保持旧值，模拟切换前的残留
    dialog = _build_dialog(config)

    assert hasattr(dialog, "_pkg_select"), "传入 pet_manager 时应构建角色包下拉框"
    assert dialog._pkg_select.currentData() == "miku", (
        f"下拉框应显示实际启用的 miku，实际={dialog._pkg_select.currentData()!r}"
    )


def test_current_active_agent_id_reads_enabled_agents():
    config = _base_config()
    dialog = _build_dialog(config)

    assert dialog._current_active_agent_id() == "miku"

    # 多宠同时启用 → None（多宠模式不强制单一角色）
    config["agents"][1]["enabled"] = True
    assert dialog._current_active_agent_id() is None

    # 仅剩 yuexinmiao 启用 → 返回它
    config["agents"][0]["enabled"] = False
    assert dialog._current_active_agent_id() == "yuexinmiao"


def test_apply_package_selection_writes_startup_truth_source():
    """_apply_package_selection 必须同时写 agents[].enabled + character +
    character_package，保证 pet_manager.launch_all（只读 agents[].enabled）重启生效。"""
    config = _base_config()
    dialog = _build_dialog(config)

    dialog._apply_package_selection("shizuku")

    by_id = {a["id"]: a for a in config["agents"]}
    assert by_id["shizuku"]["enabled"] is True, "选中的角色必须启用"
    assert by_id["miku"]["enabled"] is False, "其他角色必须禁用"
    assert by_id["yuexinmiao"]["enabled"] is False
    assert config["character"] == "shizuku"
    assert config["character_package"] == "shizuku", "展示字段应与真相源同步"

    # 下拉框也应同步（_switch_pet 切过去后基础 tab 不再显示"默认"）
    dialog._sync_pkg_select("shizuku")
    assert dialog._pkg_select.currentData() == "shizuku"


def test_save_with_dropdown_selection_persists_switch(monkeypatch):
    """用户直接在基础 tab 下拉框选 Shizuku 并点保存 → agents[].enabled 被改写，
    重启后真正生效（旧实现只写 character_package 死字段，重启不变）。"""
    config = _base_config()
    dialog = _build_dialog(config)

    saved = {}
    import ui.settings_dialog as sd
    monkeypatch.setattr(sd, "save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr(dialog, "_save_env", lambda: None)

    # 模拟用户在基础 tab 下拉框选择 Shizuku
    idx = dialog._pkg_select.findData("shizuku")
    assert idx >= 0, "下拉框应包含已安装角色 shizuku"
    dialog._pkg_select.setCurrentIndex(idx)

    dialog._save()

    by_id = {a["id"]: a for a in saved.get("agents", [])}
    assert by_id["shizuku"]["enabled"] is True, "保存后 shizuku 必须启用"
    assert by_id["miku"]["enabled"] is False, "保存后 miku 必须禁用"
    assert saved.get("character") == "shizuku"
    assert saved.get("character_package") == "shizuku"


def test_switch_pet_syncs_dropdown_and_persists(monkeypatch):
    """角色包管理 tab「切换选中桌宠」后：config 写全（agents/character/
    character_package），且基础 tab 下拉框同步，不再显示"默认"。"""
    config = _base_config()
    dialog = _build_dialog(config)

    saved = {}
    import ui.settings_dialog as sd
    monkeypatch.setattr(sd, "save_config", lambda cfg: saved.update(cfg))

    # 模拟 角色包管理 tab 列表选中 shizuku
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    item = QListWidgetItem("Shizuku v1.0.0")
    item.setData(Qt.UserRole, "shizuku")
    dialog._pkg_list.addItem(item)
    dialog._pkg_list.setCurrentRow(0)

    # 替换模态弹窗/信息框，避免测试阻塞
    import ui.settings_dialog as sd
    monkeypatch.setattr(sd.QMessageBox, "information", lambda *a, **k: None)
    dialog._switch_pet()

    by_id = {a["id"]: a for a in saved.get("agents", [])}
    assert by_id["shizuku"]["enabled"] is True
    assert by_id["miku"]["enabled"] is False
    assert saved.get("character") == "shizuku"
    assert saved.get("character_package") == "shizuku", "_switch_pet 应同步 character_package"
    assert dialog._pkg_select.currentData() == "shizuku", "基础 tab 下拉框应同步"
