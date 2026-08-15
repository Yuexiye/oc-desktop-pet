"""开场问候逻辑 — 零配置首次启动时主动打招呼。

检测条件：
  1. 配置中 greeting.enabled 为 True（默认开启）
  2. 标记文件不存在（幂等：已问候过不再问候）
  3. LLM 未配置：agent_id 为空 + 环境变量无有效 API Key

调用方（主 agent / pet.py 启动阶段）拿到 (text, emotion) 后调用 show_bubble 即可。
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def is_llm_unconfigured() -> bool:
    """判断当前环境变量是否处于「无 LLM 配置」状态。

    仅检查 os.environ 中 LLM_API_KEY 是否为空或占位符，
    agent_id 的判断由 check_first_launch 负责（config 参数传入）。

    Returns:
        True = 确认无 LLM API Key，False = 有真实 API Key
    """
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    # 空值 或 占位符
    if api_key and api_key not in ("xxx", "your_key", "YOUR_KEY", ""):
        return False
    return True


def check_first_launch(
    config: dict,
    greet_marker_path: str,
) -> bool:
    """判断是否应该播放开场问候气泡。

    三个条件必须同时满足：
      1. config["greeting"]["enabled"] == True（默认开启）
      2. 标记文件不存在
      3. 当前无 LLM 配置（is_llm_unconfigured() + agent_id 为空）

    Args:
        config: 当前完整配置字典
        greet_marker_path: 标记文件路径

    Returns:
        True = 应该播放问候
    """
    # 条件 1：功能开关
    greeting_cfg = config.get("greeting", {})
    if not greeting_cfg.get("enabled", True):
        logger.debug("greeting.disabled == True，跳过开场问候")
        return False

    # 条件 2：幂等标记
    marker = Path(greet_marker_path)
    if marker.exists():
        logger.debug("greeting marker exists, skipping")
        return False

    # 条件 3：LLM 未配置
    dialog = config.get("dialog", {})
    agent_id = dialog.get("agent_id", "").strip()
    no_llm_env = is_llm_unconfigured()
    no_agent = agent_id == ""
    if not (no_llm_env and no_agent):
        logger.debug(
            "LLM is configured (env=%s, agent_id=%s), skipping greeting",
            no_llm_env,
            repr(agent_id),
        )
        return False

    logger.info("first launch detected, will show greeting")
    return True


# ── 文案池（口语化中文，轻幽默）─────────────────────────────────────

_GREETING_CANDIDATES: list[str] = [
    "嗨，我是 {myname}～ 现在还没接通对话后端，去设置里配一下，我就能陪你聊天啦！",
    "你好呀！我是 {myname}，暂时还没连上 AI 大脑，配好之后我就能跟你好好说话啰。",
    "{myname} 报到！不过现在对话后端还没接上，去配置面板填一下 API 信息，咱就能愉快地聊天了！",
    "哇，你终于来了！我是 {myname}，现在还是个'哑巴'——去设置里给我配上对话后端，我就能开口啦！",
    "嘿！我是 {myname}，还没连上后台 AI 所以暂时不能聊天……去 config 或设置面板配一下 LLM 吧，配好叫我！",
]


def build_greeting_text(agent_name: str) -> tuple[str, str]:
    """随机选一条问候文案，返回 (文案, 情绪)。

    Args:
        agent_name: 角色名（会插进文案里的 {myname} 占位符）

    Returns:
        (greeting_text, "happy")
    """
    text = random.choice(_GREETING_CANDIDATES).format(myname=agent_name)
    return text, "happy"


def mark_greeted(greet_marker_path: str) -> None:
    """写入问候标记文件（含时间戳），确保幂等。

    Args:
        greet_marker_path: 标记 JSON 文件路径
    """
    marker = Path(greet_marker_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "greeted_at": time.time(),
        "agent_name": "",  # 占位，便于日后扩展
    }
    try:
        marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.info("greeting marker written to %s", marker)
    except OSError as e:
        logger.error("failed to write greeting marker: %s", e)


def maybe_greet(
    config: dict,
    greet_marker_path: str,
    agent_name: str,
) -> tuple[str, str] | None:
    """组合入口：检测到首次启动 + 无 LLM 则标记并返回问候。

    Args:
        config: 完整配置字典
        greet_marker_path: 标记文件路径
        agent_name: 角色显示名（如"月薪喵"）

    Returns:
        (text, emotion) 元组，或 None（不应问候）
    """
    if not check_first_launch(config, greet_marker_path):
        return None
    mark_greeted(greet_marker_path)
    return build_greeting_text(agent_name)


# ── 便捷路径生成 ────────────────────────────────────────────────────

def default_marker_path(agent_id: str) -> str:
    """生成默认问候标记路径：~/.hanako/pets/greet_{agent_id}.json

    Args:
        agent_id: 对话后端 agent ID（可传 character 名兜底）

    Returns:
        绝对路径字符串
    """
    return os.path.join(
        os.path.expanduser("~"),
        ".hanako",
        "pets",
        f"greet_{agent_id}.json",
    )
