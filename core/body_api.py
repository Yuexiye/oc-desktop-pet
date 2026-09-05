"""core/body_api.py — 进程内 BodyAPI 门面

T2-3: 三级端口收敛成进程内 BodyAPI 门面

现状：
    - status_http_server (8977) — 只读状态 + 可选白名单写
    - external_trigger_receiver (8988) — 通用外部触发入口

目标：
    - 收敛为进程内 BodyAPI，减少端口数量
    - 补充 /speak /screenshot /emotion 语义

BodyAPI 提供统一接口，内部转发到 EventBus 或对应模块。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from core.event_bus import EventBus

log = logging.getLogger(__name__)


class BodyAPI:
    """进程内 BodyAPI 门面

    用法::

        api = BodyAPI()
        api.speak("你好")
        api.set_emotion("happy")
        api.screenshot()
        api.trigger("remind", "休息一下吧")
    """

    def __init__(self):
        self._callbacks: dict[str, list[Callable]] = {}

    # ── 状态查询 ──

    def get_state(self) -> dict:
        """获取桌宠状态快照"""
        try:
            # 从 EventBus 获取状态（如果已注册）
            state = EventBus.get_state() if hasattr(EventBus, 'get_state') else {}
            return state or {}
        except Exception as e:
            log.debug("BodyAPI.get_state: %s", e)
            return {}

    def get_health(self) -> dict:
        """健康检查"""
        return {"ok": True, "service": "body-api"}

    # ── 动作控制 ──

    def set_mode(self, mode: str) -> bool:
        """设置模式（celebrating/idle/working/review/waiting/failed）"""
        SET_MODE_WHITELIST = (
            "celebrating", "idle", "working", "review", "waiting", "failed",
            "happy", "thinking", "sad", "surprised",
        )
        if mode not in SET_MODE_WHITELIST:
            log.warning("BodyAPI.set_mode: 非法模式 %s", mode)
            return False
        try:
            EventBus.emit("pet_set_mode", mode=mode)
            log.info("BodyAPI.set_mode: %s", mode)
            return True
        except Exception as e:
            log.error("BodyAPI.set_mode: %s", e)
            return False

    def trigger(self, action: str, text: str, emotion: str = "neutral") -> bool:
        """触发外部动作（remind/say/praise/custom）"""
        ACTION_WHITELIST = ("remind", "say", "praise", "custom")
        if action not in ACTION_WHITELIST:
            log.warning("BodyAPI.trigger: 非法动作 %s", action)
            return False
        try:
            EventBus.emit("external_trigger", action=action, text=text, emotion=emotion, source="body-api")
            log.info("BodyAPI.trigger: action=%s text=%s", action, text[:50])
            return True
        except Exception as e:
            log.error("BodyAPI.trigger: %s", e)
            return False

    # ── 新增语义（T2-3） ──

    def speak(self, text: str, emotion: str = "neutral") -> bool:
        """说话（触发对话引擎）"""
        if not text or not text.strip():
            log.warning("BodyAPI.speak: 空文本")
            return False
        try:
            # 转发到对话引擎
            EventBus.emit("pet_speak", text=text, emotion=emotion, source="body-api")
            log.info("BodyAPI.speak: %s", text[:50])
            return True
        except Exception as e:
            log.error("BodyAPI.speak: %s", e)
            return False

    def set_emotion(self, emotion: str) -> bool:
        """设置情绪"""
        VALID_EMOTIONS = ("happy", "sad", "angry", "surprised", "thinking", "neutral", "cute", "missing")
        if emotion not in VALID_EMOTIONS:
            log.warning("BodyAPI.set_emotion: 非法情绪 %s", emotion)
            return False
        try:
            EventBus.emit("pet_set_emotion", emotion=emotion, source="body-api")
            log.info("BodyAPI.set_emotion: %s", emotion)
            return True
        except Exception as e:
            log.error("BodyAPI.set_emotion: %s", e)
            return False

    def screenshot(self) -> Optional[bytes]:
        """截图（返回 PNG 字节流）"""
        try:
            # 触发截图事件
            EventBus.emit("pet_screenshot", source="body-api")
            # 截图需要 PetWindow 响应，这里返回 None 占位
            log.info("BodyAPI.screenshot: 已触发截图事件")
            return None
        except Exception as e:
            log.error("BodyAPI.screenshot: %s", e)
            return None

    # ── 回调注册 ──

    def on(self, event: str, callback: Callable):
        """注册事件回调"""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    def _emit(self, event: str, **kwargs: Any):
        """内部事件发射"""
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception as e:
                log.error("BodyAPI callback error: %s", e)

    # ── JSON 接口（HTTP 兼容） ──

    def handle_request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """处理 HTTP 请求（兼容旧接口）"""
        try:
            if method == "GET":
                if path == "/pet/health":
                    return self.get_health()
                elif path == "/pet/state":
                    return self.get_state()
                else:
                    return {"ok": False, "error": "Not found"}
            elif method == "POST":
                if path == "/pet/set-mode":
                    mode = body.get("mode", "") if body else ""
                    ok = self.set_mode(mode)
                    return {"ok": ok}
                elif path == "/trigger":
                    action = body.get("action", "custom") if body else "custom"
                    text = body.get("text", "") if body else ""
                    emotion = body.get("emotion", "neutral") if body else "neutral"
                    ok = self.trigger(action, text, emotion)
                    return {"ok": ok, "received": ok}
                elif path == "/speak":
                    text = body.get("text", "") if body else ""
                    emotion = body.get("emotion", "neutral") if body else "neutral"
                    ok = self.speak(text, emotion)
                    return {"ok": ok}
                elif path == "/emotion":
                    emotion = body.get("emotion", "") if body else ""
                    ok = self.set_emotion(emotion)
                    return {"ok": ok}
                elif path == "/screenshot":
                    img = self.screenshot()
                    if img:
                        return {"ok": True, "data": img.hex()}
                    return {"ok": False, "error": "Screenshot failed"}
                else:
                    return {"ok": False, "error": "Not found"}
            else:
                return {"ok": False, "error": "Method not allowed"}
        except Exception as e:
            log.error("BodyAPI.handle_request: %s", e)
            return {"ok": False, "error": str(e)}


# ── 单例 ──

_body_api: Optional[BodyAPI] = None


def get_body_api() -> BodyAPI:
    """获取 BodyAPI 单例"""
    global _body_api
    if _body_api is None:
        _body_api = BodyAPI()
    return _body_api


def init_body_api() -> BodyAPI:
    """初始化 BodyAPI（在 main.py 调用）"""
    global _body_api
    if _body_api is None:
        _body_api = BodyAPI()
        log.info("BodyAPI 初始化完成")
    return _body_api
