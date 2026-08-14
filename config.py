"""配置管理"""
import json
import os
import threading
import time

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG = {
    "character": "yuexinmiao",
    "dialog": {
        "agent_id": "",  # 对话后端绑定的 Hanako agent（空=未绑定，首次启动引导选择；不硬编码默认）
        # 每个桌宠实例可自定义绑定；多桌宠各自独立
    },
    "scale": 1.0,
    "opacity": 1.0,
    "behavior": "normal",
    "theme_mode": "auto",  # "auto" | "light" | "dark" — 主题模式（桌宠主题系统）
    "window": {
        "width": 200,
        "height": 300,
        "x": -1,
        "y": -1
    },
    "break_reminder": {
        "enabled": True,
        "idle_minutes": 15,
        "gradual": True,
        "cooldown_minutes": 30
    },
    "action_linker": {
        "enabled": True,
        "highlight_duration": 30
    },
    "tts": {
        "enabled": True,
        "volume": 0.8
    },
    "sfx": {
        "enabled": True,
        "volume": 0.5
    },
    "ui": {
        "onboarded": False  # 首屏引导是否已看过
    },
    "proactive": {
        "enabled": True,
        "cooldown_minutes": 10,
        "rules": [
            {
                "idle_min": 5,
                "foreground": ["writing", "development", "browsing"],
                "prompt": "写了这么久，休息一下吧？",
                "weight": 0.7
            },
            {
                "idle_min": 15,
                "foreground": ["gaming", "entertainment"],
                "prompt": "带我一起玩嘛～",
                "weight": 0.5
            },
            {
                "idle_min": 30,
                "foreground": ["communication"],
                "prompt": "还在忙吗？想和你说说话～",
                "weight": 0.3
            },
            {
                "idle_min": 60,
                "foreground": ["*"],
                "prompt": "好安静啊……你在做什么呢？",
                "weight": 0.3
            }
        ]
    },
    "window_interaction": {
        "enabled": True,
        "cooldown_seconds": 30
    },
    "memory": {
        "budget_chars": 0,
        "budget_percent": 1.0
    }
}

CHARACTER_INFO = {
    "default": {
        "name": "幽灵团子",
        "path": "characters/default",
    },
    "yuexinmiao": {
        "name": "月薪喵",
        "path": "characters/yuexinmiao",
    },
    "phoebe": {
        "name": "菲比",
        "path": "characters/phoebe",
    },
}

# 情绪 → 帧动画序列映射（P3 连续参数：帧区间）
# oc-pet 靠帧序列名切换表情，P3 增强后不同情绪映射到同一序列的不同帧子范围
# 格式: 情绪名 -> (序列名, 起始帧索引, 结束帧索引)
#          起始/结束为 None 时使用全序列
EXPRESSION_MAP = {
    "happy":      ("waving",   None, None),  # 开心 -> 挥手
    "surprised":  ("jumping",  None, None),  # 惊讶 -> 跳跃
    "angry":      ("jumping",  None, None),  # 生气 -> 复用跳跃（激烈动作）
    "sad":        ("failed",   None, None),  # 悲伤 -> 失败（低落动画）
    "thinking":   ("waiting",  None, None),  # 思考 -> 等待（张望）
    "working":    ("review",   None, None),  # 工作 -> 审阅
    "cute":       ("waving",   None, None),  # 卖萌 -> 复用挥手
    "missing":    ("waiting",  None, None),  # 张望 -> 等待
    "neutral":    ("idle",     None, None),  # 中性 -> 空闲
    "listening":  ("idle",     None, None),  # 倾听 -> 空闲
    "speaking":   ("idle",     None, None),  # 说话 -> 空闲
}

# 表情 -> 默认过渡样式（snap=瞬切/fade=缓出/spring=弹簧）
# 驱动 PetWindow._set_anim_seq 的 style 参数
# 选型依据：
#   - surprise/angry → 突变形情绪，弹簧反弹强化冲击
#   - happy/cute/thinking → 温和切换
#   - listening/speaking/working → 高频切勿过渡，保持同步感
#   - neutral/sad/missing → 默认缓出
EXPRESSION_TRANSITION_STYLE = {
    "happy":      "fade",
    "surprised":  "spring",
    "angry":      "spring",
    "sad":        "fade",
    "thinking":   "fade",
    "working":    "snap",
    "cute":       "fade",
    "missing":    "fade",
    "neutral":    "fade",
    "listening":  "snap",
    "speaking":   "snap",
}


def get_transition_style(emotion: str, default: str = "snap") -> str:
    """查表情过渡样式。未匹配返回 default。

    Args:
        emotion: 表情名（如 'happy'）
        default: 未匹配时的回退样式（默认 'snap'，保持向后兼容）
    Returns:
        'snap' | 'fade' | 'spring'
    """
    if not emotion:
        return default
    return EXPRESSION_TRANSITION_STYLE.get(emotion, default)

# atlas 模式的状态→动画映射（9 种动画）
# 如果 pet.json 的 emotions 字段存在，优先用它；否则用这个回退
ATLAS_STATE_MAP = {
    "idle":         "idle",
    "walking":      "running-right",
    "greeting":     "waving",
    "excited":      "jumping",
    "error":        "failed",
    "waiting":      "waiting",
    "thinking":     "review",
    "working":      "review",
    "done":         "review",
    "happy":        "waving",
    "surprised":    "jumping",
    "angry":        "jumping",
    "sad":          "failed",
    "cute":         "waving",
    "missing":      "waiting",
    "neutral":      "idle",
}

# Hanako 状态 → 桌宠动作
HANAKO_STATE_MAP = {
    "listening": {"anim": "idle", "desc": "倾听"},
    "thinking": {"anim": "extra", "desc": "思考"},
    "working": {"anim": "extra", "desc": "工作"},
    "speaking": {"anim": "idle", "desc": "说话", "bubble_bright": True},
}

def load_config():
    """加载配置，深度合并默认值（确保新增字段不丢失）"""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = _deep_merge(DEFAULT_CONFIG.copy(), cfg)
        return merged
    return DEFAULT_CONFIG.copy()


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：override 的键覆盖 base，但 base 独有的键保留。

    空值保护：override 的值为空字符串/None 时不覆盖 base 的已有非空值，
    避免旧快照用空值把真实配置（如 dialog.agent_id=aimis）冲掉。
    """
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        elif v is None or v == "":
            # 空值不覆盖已有非空值（保留 base 现值）
            if k in result:
                continue
            result[k] = v
        else:
            result[k] = v
    return result

def save_config(cfg):
    """原子写入配置文件（合并式）

    以磁盘现有内容为底，用传入 cfg 覆盖后写回。
    这样只更新调用方关心的字段，不会把其他系统（如 dialog.agent_id）
    用旧快照冲掉——避免 F5 绑定丢失问题。
    """
    import tempfile
    merged = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                merged = json.load(f)
    except Exception:
        merged = {}
    # 传入的 cfg 覆盖（含其内部 dict 键）
    for k, v in cfg.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), suffix='.tmp')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        # Windows 下目标文件可能被瞬时占用（杀软扫描/编辑器锁/其他进程），
        # os.replace 直接抛 PermissionError 会让设置保存崩溃。加短重试。
        for attempt in range(5):
            try:
                os.replace(tmp_path, CONFIG_PATH)  # 原子替换
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _AsyncConfigSaver:
    """防抖异步配置保存器 — 高频位置写入不阻塞 GUI 线程。

    设计：
      - 同一次调度周期内多次 schedule() 只落盘一次（保留最新配置）
      - 写盘在后台线程执行，绝不阻塞调用方（GUI 主线程）
      - 线程安全：schedule 可从任意线程调用

    用法：
        saver = AsyncConfigSaver()
        saver.schedule(cfg)   # 防抖 150ms 后异步写盘
    """

    def __init__(self, debounce_ms: int = 150):
        self._debounce = debounce_ms / 1000.0
        self._lock = threading.Lock()
        self._pending: dict | None = None          # 待写的最新配置
        self._due: float | None = None             # 下次写盘时刻（防抖窗口）
        self._thread: threading.Thread | None = None
        self._stop = False

    def schedule(self, cfg: dict) -> None:
        """登记一次保存。同窗口内多次调用合并为一次落地。"""
        with self._lock:
            self._pending = cfg
            now = time.monotonic()
            self._due = now + self._debounce
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self) -> None:
        """后台循环：等到防抖窗口，写盘最新快照。"""
        while True:
            with self._lock:
                if self._stop:
                    return
                now = time.monotonic()
                if self._pending is None:
                    return
                if now < self._due:
                    wait = self._due - now
                else:
                    wait = None
            if wait is not None:
                time.sleep(wait)
                continue
            # 窗口已到：取最新快照并写盘
            with self._lock:
                cfg = self._pending
                self._pending = None
            try:
                save_config(cfg)
            except Exception:
                pass
            # 若调度期间又有新值，继续循环；否则退出
            with self._lock:
                if self._pending is None:
                    return

    def shutdown(self) -> None:
        """停止并处理最后一次待写（进程退出前调用）。"""
        with self._lock:
            self._stop = True
            cfg = self._pending
            self._pending = None
        if cfg is not None:
            try:
                save_config(cfg)
            except Exception:
                pass


# 进程级共享实例：桌宠位置这类高频写入都走它，合并落盘。
async_config_saver = _AsyncConfigSaver()
