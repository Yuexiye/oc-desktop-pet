"""avatar/model_profile.py — 模型参数映射配置（D1-lite）。

T07: 将 live2d_renderer.py 中硬编码的参数写入映射提取为数据类。
每个运动/表情/预设占用的参数通道、权重、clamp、scale 都声明在此，
便于 T08 MotionMixer 仲裁，以及未来 T09 profile 配置化。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional


# ── 单个物理参数通道 ──


@dataclass(frozen=True)
class ParamChannel:
    """一个 Live2D 物理参数通道的写入描述。

    Attributes:
        std_name: ``live2d.v3.params.StandardParams`` 属性名
                  （如 ``"ParamEyeLOpen"``）。也接受裸字符串 id
                  （如 ``"EyeLOpen"``），用于非 StandardParams 的自定义参数。
        weight: ``SetParameterValue(pid, val, weight)`` 的权重。
        clamp: 可选 (min, max) 截断范围（写入前对语义值做 clamp）。
        scale: 写入前对语义值乘以的倍率（默认 1.0）。
    """

    std_name: str
    weight: float = 1.0
    clamp: Optional[tuple[float, float]] = None
    scale: float = 1.0

    def prepare_value(self, value: float) -> float:
        """应用 scale → clamp 后返回最终写入值。"""
        v = value * self.scale
        if self.clamp is not None:
            v = max(self.clamp[0], min(self.clamp[1], v))
        return v


# ── 参数组（一次 SetParameterValue 调用序列） ──


@dataclass(frozen=True)
class ParamGroup:
    """一组关联的物理参数通道，作为一次写入单元。

    组名语义：
        eyes_open     — 眼睛开合（surprised>1 截断到 1.0）
        eyes_smile    — 眯眼
        eyebrows      — 眉毛角度与形态
        mouth_form    — 嘴型
        mouth_open    — 嘴张开（说话时让位，renderer 控制是否跳过）
        gaze          — 眼神方向
        head_angle    — 头部转向（仅视线跟随关闭时生效，renderer 控制）
        breath        — 呼吸节律（派生自 breath_amp + breath_rate）
        hair          — 头发微动（派生）
        blush         — 脸红（走 _apply_blush 路径，非裸 SetParameterValue）
    """

    group: str
    channels: tuple[ParamChannel, ...]
    priority: int = 0  # 供 T08 仲裁；默认 0

    @property
    def has_blush(self) -> bool:
        return self.group == "blush"


# ── 模型 profile ──


@dataclass
class ModelProfile:
    """一个 Live2D 模型的参数映射 profile。

    包含所有可写的参数组，以及供 T08 special_params 预留的自定义通道。
    """

    model_id: str
    groups: dict[str, ParamGroup]
    special_params: dict[str, str] = field(default_factory=dict)
    emotion_facial_targets: Optional[dict[str, dict[str, float]]] = None


# ── 默认 profile（字节等价于 live2d_renderer.py:1375-1448 的硬编码逻辑） ──

# 写参顺序严格对应 live2d_renderer.py:1375-1448：
#   eyes_open → eyes_smile → eyebrows → mouth_form → mouth_open →
#   gaze → head_angle → breath → hair → blush

_DEFAULT_GROUPS: dict[str, ParamGroup] = {
    "eyes_open": ParamGroup(
        "eyes_open",
        (
            ParamChannel("ParamEyeLOpen", 0.5, clamp=(0.0, 1.0)),
            ParamChannel("ParamEyeROpen", 0.5, clamp=(0.0, 1.0)),
        ),
    ),
    "eyes_smile": ParamGroup(
        "eyes_smile",
        (
            ParamChannel("ParamEyeLSmile", 0.6),
            ParamChannel("ParamEyeRSmile", 0.6),
        ),
    ),
    "eyebrows": ParamGroup(
        "eyebrows",
        (
            ParamChannel("ParamBrowLAngle", 0.6),
            ParamChannel("ParamBrowRAngle", 0.6),
            ParamChannel("ParamBrowLForm", 0.6),
            ParamChannel("ParamBrowRForm", 0.6),
        ),
    ),
    "mouth_form": ParamGroup(
        "mouth_form",
        (ParamChannel("ParamMouthForm", 0.6),),
    ),
    "mouth_open": ParamGroup(
        "mouth_open",
        (ParamChannel("ParamMouthOpenY", 0.5, clamp=(0.0, 1.0)),),
    ),
    "gaze": ParamGroup(
        "gaze",
        (
            ParamChannel("ParamEyeBallX", 0.3),
            ParamChannel("ParamEyeBallY", 0.3),
        ),
    ),
    "head_angle": ParamGroup(
        "head_angle",
        (
            ParamChannel("ParamAngleX", 0.35, scale=15.0),
            ParamChannel("ParamAngleY", 0.35, scale=12.0),
        ),
    ),
    "breath": ParamGroup(
        "breath",
        (ParamChannel("ParamBreath", 1.0),),
    ),
    "hair": ParamGroup(
        "hair",
        (
            ParamChannel("ParamHairFront", 0.5, scale=0.6),
            ParamChannel("ParamHairSide", 0.5, scale=0.4),
        ),
    ),
    "blush": ParamGroup(
        "blush",
        (),  # 走 _apply_blush 路径，非裸 SetParameterValue
    ),
}


# 写参组顺序（字节等价于原代码行序）
DEFAULT_GROUP_ORDER: tuple[str, ...] = (
    "eyes_open",
    "eyes_smile",
    "eyebrows",
    "mouth_form",
    "mouth_open",
    "gaze",
    "head_angle",
    "breath",
    "hair",
    "blush",
)


DEFAULT_PROFILE: ModelProfile = ModelProfile(
    model_id="miku_default",
    groups=_DEFAULT_GROUPS,
    special_params={},
)


# ── T09 Phase 3: Profile 配置化加载器 ──

logger = logging.getLogger(__name__)


def load_profile_from_json(json_path: str) -> Optional[ModelProfile]:
    """从 JSON 文件加载模型 profile。

    格式参考 Soullink Emotion SDK 的 standardParamTable 设计：
    - groups.<name>.channels: [{id, weight, min?, max?, scale?}]
    - special_params: {semantic_name: live2d_param_id}
    - emotion_facial_targets: 可选，覆盖内置表

    文件不存在或解析失败时返回 None（调用方回退 DEFAULT_PROFILE）。
    """
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Profile JSON load failed %s: %s", json_path, e)
        return None

    groups: dict[str, ParamGroup] = {}
    for gname, gdata in data.get("groups", {}).items():
        channels = []
        for ch in gdata.get("channels", []):
            clamp = None
            if "min" in ch and "max" in ch:
                clamp = (float(ch["min"]), float(ch["max"]))
            channels.append(ParamChannel(
                std_name=ch["id"],
                weight=float(ch.get("weight", 1.0)),
                clamp=clamp,
                scale=float(ch.get("scale", 1.0)),
            ))
        groups[gname] = ParamGroup(
            group=gname,
            channels=tuple(channels),
            priority=int(gdata.get("priority", 0)),
        )

    special_params = dict(data.get("special_params", {}))
    eft = data.get("emotion_facial_targets", None)
    if eft:
        eft = {k: {k2: float(v2) for k2, v2 in v.items()} for k, v in eft.items()}

    profile = ModelProfile(
        model_id=data.get("model_id", "custom"),
        groups=groups,
        special_params=special_params,
        emotion_facial_targets=eft,
    )
    logger.info("Loaded model profile from %s (model_id=%s, %d groups)",
                json_path, profile.model_id, len(profile.groups))
    return profile


def load_profile_for_character(character_id: str, base_dir: str = None) -> ModelProfile:
    """加载角色对应的模型 profile。

    查找顺序：
    1. characters/<id>/live2d/profile.json
    2. 回退 DEFAULT_PROFILE
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_path = os.path.join(base_dir, "characters", character_id, "live2d", "profile.json")
    profile = load_profile_from_json(profile_path)
    if profile is None:
        logger.debug("No profile.json for %s, using DEFAULT_PROFILE", character_id)
        return DEFAULT_PROFILE
    return profile