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
        # 关键：必须显式设置位置并显示，否则 widget 存在但不显示（画了看不见）。
        # 对齐 SpriteRenderer 的 move()+lower() 行为。
        self.char_label.move(10, 0)
        self.char_label.lower()
        self.char_label.show()
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

            # GL 可用性检查：live2d 需要真实 OpenGL 上下文（glad 加载 GL 函数）。
            # 若 GL 不可用（headless / 驱动问题），glInit 会打印 "Can't initilize glad"
            # 但 C 层不抛异常，随后 Model 加载纹理时段错误崩退。
            # 这里先行探测 GL 扩展，探测失败则跳过 live2d 并回退 sprite（角色透明不崩）。
            try:
                from OpenGL import GL as _gl
                version = _gl.glGetString(_gl.GL_VERSION)
                if not version:
                    raise RuntimeError("GL version empty")
                logger.info("Live2DRenderer: OpenGL %s 可用", version)
            except Exception as e:
                logger.warning("Live2DRenderer: OpenGL 不可用，回退 sprite 渲染: %s", e)
                self._ready = False
                self._model = None
                return

            l2d.glInit()

            # 用 LAppModel（高层封装，内部管理 dt/投影/自动眨眼/呼吸）。
            # 最小测试验证 LAppModel 能正确绘制（底层 Model 画不出来）。
            model = l2d.LAppModel()
            model.LoadModelJson(self._model_path)

            self._model = model
            # LAppModel.LoadModelJson 内部已自动 CreateRenderer
            model.SetAutoBlinkEnable(True)
            model.SetAutoBreathEnable(True)

            # 关键：live2d 文档要求初次加载必须 Resize(宽,高)，否则模型不显示。
            # 这里用默认视口尺寸（真实尺寸由 on_resize 触发后重算）
            try:
                model.Resize(int(getattr(self, "_gl_w", 220) or 220),
                             int(getattr(self, "_gl_h", 260) or 260))
            except Exception as e:
                logger.warning("Live2DRenderer: Resize 失败: %s", e)

            # 显式设置初始缩放（用像素画布尺寸，与最小测试一致）。
            # 不设置则模型保持默认 scale，可能过大/过小画不出来。
            try:
                cw_px, ch_px = model.GetCanvasSizePixel()
                if cw_px and ch_px:
                    w = int(getattr(self, "_gl_w", 220) or 220)
                    h = int(getattr(self, "_gl_h", 260) or 260)
                    scale = min(w / cw_px, h / ch_px) * 0.9 * self._fit_scale
                    model.SetScale(scale)
                    logger.info("Live2DRenderer: 初始缩放 scale=%.3f", scale)
            except Exception as e:
                logger.warning("Live2DRenderer: 初始缩放失败: %s", e)

            # 收集可用表情/动作组，用于情绪映射
            try:
                self._expression_names = list(model.GetExpressions() or [])
            except Exception:
                self._expression_names = []
            try:
                if hasattr(model, "GetMotionGroups"):
                    groups = model.GetMotionGroups()
                    self._motion_groups = {g: [] for g in (groups or [])}
                else:
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
        """根据视口与模型像素画布尺寸计算缩放/偏移（best-effort，可能需按模型微调）。"""
        if not self._model or not hasattr(self, "_gl_w"):
            return
        try:
            # 用像素画布尺寸（GetCanvasSizePixel），而非逻辑单位（GetCanvasSize）。
            # 逻辑单位（如 13.6）乘以 PixelsPerUnit(88.24) 才是真实像素。
            # 若用逻辑单位算 SetScale，会放大 ~88 倍导致角色超出视口不可见。
            cw_px, ch_px = self._model.GetCanvasSizePixel()
            if not cw_px or not ch_px:
                # 回退到逻辑单位 × PixelsPerUnit
                ppu = self._model.GetPixelsPerUnit() or 1.0
                cw_log, ch_log = self._model.GetCanvasSize()
                cw_px, ch_px = cw_log * ppu, ch_log * ppu
            if not cw_px or not ch_px:
                return
            fit = min(self._gl_w / cw_px, self._gl_h / ch_px) * 0.92 * self._fit_scale
            self._model.SetScale(fit)
            ox = (self._gl_w - cw_px * fit) / 2.0 / fit + self._offset_scale[0]
            oy = (self._gl_h - ch_px * fit) / 2.0 / fit + self._offset_scale[1]
            self._model.SetOffset(ox, oy)
        except Exception as e:
            logger.warning("Live2DRenderer: 缩放计算失败: %s", e)

    def draw(self) -> None:
        """由 GLCharWidget.paintGL 调用：每帧更新并绘制模型。"""
        if not self._live2d or not self._model:
            return
        # 诊断：确认 draw 是否被调用、模型是否就绪（仅首次打印）
        if not getattr(self, "_draw_diag_logged", False):
            self._draw_diag_logged = True
            try:
                cw = ch = 0
                try:
                    cw, ch = self._model.GetCanvasSize()
                except Exception:
                    pass
                cw_px = ch_px = 0
                try:
                    cw_px, ch_px = self._model.GetCanvasSizePixel()
                except Exception:
                    pass
                logger.info(
                    "Live2DRenderer: draw 首帧就绪 model=%s canvas=(%s,%s)px=(%s,%s) gl=%sx%s scale=%.3f",
                    self._ready, cw, ch, cw_px, ch_px,
                    getattr(self, "_gl_w", "?"), getattr(self, "_gl_h", "?"), self._fit_scale,
                )
                # 离屏截图诊断：保存 GL 内容，确认模型是否真的画出来
                try:
                    from PySide6.QtWidgets import QApplication
                    self.char_label.grabFramebuffer().save(
                        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "logs", "l2d_diag.png"))
                    logger.info("Live2DRenderer: 已保存离屏截图 logs/l2d_diag.png")
                except Exception as e:
                    logger.warning("Live2DRenderer: 离屏截图失败: %s", e)
            except Exception:
                pass
        try:
            l2d = self._live2d
            # 清除画布（live2d-py 0.7.0.4 的 clearBuffer 为无参调用）
            l2d.clearBuffer()
        except Exception as e:
            logger.warning("Live2DRenderer.clearBuffer 异常: %s", e)

        # 参数驱动每步独立 try：即使某个参数调用报错，也不阻塞模型绘制
        try:
            self._update_gaze_params()
        except Exception as e:
            logger.warning("Live2DRenderer.gaze 异常: %s", e)
        try:
            self._update_mouth()
        except Exception as e:
            logger.warning("Live2DRenderer.mouth 异常: %s", e)

        # 与验证可行的最小测试保持完全一致的绘制路径：
        #   clearBuffer -> Update -> Draw
        # 不手动设置 GL 混合（live2d shader 自行管理），不设投影。
        try:
            # LAppModel.Update 无参（内部自算 dt + 管理眨眼/呼吸）
            self._model.Update()
        except Exception as e:
            logger.warning("Live2DRenderer.Update 异常: %s", e)
        try:
            self._model.Draw()
        except Exception as e:
            logger.warning("Live2DRenderer.Draw 异常: %s", e)

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
            self._model.SetParameterValue(P.ParamAngleX, self._gaze_cur_angle_x, 1.0)
            self._model.SetParameterValue(P.ParamAngleY, self._gaze_cur_angle_y, 1.0)
            self._model.SetParameterValue(P.ParamEyeBallX, self._gaze_cur_ball_x, 1.0)
            self._model.SetParameterValue(P.ParamEyeBallY, self._gaze_cur_ball_y, 1.0)
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
            self._model.SetParameterValue(P.ParamMouthOpenY, val, 1.0)
        except Exception:
            pass

    def _start_idle(self) -> None:
        if not self._model:
            return
        try:
            # 从模型实际的 motion 组挑一个（此模型组名为空字符串 ''），
            # 避免硬编码 MotionGroup.IDLE 找不到组
            if self._motion_groups:
                group = next(iter(self._motion_groups))
                self._model.StartRandomMotion(group, self._live2d.MotionPriority.IDLE)
            else:
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
                # 用像素画布尺寸计算缩放（与 _recompute_fit 一致）
                cw_px, ch_px = self._model.GetCanvasSizePixel()
                if not cw_px or not ch_px:
                    ppu = self._model.GetPixelsPerUnit() or 1.0
                    cw_log, ch_log = self._model.GetCanvasSize()
                    cw_px, ch_px = cw_log * ppu, ch_log * ppu
                fit = min(self._gl_w / cw_px, self._gl_h / ch_px) * 0.92 * abs(self._fit_scale)
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
