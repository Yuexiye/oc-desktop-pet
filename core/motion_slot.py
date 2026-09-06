"""动作槽位自动映射 — 扫描模型 motion 文件，正则分配到语义槽位

竞品参考（Mio）：`model-discovery.js` 正则把 motion 名分配到 10 个语义槽位，
未匹配进 `unassignedMotions`。先自动适配，再报告剩余。

功能：
- 扫描模型目录（live2d 模型的 motions/ 文件夹）
- 正则匹配语义槽位（idle, wave, dance, touch, happy, sad, angry, thinking, surprised, custom）
- 未匹配进 `unassignedMotions`
- 生成体检报告（JSON）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  语义槽位定义
# ════════════════════════════════════════════════════════════

# 槽位定义：槽位名 → 匹配正则（不区分大小写）
# 顺序很重要：先匹配特异性强的，再匹配通用的
SLOT_PATTERNS: dict[str, str] = {
    "idle": r"(idle|待机|stand|standing|rest|relax)",
    "wave": r"(wave|waving|hello|greet|hand|招手|打招呼|挥手)",
    "dance": r"(dance|dancing|music|beat|跳舞|舞蹈)",
    "touch": r"(touch|pat|pet|head|touching|摸头|抚摸|触摸)",
    "happy": r"(happy|joy|laugh|smile|giggle|开心|高兴|笑)",
    "sad": r"(sad|cry|tear|crying|sadness|难过|悲伤|哭)",
    "angry": r"(angry|mad|fury|rage|生气|愤怒|怒)",
    "thinking": r"(think|thinking|ponder|contemplate|思考|想)",
    "surprised": r"(surprise|shock|wow|astonish|惊讶|吃惊)",
    "custom": r"(custom|special|extra|special|特殊|自定义)",
}

@dataclass
class MotionInfo:
    """动作信息"""
    file_path: str          # 文件路径
    file_name: str          # 文件名（不含扩展名）
    slot: str = "unassigned"  # 槽位名
    matched_pattern: str = ""  # 匹配的正则


@dataclass
class SlotMappingResult:
    """槽位映射结果"""
    total_motions: int = 0
    assigned: int = 0
    unassigned: int = 0
    slots: dict[str, list[str]] = field(default_factory=dict)
    unassigned_motions: list[str] = field(default_factory=list)
    model_dir: str = ""


# ════════════════════════════════════════════════════════════
#  核心函数
# ════════════════════════════════════════════════════════════

def scan_motion_files(model_dir: str | Path) -> list[str]:
    """扫描模型目录，获取所有 motion 文件路径。
    
    支持两种结构：
    1. live2d 模型：model_dir/motions/*.motion3.json
    2. 直接目录：model_dir/*.motion3.json
    """
    model_dir = Path(model_dir)
    motion_files = []
    
    # 尝试 live2d 结构
    motions_dir = model_dir / "motions"
    if motions_dir.exists() and motions_dir.is_dir():
        motion_files = list(motions_dir.glob("*.motion3.json"))
    else:
        # 直接扫描模型目录
        motion_files = list(model_dir.glob("*.motion3.json"))
    
    # 去重（按文件名）
    seen = set()
    unique_files = []
    for f in motion_files:
        if f.name not in seen:
            seen.add(f.name)
            unique_files.append(str(f))
    
    return sorted(unique_files)


def map_motion_to_slot(file_name: str) -> tuple[str, str]:
    """把 motion 文件名映射到语义槽位。
    
    Args:
        file_name: motion 文件名（不含扩展名）
    
    Returns:
        (slot_name, matched_pattern) 或 ("unassigned", "")
    """
    file_lower = file_name.lower()
    
    for slot, pattern in SLOT_PATTERNS.items():
        if re.search(pattern, file_lower, re.IGNORECASE):
            return slot, pattern
    
    return "unassigned", ""


def map_motions(model_dir: str | Path) -> SlotMappingResult:
    """扫描模型目录，映射所有 motion 到语义槽位。
    
    Args:
        model_dir: 模型目录路径
    
    Returns:
        SlotMappingResult
    """
    result = SlotMappingResult()
    result.model_dir = str(model_dir)
    
    # 扫描 motion 文件
    motion_files = scan_motion_files(model_dir)
    result.total_motions = len(motion_files)
    
    # 映射每个 motion
    for file_path in motion_files:
        file_name = Path(file_path).stem  # 不含扩展名
        slot, pattern = map_motion_to_slot(file_name)
        
        if slot == "unassigned":
            result.unassigned += 1
            result.unassigned_motions.append(file_name)
        else:
            result.assigned += 1
            if slot not in result.slots:
                result.slots[slot] = []
            result.slots[slot].append(file_name)
    
    # 生成报告
    logger.info(
        "Slot mapping: %d/%d assigned, %d unassigned",
        result.assigned, result.total_motions, result.unassigned
    )
    
    return result


def generate_health_report(result: SlotMappingResult) -> dict:
    """生成动作槽位体检报告。
    
    Returns:
        报告字典
    """
    report = {
        "model_dir": result.model_dir,
        "summary": {
            "total": result.total_motions,
            "assigned": result.assigned,
            "unassigned": result.unassigned,
            "coverage": f"{result.assigned}/{result.total_motions}" if result.total_motions > 0 else "0/0",
        },
        "slots": {},
        "unassigned": result.unassigned_motions,
    }
    
    # 添加每个槽位的信息
    for slot, motions in result.slots.items():
        report["slots"][slot] = {
            "count": len(motions),
            "files": motions,
        }
    
    # 添加建议
    suggestions = []
    if result.unassigned > 0:
        suggestions.append(f"{result.unassigned} 个 motion 未分配，可手动配置或添加正则规则")
    if result.total_motions == 0:
        suggestions.append("未找到 motion 文件，请检查模型目录结构")
    report["suggestions"] = suggestions
    
    return report


def save_report(report: dict, output_path: str | Path) -> None:
    """保存体检报告到 JSON 文件。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("Health report saved: %s", output_path)


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "SlotMappingResult",
    "MotionInfo",
    "scan_motion_files",
    "map_motion_to_slot",
    "map_motions",
    "generate_health_report",
    "save_report",
]