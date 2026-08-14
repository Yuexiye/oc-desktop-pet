"""FrameBaker 集成 — 帧动画编辑器

FrameBaker 是一个 Bun 全栈应用，支持：
- 多源导入（GIF/MP4 帧提取、PNG 上传、CLI 生成）
- 内置 rembg 遮罩引擎切割背景
- PixiJS 洋葱皮画布编辑帧
- 时间线管理和spritesheet导出
- MCP 服务器（34 个 AI 工具）

集成方式：
1. 本地运行 FrameBaker 服务（bun dev）
2. 通过 MCP 协议控制（POST /mcp）
3. 导出 spritesheet 后由 SpriteRenderer 加载
"""
import os
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# FrameBaker 配置
FRAMEBAKER_PATH = os.environ.get("FRAMEBAKER_PATH", "")  # 用户安装路径
FRAMEBAKER_URL = "http://localhost:3000"  # 默认服务地址
FRAMEBAKER_MCP_ENDPOINT = f"{FRAMEBAKER_URL}/mcp"

# 检查 FrameBaker 是否已安装
def is_framebaker_installed() -> bool:
    """检查 FrameBaker 是否已安装并可用"""
    if not FRAMEBAKER_PATH:
        return False
    try:
        # 检查 bun 是否可用
        result = subprocess.run(["bun", "--version"], capture_output=True, timeout=5)
        if result.returncode != 0:
            logger.warning("bun 未安装，FrameBaker 无法运行")
            return False
        # 检查项目目录
        package_json = Path(FRAMEBAKER_PATH) / "package.json"
        if not package_json.exists():
            logger.warning("FrameBaker 目录无 package.json: %s", FRAMEBAKER_PATH)
            return False
        return True
    except Exception as e:
        logger.warning("检查 FrameBaker 失败: %s", e)
        return False

# 启动 FrameBaker 服务
def start_framebaker(detach: bool = True) -> bool:
    """启动 FrameBaker 开发服务器
    
    Args:
        detach: True=后台启动，False=前台阻塞
    """
    if not is_framebaker_installed():
        logger.error("FrameBaker 未安装，请先设置 FRAMEBAKER_PATH 环境变量")
        return False
    try:
        cmd = ["bun", "dev"]
        if detach:
            subprocess.Popen(
                cmd,
                cwd=FRAMEBAKER_PATH,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(cmd, cwd=FRAMEBAKER_PATH, check=True)
        logger.info("FrameBaker 启动成功: %s", FRAMEBAKER_URL)
        return True
    except Exception as e:
        logger.error("FrameBaker 启动失败: %s", e)
        return False

# 停止 FrameBaker 服务
def stop_framebaker() -> bool:
    """停止 FrameBaker 服务"""
    try:
        # 查找 bun 进程并终止
        import platform
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "bun.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "bun dev"], capture_output=True)
        logger.info("FrameBaker 已停止")
        return True
    except Exception as e:
        logger.warning("停止 FrameBaker 失败: %s", e)
        return False

# MCP 客户端（未来实现）
class FrameBakerMCP:
    """FrameBaker MCP 客户端
    
    通过 HTTP POST /mcp 与 FrameBaker 通信
    支持 34 个 AI 工具：
    - 项目管理
    - 帧编辑
    - 材质管理
    - 生成/遮罩
    - 导出 spritesheet
    """
    
    def __init__(self, endpoint: str = FRAMEBAKER_MCP_ENDPOINT):
        self.endpoint = endpoint
        self._session_id = None
    
    def initialize(self) -> bool:
        """初始化 MCP 连接"""
        # TODO: 实现 MCP 协议握手
        pass
    
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 FrameBaker 工具"""
        # TODO: 实现 MCP 工具调用
        pass
    
    def export_spritesheet(self, project_id: str, output_path: str) -> bool:
        """导出 spritesheet"""
        # TODO: 调用 FrameBaker 导出 API
        pass

# 菜单集成（供 pet.py 调用）
def get_framebaker_menu_items():
    """返回 FrameBaker 菜单项"""
    items = []
    if is_framebaker_installed():
        items.append(("🎬 启动 FrameBaker", start_framebaker))
        items.append(("🛑 停止 FrameBaker", stop_framebaker))
    else:
        items.append(("⚙️ 配置 FrameBaker", open_settings))
    return items

def open_settings():
    """打开 FrameBaker 设置"""
    from PySide6.QtWidgets import QInputDialog
    path, ok = QInputDialog.getText(
        None, "FrameBaker 路径",
        "请输入 FrameBaker 安装路径（包含 package.json 的目录）:"
    )
    if ok and path:
        os.environ["FRAMEBAKER_PATH"] = path
        logger.info("FrameBaker 路径已设置: %s", path)
        # 持久化到配置
        try:
            from config import load_config, save_config
            cfg = load_config()
            cfg.setdefault("framebaker", {})["path"] = path
            save_config(cfg)
        except Exception as e:
            logger.warning("保存 FrameBaker 配置失败: %s", e)
