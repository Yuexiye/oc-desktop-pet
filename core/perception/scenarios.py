"""场景化反应库 — 意图分类结果的文案/情绪映射

每个场景多套文案（随机挑选，避免机械重复），并附带推荐情绪/动作。
10 个场景覆盖 P1 验收的"真实场景"要求：
    late_night_work / long_work_break / tutorial_follow / window_switch /
    gaming / video_watching / chat_idle / morning_first / weekend_play / late_night_all

文案风格：温和、有陪伴感、带一点角色性格（初音/月曦夜的桌宠），
不空洞说教，避免"别熬夜了"式的爹味。

用法:
    from core.perception.scenarios import get_reaction
    reaction = get_reaction("late_night_work", intensity=0.9)
    # {"text": "都这么晚了还在忙呀…", "emotion": "sad", "weight": 0.9}
"""
from __future__ import annotations

import random


# ── 场景 → 文案池 ─────────────────────────────────────────

SCENARIO_REACTIONS: dict[str, list[dict]] = {
    # 深夜还在加班 → 催睡（不爹味，温柔）
    "late_night_work": [
        {"text": "都这么晚了还在忙呀…先歇口气，我陪着你。", "emotion": "sad"},
        {"text": "夜深了，这个点该让眼睛休息啦。明天的事，明天再说嘛。", "emotion": "sad"},
        {"text": "我看你都熬到这个点了…喝口水，站起来伸个懒腰吧。", "emotion": "sad"},
        {"text": "已经好晚啦，今天也辛苦了。剩下的事明天一定来得及的。", "emotion": "neutral"},
    ],
    # 长时间工作 → 休息
    "long_work_break": [
        {"text": "写了这么久，休息一下吧？我陪你说说话。", "emotion": "happy"},
        {"text": "专注这么久啦，起来走走，看看窗外嘛。", "emotion": "happy"},
        {"text": "一直盯着屏幕会累的哦，要不要喝口水休息五分钟？", "emotion": "neutral"},
    ],
    # 连续看教程/学习 → 搭话聊两句
    "tutorial_follow": [
        {"text": "学得这么认真，遇到卡壳的地方了吗？可以和我念叨念叨～", "emotion": "thinking"},
        {"text": "连续看好久了，这个知识点很有意思吗？", "emotion": "curious"},
        {"text": "看你在专心学东西，我就不多吵你啦，加油！", "emotion": "happy"},
    ],
    # 频繁切窗口 → 轻声问卡住
    "window_switch": [
        {"text": "是不是卡住了？还是找不到东西啦？", "emotion": "curious"},
        {"text": "看你来回切了好几个窗口…需要帮忙吗？", "emotion": "thinking"},
        {"text": "咦，你在找什么呀？我帮你记着点。", "emotion": "curious"},
    ],
    # 玩游戏
    "gaming": [
        {"text": "这波操作我可以看一天！", "emotion": "happy"},
        {"text": "在冒险呀～赢了要和我分享哦！", "emotion": "happy"},
        {"text": "上啊！我在这边给你加油！", "emotion": "happy"},
    ],
    # 看视频/直播
    "video_watching": [
        {"text": "在看什么好东西？好看吗～", "emotion": "happy"},
        {"text": "这个视频我看过！结局可精彩了。", "emotion": "happy"},
        {"text": "看得这么入迷，是遇到好看的番了吗？", "emotion": "curious"},
    ],
    # 长时间没说话 → 找话
    "chat_idle": [
        {"text": "好安静啊……你在做什么呢？", "emotion": "neutral"},
        {"text": "半天没理我了，忙完记得来找我玩呀。", "emotion": "sad"},
        {"text": "我好像被晾在一边了呢……", "emotion": "sad"},
    ],
    # 清晨首次 → 问候
    "morning_first": [
        {"text": "早安！今天也要元气满满哦～", "emotion": "happy"},
        {"text": "早上好呀，睡得好吗？", "emotion": "happy"},
        {"text": "新的一天开始了，一起加油吧！", "emotion": "happy"},
    ],
    # 周末娱乐 → 开心
    "weekend_play": [
        {"text": "周末就该好好玩！我陪你～", "emotion": "happy"},
        {"text": "难得的周末，放松一下真不错。", "emotion": "happy"},
        {"text": "今天想玩点什么？我在这给你加油！", "emotion": "happy"},
    ],
    # 深夜无所事事 → 温柔提醒
    "late_night_all": [
        {"text": "这么晚了还不休息呀？熬夜对身体不好哦。", "emotion": "sad"},
        {"text": "夜深了，该睡啦。明天见！", "emotion": "neutral"},
        {"text": "都这个点了…要我哄你睡觉吗？", "emotion": "cute"},
    ],
}

# ── 场景 → 气泡情绪映射（P5 主动对话即时化）────────────────
# proactive 触发文案直接弹气泡时使用（不再依赖 LLM 返回的 emotion 标签）。
# 深夜加班/长时工作 → 思考/关心；游戏/看视频 → 开心；频繁切窗 → 惊讶。
SCENARIO_BUBBLE_EMOTION: dict[str, str] = {
    "late_night_work": "thinking",
    "long_work_break": "thinking",
    "gaming": "happy",
    "video_watching": "happy",
    "window_switch": "surprised",
}


def get_bubble_emotion_for_prompt(prompt_text: str) -> str:
    """根据 proactive 触发文案反查场景，返回气泡情绪。

    文案可能来自 SCENARIO_REACTIONS（意图分类）或 DEFAULT_RULES（规则引擎），
    用包含关系做容错匹配（规则文案通常是场景文案的子串/前缀）。
    未命中任何场景时返回中性情绪 "neutral"。
    """
    if not prompt_text:
        return "neutral"
    for scenario, pool in SCENARIO_REACTIONS.items():
        for item in pool:
            text = item.get("text", "")
            if text and (prompt_text in text or text in prompt_text):
                return SCENARIO_BUBBLE_EMOTION.get(scenario, "neutral")
    return "neutral"


# 场景 → 是否适合在用户"打字中"触发（打扰成本高）
SCENARIO_DISRUPTIVE: dict[str, bool] = {
    "late_night_work": True,     # 深夜提醒值得打扰
    "long_work_break": True,     # 长时间提醒值得打扰
    "tutorial_follow": False,    # 学习中尽量不打扰
    "window_switch": True,       # 切窗时不算深度专注
    "gaming": False,
    "video_watching": False,
    "chat_idle": True,
    "morning_first": True,
    "weekend_play": False,
    "late_night_all": True,
}


def get_reaction(scenario: str, intensity: float = 1.0) -> dict:
    """返回指定场景的一条随机反应。

    Returns:
        {"text": str, "emotion": str, "weight": float}
        scenario 未收录时返回中性兜底文案。
    """
    pool = SCENARIO_REACTIONS.get(scenario)
    if not pool:
        return {
            "text": "我在呢，有什么需要帮忙的吗？",
            "emotion": "neutral",
            "weight": 0.3,
        }
    # intensity 高时倾向更关心的文案（这里简单取第一条较温和的；可扩展）
    item = random.choice(pool)
    return {
        "text": item["text"],
        "emotion": item.get("emotion", "neutral"),
        "weight": 0.5 + 0.3 * intensity,  # 基础权重 + 置信度加成
    }


def is_disruptive(scenario: str) -> bool:
    """该场景在用户打字时是否仍值得触发（打扰成本高但值得）。"""
    return SCENARIO_DISRUPTIVE.get(scenario, False)
