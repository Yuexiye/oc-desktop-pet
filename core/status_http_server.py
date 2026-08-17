"""本地状态口（F）— 复用 phone_receiver.py 范式的 HTTP 服务

任意外部程序（构建脚本/游戏/自动化/另一只桌宠）可查询桌宠状态；
写仅白名单、经事件总线转发、不直连渲染线程（硬约束 3）。

接口：
    GET  /pet/state      -> {"state": <6态>, "emotion": ..., "anim": ...,
                             "scenario": ..., "agent_id": ..., "renderer_format": ...,
                             "celebrating_active": bool, "ts": ...}
    GET  /pet/health     -> {"ok": True}
    POST /pet/set-mode   -> body {"mode": "celebrating"}；白名单校验
                             → EventBus.emit("pet_set_mode", mode=...)
                             → 200；白名单外 → 400；未开启写 → 403

安全/线程模型（与 PhoneActivityReceiver 一致）：
- 127.0.0.1 监听，可选 X-Auth-Token
- 写路径只 `EventBus.emit` 事件；PetWindow 订阅后经 Qt 信号转主线程再驱动，
  绝不直连渲染线程（EventBus.emit 在 HTTP 线程同步执行，但 handler 只做
  状态登记/信号发射，Qt 对象操作由信号转到主线程）。
- 默认关：config `state_http.enabled=False` 时不启动，零行为、不占端口。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable

from core.event_bus import EventBus

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8977

# POST /pet/set-mode 白名单（写，只写不读渲染线程）
SET_MODE_WHITELIST: tuple[str, ...] = (
    "celebrating", "idle", "working", "review", "waiting", "failed",
    "happy", "thinking", "sad", "surprised",
)


def _make_handler(state_provider: Callable[[], dict], auth_token: str,
                  allow_set_mode: bool):
    """动态创建请求处理器，绑定状态提供器 / auth_token / 写开关"""

    class Handler(BaseHTTPRequestHandler):
        """处理桌宠状态查询与受控写请求"""

        def _check_auth(self) -> bool:
            token = self.headers.get('X-Auth-Token', '')
            if not auth_token:
                return True  # 未配置 token 则跳过验证
            return token == auth_token

        def _send_json(self, code: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/pet/health':
                self._send_json(200, {'ok': True, 'service': 'pet-status'})
                return
            if self.path == '/pet/state':
                if not self._check_auth():
                    self._send_json(401, {'ok': False, 'error': 'Unauthorized'})
                    return
                try:
                    snapshot = state_provider() if callable(state_provider) else {}
                    if not isinstance(snapshot, dict):
                        snapshot = {}
                    snapshot.setdefault('ok', True)
                    self._send_json(200, snapshot)
                except Exception as e:
                    logger.warning("GET /pet/state error: %s", e)
                    self._send_json(500, {'ok': False, 'error': str(e)})
                return
            self._send_json(404, {'ok': False, 'error': 'Not found'})

        def do_POST(self):
            # 路由匹配
            if self.path != '/pet/set-mode':
                self._send_json(404, {'ok': False, 'error': 'Not found'})
                return
            # 认证
            if not self._check_auth():
                self._send_json(401, {'ok': False, 'error': 'Unauthorized'})
                return
            # 写开关（默认关）
            if not allow_set_mode:
                self._send_json(403, {'ok': False, 'error': 'Set-mode disabled'})
                return
            # 读取 body
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length) if length > 0 else b'{}'
                data = json.loads(raw)
            except Exception as e:
                self._send_json(400, {'ok': False, 'error': f'Invalid JSON: {e}'})
                return
            mode = str(data.get('mode', '')).strip()
            # 白名单校验
            if mode not in SET_MODE_WHITELIST:
                self._send_json(400, {
                    'ok': False, 'error': f'Invalid mode: {mode}',
                    'whitelist': list(SET_MODE_WHITELIST),
                })
                return
            # 经事件总线转发（不直连渲染线程）
            try:
                EventBus.emit('pet_set_mode', mode=mode)
                logger.info("F set-mode: %s", mode)
                self._send_json(200, {'ok': True, 'mode': mode})
            except Exception as e:
                logger.warning("F set-mode emit error: %s", e)
                self._send_json(500, {'ok': False, 'error': str(e)})

        def log_message(self, fmt, *args):
            """抑制默认 stderr 日志，用 logger 代替"""
            logger.debug(fmt, *args)

    return Handler


class PetStatusHTTPServer:
    """桌宠本地状态 HTTP 服务（守护线程，复用 PhoneActivityReceiver 模式）

    用法：
        server = PetStatusHTTPServer(state_provider=self._status_snapshot,
                                     auth_token="", port=8977)
        server.start()   # 后台守护线程
        ...
        server.stop()
    """

    def __init__(self, state_provider: Callable[[], dict], auth_token: str = "",
                 port: int = DEFAULT_PORT, allow_set_mode: bool = False):
        """
        Args:
            state_provider: 返回状态快照 dict 的可调用对象（PetWindow._status_snapshot）
            auth_token: X-Auth-Token；空则跳过验证
            port: 监听端口（默认 8977）
            allow_set_mode: 是否允许 POST /pet/set-mode（默认 False=只读）
        """
        self._state_provider = state_provider
        self._auth_token = auth_token
        self._port = int(port or DEFAULT_PORT)
        self._allow_set_mode = bool(allow_set_mode)
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        """在后台守护线程启动 HTTP 服务"""
        try:
            handler_cls = _make_handler(self._state_provider, self._auth_token,
                                        self._allow_set_mode)
            self._server = HTTPServer(('127.0.0.1', self._port), handler_cls)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("PetStatusHTTPServer started on port %d (set_mode=%s)",
                        self._port, self._allow_set_mode)
        except Exception as e:
            logger.warning("PetStatusHTTPServer start failed: %s", e)
            self._server = None
            self._thread = None

    def stop(self) -> None:
        """停止 HTTP 服务（shutdown 停循环 + server_close 释放 socket）"""
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception as e:
                logger.debug("PetStatusHTTPServer shutdown: %s", e)
            try:
                self._server.server_close()
            except Exception as e:
                logger.warning("PetStatusHTTPServer server_close failed: %s", e)
            self._server = None
            self._thread = None
            logger.info("PetStatusHTTPServer stopped")


__all__ = ["PetStatusHTTPServer", "SET_MODE_WHITELIST", "DEFAULT_PORT"]
