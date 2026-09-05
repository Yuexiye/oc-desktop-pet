"""统一面板窗口基类 - 提供一致的标题栏、刷新按钮、关闭按钮

所有面板（桌宠总览、活动流、插件、记忆、角色卡）应继承此类。
"""
from __future__ import annotations

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLabel, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent

logger = logging.getLogger(__name__)


class PanelWindow(QDialog):
    """统一面板窗口基类
    
    提供：
    - 统一的标题栏（标题 + 刷新按钮 + 关闭按钮）
    - 统一的内容区域
    - 统一的窗口样式
    - 拖拽移动支持
    """
    
    # 信号
    refresh_requested = Signal()  # 刷新按钮点击
    
    def __init__(self, title: str, parent=None, 
                 show_refresh: bool = True, 
                 min_size: tuple = (360, 400),
                 max_size: tuple = (520, 720)):
        super().__init__(parent)
        self._title = title
        self._show_refresh = show_refresh
        self._drag_pos = None
        
        # 窗口设置
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(*min_size)
        self.setMaximumSize(*max_size)
        
        # 主题
        from ui.theme.theme_manager import get_default
        mgr = get_default()
        self._ui_theme = mgr.current if mgr else "dark"
        if mgr is not None:
            mgr.theme_changed.connect(self._on_theme_changed)
        
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 卡片容器
        self._card = QFrame()
        self._card.setObjectName("panelCard")
        layout.addWidget(self._card)
        
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(14, 12, 14, 14)
        card_layout.setSpacing(8)
        
        # 标题栏
        header = self._create_header()
        card_layout.addWidget(header)
        
        # 内容区域（子类填充）
        self._content_area = QWidget()
        self.content_layout = QVBoxLayout(self._content_area)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        card_layout.addWidget(self._content_area, 1)
        
        # 底部区域（可选，子类添加）
        self._footer_area = None
        
        # 应用样式
        self.setStyleSheet(self._build_qss())
    
    def _create_header(self) -> QWidget:
        """创建统一的标题栏"""
        header = QFrame()
        header.setObjectName("panelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        
        # 标题
        title_label = QLabel(self._title)
        title_label.setObjectName("panelTitle")
        title_label.setStyleSheet(f"font-weight: 600; font-size: 13px;")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # 刷新按钮
        if self._show_refresh:
            refresh_btn = QPushButton("🔄")
            refresh_btn.setObjectName("refreshBtn")
            refresh_btn.setFixedWidth(28)
            refresh_btn.setFixedHeight(28)
            refresh_btn.setToolTip("刷新")
            refresh_btn.clicked.connect(self._on_refresh_clicked)
            header_layout.addWidget(refresh_btn)
        
        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedWidth(28)
        close_btn.setFixedHeight(28)
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        
        return header
    
    def _on_refresh_clicked(self):
        """刷新按钮点击"""
        self.refresh_requested.emit()
    
    def _on_theme_changed(self, theme: str):
        """主题变化"""
        self._ui_theme = theme
        self.setStyleSheet(self._build_qss())
    
    def _build_qss(self) -> str:
        """构建统一的 QSS 样式"""
        from ui.theme.palette import rgb, rgba
        t = self._ui_theme
        
        return f"""
            QDialog {{ background: transparent; }}
            
            #panelCard {{
                background: rgba({rgba(t, 'panel_bg')});
                border: 1px solid rgba({rgba(t, 'panel_border')});
                border-radius: 10px;
            }}
            
            #panelHeader {{
                background: rgba({rgba(t, 'tab_bg')});
                border-bottom: 1px solid rgba({rgba(t, 'panel_border')});
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
                padding: 4px 8px;
            }}
            
            #panelTitle {{
                color: rgba({rgba(t, 'text_primary')});
                font-weight: 600;
                font-size: 13px;
            }}
            
            #refreshBtn, #closeBtn {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: rgba({rgba(t, 'text_secondary')});
                font-size: 14px;
                padding: 4px;
            }}
            
            #refreshBtn:hover, #closeBtn:hover {{
                background: rgba({rgba(t, 'hover')});
                color: rgba({rgba(t, 'text_primary')});
            }}
            
            #closeBtn:hover {{
                background: rgba(255, 80, 80, 40);
                color: rgb(255, 100, 100);
            }}
        """
    
    def add_footer(self, widget: QWidget):
        """添加底部区域（可选）"""
        if self._footer_area is not None:
            return
        
        self._footer_area = QWidget()
        footer_layout = QVBoxLayout(self._footer_area)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(4)
        footer_layout.addWidget(widget)
        
        # 插入到内容区域之后
        card = self._card
        card_layout = card.layout()
        card_layout.addWidget(self._footer_area)
    
    # ── 拖拽支持 ──
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and event.y() < 40:
            # 点击标题栏区域
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
    
    # ── 子类 API ──
    
    def set_title(self, title: str):
        """设置标题"""
        self._title = title
        self.setWindowTitle(title)
        # 更新标题标签
        for child in self._card.findChildren(QLabel):
            if child.objectName() == "panelTitle":
                child.setText(title)
                break
    
    def refresh(self):
        """刷新面板内容（子类重写）"""
        pass
    
    def closeEvent(self, event):
        """关闭事件"""
        self._drag_pos = None
        super().closeEvent(event)