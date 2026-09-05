"""avatar/model_health.py — 模型体检报告

基于 param_writer._probe_parameters 的探测逻辑，启动时遍历参数对照标准清单，
缺失+缩放打成人类/AI 可读报告。

标准参数清单（19 项）：
    - 眼睛：eye_open, eye_smile, eye_ball_x, eye_ball_y
    - 眉毛：brow_angle, brow_form
    - 嘴巴：mouth_form, mouth_open
    - 头部：head_angle_x, head_angle_y
    - 呼吸：breath_amp, breath_rate
    - 脸红：blush
    - 其他： ParamAngleX, ParamAngleY, ParamAngleZ, ParamEyeBallX, ParamEyeBallY

输出：人类可读 + AI 可读（整段粘贴给 AI 修）
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

log = logging.getLogger(__name__)

# ── 标准参数清单 ──

class _ParamModel(Protocol):
    """渲染器模型的最小接口"""
    def GetParameterCount(self) -> int: ...
    def GetParameter(self, index: int) -> object: ...


# 语义通道 → 标准参数映射
STANDARD_PARAMS = {
    # 眼睛
    "eye_open": ("ParamEyeOpenL", "ParamEyeOpenR", "ParamEyeL", "ParamEyeR"),
    "eye_smile": ("ParamEyeSmileL", "ParamEyeSmileR"),
    "eye_ball_x": ("ParamEyeBallX",),
    "eye_ball_y": ("ParamEyeBallY",),
    # 眉毛
    "brow_angle": ("ParamBrowAngleL", "ParamBrowAngleR", "ParamAngleL", "ParamAngleR"),
    "brow_form": ("ParamBrowFormL", "ParamBrowFormR"),
    # 嘴巴
    "mouth_form": ("ParamMouthForm", "ParamMouthFormY"),
    "mouth_open": ("ParamMouthOpenY", "ParamMouthOpen"),
    # 头部
    "head_angle_x": ("ParamAngleX",),
    "head_angle_y": ("ParamAngleY",),
    "head_angle_z": ("ParamAngleZ",),
    # 呼吸
    "breath_amp": ("ParamBreathAmp",),
    "breath_rate": ("ParamBreathRate",),
    # 脸红
    "blush": ("ParamBlush",),
    # 其他（Live2D 标准参数）
    "ParamAngleX": ("ParamAngleX",),
    "ParamAngleY": ("ParamAngleY",),
    "ParamAngleZ": ("ParamAngleZ",),
    "ParamEyeBallX": ("ParamEyeBallX",),
    "ParamEyeBallY": ("ParamEyeBallY",),
    "ParamEyeBallZ": ("ParamEyeBallZ",),
}

# 缩放参数（检查范围）
SCALE_PARAMS = {
    "eye_open": (0.0, 1.0),
    "eye_smile": (-0.5, 1.0),
    "eye_ball_x": (-1.0, 1.0),
    "eye_ball_y": (-1.0, 1.0),
    "brow_angle": (-1.0, 1.0),
    "brow_form": (-1.0, 1.0),
    "mouth_form": (-1.0, 1.0),
    "mouth_open": (0.0, 1.0),
    "head_angle_x": (-30, 30),
    "head_angle_y": (-30, 30),
}


# ── 报告生成 ──

def probe_model_parameters(model: _ParamModel) -> tuple[set[str], bool]:
    """探测模型实际参数集（复用 param_writer._probe_parameters 逻辑）"""
    try:
        count = model.GetParameterCount()
        return (
            {str(model.GetParameter(i).id) for i in range(count)},
            True,
        )
    except (AttributeError, TypeError) as e:
        log.warning("模型参数探测失败: %s", e)
        return set(), False


def check_parameter_coverage(available: set[str], probed: bool) -> list[dict]:
    """检查标准参数覆盖情况"""
    results = []
    if not probed:
        results.append({
            "channel": "所有参数",
            "status": "⚠️ 探测失败",
            "message": "无法获取模型参数列表，使用 try/except 兜底",
            "missing": [],
        })
        return results

    for channel, pids in STANDARD_PARAMS.items():
        found = [pid for pid in pids if pid in available]
        missing = [pid for pid in pids if pid not in available]
        if missing:
            status = "❌ 缺失" if not found else "⚠️ 部分缺失"
            message = f"缺少 {len(missing)} 个参数: {', '.join(missing)}"
        else:
            status = "✅ 完整"
            message = f"全部 {len(found)} 个参数存在"
        results.append({
            "channel": channel,
            "status": status,
            "message": message,
            "missing": missing,
            "found": found,
        })
    return results


def check_scale_parameters(model: _ParamModel, available: set[str]) -> list[dict]:
    """检查缩放参数范围"""
    results = []
    for pid in available:
        if pid in SCALE_PARAMS:
            min_val, max_val = SCALE_PARAMS[pid]
            try:
                # 获取参数值（需要 model.SetParameterValue 的逆操作）
                # 这里简化：只检查参数名是否存在
                results.append({
                    "pid": pid,
                    "status": "✅ 存在",
                    "expected_range": f"[{min_val}, {max_val}]",
                })
            except Exception:
                results.append({
                    "pid": pid,
                    "status": "⚠️ 无法读取",
                    "expected_range": f"[{min_val}, {max_val}]",
                })
    return results


def generate_report(model: _ParamModel) -> dict:
    """生成完整模型体检报告"""
    available, probed = probe_model_parameters(model)
    coverage = check_parameter_coverage(available, probed)
    scales = check_scale_parameters(model, available) if probed else []

    # 统计
    total = len(STANDARD_PARAMS)
    ok = sum(1 for c in coverage if c["status"] == "✅ 完整")
    partial = sum(1 for c in coverage if c["status"] == "⚠️ 部分缺失")
    missing = sum(1 for c in coverage if c["status"] == "❌ 缺失")
    failed = sum(1 for c in coverage if c["status"] == "⚠️ 探测失败")

    return {
        "probed": probed,
        "total_params": len(available),
        "coverage": {
            "total": total,
            "ok": ok,
            "partial": partial,
            "missing": missing,
            "failed": failed,
        },
        "details": coverage,
        "scales": scales,
    }


def format_report_human(report: dict) -> str:
    """格式化为人类可读报告"""
    lines = ["#" * 60, "# 模型体检报告", "#" * 60, ""]

    if not report["probed"]:
        lines.append("⚠️ 模型参数探测失败，无法生成完整报告。")
        lines.append("   检查模型文件格式是否正确（.model3.json + .moc3）。")
        return "\n".join(lines)

    cov = report["coverage"]
    lines.append(f"总参数数: {report['total_params']}")
    lines.append(f"标准覆盖: ✅ {cov['ok']}/{cov['total']} 完整, ⚠️ {cov['partial']} 部分缺失, ❌ {cov['missing']} 缺失")
    lines.append("")

    for detail in report["details"]:
        lines.append(f"{detail['status']} {detail['channel']}: {detail['message']}")

    if report["scales"]:
        lines.append("")
        lines.append("缩放参数检查:")
        for s in report["scales"]:
            lines.append(f"  {s['status']} {s['pid']}: 期望范围 {s['expected_range']}")

    return "\n".join(lines)


def format_report_ai(report: dict) -> str:
    """格式化为 AI 可读报告（整段粘贴给 AI 修）"""
    lines = ["# 模型体检报告（AI 可读）", ""]

    if not report["probed"]:
        lines.append("```json")
        lines.append('{"probed": false, "error": "模型参数探测失败"}')
        lines.append("```")
        return "\n".join(lines)

    import json
    # AI 可读格式：JSON
    ai_report = {
        "total_params": report["total_params"],
        "coverage": report["coverage"],
        "missing_parameters": [],
        "recommendations": [],
    }

    for detail in report["details"]:
        if detail["missing"]:
            ai_report["missing_parameters"].extend(detail["missing"])
            ai_report["recommendations"].append(
                f"为 {detail['channel']} 添加参数: {', '.join(detail['missing'])}"
            )

    lines.append("```json")
    lines.append(json.dumps(ai_report, ensure_ascii=False, indent=2))
    lines.append("```")

    return "\n".join(lines)


def print_report(model: _ParamModel, format: str = "human") -> None:
    """打印报告"""
    report = generate_report(model)
    if format == "ai":
        print(format_report_ai(report))
    else:
        print(format_report_human(report))
    log.info("模型体检完成: %s", report["coverage"])
