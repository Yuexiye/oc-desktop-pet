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
import time
from typing import Optional

from PySide6.QtCore import Qt, QPoint

from avatar.base import AvatarRenderer
from avatar.gl_char_widget import GLCharWidget

logger = logging.getLogger(__name__)

_global_l2d_inited: bool = False  # live2d.v3.init() 进程级只调一次（多宠不重复初始化）


class Live2DRenderer(AvatarRenderer):
    """Live2D (Cubism) 渲染器。"""

    # 情绪 -> 表情名关键词（模型有语义表情时匹配；否则用 motion 或忽略）
    _EMOTION_KEYWORDS = {
        "happy": ("happy", "joy", "smile", "fun", "usui", "唱歌", "比心"),
        "angry": ("angry", "ikari", "mad"),
        "sad": ("sad", "kanashii", "cry"),
        "surprised": ("surprise", "odoroki", "shock", "圈圈", "前倾"),
        "thinking": ("think", "thinking", "doubt", "kangaeru", "圈圈", "前倾"),
        "cute": ("cute", "脸红"),
        "neutral": (),
    }
    # 自动排除的表情名关键词（作者水印/版权声明等，桌宠不展示）
    _IGNORED_EXPRESSIONS = ("水印", "watermark", "版权", "author", "credit", "logo")
    # 卡手势防御：非 idle motion 播满此秒数强制回 idle（模型 motion 全 Loop=true，
    # waving/touch 等手势 mp3.json 都是 2.667s 循环，播 1.5 圈后回位）
    GESTURE_TIMEOUT = 3.0
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
        self._fit_scale_x: float = 1.0  # 横向比例系数（超高画布模型微调用）
        self._center_offset_x: float = 0.0  # 水平居中偏移（画布单位，_recompute_fit 里 SetScale 后应用）
        self._mirror_facing_enabled: bool = False  # 朝向镜像（精灵图玩法，Live2D 不启用）
        self._facing_right: bool = True
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
            # 横向比例系数：对超高画布（如 3500x8888）模型横向偏窄，
            # 用 SetScaleX(fit*sx)/SetScaleY(fit) 分开设，让角色更“方正”。
            self._fit_scale_x = float(l2d.get("scale_x", 1.0))
            if "offset" in l2d and isinstance(l2d["offset"], (list, tuple)):
                self._offset_scale = (float(l2d["offset"][0]), float(l2d["offset"][1]))
        except Exception as e:
            logger.warning("读取 live2d meta 失败: %s", e)

    def on_gl_initialized(self) -> None:
        """GL 上下文就绪：初始化 live2d 后端并加载模型。"""
        # 调试二分：环境变量 L2D_DEBUG_MINIMAL=1 时跳过所有附加逻辑，
        # 只保留纯测试路径（init->glInit->LAppModel->Load->Resize->SetScale->Draw）
        self._debug_minimal = os.environ.get("L2D_DEBUG_MINIMAL") == "1"
        self._debug = os.environ.get("L2D_DEBUG") == "1"  # 调试诊断输出总开关
        if self._ready or not self._model_path:
            return
        try:
            import live2d.v3 as l2d
            self._live2d = l2d
            global _global_l2d_inited
            if not _global_l2d_inited:
                l2d.init()
                # 压住原生 C 库的 Info 刷屏（`motion priority is too low.`、`[CSM][I]`、
                # `Clear all expressions` 直接写 stderr，不走 Python logging，拦不住只能降级）。
                # setLogLevel 只按级别过滤，拦不住全部 Info；enableLog(False) 是真正总开关。
                try:
                    l2d.enableLog(False)
                    _l2d_log=True
                except Exception:
                    _l2d_log=False
                finally:
                    try:
                        l2d.setLogLevel(1)
                    except Exception:
                        pass
                logger.info("Live2DRenderer: enableLog(False) → 原生库日志已关闭%s", "" if _l2d_log else "(API 不可用，跳过)")
                _global_l2d_inited = True

            # GL 可用性检查：不 import PyOpenGL 的 GL（它与 live2d-py 的 glad 加载的
            # GL 函数可能冲突，污染函数指针导致模型绘制失败——纯测试从不 import
            # PyOpenGL 能正常画出）。直接尝试 glInit，失败则回退 sprite。
            try:
                import live2d.v3
                l2d.glInit()
                logger.info("Live2DRenderer: glInit OK（未依赖 PyOpenGL）")
            except Exception as e:
                logger.warning("Live2DRenderer: glInit 失败，回退 sprite 渲染: %s", e)
                self._ready = False
                self._model = None
                return

            # 用 LAppModel（高层封装，内部管理 dt/投影/自动眨眼/呼吸）。
            # 最小测试验证 LAppModel 能正确绘制（底层 Model 画不出来）。
            model = l2d.LAppModel()
            model.LoadModelJson(self._model_path)

            self._model = model
            # LAppModel.LoadModelJson 内部已自动 CreateRenderer
            if not self._debug_minimal:
                model.SetAutoBlinkEnable(True)
                model.SetAutoBreathEnable(True)
            else:
                logger.info("Live2DRenderer: L2D_DEBUG_MINIMAL=1 跳过自动眨眼/呼吸")

            # 关键：live2d 文档要求初次加载必须 Resize(宽,高)，否则模型不显示。
            # 用 GLCharWidget 实际尺寸（而非硬编码默认 220），与纯测试一致。
            # 若 _gl_w 未设置（initializeGL 早于 resizeGL），回退到 widget 实际 size。
            try:
                gl_w = int(getattr(self, "_gl_w", 0) or 0)
                gl_h = int(getattr(self, "_gl_h", 0) or 0)
                if not gl_w or not gl_h:
                    cl = getattr(self, "char_label", None)
                    if cl is not None:
                        gl_w = cl.width() or 220
                        gl_h = cl.height() or 260
                model.Resize(gl_w, gl_h)
                logger.info("Live2DRenderer: Resize(%s,%s)", gl_w, gl_h)
            except Exception as e:
                logger.warning("Live2DRenderer: Resize 失败: %s", e)

            # 显式设置初始缩放（用像素画布尺寸，与最小测试一致）。
            # 不设置则模型保持默认 scale，可能过大/过小画不出来。
            try:
                cw_px, ch_px = model.GetCanvasSizePixel()
                if cw_px and ch_px:
                    gl_w = int(getattr(self, "_gl_w", 0) or 0)
                    gl_h = int(getattr(self, "_gl_h", 0) or 0)
                    if not gl_w or not gl_h:
                        cl = getattr(self, "char_label", None)
                        if cl is not None:
                            gl_w = cl.width() or 220
                            gl_h = cl.height() or 260
                    # SetScale 语义：1.0=画布适配窗口。fit 直接取用户缩放系数。
                    scale = self._fit_scale
                    model.SetScale(scale)
                    logger.info("Live2DRenderer: 初始缩放 scale=%.3f", scale)
            except Exception as e:
                logger.warning("Live2DRenderer: 初始缩放失败: %s", e)

            # 收集可用表情/动作组，用于情绪映射
            self._expression_names = []
            self._motion_groups = {}
            self._motion_files: list[str] = []      # 空组下的 motion 文件名列表
            self._motion_group_name: str = ""       # 实际使用的 motion 组名
            # 卡手势防御：模型 motion 全是 Loop=True（永不 finished），非 idle 手势一旦
            # 播放，IsMotionFinished 永不 True → idle 永不重启 → 卡在最后动作上。
            # 这里记录当前 motion 是否常态 idle + 起始时间，播满 GESTURE_TIMEOUT 强制回 idle。
            self._motion_is_idle = True
            self._motion_started_at = time.monotonic()
            # emotion → motion 上次播放时间，防止同一情绪手势被连续触发、看起来“永久卡死”
            self._emotion_motion_cooldown: dict[str, float] = {}
            if not self._debug_minimal:
                # wrapper 0.7.0.4 的真实 API 是 GetExpressionIds（pyi 写的 GetExpressions 不存在），
                # 且 GetExpressionIds 在初始化早期可能抛异常——都 fallback 到 model3.json 解析。
                self._expression_names = []
                try:
                    ids = model.GetExpressionIds()
                    if ids:
                        self._expression_names = [
                            n for n in ids
                            if not any(k in str(n).lower() for k in self._IGNORED_EXPRESSIONS)
                        ]
                except Exception:
                    pass
                if not self._expression_names:
                    import json as _json
                    try:
                        with open(self._model_path, encoding="utf-8") as _f:
                            _m3 = _json.load(_f)
                        exprs = _m3.get("FileReferences", {}).get("Expressions", []) or []
                        names = []
                        for _e in exprs:
                            nm = _e.get("Name") or os.path.splitext(os.path.basename(_e.get("File", "")))[0]
                            if nm and not any(k in str(nm).lower() for k in self._IGNORED_EXPRESSIONS):
                                names.append(nm)
                        # 去重保序
                        self._expression_names = list(dict.fromkeys(names))
                        if names:
                            logger.info("Live2DRenderer: 从 model3.json 解析 %d 个表情名: %s", len(names), names)
                    except Exception as _e:
                        logger.warning("Live2DRenderer: 解析表情失败: %s", _e)
                try:
                    if hasattr(model, "GetMotionGroups"):
                        groups = model.GetMotionGroups()
                        # 保留所有组名（含空串）。此模型 lafei.model3.json 把动作都放在
                        # 空字符串组 "" 下（idle/login/touch_* 等 14 个），空串是合法组名。
                        # 之前过滤空串导致 _motion_groups 为空，_start_idle 退回用 "Idle"
                        # 却找不到，待机动画不启动。这里保留空串组。
                        self._motion_groups = {g: [] for g in (groups or []) if g is not None}
                    else:
                        self._motion_groups = dict(model.GetMotions() or {})
                except Exception:
                    self._motion_groups = {}

                # 建 motion 文件索引（按文件名关键词匹配播放，因为组名是空串匹配不上）
                try:
                    motions = model.GetMotions()
                    self._motion_group_name = next(
                        (g for g in motions if g), next(iter(motions), ""))
                    self._motion_files = [
                        (m.get("File", "") if isinstance(m, dict) else "")
                        for m in motions.get(self._motion_group_name, [])
                    ]
                except Exception:
                    self._motion_files = []

                # 清除默认表情（模型常带作者水印/LOGO 表情，默认显示会遮挡角色）
                try:
                    model.ResetExpressions()
                except Exception:
                    pass
                # 缓存水印参数索引，每帧强制关闭（Param137=水印）
                self._cache_watermark_index()
                # 起始待机动作
                self._start_idle()

            self._ready = True
            logger.info(
                "Live2DRenderer: 模型加载成功 (expressions=%d, motion_groups=%s)",
                len(self._expression_names), list(self._motion_groups.keys()),
            )
            # 模型就绪后延迟测量角色 bbox，请求窗口自动贴合模型大小
            # （等 draw 首帧跑完，HitDrawable 才有有效状态）
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, self._fit_window_to_model)
        except Exception as e:
            logger.error("Live2DRenderer: 模型加载失败（角色区域将透明）: %s", e)
            self._ready = False
            self._model = None

    def _scan_bbox_adaptive(self, frames: int = 3, coarse_step: int = 32, fine_step: int = 6) -> tuple | None:
        """自适应两段式扫描：每帧先粗扫定位，再在同一帧内四带精扫，帧间并集。

        关键设计：粗扫与精扫必须在**同一帧**（同一摆动相位）执行——
        若分开跑，band 要同时覆盖粗步长误差 + 帧间摆动差（可达 56px+），盖不住。
        每帧成本 ≈ 粗扫(≈255 次) + 四带精扫(≈1.5k 次) ≈ 1.8k 次，3 帧 ≈ 5.4k 次，
        对比原 3 帧全视口 step=3 的 8 万次，降一个量级（约 15x）。
        返回 (min_x, min_y, max_x, max_y)，未命中返回 None。
        """
        mm = getattr(self._model, "_model", None) or self._model
        gl_w = int(getattr(self, "_gl_w", 0))
        gl_h = int(getattr(self, "_gl_h", 0))
        if gl_w <= 0 or gl_h <= 0:
            return None
        min_x, min_y, max_x, max_y = gl_w, gl_h, -1, -1
        hit_any_frame = False
        for _ in range(frames):
            try:
                self._model.Update()
                self._model.Draw()
            except Exception:
                pass

            def _hit(x: int, y: int) -> bool:
                try:
                    return bool(mm.HitDrawable(float(x), float(y)))
                except Exception:
                    return False

            # ① 粗扫本帧：大步长定位大致 bbox
            c_min_x, c_min_y, c_max_x, c_max_y = gl_w, gl_h, -1, -1
            for y in range(0, gl_h + 1, coarse_step):
                for x in range(0, gl_w + 1, coarse_step):
                    if _hit(x, y):
                        if x < c_min_x: c_min_x = x
                        if x > c_max_x: c_max_x = x
                        if y < c_min_y: c_min_y = y
                        if y > c_max_y: c_max_y = y
            if c_max_x < 0:
                continue  # 本帧未命中，等下一帧
            hit_any_frame = True
            # ② 同帧四带精扫：band 只需覆盖粗步长误差（同帧无摆动差）
            band = coarse_step + 8  # 40
            # 左带 / 右带（全高）
            for x in range(max(0, c_min_x - band), min(gl_w, c_min_x + band) + 1, fine_step):
                for y in range(0, gl_h + 1, fine_step):
                    if _hit(x, y):
                        if x < min_x: min_x = x
                        if x > max_x: max_x = x
                        if y < min_y: min_y = y
                        if y > max_y: max_y = y
            for x in range(max(0, c_max_x - band), min(gl_w, c_max_x + band) + 1, fine_step):
                for y in range(0, gl_h + 1, fine_step):
                    if _hit(x, y):
                        if x < min_x: min_x = x
                        if x > max_x: max_x = x
                        if y < min_y: min_y = y
                        if y > max_y: max_y = y
            # 上带 / 下带（角色宽度范围，含外扩）
            x_lo = max(0, c_min_x - band)
            x_hi = min(gl_w, c_max_x + band)
            for y in range(max(0, c_min_y - band), min(gl_h, c_min_y + band) + 1, fine_step):
                for x in range(x_lo, x_hi + 1, fine_step):
                    if _hit(x, y):
                        if x < min_x: min_x = x
                        if x > max_x: max_x = x
                        if y < min_y: min_y = y
                        if y > max_y: max_y = y
            for y in range(max(0, c_max_y - band), min(gl_h, c_max_y + band) + 1, fine_step):
                for x in range(x_lo, x_hi + 1, fine_step):
                    if _hit(x, y):
                        if x < min_x: min_x = x
                        if x > max_x: max_x = x
                        if y < min_y: min_y = y
                        if y > max_y: max_y = y
        if not hit_any_frame or max_x < 0:
            return None
        return (min_x, min_y, max_x, max_y)

    def _fit_window_to_model(self) -> None:
        """测量角色本体 bbox，请求窗口 resize 到模型大小（去掉多余透明边距）。

        HitDrawable 在 draw 后才有有效状态。原实现 3 帧全视口 STEP=3 扫描
        （458x520 视口约 8 万次命中检测，耗时 ≈ 70s）。
        现改为自适应两段式（每帧：粗扫定位 + 同帧四带精扫，帧间并集），
        总调用降一个量级（≈5.4k 次，约 15x），耗时降到秒级。
        """
        if not self._model or not self._ready:
            logger.debug("Live2DRenderer: fit 跳过（模型未就绪）")
            return
        try:
            mm = getattr(self._model, "_model", None) or self._model
            if not hasattr(mm, "HitDrawable"):
                logger.debug("Live2DRenderer: fit 跳过（无 HitDrawable）")
                return
            gl_w = int(getattr(self, "_gl_w", 0) or self._renderer_w() or 0)
            gl_h = int(getattr(self, "_gl_h", 0) or self._renderer_h() or 0)
            if gl_w <= 0 or gl_h <= 0:
                logger.debug("Live2DRenderer: fit 跳过（视口无效 %dx%d）", gl_w, gl_h)
                return
            logger.info("Live2DRenderer: fit 开始 gl=%dx%d", gl_w, gl_h)
            t0 = time.time()
            bbox = self._scan_bbox_adaptive(frames=3, coarse_step=32, fine_step=6)
            t1 = time.time()
            if bbox is None:
                logger.info("Live2DRenderer: 未命中角色像素，跳过窗口贴合")
                return
            min_x, min_y, max_x, max_y = bbox
            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            logger.info(
                "Live2DRenderer: fit 耗时 %.2fs (bbox=%dx%d)",
                t1 - t0, bw, bh,
            )
            # 居中补偿：模型在画布里固位偏右（moc3 留白），用 SetOffsetX 平移居中。
            # 不在这里直接设——_fit_window_to_model 之后窗口贴合成新视口会触发 SetScale，
            # 与 offset 的时序交互在真实多并发环境有过闪退。改为缓存 offx，统一由
            # _recompute_fit 在每次 SetScale 之后应用（保证 SetScale → SetOffsetX 顺序，
            # 实测该顺序稳定不闪退）。
            try:
                center_x = (min_x + max_x) / 2.0
                offx = (gl_w / 2.0 - center_x) / (0.591 * gl_w)
                offx = max(-0.9, min(0.9, offx))
                self._center_offset_x = offx
                logger.info(
                    "Live2DRenderer: 居中补偿 中心x=%.0f→%.0f offx=%+.3f",
                    center_x, gl_w / 2.0, offx,
                )
            except Exception as _e:
                logger.debug("居中补偿失败（跳过）: %s", _e)
            # 边距：给 idle 摆幅留余量（窗口尺寸用摆动范围 bw）。
            # 底部额外多留（HitDrawable 命中区 < 可见像素，脚部可能画在命中区外）：
            # pad_h 用 0.38 且保底 26px，专门防角色脚/裙摆被窗口下边缘截断。
            pad_w = max(14, int(bw * 0.28))
            pad_h = max(26, int(bh * 0.38))
            # 额外底部补偿：HitDrawable 命中区通常只覆盖身体，脚部/裙摆/长发常画在命中区
            # 之外，导致 hit-bbox 底部不含脚、bh 偏小、窗口过矮而裁脚。直接用固定大边距
            # 而非 bh 百分比，因为 bh 本身已漏掉脚部，百分比追不上。
            pad_bottom = max(200, int(bh * 0.15))
            target_w = max(40, bw + pad_w)
            target_h = max(40, bh + pad_h + pad_bottom)
            # 关键修复（截脚根因）：HitDrawable 命中区只覆盖上半身，脚部/裙摆在命中区
            # 之外，扫描出的 bbox 不含脚。仅加窗口高度只是把"画布底→窗口底"的空白拉大，
            # 模型在 Live2D 画布里的位置没动，脚仍落在窗口可见区之外被裁。
            # 必须把模型在画布里【上移】，等效于"顶部留白、脚贴窗口下缘"。
            # offsetY 单位≈整个画布高度偏移比例：把 pad_bottom 占窗口高度的比例换算成
            # 上移量（正值=上移）。初音实测 pad_bottom≈200/target_h≈793 → 约 0.25 上移。
            # 用 max 保底 0.18，避免换算异常时脚仍被裁。
            try:
                _ratio = pad_bottom / float(target_h) if target_h > 0 else 0.25
            except Exception:
                _ratio = 0.25
            self._fit_offset_y = max(0.18, min(0.45, _ratio))
            logger.info(
                "Live2DRenderer: 角色 bbox=%dx%d (偏移 %d,%d)，窗口贴合到 %dx%d (+%dpx 边距, 上移 offsetY=%.3f)",
                bw, bh, min_x, min_y, target_w, target_h, pad_w, self._fit_offset_y,
            )
            parent = getattr(self, "_parent", None)
            fit_win = getattr(parent, "fit_window_to_model", None)
            if callable(fit_win):
                fit_win(target_w, target_h)
            # 保险：若窗口尺寸未变（on_resize 不触发），主动应用一次居中
            if getattr(self, "_center_offset_x", 0.0):
                try:
                    self._recompute_fit()
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Live2DRenderer: 窗口贴合失败: %s", e)

    def _renderer_w(self):
        try:
            cl = getattr(self, "char_label", None)
            return cl.width() if cl is not None else 0
        except Exception:
            return 0

    def _renderer_h(self):
        try:
            cl = getattr(self, "char_label", None)
            return cl.height() if cl is not None else 0
        except Exception:
            return 0

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
            # SetScale 语义：1.0 = 角色画布适配窗口（实测 scale=1.0 时角色占满窗口 100%×95%）。
            # 所以 fit 直接取用户配置的缩放系数（pet.json live2d.scale）：
            #   1.0 = 本体填满窗口（无背景）
            #   <1  = 缩小留白
            #   >1  = 放大裁剪（特写）
            fit = self._fit_scale
            # 横向比例系数：
            # - pet.json 显式配置 scale_x 时用它
            # - 否则超高画布（如 miku 3500x8888）等比缩放会因画布超高导致角色横向偏细，
            #   自动按画布比例补正（缓存后稳定，不再每次重算）。
            # 之前“偏移不稳定”的真凶是 offset 时序 bug（已移除），不是 scale_x 本身；
            # scale_x 是确定性缩放，恢复后模型稳定不跳。
            sx = getattr(self, "_fit_scale_x", 1.0)
            if sx == 1.0 and cw_px and ch_px and cw_px / ch_px < 0.7:
                try:
                    ratio = cw_px / ch_px
                    # 参考比 0.48：补到接近人体但不过度。之前 0.55 会让偏右的 miku
                    # 横向放得过大，右边贴到视口边缘被裁。0.48 → sx≈1.22，模型不瘦、
                    # 右边留一点余量不裁。
                    sx = round(max(1.0, min(0.48 / ratio, 1.5)), 3)
                    self._fit_scale_x = sx
                except Exception:
                    sx = 1.0
            if sx != 1.0:
                try:
                    self._model.SetScaleX(fit * sx)
                    self._model.SetScaleY(fit)
                except Exception:
                    # 老版本 wrapper 可能没有 SetScaleX；回退等比
                    self._model.SetScale(fit)
            else:
                self._model.SetScale(fit)
            # 居中：SetScale 之后应用水平偏移（保证 SetScale→SetOffsetX 顺序，实测稳定）。
            # 水平 = 自动居中补偿(_center_offset_x) + pet.json 的 live2d.offset[0]，二者叠加。
            _offx = getattr(self, "_center_offset_x", 0.0)
            _os = getattr(self, "_offset_scale", (0.0, 0.0))
            try:
                _ox = float(_offx) + float(_os[0] if len(_os) > 0 else 0.0)
                self._model.SetOffsetX(_ox)
            except Exception:
                pass
            # 垂直偏移：pet.json 的 live2d.offset[1]（脚贴地微调）+ fit 自动上移补偿。
            # Live2D 模型坐标 Y 轴向上，SetOffsetY 正值=上移、负值=下移。
            # 第二来源 _fit_offset_y：截脚修复——HitDrawable 命中区只覆盖上半身，bbox 不含脚，
            # 仅加窗口高度会把脚推出可见区；故把模型上移 _fit_offset_y（见 _fit_window_to_model），
            # 等效"顶部留白、脚贴窗口下缘"，让脚真正显示在窗口内。
            # 二者叠加：用户自定义 offset[1]（通常微调用，正值上移）与自动补偿合并。
            try:
                _oy_user = float(_os[1] if len(_os) > 1 else 0.0)
                _oy_fit = getattr(self, "_fit_offset_y", 0.0) or 0.0
                _oy = _oy_user + _oy_fit
                if _oy:
                    self._model.SetOffsetY(_oy)
            except Exception:
                pass
            logger.debug("Live2DRenderer: 缩放 fit=%.3f sx=%.3f offx=%.3f offy=%.3f (gl=%sx%s, canvas_px=%sx%s)",
                         fit, sx, float(_offx) + float(_os[0] if len(_os) > 0 else 0.0),
                         float(_os[1] if len(_os) > 1 else 0.0),
                         self._gl_w, self._gl_h, cw_px, ch_px)
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
                # 离屏截图诊断（L2D_DEBUG=1 才启用）：保存 GL 内容，确认模型是否真的画出来
                if getattr(self, "_debug", False):
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
        if not self._debug_minimal:
            # 每帧检测：待机动作播完则重新启动，实现持续循环
            try:
                if self._model.IsMotionFinished():
                    if getattr(self, "_debug", False):
                        logger.info("Live2DRenderer: idle 动作播完，重新触发")
                    self._start_idle()
            except Exception as e:
                logger.warning("Live2DRenderer.idle 循环异常: %s", e)

            # 卡手势防御：模型 motion 全是 Loop=True，非 idle 手势永不 finished，
            # idle 永不重启 → 卡在最后手势（摸头/挥手等）。播满 GESTURE_TIMEOUT 秒强制回 idle。
            try:
                elapsed = time.monotonic() - self._motion_started_at
                if not self._motion_is_idle and elapsed > self.GESTURE_TIMEOUT:
                    logger.info(
                        "Live2DRenderer: 非 idle motion 超时 %.1fs/%.1fs（%s），强制回 idle",
                        elapsed, self.GESTURE_TIMEOUT,
                        getattr(self, "_current_motion_idx", "?"),
                    )
                    self._force_idle()
            except Exception as e:
                logger.warning("Live2DRenderer.motion 超时检查异常: %s", e)

        # 完整帧更新：绕过 live2d-py 0.7.0.4 wrapper 残缺的 Update()（motion/blink/呼吸全被注释），
        # 直接驱动 C++ Model 的完整更新序列（UpdateMotion → Blink → Breath → Physics → Pose）。
        try:
            self._frame_update()
        except Exception as e:
            logger.warning("Live2DRenderer._frame_update 异常: %s", e)

        # 手动参数叠加（gaze/mouth）必须在 motion 更新之后、SaveParameters 之前设置，
        # 否则会被 motion 曲线覆盖。weight<1 实现混合（motion 为主，手动为辅）。
        if not self._debug_minimal:
            try:
                self._update_gaze_params()
            except Exception as e:
                logger.warning("Live2DRenderer.gaze 异常: %s", e)
            try:
                self._update_mouth()
            except Exception as e:
                logger.warning("Live2DRenderer.mouth 异常: %s", e)

        try:
            self._model.Draw()
        except Exception as e:
            logger.warning("Live2DRenderer.Draw 异常: %s", e)

    def _frame_update(self) -> None:
        """完整 Live2D 帧更新：直接驱动 C++ Model（绕过 wrapper 残缺 Update）。

        官方 LAppModel::Update 的标准序列（live2d-py 0.7.0.4 的 Python Update() 把它们全注释了）：
          Update(dt) → LoadParameters → UpdateMotion(dt) → [无 motion 时 UpdateBlink]
          → UpdateExpression → UpdateDrag → UpdateBreath → UpdatePhysics → UpdatePose
          → SaveParameters
        """
        mm = getattr(self._model, "_model", None)
        if mm is None:
            # 回退：wrapper 的 Update（虽然残缺，至少不崩）
            self._model.Update()
            return
        now = time.monotonic()
        dt = min(now - getattr(self, "_frame_last_t", now), 0.1)
        self._frame_last_t = now

        try:
            mm.Update(dt)
        except Exception:
            pass
        try:
            mm.LoadParameters()
        except Exception:
            pass
        motion_updated = False
        try:
            motion_updated = bool(mm.UpdateMotion(dt))
        except Exception:
            pass
        if not motion_updated:
            try:
                mm.UpdateBlink(dt)
            except Exception:
                pass
        try:
            mm.UpdateExpression(dt)
        except Exception:
            pass
        # 水印抑制：模型自带 Param137(水印) 表情默认开，表情更新后强制置 0
        self._suppress_watermark(mm)
        try:
            mm.UpdateDrag(dt)
        except Exception:
            pass
        try:
            mm.UpdateBreath(dt)
        except Exception:
            pass
        try:
            mm.UpdatePhysics(dt)
        except Exception:
            pass
        try:
            mm.UpdatePose(dt)
        except Exception:
            pass
        try:
            mm.SaveParameters()
        except Exception:
            pass

    # ── 内部：参数驱动 ──

    def _cache_watermark_index(self) -> None:
        """缓存水印部件索引。

        多数免费模型把作者版权水印做成独立部件：
        cdi3.json 里 Part 有 Id（如 Part18）与 Name（如 水印.psd）两个字段，
        GetPartIds() 只暴露 Id，中文名在 cdi3 里——所以先读 cdi3 做 Id→Name 映射，
        再匹配“水印/watermark/logo/版权”关键词，记录索引，每帧强制透明度 0。
        这是作者文档所说的“水印按键可在设置表情中关闭”的真正实现：
        水印不是一个参数，是一个可见部件。
        """
        self._watermark_part_idx: list[int] = []
        self._watermark_idx = -1   # 兼容旧字段（参数索引，已弃用）
        if not self._model:
            return
        try:
            parts = self._model.GetPartIds()
            if not parts:
                return
            # 从 model3.json 同目录的 cdi3.json 读 Id→Name
            wm_ids = set()
            import json as _json
            base = os.path.dirname(self._model_path or "")
            cdi3 = os.path.join(base, os.path.splitext(os.path.basename(self._model_path or ""))[0] + ".cdi3.json")
            if not os.path.exists(cdi3):
                # 回退：同目录里唯一的 cdi3
                _cands = [f for f in os.listdir(base) if f.endswith(".cdi3.json")] if base and os.path.isdir(base) else []
                if _cands:
                    cdi3 = os.path.join(base, _cands[0])
            if os.path.exists(cdi3):
                try:
                    _c = _json.load(open(cdi3, encoding="utf-8"))
                    wm_kw = ("水印", "watermark", "logo", "版权")
                    for _p in _c.get("Parts", []):
                        if any(k in str(_p.get("Name", "")).lower() for k in wm_kw):
                            wm_ids.add(_p.get("Id"))
                except Exception:
                    wm_ids = set()
            if wm_ids:
                self._watermark_part_idx = [i for i, p in enumerate(parts) if p in wm_ids]
                logger.info(
                    "Live2DRenderer: 检测到水印部件 %s，每帧强制隐藏",
                    [parts[i] for i in self._watermark_part_idx],
                )
        except Exception:
            self._watermark_part_idx = []

    def _suppress_watermark(self, mm=None) -> None:
        """每帧强制隐藏水印部件（Part 透明度=0）。

        作者版权水印默认显示；via 部件透明度直接隐藏，
        比参数方案可靠（水印部件不绑参数，Param137 只是装饰）。
        """
        idxs = getattr(self, "_watermark_part_idx", None)
        if not idxs:
            return
        target = mm or getattr(self._model, "_model", None) or self._model
        if target is None:
            return
        for i in idxs:
            try:
                target.SetPartOpacity(i, 0.0)
            except Exception:
                pass

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
            self._model.SetParameterValue(P.ParamAngleX, self._gaze_cur_angle_x, 0.3)
            self._model.SetParameterValue(P.ParamAngleY, self._gaze_cur_angle_y, 0.3)
            self._model.SetParameterValue(P.ParamEyeBallX, self._gaze_cur_ball_x, 0.3)
            self._model.SetParameterValue(P.ParamEyeBallY, self._gaze_cur_ball_y, 0.3)
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

    def _note_motion_started(self, fname: str = "", is_idle: bool = False) -> None:
        """记录当前 motion 是否常态 idle 并重置计时（卡手势超时兜底用）。

        is_idle 由调用方显式传入（idle 动作=True，手势/随机动作=False），
        不再依赖文件名是否含 "idle" 猜测——避免某手势 motion 文件名恰含 "idle"
        被错判为 idle，导致 GESTURE_TIMEOUT 兜底永不触发、手势永久卡住（如比心）。
        """
        self._motion_is_idle = bool(is_idle)
        self._motion_started_at = time.monotonic()

    def _force_idle(self) -> None:
        """StopAllMotions 后重启 idle。

        idle 用 IDLE 优先级，打不过正在播的 NORMAL 手势（priority too low 会被拒），
        所以先清空 motion 队列再播。
        兜底：_start_idle 异常时仍把状态机切回 idle，避免手势状态永远挂住。
        """
        try:
            if hasattr(self._model, "StopAllMotions"):
                self._model.StopAllMotions()
        except Exception:
            pass
        try:
            self._start_idle()
        except Exception as e:
            logger.warning("Live2DRenderer: _start_idle 失败，兜底状态回 idle: %s", e)
            self._note_motion_started("force_idle_fallback", is_idle=True)

    def _start_idle(self) -> None:
        if not self._model:
            return
        try:
            # 优先用 GetMotions() 取具体 motion 列表，按索引播放，避免 StartRandomMotion
            # 对空串组名可能静默失败的问题。
            motions = self._model.GetMotions()
            if motions:
                # 优先取空串组（此模型所有动作都在 "" 组），否则取第一个非空组
                group = next((g for g in motions if g), next(iter(motions), ""))
                motion_list = motions.get(group, [])
                if motion_list:
                    # 选包含 "idle" 的 motion（优先），否则播第 0 个
                    idx = 0
                    for i, m in enumerate(motion_list):
                        if isinstance(m, dict) and "idle" in m.get("File", "").lower():
                            idx = i
                            break
                    fname = (motion_list[idx].get("File", "")
                             if isinstance(motion_list[idx], dict) else "")
                    self._model.StartMotion(group, idx, self._live2d.MotionPriority.IDLE)
                    self._note_motion_started(fname, is_idle=True)
                    return
            # fallback：StartRandomMotion（组名非空时有效）
            if self._motion_groups:
                group = next(iter(self._motion_groups))
                self._model.StartRandomMotion(group, self._live2d.MotionPriority.IDLE)
            else:
                self._model.StartRandomMotion(self._live2d.MotionGroup.IDLE,
                                              self._live2d.MotionPriority.IDLE)
            self._note_motion_started("idle", is_idle=True)  # fallback 视为 idle，不受超时限制
        except Exception as e:
            # 忽略 "motion priority is too low" 警告（正常行为，idle 被更高优先级 motion 打断）
            if "priority is too low" not in str(e):
                logger.warning("Live2DRenderer: 起始待机动作失败: %s", e)

    def _play_motion_kw(self, *groups, priority=None) -> bool:
        """按文件名关键词从 motion 列表找第一个匹配并播放。

        模型所有动作都在一个组（如空串 ""），组名匹配不上任何关键词，
        所以按 GetMotions() 的 File 文件名匹配（如 touch_head → "touch"+"head"）。
        支持多组备选关键词：每组内先 all 后 any 匹配，组间按顺序（前组优先）——
        兼容 lafei（main_1/2/3、home…）与 miku（waving/touch/thinking…）两种模型命名。

        Returns:
            True 表示找到了并播放；False 表示无匹配（调用方可回退）。
        """
        if not self._model or not self._motion_files:
            return False
        try:
            # 兼容旧式单组扁平调用（("main", "1")）→ 包成多组
            if groups and not isinstance(groups[0], (tuple, list)):
                groups = (groups,)
            for kws in groups:
                kws = [k.lower() for k in kws if k]
                if not kws:
                    continue
                # 严格：全部关键词命中
                for i, f in enumerate(self._motion_files):
                    low = f.lower()
                    if all(k in low for k in kws):
                        return self._start_motion_at(i, priority)
                # 宽松：任一关键词命中
                for i, f in enumerate(self._motion_files):
                    low = f.lower()
                    if any(k in low for k in kws):
                        return self._start_motion_at(i, priority)
            return False
        except Exception as e:
            # 忽略 "motion priority is too low" 警告（正常行为）
            if "priority is too low" not in str(e):
                logger.warning("Live2DRenderer._play_motion_kw 异常: %s", e)
            return False

    def _start_motion_at(self, idx: int, priority=None) -> bool:
        """按索引播 motion 并记录起始状态（卡手势超时兜底用）。

        去重：同一 motion 已在播（Loop=True 帧动画）时不重复 StartMotion、
        也不重置计时——否则 emotion 周期刷新（happy 每 3s 续期）会不断
        重置 _motion_started_at，卡手势超时兜底永不触发（比心/挥手持久）。
        """
        try:
            fname = self._motion_files[idx] if idx < len(self._motion_files) else ""
            cur_idx = getattr(self, "_current_motion_idx", None)
            if idx == cur_idx and not getattr(self, "_motion_is_idle", False):
                if getattr(self, "_debug", False):
                    logger.debug("Live2DRenderer: 同一 motion 已在播(idx=%d)，去重跳过", idx)
                return True  # 继续播（Loop），不计时不受影响
            prio = priority if priority is not None else self._live2d.MotionPriority.NORMAL
            self._model.StartMotion(self._motion_group_name, idx, prio)
            self._current_motion_idx = idx
            self._note_motion_started(fname, is_idle=False)
            return True
        except Exception as e:
            logger.warning("Live2DRenderer.StartMotion 异常: %s", e)
            return False

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

    # 精灵动画名/情绪 → Live2D motion 文件名关键词（模型动作全在空组，按文件名匹配）。
    # 值是多组备选关键词：每组按 all→any 匹配，组间按顺序（前组优先）。
    # 兼容两种模型命名：lafei（main_1/2/3、home、touch_head…）与 miku（happy/waving/touch…）
    _ANIM_TO_MOTION_KW = {
        "idle": (("idle",),),
        "waving": (("waving",), ("main", "1")),
        "happy": (("happy",), ("main", "1")),
        "walk": (("walk",), ("main", "2")),
        "sleep": (("sleep",), ("home",)),
        "working": (("working",), ("main", "3")),
        "thinking": (("thinking",), ("main", "3")),
        "failed": (("failed",), ("mission",)),
        "sad": (("sad",), ("mission",)),
        "surprised": (("surprised",), ("login",)),
        "angry": (("angry",), ("mission_complete",)),
        "touch": (("touch",), ("touch_head",)),
        "pat": (("touch",), ("touch_head",)),
        "stroke": (("stroke",), ("touch_body",)),
        "pet": (("stroke",), ("touch_body",)),
        "special": (("special",), ("touch_special",)),
        "wedding": (("wedding",),),
        "login": (("login",),),
        "mail": (("mail",),),
        "complete": (("complete",),),
    }

    def play_anim(self, anim: str, emotion: str = "", frame_range=None) -> None:
        self._current_anim = anim
        if emotion:
            # 缺陷② 修复：播放指定动作时，情绪只同步表情、不再重复播情绪 motion，
            # 否则“情绪 motion + 动作 motion”连着播放，出现「生气表情 + 唱歌动作」错位。
            self.set_emotion_expression_only(emotion)
        # Live2D：按精灵动画名映射到 motion 文件名播放（组名是空串匹配不上）
        kws = self._ANIM_TO_MOTION_KW.get(anim) or self._ANIM_TO_MOTION_KW.get(emotion)
        if kws:
            if self._play_motion_kw(*kws):
                return
        # fallback：老逻辑（组名匹配）
        if self._model and anim in self._motion_groups:
            try:
                self._model.StartRandomMotion(anim, self._live2d.MotionPriority.NORMAL)
                self._note_motion_started("")  # 未知 motion → 按限时手势处理
            except Exception:
                pass

    def set_emotion_expression_only(self, emotion: str) -> None:
        """仅同步情绪表情（不播动作）。给 play_anim 用，避免动作/表情错位。"""
        self._current_emotion = emotion
        self._emotion_target = emotion
        if not self._model:
            return
        self._apply_expression(emotion)

    def _apply_expression(self, emotion: str) -> None:
        """应用情绪对应的表情（不碰 motion）。"""
        expr = self._match_expression(emotion)
        try:
            if expr is None:
                self._model.ResetExpressions()
            else:
                self._model.SetExpression(expr)
        except Exception as e:
            logger.warning("Live2DRenderer: 设置表情失败: %s", e)

    def set_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        self._current_emotion = emotion
        self._emotion_target = emotion
        if not self._model:
            return

        # emotion 级冷却：同一情绪触发的手势播完后，GESTURE_TIMEOUT 内不再重播该情绪 motion
        # （只同步表情）。避免“屏幕/对话反复推 happy → 比心永远切不回来”的观感。
        # 仅在当前已是 idle 时生效；若当前正播其他 gesture，新情绪正常打断。
        now = time.monotonic()
        last_at = self._emotion_motion_cooldown.get(emotion, 0.0)
        in_cooldown = (
            getattr(self, "_motion_is_idle", True)
            and now - last_at < self.GESTURE_TIMEOUT
        )
        if in_cooldown:
            logger.debug("Live2DRenderer: emotion=%s 仍在 %.1fs 冷却期，只同步表情", emotion, self.GESTURE_TIMEOUT)
            self._apply_expression(emotion)
            return

        # 优先：情绪 → motion 文件关键词（NORMAL 优先级，不打断主要动作）
        kws = self._ANIM_TO_MOTION_KW.get(emotion)
        motion_played = False
        if kws:
            motion_played = self._play_motion_kw(*kws)
            if not motion_played:
                # 回退：情绪 → motion 组
                motion = self._match_motion(emotion)
                if motion:
                    try:
                        self._model.StartRandomMotion(motion, self._live2d.MotionPriority.FORCE)
                        motion_played = True
                    except Exception:
                        pass
        else:
            # 无映射：情绪 → motion 组
            motion = self._match_motion(emotion)
            if motion:
                try:
                    self._model.StartRandomMotion(motion, self._live2d.MotionPriority.FORCE)
                    motion_played = True
                except Exception:
                    pass
        # 记录该情绪 motion 的播放时间，用于 emotion 级冷却
        if motion_played:
            self._emotion_motion_cooldown[emotion] = time.monotonic()
        # 表情
        self._apply_expression(emotion)

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
        # 用窗口尺寸设置角色 label（不再用固定 220x260 基准——那比 200 宽的窗口还宽，
        # 导致角色宽度溢出被窗口裁切、显示不完整）。
        # 模型是正方形(1200px)，label 用窗口宽，角色按高度填满。
        # 注意：window_w/window_h 已是缩放后的最终尺寸（pet.py _recalc_geometry 传入），
        # 这里不再乘 _scale，避免双重缩放导致 label 尺寸异常（窗口框远超模型）。
        w = int(window_w)
        h = int(window_h)
        self.char_label.setFixedSize(w, h)
        self._base_label_pos = QPoint(0, 0)
        self._recompute_fit()

    def set_facing(self, right: bool) -> None:
        self._facing_right = right
        # Live2D 是 3D 立绘，保持正面：水平镜像（SetScaleX 负值）会把模型整体
        # 左右翻转，与画布偏右的居中补偿（SetOffsetX 左移）叠加，转向后模型
        # 明显偏左/偏右——用户反馈的“又偏左/偏右”就是镜像+居中的冲突。
        # 镜像朝向是精灵图桌宠的玩法，Live2D 不用。这里仅记录 facing 状态，
        # 供 walk 动画/交互语义使用，不改 model 矩阵。若将来要“转身”，
        # 应走 Live2D 参数（如 ParamAngleY 体感转向）而非负 scale。
        if self._model and getattr(self, "_mirror_facing_enabled", False):
            try:
                # 用像素画布尺寸计算缩放（与 _recompute_fit 一致，按高度缩放）
                cw_px, ch_px = self._model.GetCanvasSizePixel()
                if not cw_px or not ch_px:
                    ppu = self._model.GetPixelsPerUnit() or 1.0
                    cw_log, ch_log = self._model.GetCanvasSize()
                    cw_px, ch_px = cw_log * ppu, ch_log * ppu
                fit = abs(self._fit_scale)
                sx = abs(getattr(self, "_fit_scale_x", 1.0))
                self._model.SetScaleX(fit * sx * (1 if right else -1))
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
        # 注意：这里不调用 self._live2d.glRelease()。self._live2d 是进程级的 live2d.v3 模块，
        # 其 glRelease() 会释放全局 GL 状态。多宠场景下关闭某个 Live2D 宠就释放全局 GL，
        # 会导致其它仍在渲染的 Live2D 宠崩坏。GL 上下文由各自的 QOpenGLWidget 自行管理，
        # 进程退出时系统自动回收，因此单个 renderer 清理时不应释放全局 GL。
        try:
            if self._model is not None:
                self._model = None
            if self._live2d is not None:
                self._live2d = None
        except Exception:
            pass
