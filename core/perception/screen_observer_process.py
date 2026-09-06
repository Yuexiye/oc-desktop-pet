"""屏幕观察进程 — 独立进程运行截屏 + 视觉分析，避免卡 UI

竞品参考（Mio）：`screen_observer_process.py` 拆独立进程，主进程只收结果。
验收：截屏/视觉分析期间桌宠不掉帧。

架构：
- 独立进程：运行 ScreenPerception（截屏 + 视觉分析）
- 主进程：通过管道接收结果，转发回调
- 通信：使用 multiprocessing.Pipe（JSON 序列化）
"""
from __future__ import annotations

import json
import logging
import multiprocessing
import pickle
import threading
from typing import Callable, Optional

from .screen import ScreenPerception

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  进程内 worker（子进程运行）
# ════════════════════════════════════════════════════════════

def _worker(conn: multiprocessing.Connection, interval: int = 120):
    """子进程 worker：运行 ScreenPerception，结果通过管道发送"""
    logger.info("ScreenObserverProcess worker started")
    
    perception = ScreenPerception(interval=interval)
    
    # 回调：结果通过管道发送
    def on_update(desc):
        try:
            conn.send({
                "type": "update",
                "data": {"description": desc}
            })
        except Exception as e:
            logger.warning("Pipe send failed: %s", e)
    
    def on_emotion(emotion, intensity):
        try:
            conn.send({
                "type": "emotion",
                "data": {"emotion": emotion, "intensity": intensity}
            })
        except Exception as e:
            logger.warning("Pipe send failed: %s", e)
    
    def on_screen_proactive(prompt):
        try:
            conn.send({
                "type": "proactive",
                "data": {"prompt": prompt}
            })
        except Exception as e:
            logger.warning("Pipe send failed: %s", e)
    
    def on_scene(scene):
        try:
            conn.send({
                "type": "scene",
                "data": scene.__dict__ if scene else None
            })
        except Exception as e:
            logger.warning("Pipe send failed: %s", e)
    
    perception.on_update = on_update
    perception.on_emotion = on_emotion
    perception.on_screen_proactive = on_screen_proactive
    perception.on_scene = on_scene
    
    # 启动
    perception.start()
    
    # 保持进程存活，监听管道关闭
    try:
        while conn.poll():
            # 接收命令（如停止）
            try:
                msg = conn.recv()
                if msg.get("type") == "stop":
                    perception.stop()
                    break
            except EOFError:
                break
    except Exception as e:
        logger.warning("Worker loop error: %s", e)
    
    perception.stop()
    logger.info("ScreenObserverProcess worker stopped")


# ════════════════════════════════════════════════════════════
#  主进程封装
# ════════════════════════════════════════════════════════════

class ScreenObserverProcess:
    """屏幕观察进程封装（主进程侧）"""
    
    def __init__(self, interval: int = 120):
        self._interval = interval
        self._pipe_parent: Optional[multiprocessing.Connection] = None
        self._pipe_child: Optional[multiprocessing.Connection] = None
        self._process: Optional[multiprocessing.Process] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        
        # 回调
        self.on_update: Callable[[str], None] = lambda desc: None
        self.on_emotion: Callable[[str, float], None] = lambda emotion, intensity: None
        self.on_screen_proactive: Callable[[str], None] = lambda prompt: None
        self.on_scene: Callable[[Optional[object]], None] = lambda scene: None
    
    def start(self):
        """启动子进程"""
        with self._lock:
            if self._running:
                return
            
            self._pipe_parent, self._pipe_child = multiprocessing.Pipe()
            self._process = multiprocessing.Process(
                target=_worker,
                args=(self._pipe_child, self._interval),
                daemon=True,
                name="ScreenObserverProcess",
            )
            self._process.start()
            self._pipe_child.close()  # 父进程关闭子端
            
            # 启动监听线程
            self._listener_thread = threading.Thread(
                target=self._listen,
                daemon=True,
                name="ScreenObserverListener",
            )
            self._listener_thread.start()
            
            self._running = True
            logger.info("ScreenObserverProcess started (pid=%d)", self._process.pid)
    
    def stop(self):
        """停止子进程"""
        with self._lock:
            if not self._running:
                return
            
            try:
                self._pipe_parent.send({"type": "stop"})
            except Exception as e:
                logger.warning("Pipe send stop failed: %s", e)
            
            if self._process:
                self._process.join(timeout=5)
                if self._process.is_alive():
                    self._process.terminate()
                    logger.warning("ScreenObserverProcess terminated")
            
            if self._pipe_parent:
                self._pipe_parent.close()
            
            self._running = False
            logger.info("ScreenObserverProcess stopped")
    
    def _listen(self):
        """监听子进程消息"""
        while self._running:
            try:
                if self._pipe_parent.poll(timeout=1.0):
                    msg = self._pipe_parent.recv()
                    self._handle_message(msg)
                else:
                    # 检查进程是否还活着
                    if self._process and not self._process.is_alive():
                        logger.warning("ScreenObserverProcess died unexpectedly")
                        self._running = False
                        break
            except EOFError:
                logger.warning("Pipe closed")
                self._running = False
                break
            except Exception as e:
                logger.warning("Listener error: %s", e)
    
    def _handle_message(self, msg: dict):
        """处理子进程消息"""
        msg_type = msg.get("type")
        data = msg.get("data", {})
        
        try:
            if msg_type == "update":
                self.on_update(data.get("description", ""))
            elif msg_type == "emotion":
                self.on_emotion(data.get("emotion", ""), data.get("intensity", 0.0))
            elif msg_type == "proactive":
                self.on_screen_proactive(data.get("prompt", ""))
            elif msg_type == "scene":
                self.on_scene(data)  # data 可能是 dict 或 None
        except Exception as e:
            logger.warning("Handle message error: %s", e)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "ScreenObserverProcess",
]