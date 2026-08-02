"""Live2DRenderer - Live2D (Cubism) 角色渲染器

用 live2d-py (live2d.v3, Cubism Native) 在透明 QOpenGLWidget 上渲染 Live2D 模型，
实现 AvatarRenderer 接口，使后端业务（对话/行为/感知）无需关心底层是精灵还是 Live2D。

参数驱动（让角色"活起来"，对齐视频里 Amadeus 的效果）：
    - 情绪(emotion)   -> Live2D Expression（按模型可用表情名模糊匹配）
    - TTS 说话         -> ParamMouthOpenY 口型（说话时振荡开合）
    - 视线(look_at)    -> ParamAngleX/Y + ParamEyeBallX/Y（瞳孔/头部朝向鼠标）
    - 待机            -> SetAutoBlink + SetAutoBreath（免费眨眼/呼吸）
    - 朝向            -> SetScaleX 正负镜像

注意：
    - 真实 GL 绘制必须在有 GPU/显示的机器上验证（headless/沙箱里 live2d.v3.init()
      会段错误，属环境限制，非代码缺陷）。
    - 所有 GL/live2d 调用都做了 try/except 降级：缺模型或 GL 不可用时角色区域透明，
      其余功能（气泡/抽卡/HUD）不受影响。
    - 模型缩放/偏移在不同 Cubism 模型上可能需要微调；可通过角色 pet.json 的
      live2d.scale / live2d.offset 覆盖。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional

from PySide6.QtCore import Qt, QPoint

from avatar.base import AvatarRenderer
from avatar.gl_char_widget import GLCharWidget

logger = logging.getLogger(__name__)


class Live2DRenderer(AvatarRenderer):
    """Live2D (Cubism) 渲染器。"""

    # 情绪 -> 表情名关键词（模型有语义表情时匹配；否则用 motion 或忽略）
    _EMOTION_KEYWORDS = {
        "happy": ("happy", "joy", "smile", "fun", "usui"),
        "angry": ("angry", "ikari", "mad"),
        "sad": ("sad", "kanashii", "cry"),
        "surprised": ("surprise", "odoroki", "shock"),
        "thinking": ("think", "thinking", "doubt", "kangaeru"),
        "neutral": (),
    }
    # 情绪 -> motion 组名（模型有对应动作组时播放）
    _EMOTION_MOTION = {
        "happy": ("happy", "joy", "fun"),
        "angry": ("angry", "mad"),
        "sad": ("sad", "cry"),
        "surprised": ("surprise", "shock"),
        "thinking": ("think", "doubt"),
    }

    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self._scale: float = 1.0
        self._facing_right: bool = True
        self._model_path: Optional[str] = None
        self._model = None          # live2d.v3.Model 实例（GL 就绪后加载）
        self._ready: bool = False   # 模型是否成功加载并可在 draw 中渲染
        self._fit_scale: float = 1.0
        self._offset_scale: tuple[float, float] = (0.0, 0.0)

        # 视线/朝向目标（draw 中平滑插值）
        self._gaze_target_angle_x: float = 0.0
        self._gaze_target_angle_y: float = 0.0
        self._gaze_target_ball_x: float = 0.0
        self._gaze_target_ball_y: float = 0.0
        self._gaze_cur_angle_x: float = 0.0
        self._gaze_cur_angle_y: float = 0.0
        self._gaze_cur_ball_x: float = 0.0
        self._gaze_cur_ball_y: float = 0.0
        self._gaze_enabled: bool = True

        # 说话（TTS 口型）
        self._speaking: bool = False
        self._mouth_phase: float = 0.0

        # 当前情绪
        self._emotion_target: str = "neutral"

        # 兼容属性（避免 pet.py 直接访问崩溃）
        self._base_label_pos: QPoint = QPoint(10, 0)
        self._gaze_offset_x: float = 0.0
        self._gaze_offset_y: float = 0.0
        self._frames: dict = {}
        self._frame_tops: dict = {}
        self._anim_timer = None
        self._anim_seq: str = "idle"
        self._anim_idx: int = 0
        self._anim_range: tuple = (None, None)
        self._opacity_effect = None
        self._opacity: float = 1.0

        # GL 承载控件（真正的渲染表面）
        self.char_label = GLCharWidget(parent)
        self.char_label.setFixedSize(220, 260)
        self.char_label.move(10, 0)
        self.char_label.lower()
        self.char_label.set_renderer(self)
        self.char_label.installEventFilter(parent)

        # live2d 模块（延迟导入，避免无谓 banner；init 在 GL 就绪后调用）
        self._live2d = None
        self._motion_groups: dict = {}
        self._expression_names: list = []

    # ── 生命周期 ──

    def load(self, character_id: str, sprite_dir: str = None) -> bool:
        """记录模型路径（真实加载推迟到 GL 上下文就绪）。"""
        self._character_id = character_id
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        char_dir = os.path.join(base, "characters", character_id)
        live2d_dir = os.path.join(char_dir, "live2d")
        if not os.path.isdir(live2d_dir):
            logger.warning("Live2DRenderer: 未找到 live2d/ 目录: %s", char_dir)
            return False
        for f in sorted(os.listdir(live2d_dir)):
            low = f.lower()
            if low.endswith(".model3.json") or low.endswith(".model.json"):
                self._model_path = os.path.join(live2d_dir, f)
                # 可选：pet.json 里的 live2d 缩放/偏移覆盖
                self._apply_live2d_meta(char_dir)
                logger.info("Live2DRenderer: 模型路径已记录 %s", self._model_path)
                return True
        logger.warning("Live2DRenderer: live2d/ 下无 .model3.json/.model.json: %s", live2d_dir)
        return False

    def _apply_live2d_meta(self, char_dir: str) -> None:
        import json
        meta_path = os.path.join(char_dir, "pet.json")
        if not os.path.exists(meta_path):
            return
        try:
            meta = json.loads(open(meta_path, encoding="utf-8").read())
            l2d = meta.get("live2d", {})
            if "scale" in l2d:
                self._fit_scale = float(l2d["scale"])
            if "offset" in l2d and isinstance(l2d["offset"], (list, tuple)):
                self._offset_scale = (float(l2d["offset"][0]), float(l2d["offset"][1]))
        except Exception as e:
            logger.warning("读取 live2d meta 失败: %s", e)

    def on_gl_initialized(self) -> None:
        """GL 上下文就绪：初始化 live2d 后端并加载模型。"""
        if self._ready or not self._model_path:
            return
        try:
            import live2d.v3 as l2d
            self._live2d = l2d
            if not getattr(self, "_global_inited", False):
                l2d.init()
                self._global_inited = True
            l2d.glInit()

            try:
                model = l2d.Model(self._model_path)
            except Exception:
                model = l2d.Model()
                model.LoadModelJson(self._model_path)

            self._model = model
            model.SetAutoBlink(True)
            model.SetAutoBreath(True)

            # 收集可用表情/动作组，用于情绪映射
            try:
                self._expression_names = list(model.GetExpressions() or [])
            except Exception:
                self._expression_names = []
            try:
                self._motion_groups = dict(model.GetMotions() or {})
            except Exception:
                self._motion_groups = {}

            # 起始待机动作
            self._start_idle()

            self._ready = True
            logger.info(
                "Live2DRenderer: 模型加载成功 (expressions=%d, motion_groups=%s)",
                len(self._expression_names), list(self._motion_groups.keys()),
            )
        except Exception as e:
            logger.error("Live2DRenderer: 模型加载失败（角色区域将透明）: %s", e)
            self._ready = False
            self._model = None

    def on_resize(self, w: int, h: int) -> None:
        self._gl_w = w
        self._gl_h = h
        self._recompute_fit()

    def _recompute_fit(self) -> None:
        """根据视口与模型画布尺寸计算缩放/偏移（best-effort，可能需按模型微调）。"""
        if not self._model or not hasattr(self, "_gl_w"):
            return
        try:
            cw, ch = self._model.GetCanvasSize()
            if not cw or not ch:
                return
            fit = min(self._gl_w / cw, self._gl_h / ch) * 0.92 * self._fit_scale
            self._model.SetScale(fit)
            ox = (self._gl_w - cw * fit) / 2.0 / fit + self._offset_scale[0]
            oy = (self._gl_h - ch * fit) / 2.0 / fit + self._offset_scale[1]
            self._model.SetOffset(ox, oy)
        except Exception as e:
            logger.warning("Live2DRenderer: 缩放计算失败: %s", e)

    def draw(self) -> None:
        """由 GLCharWidget.paintGL 调用：每帧更新并绘制模型。"""
        if not self._live2d or not self._model:
            return
        try:
            l2d = self._live2d
            l2d.clearBuffer(0.0, 0.0, 0.0, 0.0)  # 透明背景

            self._update_gaze_params()
            self._update_mouth()

            self._model.Update()
            self._model.Draw()
        except Exception as e:
            logger.warning("Live2DRenderer.draw 异常: %s", e)

    # ── 内部：参数驱动 ──

    def _update_gaze_params(self) -> None:
        if not self._model:
            return
        P = self._live2d.StandardParams
        # 平滑插值
        s = 0.18
        self._gaze_cur_angle_x += (self._gaze_target_angle_x - self._gaze_cur_angle_x) * s
        self._gaze_cur_angle_y += (self._gaze_target_angle_y - self._gaze_cur_angle_y) * s
        self._gaze_cur_ball_x += (self._gaze_target_ball_x - self._gaze_cur_ball_x) * s
        self._gaze_cur_ball_y += (self._gaze_target_ball_y - self._gaze_cur_ball_y) * s
        try:
            self._model.SetParameterValue(P.ParamAngleX, self._gaze_cur_angle_x)
            self._model.SetParameterValue(P.ParamAngleY, self._gaze_cur_angle_y)
            self._model.SetParameterValue(P.ParamEyeBallX, self._gaze_cur_ball_x)
            self._model.SetParameterValue(P.ParamEyeBallY, self._gaze_cur_ball_y)
        except Exception:
            pass

    def _update_mouth(self) -> None:
        if not self._model:
            return
        P = self._live2d.StandardParams
        if self._speaking:
            self._mouth_phase += 0.35
            val = 0.5 + 0.5 * math.sin(self._mouth_phase * 3.0)
            val = 0.15 + val * 0.6
        else:
            val = 0.0
        try:
            self._model.SetParameterValue(P.ParamMouthOpenY, val)
        except Exception:
            pass

    def _start_idle(self) -> None:
        if not self._model:
            return
        try:
            self._model.StartRandomMotion(self._live2d.MotionGroup.IDLE,
                                          self._live2d.MotionPriority.IDLE)
        except Exception as e:
            logger.warning("Live2DRenderer: 起始待机动作失败: %s", e)

    def _match_expression(self, emotion: str):
        """返回情绪对应的 Live2D 表情名（无匹配返回 None）。"""
        if emotion in ("neutral", ""):
            return None
        kws = self._EMOTION_KEYWORDS.get(emotion, ())
        for name in self._expression_names:
            low = str(name).lower()
            if any(k in low for k in kws):
                return name
        return None

    def _match_motion(self, emotion: str):
        """返回情绪对应的 motion 组名（无匹配返回 None）。"""
        groups = list(self._motion_groups.keys())
        kws = self._EMOTION_MOTION.get(emotion, ())
        for g in groups:
            low = str(g).lower()
            if any(k in low for k in kws):
                return g
        return None

    # ── 动画控制 ──

    def play_anim(self, anim: str, emotion: str = "", frame_range=None) -> None:
        self._current_anim = anim
        if emotion:
            self.set_emotion(emotion)
        # 尝试用动作名播放 Live2D motion 组
        if self._model and anim in self._motion_groups:
            try:
                self._model.StartRandomMotion(anim, self._live2d.MotionPriority.NORMAL)
            except Exception:
                pass

    def set_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        self._current_emotion = emotion
        self._emotion_target = emotion
        if not self._model:
            return
        # 优先：情绪对应的 motion 组
        motion = self._match_motion(emotion)
        if motion:
            try:
                self._model.StartRandomMotion(motion, self._live2d.MotionPriority.FORCE)
            except Exception:
                pass
        # 其次：表情
        expr = self._match_expression(emotion)
        try:
            if expr is None:
                self._model.ResetExpressions()
            else:
                self._model.SetExpression(expr)
        except Exception as e:
            logger.warning("Live2DRenderer: 设置表情失败: %s", e)

    # ── 视线 ──

    def look_at(self, x: int, y: int) -> None:
        if not self._gaze_enabled or not self._model:
            return
        # 鼠标全局坐标 -> 控件局部坐标
        pos = self.char_label.mapFromGlobal(QPoint(x, y))
        cx = self.char_label.width() / 2.0
        cy = self.char_label.height() / 2.0
        dx = pos.x() - cx
        dy = pos.y() - cy
        dist = math.hypot(dx, dy) or 1.0
        norm_x = dx / dist
        norm_y = dy / dist
        strength = min(1.0, dist / 300.0)
        self._gaze_target_angle_x = norm_x * 25.0 * strength
        self._gaze_target_angle_y = -norm_y * 20.0 * strength
        self._gaze_target_ball_x = norm_x * 1.0 * strength
        self._gaze_target_ball_y = -norm_y * 1.0 * strength

    def set_gaze_enabled(self, enabled: bool) -> None:
        self._gaze_enabled = enabled
        if not enabled:
            self._gaze_target_angle_x = self._gaze_target_angle_y = 0.0
            self._gaze_target_ball_x = self._gaze_target_ball_y = 0.0

    def update_gaze(self) -> None:
        """每帧调用（pet 的平滑定时器）；实际插值在 draw 中完成。"""
        pass

    def reset_gaze(self) -> None:
        self._gaze_target_angle_x = self._gaze_target_angle_y = 0.0
        self._gaze_target_ball_x = self._gaze_target_ball_y = 0.0

    def get_char_top_y(self) -> int:
        return self.char_label.y()

    # ── 说话（TTS 口型） ──

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)

    # ── 变换 ──

    def set_position(self, x: int, y: int) -> None:
        pass

    def get_size(self) -> tuple[int, int]:
        return (self.char_label.width(), self.char_label.height())

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        w = int(220 * scale)
        h = int(260 * scale)
        self.char_label.setFixedSize(w, h)
        self._base_label_pos = QPoint(10, 0)
        self._recompute_fit()

    def get_scale(self) -> float:
        return self._scale

    def recalc_geometry(self, window_w: int, window_h: int) -> None:
        w = int(220 * self._scale)
        h = int(260 * self._scale)
        self.char_label.setFixedSize(w, h)
        self._base_label_pos = QPoint(10, 0)
        self._recompute_fit()

    def set_facing(self, right: bool) -> None:
        self._facing_right = right
        if self._model:
            try:
                sx = self._fit_scale if right else -self._fit_scale
                # 翻转仅影响水平方向：重算缩放（保留 fit）
                cw, ch = self._model.GetCanvasSize()
                fit = min(self._gl_w / cw, self._gl_h / ch) * 0.92 * abs(sx)
                self._model.SetScaleX(fit * (1 if right else -1))
                self._model.SetScaleY(fit)
            except Exception:
                pass

    def get_facing(self) -> bool:
        return self._facing_right

    def set_label_base_pos(self, pos: QPoint) -> None:
        self._base_label_pos = pos

    # ── 透明度 ──

    def set_alpha(self, alpha: float) -> None:
        alpha = max(0.0, min(1.0, alpha))
        self._opacity = alpha
        try:
            self.char_label.setWindowOpacity(alpha)
        except Exception:
            pass

    def get_alpha(self) -> float:
        return self._opacity

    # ── 兼容接口 ──

    @property
    def label(self):
        return self.char_label

    @property
    def eye_overlay(self):
        return None

    def show_eyes(self):
        pass

    def hide_eyes(self):
        self.reset_gaze()

    def cleanup(self) -> None:
        try:
            if self._model is not None:
                self._model = None
            if self._live2d is not None:
                self._live2d.glRelease()
        except Exception:
            pass
