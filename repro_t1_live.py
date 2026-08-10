"""T1 真机复现：连真实 Hanako 服务端，模拟桌宠 chat_via_hanako 的 session 逻辑

复现目标：连续发 N 条消息，观察服务端实际创建了几个 session。
  - 若每次 create_session 都新建 → 复现「一条/多条消息建多个 Session」
  - 若复用 → 正常

不依赖 GUI，直接走真实 REST + WS（端口 14500，token 从 server-info.json 读）。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.hanako_ws_client import HanakoWSClient
from core.hanako_session_manager import HanakoSessionManager

# 读取真实配置
cfg = __import__("env_config").get_hanako_config()
base_url = cfg["base_url"]
token = cfg["api_token"]
print(f"[cfg] base_url={base_url} transport={cfg['transport_mode']}")

ws = HanakoWSClient(base_url, token)
mgr = HanakoSessionManager(ws, base_url, token, reply_timeout=45)
ws.start()
if not ws.wait_until_ready(10):
    print("[FAIL] WS 未就绪，服务端可能没起")
    sys.exit(1)
print("[ok] WS 已连接")

# 模拟桌宠首次：list_sessions 拿当前列表
sessions_before = mgr.list_sessions(agent_id="yuexinmiao")
print(f"[info] 服务端 yuexinmiao 现有 session 数: {len(sessions_before)}")
for s in sessions_before:
    print(f"    - {s.session_id} | {s.session_path} | modified={s.modified}")

# 场景A：模拟 chat_via_hanako 首次（_current_session None，无 pinned）
print("\n=== 场景A：首次 chat（无 pinned）===")
created = mgr.create_session(agent_id="yuexinmiao")
print(f"[A] create_session -> {created.session_id} | path={created.session_path}")
print(f"[A] 现服务端 session 总数: {len(mgr.list_sessions(agent_id='yuexinmiao'))}")

# 场景B：模拟后续 chat，pinned 指向上面的 session，看 ensure_session 是否复用
print("\n=== 场景B：pinned 指向已建 session，ensure_session 应复用 ===")
reused = mgr.ensure_session(
    agent_id="yuexinmiao",
    preferred_session_id=created.session_id,
)
print(f"[B] ensure_session -> {reused.session_id}")
print(f"[B] 复用成功? {reused.session_id == created.session_id}")
print(f"[B] 现服务端 session 总数: {len(mgr.list_sessions(agent_id='yuexinmiao'))}")

# 场景C：pinned 指向一个不存在的 session_id，模拟「pin 失联」
print("\n=== 场景C：pinned 指向不存在的 session_id（模拟失联）===")
ghost = mgr.ensure_session(
    agent_id="yuexinmiao",
    preferred_session_id="sess_ghost_does_not_exist",
)
print(f"[C] ensure_session(ghost) -> {ghost.session_id}")
print(f"[C] 新建了? {ghost.session_id != created.session_id}")
print(f"[C] 现服务端 session 总数: {len(mgr.list_sessions(agent_id='yuexinmiao'))}")

# 场景D：连续 6 次失联 pinned，模拟「5 秒 6 个 Session」
print("\n=== 场景D：连续 6 次失联 pinned ===")
count_before = len(mgr.list_sessions(agent_id="yuexinmiao"))
ids = []
for i in range(6):
    s = mgr.ensure_session(
        agent_id="yuexinmiao",
        preferred_session_id=f"ghost_{i}",
    )
    ids.append(s.session_id)
count_after = len(mgr.list_sessions(agent_id="yuexinmiao"))
newly = count_after - count_before
print(f"[D] 6 次 ensure_session(全部失联) 后，服务端新增 session 数: {newly}")
print(f"[D] 新建的 id: {ids}")

mgr.close()
print("\n[done] 复现完成")