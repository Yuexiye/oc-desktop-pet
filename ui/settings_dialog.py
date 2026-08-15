"""配置面板 - GUI 设置对话框

可配置项：
  - Agent 管理：启用/禁用桌宠、新增/移除
  - TTS：开关、音量、引擎
  - 行为模式：静默/正常/活跃/黏人
  - 鼠标交互：开关
  - 主动对话：开关、冷却时间
  - 屏幕感知：开关、截屏间隔
  - 语音输入：引擎选择
  - 记忆注入：预算模式、上限
  - 窗口：透明度、缩放
  - 久坐提醒：开关、间隔
  - API 配置：LLM/TTS/ASR
  - 角色包管理 (M5)
"""
from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QCheckBox, QSlider, QSpinBox, QComboBox,
    QPushButton, QLabel, QGroupBox, QTabWidget, QWidget,
    QLineEdit, QListWidget, QListWidgetItem, QAbstractItemView,
    QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from config import load_config, save_config
from ui.theme.palette import rgb, rgba
from ui.theme.theme_manager import get_default


class SettingsDialog(QDialog):
    """配置面板"""

    def __init__(self, config: dict = None, pet_manager=None, parent=None):
        super().__init__(parent)
        self._config = config or load_config()
        self._pet_manager = pet_manager
        self.setWindowTitle("设置")
        self.setMinimumSize(460, 600)
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        if mgr is not None:
            mgr.theme_changed.connect(self._on_dialog_theme_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._main_tabs = QTabWidget()
        self._main_tabs.setStyleSheet(self._tab_qss())
        layout.addWidget(self._main_tabs)

        # ── Tab 1: 基础设置 ──
        basic_tab = QWidget()
        basic_layout = QVBoxLayout(basic_tab)
        basic_layout.setContentsMargins(8, 8, 8, 8)
        basic_layout.setSpacing(14)

        # 页面引导说明（产品级：明确页面定位，建立标题层级）
        page_hint = QLabel("调整桌宠的行为、外观与反馈。改动保存后立即生效。")
        page_hint.setWordWrap(True)
        page_hint.setStyleSheet("color: rgb(%s); font-size: 11px; margin-bottom: 2px;" % rgb(self._ui_theme, "text_muted"))
        basic_layout.addWidget(page_hint)

        # 角色包选择（桌宠角色唯一入口；agents 增删/启停已并入角色包体系）
        if pet_manager:
            pkg_group = QGroupBox("桌宠角色")
            pkg_layout_g = QVBoxLayout(pkg_group)
            pkg_hint = QLabel("切换当前桌宠使用的角色包。改动保存后立即生效。")
            pkg_hint.setWordWrap(True)
            pkg_hint.setStyleSheet("color: rgb(%s); font-size: 11px;" % rgb(self._ui_theme, "text_muted"))
            pkg_layout_g.addWidget(pkg_hint)

            pkg_select_layout = QHBoxLayout()
            pkg_select_layout.addWidget(QLabel("角色包:"))

            self._pkg_select = QComboBox()
            self._pkg_select.addItem("默认", "default")
            # 加载已安装的角色包
            try:
                from core.character_package import CharacterPackageManager
                pkg_mgr = CharacterPackageManager()
                installed = pkg_mgr.list_installed_packages()
                for pkg in installed:
                    self._pkg_select.addItem(pkg.name or "未知", pkg.agent_id)
            except Exception:
                pass

            # 设置当前选中
            current_pkg = self._config.get("character_package", "default")
            idx = self._pkg_select.findData(current_pkg)
            if idx >= 0:
                self._pkg_select.setCurrentIndex(idx)

            pkg_select_layout.addWidget(self._pkg_select, 1)
            pkg_layout_g.addLayout(pkg_select_layout)

            basic_layout.addWidget(pkg_group)

        # 行为模式
        beh_group = QGroupBox("行为模式")
        beh_layout = QFormLayout(beh_group)

        self.behavior = QComboBox()
        self.behavior.addItems(["静默 (quiet)", "正常 (normal)", "活跃 (active)", "黏人 (cling)"])
        beh_map = {"quiet": 0, "normal": 1, "active": 2, "cling": 3}
        self.behavior.setCurrentIndex(beh_map.get(self._config.get("behavior", "normal"), 1))
        beh_layout.addRow("模式", self.behavior)

        basic_layout.addWidget(beh_group)

        # 窗口
        win_group = QGroupBox("窗口")
        win_layout = QFormLayout(win_group)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(20, 100)
        self.opacity.setValue(int(self._config.get("opacity", 1.0) * 100))
        self._opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(lambda v: self._opacity_label.setText(f"{v}%"))
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity)
        op_row.addWidget(self._opacity_label)
        win_layout.addRow("透明度", op_row)

        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(50, 200)
        self.scale.setValue(int(self._config.get("scale", 1.0) * 100))
        self._scale_label = QLabel(f"{self.scale.value()}%")
        self.scale.valueChanged.connect(lambda v: self._scale_label.setText(f"{v}%"))
        sc_row = QHBoxLayout()
        sc_row.addWidget(self.scale)
        sc_row.addWidget(self._scale_label)
        win_layout.addRow("缩放", sc_row)

        self.mouse_interaction = QCheckBox("鼠标交互（视线跟随 + 反应）")
        self.mouse_interaction.setChecked(self._config.get("mouse_interaction", True))
        win_layout.addRow(self.mouse_interaction)

        basic_layout.addWidget(win_group)

        # 音效
        sfx_group = QGroupBox("音效")
        sfx_layout = QFormLayout(sfx_group)

        self.sfx_enabled = QCheckBox("启用交互音效")
        self.sfx_enabled.setChecked(self._config.get("sfx", {}).get("enabled", True))
        sfx_layout.addRow(self.sfx_enabled)

        self.sfx_volume = QSlider(Qt.Horizontal)
        self.sfx_volume.setRange(0, 100)
        self.sfx_volume.setValue(int(self._config.get("sfx", {}).get("volume", 0.5) * 100))
        self._sfx_vol_label = QLabel(f"{self.sfx_volume.value()}%")
        self.sfx_volume.valueChanged.connect(lambda v: self._sfx_vol_label.setText(f"{v}%"))
        sfx_vol_row = QHBoxLayout()
        sfx_vol_row.addWidget(self.sfx_volume)
        sfx_vol_row.addWidget(self._sfx_vol_label)
        sfx_layout.addRow("音量", sfx_vol_row)

        basic_layout.addWidget(sfx_group)
        
        # ── 渲染格式切换（Live2D / 精灵图）──
        render_group = QGroupBox("渲染格式")
        render_layout = QFormLayout(render_group)
        
        self.render_format_select = QComboBox()
        self.render_format_select.addItem("自动检测", "auto")
        self.render_format_select.addItem("精灵图 (Sprite)", "sprite")
        self.render_format_select.addItem("Live2D", "live2d")
        # 读取当前角色格式
        try:
            from avatar.factory import detect_format
            current_format = detect_format(self._config.get("agent_id", "yuexinmiao"))
            fmt_map = {"sprite": 1, "live2d": 2, "auto": 0}
            self.render_format_select.setCurrentIndex(fmt_map.get(current_format, 0))
        except Exception:
            pass
        render_layout.addRow("格式", self.render_format_select)
        
        render_hint = QLabel("<i>切换后重启桌宠生效。精灵图适合静态角色，Live2D 支持更丰富的表情和动作。</i>")
        render_hint.setWordWrap(True)
        render_hint.setStyleSheet("color: rgb(%s); font-size: 10px;" % rgb(self._ui_theme, "text_muted"))
        render_layout.addRow("", render_hint)
        
        basic_layout.addWidget(render_group)

        basic_layout.addStretch()
        self._main_tabs.addTab(basic_tab, "基础")

        # ── Tab 2: 功能设置 ──
        func_tab = QWidget()
        func_layout = QVBoxLayout(func_tab)
        func_layout.setContentsMargins(16, 16, 16, 16)
        func_layout.setSpacing(16)

        # 功能页子标签
        self.func_sub_tabs = QTabWidget()
        self.func_sub_tabs.setStyleSheet(self._tab_qss())

        # ── 子标签 1: 语音 ──
        voice_tab = QWidget()
        voice_layout = QVBoxLayout(voice_tab)
        voice_layout.setContentsMargins(12, 12, 12, 12)
        voice_layout.setSpacing(16)

        # TTS
        tts_group = QGroupBox("语音输出")
        tts_layout = QFormLayout(tts_group)
        tts_layout.setSpacing(10)

        self.tts_enabled = QCheckBox("启用 TTS 语音")
        self.tts_enabled.setChecked(self._config.get("tts", {}).get("enabled", True))
        tts_layout.addRow(self.tts_enabled)

        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["本地 CosyVoice", "MIMO TTS", "API 调用", "微软 Edge (免费)"])
        tts_prov_map = {"cosyvoice": 0, "mimo": 1, "api": 2, "edge": 3}
        self.tts_provider.setCurrentIndex(tts_prov_map.get(self._config.get("tts", {}).get("provider", "cosyvoice"), 0))
        tts_layout.addRow("TTS 引擎", self.tts_provider)

        # 微软 Edge 音色选择（仅当选中 Edge 引擎时显示）
        self.tts_edge_voice = QComboBox()
        try:
            from tts_provider.edge_tts import EDGE_VOICES, DEFAULT_VOICE
        except Exception:
            EDGE_VOICES, DEFAULT_VOICE = ["zh-CN-XiaoxiaoNeural"], "zh-CN-XiaoxiaoNeural"
        self.tts_edge_voice.addItems(EDGE_VOICES)
        cur_voice = self._config.get("tts", {}).get("edge_voice", DEFAULT_VOICE)
        idx = self.tts_edge_voice.findText(cur_voice)
        self.tts_edge_voice.setCurrentIndex(idx if idx >= 0 else 0)
        tts_layout.addRow("Edge 音色", self.tts_edge_voice)

        def _toggle_edge_voice():
            is_edge = self.tts_provider.currentIndex() == 3
            self.tts_edge_voice.setVisible(is_edge)
            self.tts_edge_voice.setEnabled(is_edge)
            # 重新布局以收起空行
            ok = self.tts_edge_voice.isVisibleTo(self)
            label = tts_layout.labelForField(self.tts_edge_voice)
            if label:
                label.setVisible(ok)

        self.tts_provider.currentIndexChanged.connect(_toggle_edge_voice)
        _toggle_edge_voice()

        self.tts_volume = QSlider(Qt.Horizontal)
        self.tts_volume.setRange(0, 100)
        self.tts_volume.setValue(int(self._config.get("tts", {}).get("volume", 0.8) * 100))
        self.tts_vol_label = QLabel(f"{self.tts_volume.value()}%")
        self.tts_volume.valueChanged.connect(lambda v: self.tts_vol_label.setText(f"{v}%"))
        vol_row = QHBoxLayout()
        vol_row.addWidget(self.tts_volume)
        vol_row.addWidget(self.tts_vol_label)
        tts_layout.addRow("音量", vol_row)

        voice_layout.addWidget(tts_group)

        # ASR
        asr_group = QGroupBox("语音输入")
        asr_layout = QFormLayout(asr_group)
        asr_layout.setSpacing(10)

        self.asr_provider = QComboBox()
        self.asr_provider.addItems(["本地 Whisper", "MIMO ASR", "API 调用"])
        asr_prov_map = {"whisper_local": 0, "mimo": 1, "api": 2}
        self.asr_provider.setCurrentIndex(asr_prov_map.get(self._config.get("asr", {}).get("provider", "whisper_local"), 0))
        asr_layout.addRow("ASR 引擎", self.asr_provider)

        # 本地 Whisper 后端（faster-whisper 需用户手动 pip install，不自动下载）
        self.asr_backend = QComboBox()
        self.asr_backend.addItems(["whisper (默认)", "faster-whisper (需手动安装)"])
        asr_backend_map = {"whisper": 0, "faster_whisper": 1}
        self.asr_backend.setCurrentIndex(
            asr_backend_map.get(self._config.get("asr", {}).get("backend", "whisper"), 0)
        )
        asr_layout.addRow("本地后端", self.asr_backend)

        # 语言：中文优化 / 自动检测（中英混合）
        self.asr_lang = QComboBox()
        self.asr_lang.addItems(["中文优化", "自动检测 (中英混合)"])
        asr_lang_map = {"zh": 0, "auto": 1}
        self.asr_lang.setCurrentIndex(
            asr_lang_map.get(self._config.get("asr", {}).get("language", "auto"), 1)
        )
        asr_layout.addRow("识别语言", self.asr_lang)

        voice_layout.addWidget(asr_group)
        voice_layout.addStretch()

        self.func_sub_tabs.addTab(voice_tab, "语音")

        # ── 子标签 2: 交互 ──
        interact_tab = QWidget()
        interact_layout = QVBoxLayout(interact_tab)
        interact_layout.setContentsMargins(12, 12, 12, 12)
        interact_layout.setSpacing(16)

        # 主动对话
        pro_group = QGroupBox("主动对话")
        pro_layout = QFormLayout(pro_group)
        pro_layout.setSpacing(10)

        self.pro_enabled = QCheckBox("启用主动搭话")
        self.pro_enabled.setChecked(self._config.get("proactive", {}).get("enabled", True))
        pro_layout.addRow(self.pro_enabled)

        self.pro_cooldown = QSpinBox()
        self.pro_cooldown.setRange(1, 120)
        self.pro_cooldown.setSuffix(" 分钟")
        self.pro_cooldown.setValue(self._config.get("proactive", {}).get("cooldown_minutes", 10))
        pro_layout.addRow("冷却时间", self.pro_cooldown)

        interact_layout.addWidget(pro_group)

        # 屏幕感知
        screen_group = QGroupBox("屏幕感知")
        screen_layout = QFormLayout(screen_group)
        screen_layout.setSpacing(10)

        self.screen_enabled = QCheckBox("启用屏幕截屏分析")
        self.screen_enabled.setChecked(self._config.get("screen", {}).get("enabled", True))
        screen_layout.addRow(self.screen_enabled)

        self.screen_blur = QCheckBox("截图高斯模糊（降低文字可读性）")
        self.screen_blur.setChecked(self._config.get("screen", {}).get("blur", False))
        screen_layout.addRow(self.screen_blur)

        self.screen_blacklist = QCheckBox("敏感窗口黑名单（密码管理器/登录页自动跳过）")
        self.screen_blacklist.setChecked(self._config.get("screen", {}).get("blacklist", False))
        screen_layout.addRow(self.screen_blacklist)

        self.screen_compress = QCheckBox("截图缩放压缩（缩小4倍+50%质量，省流量）")
        self.screen_compress.setChecked(self._config.get("screen", {}).get("compress", True))
        screen_layout.addRow(self.screen_compress)

        self.screen_interval = QSpinBox()
        self.screen_interval.setRange(30, 600)
        self.screen_interval.setSuffix(" 秒")
        self.screen_interval.setValue(self._config.get("screen", {}).get("interval", 120))
        screen_layout.addRow("截屏间隔", self.screen_interval)

        # 随机间隔范围（可选）：勾选后每次截屏在下限~上限间随机，避免固定节奏
        self.screen_rand = QCheckBox("随机截屏间隔（更自然）")
        self.screen_rand.setChecked(bool(
            self._config.get("screen", {}).get("interval_min")
            and self._config.get("screen", {}).get("interval_max")
        ))
        screen_layout.addRow(self.screen_rand)
        self.screen_interval_min = QSpinBox()
        self.screen_interval_min.setRange(30, 600)
        self.screen_interval_min.setSuffix(" 秒")
        self.screen_interval_min.setValue(
            self._config.get("screen", {}).get("interval_min")
            or max(30, int(self._config.get("screen", {}).get("interval", 120) * 0.7))
        )
        self.screen_interval_max = QSpinBox()
        self.screen_interval_max.setRange(30, 600)
        self.screen_interval_max.setSuffix(" 秒")
        self.screen_interval_max.setValue(
            self._config.get("screen", {}).get("interval_max")
            or max(31, int(self._config.get("screen", {}).get("interval", 120) * 1.3))
        )
        rand_row = QHBoxLayout()
        rand_row.addWidget(QLabel("范围:"))
        rand_row.addWidget(self.screen_interval_min)
        rand_row.addWidget(QLabel(" 至 "))
        rand_row.addWidget(self.screen_interval_max)
        # 随机范围仅在勾选时启用
        self.screen_interval_min.setEnabled(self.screen_rand.isChecked())
        self.screen_interval_max.setEnabled(self.screen_rand.isChecked())
        self.screen_rand.toggled.connect(self.screen_interval_min.setEnabled)
        self.screen_rand.toggled.connect(self.screen_interval_max.setEnabled)
        screen_layout.addRow("", rand_row)

        interact_layout.addWidget(screen_group)

        # 窗口互动
        wi_group = QGroupBox("窗口互动")
        wi_layout = QFormLayout(wi_group)
        wi_layout.setSpacing(10)

        self.wi_enabled = QCheckBox("启用窗口互动")
        self.wi_enabled.setChecked(self._config.get("window_interaction", {}).get("enabled", True))
        wi_layout.addRow(self.wi_enabled)

        self.wi_auto_walk = QCheckBox("自动跟随窗口（切换窗口时桌宠走到窗口旁）")
        self.wi_auto_walk.setChecked(self._config.get("window_interaction", {}).get("auto_walk", False))
        wi_layout.addRow(self.wi_auto_walk)

        self.wi_cooldown = QSpinBox()
        self.wi_cooldown.setRange(5, 1800)
        self.wi_cooldown.setSuffix(" 秒")
        self.wi_cooldown.setValue(self._config.get("window_interaction", {}).get("cooldown_seconds", 600))
        wi_layout.addRow("冷却时间", self.wi_cooldown)

        interact_layout.addWidget(wi_group)

        # 久坐提醒
        break_group = QGroupBox("久坐提醒")
        break_layout = QFormLayout(break_group)
        break_layout.setSpacing(10)

        self.break_enabled = QCheckBox("启用久坐提醒")
        self.break_enabled.setChecked(self._config.get("break_reminder", {}).get("enabled", True))
        break_layout.addRow(self.break_enabled)

        self.break_idle = QSpinBox()
        self.break_idle.setRange(5, 120)
        self.break_idle.setSuffix(" 分钟")
        self.break_idle.setValue(self._config.get("break_reminder", {}).get("idle_minutes", 15))
        break_layout.addRow("空闲阈值", self.break_idle)

        self.break_cooldown = QSpinBox()
        self.break_cooldown.setRange(5, 120)
        self.break_cooldown.setSuffix(" 分钟")
        self.break_cooldown.setValue(self._config.get("break_reminder", {}).get("cooldown_minutes", 30))
        break_layout.addRow("提醒间隔", self.break_cooldown)

        interact_layout.addWidget(break_group)
        interact_layout.addStretch()

        self.func_sub_tabs.addTab(interact_tab, "交互")

        # ── 子标签 3: 记忆 ──
        memory_tab = QWidget()
        memory_layout = QVBoxLayout(memory_tab)
        memory_layout.setContentsMargins(12, 12, 12, 12)
        memory_layout.setSpacing(16)

        # 记忆注入
        mem_group = QGroupBox("记忆注入")
        mem_layout = QFormLayout(mem_group)
        mem_layout.setSpacing(10)

        mem_mode = self._config.get("memory", {}).get("budget_mode", "auto")
        self.mem_mode = QComboBox()
        self.mem_mode.addItems(["自动（按模型上下文 1%）", "手动指定"])
        self.mem_mode.setCurrentIndex(0 if mem_mode == "auto" else 1)
        mem_layout.addRow("预算模式", self.mem_mode)

        self.mem_budget = QSpinBox()
        self.mem_budget.setRange(200, 20000)
        self.mem_budget.setSuffix(" 字符")
        self.mem_budget.setSingleStep(200)
        self.mem_budget.setValue(self._config.get("memory", {}).get("budget_chars", 3000))
        self.mem_budget.setEnabled(mem_mode != "auto")
        self.mem_mode.currentIndexChanged.connect(
            lambda idx: self.mem_budget.setEnabled(idx == 1)
        )
        mem_layout.addRow("记忆上限", self.mem_budget)

        self.mem_hint = QLabel("agnes-2.0-flash (1M tokens) → 自动预算 6000 字符")
        self.mem_hint.setStyleSheet("color: rgb(%s); font-size: 10px;" % rgb(self._ui_theme, "text_muted"))
        mem_layout.addRow(self.mem_hint)

        memory_layout.addWidget(mem_group)
        memory_layout.addStretch()

        self.func_sub_tabs.addTab(memory_tab, "记忆")

        func_layout.addWidget(self.func_sub_tabs)
        self._main_tabs.addTab(func_tab, "功能")

        # ── Tab 2.5: 角色包管理 (M5) ──
        pkg_tab = QWidget()
        pkg_layout = QVBoxLayout(pkg_tab)
        pkg_layout.setContentsMargins(12, 12, 12, 12)
        pkg_layout.setSpacing(16)

        try:
            from core.character_package import CharacterPackageManager
            self._pkg_mgr = CharacterPackageManager()
        except Exception as e:
            self._pkg_mgr = None
            logger = __import__('logging').getLogger(__name__)
            logger.warning("CharacterPackageManager not available: %s", e)

        pkg_group = QGroupBox("角色包管理 (M5)")
        pkg_group_layout = QVBoxLayout(pkg_group)

        # 已安装列表
        self._pkg_list = QListWidget()
        self._pkg_list.setMinimumHeight(100)
        pkg_group_layout.addWidget(self._pkg_list)

        # 操作按钮行
        pkg_btns_row1 = QHBoxLayout()

        self._import_pkg_btn = QPushButton("📦 导入 .pet")
        self._import_pkg_btn.clicked.connect(self._import_package)
        pkg_btns_row1.addWidget(self._import_pkg_btn)

        self._export_pkg_btn = QPushButton("💾 导出选中")
        self._export_pkg_btn.clicked.connect(self._export_package)
        self._export_pkg_btn.setEnabled(False)
        self._pkg_list.currentRowChanged.connect(lambda r: self._export_pkg_btn.setEnabled(r >= 0))
        pkg_btns_row1.addWidget(self._export_pkg_btn)

        pkg_group_layout.addLayout(pkg_btns_row1)

        pkg_btns_row2 = QHBoxLayout()

        self._uninstall_pkg_btn = QPushButton("🗑️ 卸载选中")
        self._uninstall_pkg_btn.setObjectName("danger")
        self._uninstall_pkg_btn.clicked.connect(self._uninstall_package)
        self._uninstall_pkg_btn.setEnabled(False)
        self._pkg_list.currentRowChanged.connect(lambda r: self._uninstall_pkg_btn.setEnabled(r >= 0))
        pkg_btns_row2.addWidget(self._uninstall_pkg_btn)

        self._refresh_pkg_btn = QPushButton("🔄 刷新列表")
        self._refresh_pkg_btn.clicked.connect(self._refresh_package_list)
        pkg_btns_row2.addWidget(self._refresh_pkg_btn)

        pkg_group_layout.addLayout(pkg_btns_row2)

        # 切换桌宠按钮
        self._switch_pet_btn = QPushButton("🔄 切换选中桌宠")
        self._switch_pet_btn.clicked.connect(self._switch_pet)
        self._switch_pet_btn.setEnabled(False)
        self._pkg_list.currentRowChanged.connect(lambda r: self._switch_pet_btn.setEnabled(r >= 0))
        pkg_group_layout.addWidget(self._switch_pet_btn)

        # 状态标签
        self._pkg_status_label = QLabel("就绪")
        self._pkg_status_label.setStyleSheet("color: rgb(%s); font-size: 10px;" % rgb(self._ui_theme, "text_muted"))
        pkg_group_layout.addWidget(self._pkg_status_label)

        # 刷新列表推迟到 showEvent，避免构造时阻塞

        pkg_layout.addWidget(pkg_group)
        pkg_layout.addStretch()
        self._main_tabs.addTab(pkg_tab, "角色包")

        # ── Tab 3: API 配置 ──
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)
        api_layout.setContentsMargins(16, 16, 16, 16)
        api_layout.setSpacing(16)

        api_group = QGroupBox("API 配置（留空 = 用 Hanako 默认）")
        api_form = QFormLayout(api_group)
        api_form.setSpacing(10)

        # 读取 provider catalog 获取可用模型
        self._catalog_models = self._load_catalog_models()

        # LLM Provider 快速选择
        self.llm_provider_select = QComboBox()
        self.llm_provider_select.addItem("手动填写", "")
        for pid in self._catalog_models.get("providers", []):
            self.llm_provider_select.addItem(pid, pid)
        self.llm_provider_select.currentIndexChanged.connect(self._on_llm_provider_select)
        api_form.addRow("LLM Provider", self.llm_provider_select)

        self.llm_url = QLineEdit()
        self.llm_url.setPlaceholderText("留空用 Hanako")
        api_form.addRow("LLM 地址", self.llm_url)

        self.llm_key = QLineEdit()
        self.llm_key.setEchoMode(QLineEdit.Password)
        self.llm_key.setPlaceholderText("留空用 Hanako")
        api_form.addRow("LLM Key", self.llm_key)

        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.llm_model.addItems(self._catalog_models.get("llm", []))
        self.llm_model.setCurrentText("")
        self.llm_model.lineEdit().setPlaceholderText("留空用 Hanako")
        api_form.addRow("LLM 模型", self.llm_model)

        # TTS API provider 快速选择
        self.tts_provider_select = QComboBox()
        self.tts_provider_select.addItem("手动填写", "")
        for pid in self._catalog_models.get("providers", []):
            self.tts_provider_select.addItem(pid, pid)
        self.tts_provider_select.currentIndexChanged.connect(self._on_tts_provider_select)
        api_form.addRow("TTS Provider", self.tts_provider_select)

        self.tts_url = QLineEdit()
        self.tts_url.setPlaceholderText("TTS API 地址")
        api_form.addRow("TTS 地址", self.tts_url)

        self.tts_key = QLineEdit()
        self.tts_key.setEchoMode(QLineEdit.Password)
        self.tts_key.setPlaceholderText("TTS Key")
        api_form.addRow("TTS Key", self.tts_key)

        self.tts_model = QComboBox()
        self.tts_model.setEditable(True)
        self.tts_model.lineEdit().setPlaceholderText("tts-1（OpenAI 默认）")
        api_form.addRow("TTS 模型", self.tts_model)

        self.tts_voice = QComboBox()
        self.tts_voice.setEditable(True)
        # MIMO + 通用音色
        mimo_voices = ["mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]
        openai_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        self.tts_voice.addItems(mimo_voices + ["─── OpenAI ───"] + openai_voices)
        self.tts_voice.lineEdit().setPlaceholderText("选择或输入音色")
        api_form.addRow("TTS 音色", self.tts_voice)

        # ASR API provider 快速选择
        self.asr_provider_select = QComboBox()
        self.asr_provider_select.addItem("手动填写", "")
        for pid in self._catalog_models.get("providers", []):
            self.asr_provider_select.addItem(pid, pid)
        self.asr_provider_select.currentIndexChanged.connect(self._on_asr_provider_select)
        api_form.addRow("ASR Provider", self.asr_provider_select)

        self.asr_url = QLineEdit()
        self.asr_url.setPlaceholderText("ASR API 地址")
        api_form.addRow("ASR 地址", self.asr_url)

        self.asr_key = QLineEdit()
        self.asr_key.setEchoMode(QLineEdit.Password)
        self.asr_key.setPlaceholderText("ASR Key")
        api_form.addRow("ASR Key", self.asr_key)

        self.asr_model = QComboBox()
        self.asr_model.setEditable(True)
        self.asr_model.lineEdit().setPlaceholderText("whisper-1（OpenAI 默认）")
        api_form.addRow("ASR 模型", self.asr_model)

        # ── 视觉模型配置（M2 屏幕感知专用）──
        self._vision_separator = QLabel("─── 视觉模型（屏幕感知专用）───")
        self._vision_separator.setStyleSheet("color: rgb(%s); font-weight: bold; margin-top: 10px;" % rgb(self._ui_theme, "text_muted"))
        api_form.addRow(self._vision_separator)

        self._vision_hint = QLabel("留空则使用 LLM 配置。建议使用支持图片的模型（如 agnes-2.0-flash、GPT-4V 等）")
        self._vision_hint.setWordWrap(True)
        self._vision_hint.setStyleSheet("color: rgb(%s); font-size: 11px;" % rgb(self._ui_theme, "text_muted"))
        api_form.addRow(self._vision_hint)

        # 视觉 Provider 快速选择
        self.vision_provider_select = QComboBox()
        self.vision_provider_select.addItem("手动填写", "")
        for pid in self._catalog_models.get("providers", []):
            self.vision_provider_select.addItem(pid, pid)
        self.vision_provider_select.currentIndexChanged.connect(self._on_vision_provider_select)
        api_form.addRow("视觉 Provider", self.vision_provider_select)

        self.vision_url = QLineEdit()
        self.vision_url.setPlaceholderText("视觉 API 地址（留空用 LLM 配置）")
        api_form.addRow("视觉地址", self.vision_url)

        self.vision_key = QLineEdit()
        self.vision_key.setEchoMode(QLineEdit.Password)
        self.vision_key.setPlaceholderText("视觉 API Key（留空用 LLM 配置）")
        api_form.addRow("视觉 Key", self.vision_key)

        self.vision_model = QComboBox()
        self.vision_model.setEditable(True)
        # 推荐的视觉模型
        vision_models = ["agnes-2.0-flash", "gpt-4o", "gpt-4-vision-preview", "claude-3-opus", "claude-3-sonnet"]
        self.vision_model.addItems(vision_models)
        self.vision_model.setCurrentText("")
        self.vision_model.lineEdit().setPlaceholderText("留空用 LLM 模型")
        api_form.addRow("视觉模型", self.vision_model)

        api_layout.addWidget(api_group)
        api_layout.addStretch()
        self._main_tabs.addTab(api_tab, "API")

        # .env / agent / 角色包列表在 showEvent 中异步加载

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setObjectName("save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def showEvent(self, event):
        """对话框显示后再异步加载可能阻塞的列表，避免卡死"""
        super().showEvent(event)
        QTimer.singleShot(0, self._load_deferred_data)

    def _load_deferred_data(self):
        """延迟加载 角色包/环境变量，失败也不阻塞 UI"""
        try:
            self._load_env_to_ui()
        except Exception:
            pass
        try:
            self._refresh_package_list()
        except Exception:
            pass

    # ── M5: 角色包管理 ──

    def _refresh_package_list(self):
        """刷新角色包列表"""
        if not hasattr(self, '_pkg_list') or not self._pkg_mgr:
            return
        self._pkg_list.clear()
        try:
            packages = self._pkg_mgr.list_installed_packages()
            for pkg in packages:
                version_tag = f" v{pkg.version}" if pkg.version and pkg.version != "?" else ""
                desc_tag = f" - {pkg.description}" if pkg.description and pkg.description != "(无 manifest)" else ""
                display_text = f"{pkg.name}{version_tag}{desc_tag}"
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, pkg.agent_id)  # 存储 agent_id
                self._pkg_list.addItem(item)
            self._pkg_status_label.setText(f"共 {len(packages)} 个已安装角色")
        except Exception as e:
            logger = __import__('logging').getLogger(__name__)
            logger.warning("刷新角色包列表失败: %s", e)
            self._pkg_status_label.setText(f"加载失败: {e}")

    def _import_package(self):
        """导入 .pet 文件"""
        if not self._pkg_mgr:
            QMessageBox.warning(self, "提示", "角色包管理器不可用")
            return

        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "导入角色包", "",
            "角色包 (*.pet);;所有文件 (*)"
        )
        if not path:
            return

        try:
            result = self._pkg_mgr.install_package(path, overwrite=False)
            self._pkg_status_label.setText(f"导入成功: {result}")
            self._refresh_package_list()
            QMessageBox.information(self, "成功", f"角色包安装成功！\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            self._pkg_status_label.setText(f"导入失败: {e}")

    def _export_package(self):
        """导出选中的角色为 .pet 文件"""
        if not self._pkg_mgr:
            return
        row = self._pkg_list.currentRow()
        if row < 0:
            return

        # 从 UserRole 数据获取 agent_id
        item = self._pkg_list.item(row)
        agent_id = item.data(Qt.UserRole)
        if not agent_id:
            # 兜底：从文本中提取
            item_text = item.text()
            agent_id = item_text.split(" ")[0]

        from PySide6.QtWidgets import QFileDialog
        out_path, _ = QFileDialog.getSaveFileName(
            self, "导出角色包", f"{agent_id}.pet", "角色包 (*.pet)"
        )
        if not out_path:
            return

        try:
            result_path = self._pkg_mgr.create_package(agent_id, output_path=out_path)
            self._pkg_status_label.setText(f"导出成功: {result_path}")
            QMessageBox.information(self, "成功", f"角色包已导出到:\n{result_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            self._pkg_status_label.setText(f"导出失败: {e}")

    def _uninstall_package(self):
        """卸载选中的角色"""
        if not self._pkg_mgr:
            return
        row = self._pkg_list.currentRow()
        if row < 0:
            return

        # 从 UserRole 数据获取 agent_id
        item = self._pkg_list.item(row)
        agent_id = item.data(Qt.UserRole)
        if not agent_id:
            # 兜底：从文本中提取
            item_text = item.text()
            agent_id = item_text.split(" ")[0]

        reply = QMessageBox.question(
            self, "确认卸载",
            f"确定要卸载角色 '{agent_id}' 吗？\n（精灵文件和配置将被删除）",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            success = self._pkg_mgr.uninstall_package(agent_id)
            if success:
                self._pkg_status_label.setText(f"已卸载: {agent_id}")
                self._refresh_package_list()
                QMessageBox.information(self, "成功", f"角色 '{agent_id}' 已卸载")
            else:
                QMessageBox.warning(self, "提示", f"角色 '{agent_id}' 不存在")
        except Exception as e:
            QMessageBox.critical(self, "卸载失败", str(e))

    def _switch_pet(self):
        """切换到选中的桌宠"""
        row = self._pkg_list.currentRow()
        if row < 0:
            return
        
        item = self._pkg_list.item(row)
        agent_id = item.data(Qt.UserRole)
        if not agent_id:
            item_text = item.text()
            agent_id = item_text.split(" ")[0]
        
        # 保存到配置并持久化：pet_manager 启动时读取 agents 列表，
        # 因此需要把选中的角色设为启用、其余角色禁用（并保留原有配置）。
        agents = self._config.setdefault("agents", [])
        found = False
        for a in agents:
            if a.get("id") == agent_id:
                a["enabled"] = True
                found = True
            else:
                a["enabled"] = False
        if not found:
            is_builtin = False
            try:
                if self._pkg_mgr and (self._pkg_mgr.characters_dir / agent_id).exists():
                    is_builtin = True
            except Exception:
                pass
            agents.append({
                "id": agent_id,
                "enabled": True,
                "position": {"x": -1, "y": -1},
                "scale": 1.0,
                "builtin": is_builtin,
            })
        self._config["character"] = agent_id
        save_config(self._config)
        self._pkg_status_label.setText(f"已切换到: {agent_id}")
        QMessageBox.information(self, "切换成功", f"桌宠已切换为 '{agent_id}'，重启后生效")

    # ── Provider Catalog ──

    @staticmethod
    def _load_catalog_models() -> dict:
        """从 provider-catalog.json 读取所有可用模型

        Returns:
            {"llm": [...], "providers": [...], "provider_map": {...},
             "provider_configs": {"prov_id": {base_url, api_key, models}}}
        """
        import json
        from pathlib import Path
        catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
        llm_models = []
        provider_map = {}
        provider_configs = {}
        try:
            if catalog_path.exists():
                data = json.loads(catalog_path.read_text("utf-8"))
                for prov_id, prov_cfg in data.get("providers", {}).items():
                    provider_configs[prov_id] = {
                        "base_url": prov_cfg.get("base_url", ""),
                        "api_key": prov_cfg.get("api_key", ""),
                    }
                    for m in prov_cfg.get("models", []):
                        if isinstance(m, dict):
                            mid = m.get("id", "")
                            if mid:
                                label = f"{mid}  [{prov_id}]"
                                llm_models.append(label)
                                provider_map[label] = prov_id
                        elif isinstance(m, str) and m:
                            label = f"{m}  [{prov_id}]"
                            llm_models.append(label)
                            provider_map[label] = prov_id
        except Exception:
            pass
        return {
            "llm": sorted(set(llm_models)),
            "providers": sorted(provider_configs.keys()),
            "provider_map": provider_map,
            "provider_configs": provider_configs,
        }

    def _on_llm_provider_select(self, idx: int):
        """LLM provider 下拉选择 → 自动填充 URL、Key、模型列表"""
        prov_id = self.llm_provider_select.itemData(idx)
        if not prov_id:
            return
        cfg = self._catalog_models.get("provider_configs", {}).get(prov_id, {})
        if cfg.get("base_url"):
            self.llm_url.setText(cfg["base_url"])
        if cfg.get("api_key"):
            self.llm_key.setText(cfg["api_key"])
        self.llm_model.clear()
        models = []
        try:
            from pathlib import Path
            import json
            catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
            data = json.loads(catalog_path.read_text("utf-8"))
            prov_models = data.get("providers", {}).get(prov_id, {}).get("models", [])
            for m in prov_models:
                if isinstance(m, dict):
                    models.append(m.get("id", ""))
                elif isinstance(m, str):
                    models.append(m)
        except Exception:
            pass
        self.llm_model.addItems([m for m in models if m])

    def _on_tts_provider_select(self, idx: int):
        """TTS provider 下拉选择 → 自动填充 URL、Key、模型列表"""
        prov_id = self.tts_provider_select.itemData(idx)
        if not prov_id:
            return
        
        # 只在 TTS 引擎是 "本地 CosyVoice"（索引 0）时才联动
        # 如果用户已经手动选择了 "MIMO TTS" 或 "API 调用"，则不覆盖
        if self.tts_provider.currentIndex() == 0:
            # TTS 引擎下拉框选项：["本地 CosyVoice", "MIMO TTS", "API 调用"]，索引 2 是 "API 调用"
            self.tts_provider.setCurrentIndex(2)
        
        cfg = self._catalog_models.get("provider_configs", {}).get(prov_id, {})
        if cfg.get("base_url"):
            self.tts_url.setText(cfg["base_url"])
        if cfg.get("api_key"):
            self.tts_key.setText(cfg["api_key"])
        # 填充该 provider 的模型列表
        self.tts_model.clear()
        models = []
        try:
            from pathlib import Path
            import json
            catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
            data = json.loads(catalog_path.read_text("utf-8"))
            prov_models = data.get("providers", {}).get(prov_id, {}).get("models", [])
            for m in prov_models:
                if isinstance(m, dict):
                    models.append(m.get("id", ""))
                elif isinstance(m, str):
                    models.append(m)
        except Exception:
            pass
        self.tts_model.addItems([m for m in models if m])

    def _on_asr_provider_select(self, idx: int):
        """ASR provider 下拉选择 → 自动填充 URL、Key、模型列表"""
        prov_id = self.asr_provider_select.itemData(idx)
        if not prov_id:
            return
        
        # 只在 ASR 引擎是 "本地 Whisper"（索引 0）时才联动
        # 如果用户已经手动选择了 "MIMO ASR" 或 "API 调用"，则不覆盖
        if self.asr_provider.currentIndex() == 0:
            # ASR 引擎下拉框选项：["本地 Whisper", "MIMO ASR", "API 调用"]，索引 2 是 "API 调用"
            self.asr_provider.setCurrentIndex(2)
        
        cfg = self._catalog_models.get("provider_configs", {}).get(prov_id, {})
        if cfg.get("base_url"):
            self.asr_url.setText(cfg["base_url"])
        if cfg.get("api_key"):
            self.asr_key.setText(cfg["api_key"])
        self.asr_model.clear()
        models = []
        try:
            from pathlib import Path
            import json
            catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
            data = json.loads(catalog_path.read_text("utf-8"))
            prov_models = data.get("providers", {}).get(prov_id, {}).get("models", [])
            for m in prov_models:
                if isinstance(m, dict):
                    models.append(m.get("id", ""))
                elif isinstance(m, str):
                    models.append(m)
        except Exception:
            pass
        self.asr_model.addItems([m for m in models if m])

    def _on_vision_provider_select(self, idx: int):
        """视觉 provider 下拉选择 → 自动填充 URL、Key、模型列表"""
        prov_id = self.vision_provider_select.itemData(idx)
        if not prov_id:
            return
        cfg = self._catalog_models.get("provider_configs", {}).get(prov_id, {})
        if cfg.get("base_url"):
            self.vision_url.setText(cfg["base_url"])
        if cfg.get("api_key"):
            self.vision_key.setText(cfg["api_key"])
        # 保留推荐模型，追加该 provider 的模型列表
        current_models = ["agnes-2.0-flash", "gpt-4o", "gpt-4-vision-preview", "claude-3-opus", "claude-3-sonnet"]
        try:
            from pathlib import Path
            import json
            catalog_path = Path.home() / ".hanako" / "provider-catalog.json"
            data = json.loads(catalog_path.read_text("utf-8"))
            prov_models = data.get("providers", {}).get(prov_id, {}).get("models", [])
            for m in prov_models:
                if isinstance(m, dict):
                    model_id = m.get("id", "")
                elif isinstance(m, str):
                    model_id = m
                else:
                    continue
                if model_id and model_id not in current_models:
                    current_models.append(model_id)
        except Exception:
            pass
        self.vision_model.clear()
        self.vision_model.addItems(current_models)

    # ── .env 读写 ──

    def _load_env_to_ui(self):
        from env_config import ENV_PATH
        if not ENV_PATH.exists():
            return
        try:
            for line in ENV_PATH.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                mapping = {
                    "LLM_BASE_URL": self.llm_url,
                    "LLM_API_KEY": self.llm_key,
                    "TTS_BASE_URL": self.tts_url,
                    "TTS_API_KEY": self.tts_key,
                    "ASR_BASE_URL": self.asr_url,
                    "ASR_API_KEY": self.asr_key,
                }
                if key in mapping:
                    mapping[key].setText(val)
                elif key == "LLM_PROVIDER" and val:
                    for i in range(self.llm_provider_select.count()):
                        if self.llm_provider_select.itemData(i) == val:
                            self.llm_provider_select.setCurrentIndex(i)
                            break
                elif key == "TTS_PROVIDER" and val:
                    for i in range(self.tts_provider_select.count()):
                        if self.tts_provider_select.itemData(i) == val:
                            self.tts_provider_select.setCurrentIndex(i)
                            break
                elif key == "ASR_PROVIDER" and val:
                    for i in range(self.asr_provider_select.count()):
                        if self.asr_provider_select.itemData(i) == val:
                            self.asr_provider_select.setCurrentIndex(i)
                            break
                elif key == "LLM_MODEL" and val:
                    # 先精确匹配，再按 model_id 前缀匹配
                    idx = self.llm_model.findText(val)
                    if idx < 0:
                        for i in range(self.llm_model.count()):
                            if self.llm_model.itemText(i).startswith(val):
                                idx = i
                                break
                    if idx >= 0:
                        self.llm_model.setCurrentIndex(idx)
                    else:
                        self.llm_model.setEditText(val)
                elif key == "TTS_MODEL" and val:
                    idx = self.tts_model.findText(val)
                    if idx >= 0:
                        self.tts_model.setCurrentIndex(idx)
                    else:
                        self.tts_model.setEditText(val)
                elif key == "TTS_VOICE" and val:
                    idx = self.tts_voice.findText(val)
                    if idx >= 0:
                        self.tts_voice.setCurrentIndex(idx)
                    else:
                        self.tts_voice.setEditText(val)
                elif key == "ASR_MODEL" and val:
                    idx = self.asr_model.findText(val)
                    if idx >= 0:
                        self.asr_model.setCurrentIndex(idx)
                    else:
                        self.asr_model.setEditText(val)
                # 视觉模型配置
                elif key == "VISION_PROVIDER" and val:
                    for i in range(self.vision_provider_select.count()):
                        if self.vision_provider_select.itemData(i) == val:
                            self.vision_provider_select.setCurrentIndex(i)
                            # 触发模型列表刷新，避免只保留预设硬编码列表
                            self._on_vision_provider_select(i)
                            break
                elif key == "VISION_BASE_URL" and val:
                    self.vision_url.setText(val)
                elif key == "VISION_API_KEY" and val:
                    self.vision_key.setText(val)
                elif key == "VISION_MODEL" and val:
                    idx = self.vision_model.findText(val)
                    if idx >= 0:
                        self.vision_model.setCurrentIndex(idx)
                    else:
                        self.vision_model.setEditText(val)
        except Exception:
            pass

    @staticmethod
    def _strip_provider_suffix(text: str) -> str:
        """去掉 'model_id  [provider]' 后缀，返回纯 model_id"""
        import re
        return re.sub(r"\s{2,}\[[^\]]+\]\s*$", "", text).strip()

    def _save_env(self):
        from env_config import ENV_PATH
        lines = [
            "# OC Desktop Pet - API 配置",
            "# 留空则回退到 Hanako 的默认配置",
            "",
            "# LLM",
            f"LLM_PROVIDER={self.llm_provider_select.currentData() or ''}",
            f"LLM_BASE_URL={self.llm_url.text().strip()}",
            f"LLM_API_KEY={self.llm_key.text().strip()}",
            f"LLM_MODEL={self._strip_provider_suffix(self.llm_model.currentText())}",
            "",
            "# TTS API",
            f"TTS_PROVIDER={self.tts_provider_select.currentData() or ''}",
            f"TTS_BASE_URL={self.tts_url.text().strip()}",
            f"TTS_API_KEY={self.tts_key.text().strip()}",
            f"TTS_MODEL={self.tts_model.currentText().strip()}",
            f"TTS_VOICE={self.tts_voice.currentText().strip()}",
            "",
            "# ASR API",
            f"ASR_PROVIDER={self.asr_provider_select.currentData() or ''}",
            f"ASR_BASE_URL={self.asr_url.text().strip()}",
            f"ASR_API_KEY={self.asr_key.text().strip()}",
            f"ASR_MODEL={self.asr_model.currentText().strip()}",
            "",
            "# Vision API（屏幕感知专用，留空用 LLM 配置）",
            f"VISION_PROVIDER={self.vision_provider_select.currentData() or ''}",
            f"VISION_BASE_URL={self.vision_url.text().strip()}",
            f"VISION_API_KEY={self.vision_key.text().strip()}",
            f"VISION_MODEL={self.vision_model.currentText().strip()}",
        ]
        ENV_PATH.write_text("\n".join(lines) + "\n", "utf-8")

    # ── 保存 ──

    def _save(self):
        c = self._config

        # 行为
        beh_idx = self.behavior.currentIndex()
        c["behavior"] = ["quiet", "normal", "active", "cling"][beh_idx]

        # 窗口
        c["opacity"] = self.opacity.value() / 100
        c["scale"] = self.scale.value() / 100
        c["mouse_interaction"] = self.mouse_interaction.isChecked()

        # TTS
        c.setdefault("tts", {})["enabled"] = self.tts_enabled.isChecked()
        c["tts"]["provider"] = ["cosyvoice", "mimo", "api", "edge"][self.tts_provider.currentIndex()]
        c["tts"]["volume"] = self.tts_volume.value() / 100
        if hasattr(self, "tts_edge_voice"):
            c["tts"]["edge_voice"] = self.tts_edge_voice.currentText()

        # SFX
        c.setdefault("sfx", {})["enabled"] = self.sfx_enabled.isChecked()
        c["sfx"]["volume"] = self.sfx_volume.value() / 100

        # 主动对话
        c.setdefault("proactive", {})["enabled"] = self.pro_enabled.isChecked()
        c["proactive"]["cooldown_minutes"] = self.pro_cooldown.value()

        # 屏幕感知
        c.setdefault("screen", {})["enabled"] = self.screen_enabled.isChecked()
        c["screen"]["interval"] = self.screen_interval.value()
        c["screen"]["blur"] = self.screen_blur.isChecked()
        c["screen"]["blacklist"] = self.screen_blacklist.isChecked()
        c["screen"]["compress"] = self.screen_compress.isChecked()
        # 随机截屏间隔：勾选才写范围，不勾则清掉（回退到基准±30%）
        if self.screen_rand.isChecked():
            lo, hi = self.screen_interval_min.value(), self.screen_interval_max.value()
            c["screen"]["interval_min"] = min(lo, hi)
            c["screen"]["interval_max"] = max(lo, hi)
        else:
            c["screen"].pop("interval_min", None)
            c["screen"].pop("interval_max", None)

        # 窗口互动
        c.setdefault("window_interaction", {})["enabled"] = self.wi_enabled.isChecked()
        c["window_interaction"]["auto_walk"] = self.wi_auto_walk.isChecked()
        c["window_interaction"]["cooldown_seconds"] = self.wi_cooldown.value()

        # 久坐提醒
        c.setdefault("break_reminder", {})["enabled"] = self.break_enabled.isChecked()
        c["break_reminder"]["idle_minutes"] = self.break_idle.value()
        c["break_reminder"]["cooldown_minutes"] = self.break_cooldown.value()

        # ASR
        c.setdefault("asr", {})["provider"] = ["whisper_local", "mimo", "api"][self.asr_provider.currentIndex()]
        if hasattr(self, "asr_backend"):
            c["asr"]["backend"] = ["whisper", "faster_whisper"][self.asr_backend.currentIndex()]
        if hasattr(self, "asr_lang"):
            c["asr"]["language"] = ["zh", "auto"][self.asr_lang.currentIndex()]

        # 记忆注入
        c.setdefault("memory", {})["budget_mode"] = "auto" if self.mem_mode.currentIndex() == 0 else "manual"
        c["memory"]["budget_chars"] = self.mem_budget.value()

        # API .env
        self._save_env()

        # 角色包选择
        if hasattr(self, '_pkg_select'):
            pkg_data = self._pkg_select.currentData()
            if pkg_data:
                c["character_package"] = pkg_data
        
        # 渲染格式切换
        if hasattr(self, 'render_format_select'):
            fmt_data = self.render_format_select.currentData()
            if fmt_data and fmt_data != "auto":
                c["render_format"] = fmt_data
            else:
                c.pop("render_format", None)

        self.accept()

    def get_config(self) -> dict:
        return self._config

    # ── 主题化（颜色统一来自 ui/theme/palette，随主题切换刷新）──

    def _tab_qss(self, theme=None):
        t = theme or getattr(self, "_ui_theme", "dark")
        return f"""
            QTabWidget::pane {{ border: 1px solid rgb({rgb(t, 'panel_border')}); background: rgba({rgba(t, 'panel_bg')}); }}
            QTabBar::tab {{ background: rgba({rgba(t, 'tab_bg')}); color: rgb({rgb(t, 'text_secondary')}); padding: 6px 12px; }}
            QTabBar::tab:selected {{ background: rgba({rgba(t, 'panel_bg')}); color: rgb({rgb(t, 'text_primary')}); border-bottom: 2px solid rgb({rgb(t, 'btn_primary')}); }}
        """

    def _on_dialog_theme_changed(self, theme: str):
        self._ui_theme = theme
        if hasattr(self, "_main_tabs"):
            self._main_tabs.setStyleSheet(self._tab_qss(theme))
        if hasattr(self, "func_sub_tabs"):
            self.func_sub_tabs.setStyleSheet(self._tab_qss(theme))
        if hasattr(self, "mem_hint"):
            self.mem_hint.setStyleSheet("color: rgb(%s); font-size: 10px;" % rgb(theme, "text_muted"))
        if hasattr(self, "_pkg_status_label"):
            self._pkg_status_label.setStyleSheet("color: rgb(%s); font-size: 10px;" % rgb(theme, "text_muted"))
        if hasattr(self, "_vision_separator"):
            self._vision_separator.setStyleSheet("color: rgb(%s); font-weight: bold; margin-top: 10px;" % rgb(theme, "text_muted"))
        if hasattr(self, "_vision_hint"):
            self._vision_hint.setStyleSheet("color: rgb(%s); font-size: 11px;" % rgb(theme, "text_muted"))
