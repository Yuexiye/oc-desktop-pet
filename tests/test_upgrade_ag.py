"""升级全景 A-G 纯逻辑单测（不依赖 Qt / GUI / Live2D）

覆盖：
- A EventStream：append/read 排序/坏行隔离/prune
- B CompanionMemory.record_event 情绪 provider 注入
- C scene_cluster.cluster_events：同 category/scenario 合并、跨天拆分、时长门槛
- C/D SceneMemory：rebuild 幂等 / find_matching / recent_scenes / prune
- E SceneMemory.associate 标签交集
- G PetStatusMapper：render_for 鸭子类型分派（fake renderer）+ celebrating 帧回退
- D scenarios.get_recall_reaction / get_associate_reaction

运行：
    export HOME=/c/Users/Administrator
    /c/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_upgrade_ag.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.event_stream import EventStream
from core.companion_memory import CompanionMemory
from core.perception.scene_cluster import Scene, cluster_events
from core.scene_memory import SceneMemory
from core.perception.scenarios import (
    get_recall_reaction, get_associate_reaction, RECALL_REACTIONS,
)
from core.pet_status import PetStatusMapper, PET_STATES


# ───────────────────────── A：EventStream ─────────────────────────

def _mk_stream(tmp_path, agent="tester"):
    return EventStream(agent, tmp_path)


def test_event_stream_append_and_read_sorted(tmp_path):
    s = _mk_stream(tmp_path)
    s.append({"ts": 3.0, "category": "gaming", "source": "topic"})
    s.append({"ts": 1.0, "category": "development", "source": "foreground"})
    s.append({"ts": 2.0, "category": "gaming", "source": "vision"})
    all_recs = s.read_all()
    assert [r["ts"] for r in all_recs] == [1.0, 2.0, 3.0]
    # 文件存在且为 JSONL
    assert s.path.exists()
    lines = s.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_event_stream_topic_truncation(tmp_path):
    s = _mk_stream(tmp_path)
    long_topic = "长" * 100
    s.append({"ts": 1.0, "category": "development", "topic": long_topic, "source": "topic"})
    rec = s.read_all()[0]
    assert len(rec["topic"]) <= 60


def test_event_stream_bad_line_isolation(tmp_path):
    """手动损坏一行后 read 不崩、其余行可读。"""
    s = _mk_stream(tmp_path)
    s.append({"ts": 1.0, "category": "gaming", "source": "topic"})
    s.append({"ts": 2.0, "category": "development", "source": "foreground"})
    s.path.write_text(
        s.path.read_text(encoding="utf-8").replace(
            '{"ts": 1.0', '{"ts": 1.0 broken-line'
        ), encoding="utf-8"
    )
    recs = s.read_all()
    assert len(recs) == 1
    assert recs[0]["category"] == "development"


def test_event_stream_read_since_and_range(tmp_path):
    s = _mk_stream(tmp_path)
    s.append({"ts": 10.0, "category": "a"})
    s.append({"ts": 20.0, "category": "b"})
    s.append({"ts": 30.0, "category": "c"})
    assert [r["category"] for r in s.read_since(20.0)] == ["b", "c"]
    assert [r["category"] for r in s.read_range(15.0, 25.0)] == ["b"]


def test_event_stream_prune_by_days(tmp_path):
    s = _mk_stream(tmp_path)
    now = time.time()
    s.append({"ts": now, "category": "new"})
    s.append({"ts": now - 40 * 86400, "category": "old"})
    removed = s.prune(max_days=30, max_entries=5000)
    assert removed == 1
    assert [r["category"] for r in s.read_all()] == ["new"]


def test_event_stream_prune_by_entries(tmp_path):
    s = _mk_stream(tmp_path)
    now = time.time()
    for i in range(10):
        s.append({"ts": now - (9 - i) * 60, "category": f"c{i}"})
    removed = s.prune(max_days=30, max_entries=3)
    assert removed == 7
    assert len(s.read_all()) == 3


# ───────────────────────── B：CompanionMemory.record_event ─────────────────────────

def test_companion_record_event_with_emotion_provider(tmp_path):
    mem = CompanionMemory("tester", memory_dir=tmp_path)
    s = EventStream("tester", tmp_path)
    mem.set_event_stream(s)
    mem.set_emotion_provider(lambda: "happy")
    mem.record_event(category="gaming", start_ts=1.0, end_ts=2.0, source="foreground")
    rec = s.read_all()[0]
    assert rec["category"] == "gaming"
    assert rec["emotion"] == "happy"
    assert rec["source"] == "foreground"


def test_companion_record_event_no_stream_is_noop(tmp_path):
    """未注入 EventStream 时 record_event 为空操作（回滚安全）。"""
    mem = CompanionMemory("tester", memory_dir=tmp_path)
    mem.record_event(category="gaming", source="foreground")
    assert not (tmp_path / "tester_events.jsonl").exists()


def test_companion_legacy_fields_unchanged(tmp_path):
    """旧字段/接口语义不变：record_activity / record_topic / today 照常。"""
    mem = CompanionMemory("tester", memory_dir=tmp_path)
    mem.record_activity("development")
    mem.record_topic("你昨天说的那个项目后来怎么样了？")
    assert mem._today.get("development", 0) == 1
    assert "development" in mem.day_summary()
    assert mem.last_topic == "你昨天说的那个项目后来怎么样了？"
    mem.close()
    assert (tmp_path / "tester.json").exists()


# ───────────────────────── C：cluster_events ─────────────────────────

def _ev(ts, category="gaming", scenario="gaming", emotion="happy", topic=""):
    return {"ts": float(ts), "start_ts": float(ts), "end_ts": float(ts),
            "category": category, "scenario": scenario,
            "emotion": emotion, "topic": topic, "source": "foreground"}


def _mk_scene_events(day_offset_days, category="gaming", scenario="gaming",
                     emotion="happy", topic="", n=5, span_min=25):
    """生成一段跨 ≥20 分钟的场景事件（满足 cluster_events 时长门槛）。

    用当天 12:00 作基准，避免跨午夜导致 day_rollover 拆分（确定性）。
    """
    import datetime
    today_noon = datetime.datetime.combine(
        datetime.date.today(), datetime.time(12, 0)
    ).timestamp()
    base = today_noon - day_offset_days * 86400
    events = []
    for i in range(n):
        ts = base + i * (span_min * 60.0 / max(1, n - 1))
        events.append(_ev(ts, category, scenario, emotion, topic))
    return events


def test_cluster_events_merges_same_category_scenario(tmp_path=None):
    # 两段场景放不同天（12:00 基准），避免时间交织；n=5 保证相邻间隔 ≤ gap_min
    events = _mk_scene_events(2, "gaming", "gaming", "happy", "打到第三关") + \
        _mk_scene_events(1, "development", "late_night_work", "anxious", n=5, span_min=22)
    scenes = cluster_events(events, min_duration_min=1, gap_min=10)
    assert len(scenes) == 2
    gaming = next(s for s in scenes if s.category == "gaming")
    assert gaming.count == 5
    assert gaming.emotion_summary == "happy"
    work = next(s for s in scenes if s.category == "development")
    assert work.label == "深夜加班"


def test_cluster_events_min_duration_gate(tmp_path=None):
    """合并段时长 < min_duration_min 不成场景。"""
    now = time.time()
    events = [_ev(now, "gaming", "gaming")]
    scenes = cluster_events(events, min_duration_min=20, gap_min=10)
    assert scenes == []


def test_cluster_events_day_rollover_split(tmp_path=None):
    """跨天强制拆分：同 category/scenario 但不同天 → 两条场景。"""
    # 用固定时间戳跨天（00:00 前后），各 2 个事件跨 ≥1 分钟
    import datetime
    day1 = datetime.datetime(2026, 8, 16, 23, 50).timestamp()
    day2 = datetime.datetime(2026, 8, 17, 0, 10).timestamp()
    events = [
        _ev(day1, "gaming", "gaming"),
        _ev(day1 + 60, "gaming", "gaming"),
        _ev(day2, "gaming", "gaming"),
        _ev(day2 + 60, "gaming", "gaming"),
    ]
    scenes = cluster_events(events, min_duration_min=0, gap_min=30, day_rollover=True)
    assert len(scenes) == 2
    scenes_no_roll = cluster_events(events, min_duration_min=0, gap_min=30, day_rollover=False)
    assert len(scenes_no_roll) == 1


def test_cluster_events_topics_capped(tmp_path=None):
    now = time.time()
    events = [_ev(now + i * 10, topic=f"话题{i}") for i in range(8)]
    scenes = cluster_events(events, min_duration_min=1, gap_min=10)
    assert scenes
    assert len(scenes[0].topics) <= 5


# ───────────────────────── C/D：SceneMemory ─────────────────────────

def test_scene_memory_rebuild_idempotent(tmp_path):
    sm = SceneMemory("tester", memory_dir=tmp_path)
    now = time.time()
    events = [
        _ev(now, "gaming", "gaming", topic="打到第三关"),
        _ev(now + 60, "gaming", "gaming"),
        _ev(now + 25 * 60, "development", "late_night_work", emotion="anxious"),
    ]
    n1 = sm.rebuild(events)
    n2 = sm.rebuild(events)  # 幂等：重复 rebuild 不翻倍
    assert n1 == n2
    assert (tmp_path / "tester_scenes.json").exists()


def test_scene_memory_find_matching(tmp_path):
    sm = SceneMemory("tester", memory_dir=tmp_path)
    events = _mk_scene_events(3, "gaming", "gaming", "happy", "上次的存档")
    sm.rebuild(events)
    matches = sm.find_matching("gaming", "gaming", ["gaming", "gaming", "晚上", "happy"])
    assert matches
    assert matches[0].category == "gaming"


def test_scene_memory_recent_and_prune(tmp_path):
    sm = SceneMemory("tester", memory_dir=tmp_path)
    events = _mk_scene_events(0, "gaming", "gaming") + \
        _mk_scene_events(100, "development", "late_night_work", n=5, span_min=22)
    sm.rebuild(events)
    assert len(sm.recent_scenes(5)) == 2
    removed = sm.prune(max_days=90, max_scenes=500)
    assert removed == 1
    assert all(s.category != "development" for s in sm.scenes)


def test_scene_memory_load_corrupted_uses_empty(tmp_path):
    path = tmp_path / "tester_scenes.json"
    path.write_text("{broken json", encoding="utf-8")
    sm = SceneMemory("tester", memory_dir=tmp_path)
    assert sm.scenes == []


# ───────────────────────── E：associate ─────────────────────────

def test_scene_memory_associate_tag_intersection(tmp_path):
    sm = SceneMemory("tester", memory_dir=tmp_path)
    events = _mk_scene_events(2, "gaming", "gaming", "happy", "通了那关")
    sm.rebuild(events)
    current = {"category": "video_watching", "scenario": "video_watching",
               "emotion": "happy", "period": "晚上"}
    hit = sm.associate(current, sm.recent_scenes(20))
    # gaming 与 video_watching 是相邻分类（CATEGORY_NEIGHBORS）→ 应命中
    assert hit is not None
    assert hit.category == "gaming"


def test_scene_memory_associate_excludes_direct_match(tmp_path):
    sm = SceneMemory("tester", memory_dir=tmp_path)
    events = _mk_scene_events(1, "gaming", "gaming", "happy")
    sm.rebuild(events)
    current = {"category": "gaming", "scenario": "gaming", "emotion": "happy", "period": "晚上"}
    hit = sm.associate(current, sm.recent_scenes(20))
    assert hit is None  # 同场景直匹配是 D 的活，E 排除


# ───────────────────────── D：recall reactions ─────────────────────────

def test_get_recall_reaction_with_topic():
    scene = Scene(
        scene_id="gaming|gaming|2026-08-16", label="打游戏", category="gaming",
        scenario="gaming", tags=["gaming", "gaming", "晚上", "happy"],
        first_ts=0.0, last_ts=1.0, count=2, duration_min=10.0,
        emotion_summary="happy", topics=["上次的存档"],
    )
    text = get_recall_reaction(scene)
    assert text is not None
    assert "上次" in text or "还记得" in text or "卡了好久" in text
    # 未知场景 → None（调用方保持沉默）
    unknown = Scene(
        scene_id="x", label="x", category="unknown", scenario="unknown",
        tags=[], first_ts=0.0, last_ts=1.0, count=1, duration_min=1.0,
        emotion_summary="neutral", topics=[],
    )
    assert get_recall_reaction(unknown) is None


def test_get_associate_reaction():
    scene = Scene(
        scene_id="gaming|gaming|2026-08-16", label="打游戏", category="gaming",
        scenario="gaming", tags=[], first_ts=0.0, last_ts=1.0, count=1,
        duration_min=1.0, emotion_summary="happy", topics=["通了那关"],
    )
    text = get_associate_reaction(scene)
    assert text is not None
    assert "上次" in text


# ───────────────────────── G：PetStatusMapper ─────────────────────────

class _FakeSpriteRenderer:
    """模拟 SpriteRenderer（鸭子类型：有 _frames / play_anim / set_emotion）"""
    def __init__(self, frames=None):
        self._frames = frames if frames is not None else {"idle": [], "jumping": [], "waving": []}
        self.played = []
        self.emotions = []

    def play_anim(self, anim, emotion="", frame_range=None):
        self.played.append((anim, emotion))

    def set_emotion(self, emotion, intensity=1.0):
        self.emotions.append(emotion)


class _FakeLive2DRenderer:
    """模拟 Live2DRenderer（鸭子类型：有 _model + _frames，必须先判 _model）"""
    def __init__(self):
        self._model = object()
        self._frames = {}
        self.played = []
        self.emotions = []

    def play_anim(self, anim, emotion="", frame_range=None):
        self.played.append((anim, emotion))

    def set_emotion(self, emotion, intensity=1.0):
        self.emotions.append(emotion)


class _FakeVRMRenderer:
    """模拟 VRMRenderer 占位（vrm_renderer.py 真实形态：unsupported=True + _frames 兼容占位）"""
    def __init__(self):
        self._frames = {"idle": []}   # VRM 也有 _frames（架构师复核确认）
        self.unsupported = True
        self.emotions = []
        self.played = []

    def set_emotion(self, emotion, intensity=1.0):
        self.emotions.append(emotion)

    def play_anim(self, anim, emotion="", frame_range=None):
        self.played.append((anim, emotion))


def test_pet_status_states():
    assert PET_STATES == ("idle", "working", "review", "waiting", "failed", "celebrating")


def test_mapper_set_state_and_current():
    m = PetStatusMapper()
    assert m.current() == "idle"
    m.set_state("celebrating")
    assert m.current() == "celebrating"
    m.set_state("不存在的状态")
    assert m.current() == "idle"


def test_mapper_sprite_dispatch_and_celebrate_fallback():
    mapper = PetStatusMapper()
    r = _FakeSpriteRenderer(frames={"idle": [], "jumping": [], "waving": []})
    mapper.render_for("celebrating", r)
    # celebrate 序列不存在 → 回退 jumping
    assert r.played == [("jumping", "happy")]
    assert r.emotions == ["happy"]

    r2 = _FakeSpriteRenderer(frames={"idle": [], "celebrate": [], "waving": []})
    mapper.render_for("celebrating", r2)
    assert r2.played == [("celebrate", "happy")]


def test_mapper_live2d_dispatch():
    """Live2D 有 _frames 也有 _model → 必须先按 _model 分派（设计修正点）。"""
    mapper = PetStatusMapper()
    r = _FakeLive2DRenderer()
    mapper.render_for("celebrating", r)
    assert r.played == [("complete", "happy")]
    assert r.emotions == ["happy"]
    mapper.render_for("failed", r)
    assert r.played[-1] == ("failed", "sad")


def test_mapper_vrm_placeholder_no_crash():
    """VRM 占位（有 _frames + unsupported=True）→ 走 VRM 降级分支：只 set_emotion，不调 play_anim。"""
    mapper = PetStatusMapper()
    r = _FakeVRMRenderer()
    mapper.render_for("celebrating", r)
    assert r.emotions == ["happy"]
    assert r.played == []  # 关键：VRM 不播动画（不会被误判进 Sprite 分支）
    mapper.render_for(None, None)  # 空渲染器安全


def test_mapper_sprite_still_goes_sprite_branch():
    """Sprite 桩（有 _frames、无 _model、无 unsupported）→ 仍走 Sprite 分支（播动画）。"""
    mapper = PetStatusMapper()
    r = _FakeSpriteRenderer(frames={"idle": [], "jumping": [], "waving": []})
    mapper.render_for("idle", r)
    assert r.played == [("idle", "neutral")]  # Sprite 分支会调 play_anim


# ───────────────────────── 接线点集成（沿用 test_fix_batch1 源码断言风格）────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_pet_wiring_points_exist():
    """A/C/F 接线点真实存在：注入 provider / 订阅 activity_event / closeEvent 聚类。"""
    pet_src = _read("pet.py")
    assert "self._companion_memory.set_event_stream(self._event_stream)" in pet_src
    assert "self._companion_memory.set_emotion_provider(" in pet_src
    assert 'EventBus.on("activity_event", self._on_activity_event)' in pet_src
    assert 'EventBus.on("pet_set_mode", self._on_pet_set_mode)' in pet_src
    assert "scene_memory.rebuild(events)" in pet_src
    assert "mem.prune_events()" in pet_src
    assert "self._status_mapper" in pet_src
    assert "tts_celebration_signal" in pet_src
    assert "pet_set_mode_signal" in pet_src


def test_screen_activity_event_emit_exists():
    """A：screen.py append 后 emit activity_event（不记录 summary 文本）。"""
    src = _read("core/perception/screen.py")
    assert 'EventBus.emit("activity_event", event=activity)' in src


def test_chat_mixin_record_event_exists():
    """A：_record_topic 追加写事件流（source=topic）。"""
    src = _read("pet_mixins/chat_mixin.py")
    assert 'mem.record_event(category=category, topic=text, source="topic")' in src


def test_bubble_celebrating_branch_before_safe_anims():
    """G：celebrating 分支在 safe_anims 收窄之前。"""
    src = _read("pet_mixins/bubble_mixin.py")
    assert 'if state == "celebrating":' in src
    assert "_do_celebrating()" in src
    assert "_synth_celebration_tts" in src
    assert "_do_tts_celebration" in src
    # 分支必须在 safe_anims 之前
    branch_pos = src.index('if state == "celebrating":')
    safe_pos = src.index("safe_anims = ['idle', 'walk', 'extra']")
    assert branch_pos < safe_pos


def test_hanako_monitor_celebrating_and_label():
    """G：tool_end success → mood=celebrating；状态标签含 🎉。"""
    src = _read("core/hanako_monitor.py")
    assert 'mood = "celebrating"' in src
    assert '"celebrating": "🎉 完成"' in src


def test_proactive_recall_wiring():
    """D/E：proactive 有 set_scene_memory/_try_recall，tick 内意图→回忆→规则。"""
    src = _read("core/perception/proactive.py")
    assert "def set_scene_memory" in src
    assert "def _try_recall" in src
    assert "def load_memory_config" in src
    assert "get_recall_reaction" in src
    assert "get_associate_reaction" in src
    # 调用顺序：intent → recall → rules
    intent_pos = src.index("_try_intent(now, signals)")
    recall_pos = src.index("_try_recall(now, signals)")
    assert intent_pos < recall_pos


def test_config_new_sections():
    """D/E/G/F 配置段存在且有默认值。"""
    src = _read("config.py")
    assert '"recall"' in src and '"cooldown_minutes": 30' in src
    assert '"associate"' in src
    assert '"celebrating"' in src and '"tts_enabled": True' in src
    assert '"state_http"' in src and '"allow_set_mode": False' in src


def test_pet_init_companion_memory_harness(tmp_path, monkeypatch):
    """端到端轻量接线：用 stub 对象调 PetWindow._init_companion_memory。

    验证不启动 GUI/Live2D 的情况下：EventStream/SceneMemory/PetStatusMapper 被创建、
    proactive 注入 scene_memory、订阅事件总线、旧 JSON 不破坏。
    """
    import datetime
    # 让 memory 目录落到临时目录，避免污染真实 ~/.oc-pet
    for mod_name in ("core.companion_memory", "core.event_stream", "core.scene_memory"):
        mod = __import__(mod_name, fromlist=["DEFAULT_MEMORY_DIR"])
        monkeypatch.setattr(mod, "DEFAULT_MEMORY_DIR", tmp_path)

    from core.event_bus import EventBus
    EventBus.clear()
    try:
        from pet import PetWindow

        class _EmotionStub:
            @property
            def current(self):
                return "happy"

        class _PerceptionStub:
            emotion = _EmotionStub()
            proactive = None

            def set_scene_memory(self, sm):
                self._sm = sm

        class _ProactiveStub:
            def __init__(self):
                self.scene_memory = None
                self.mem_cfg = None

            def set_scene_memory(self, sm):
                self.scene_memory = sm

            def load_memory_config(self, cfg):
                self.mem_cfg = cfg

        class _Stub:
            pass

        stub = _Stub()
        stub._agent_id = "harness"
        stub.config = {"memory": {"recall": {"enabled": True, "cooldown_minutes": 30},
                                  "associate": {"enabled": True}},
                       "state_http": {"enabled": False}}
        stub._perception = _PerceptionStub()
        stub._proactive = _ProactiveStub()
        stub._foreground_watcher = _Stub()  # 哑对象：on_change 可赋值
        stub._last_fg_start_ts = 0.0
        stub._show_bubble = lambda *a, **k: None
        stub._on_foreground_change = lambda *a, **k: None
        # 把 PetWindow 的相关方法绑定到 stub（不实例化 QWidget）
        for name in ("_on_activity_event", "_on_pet_set_mode", "_init_status_http",
                     "_status_snapshot", "_renderer_format", "_do_pet_set_mode",
                     "_on_foreground_change_with_memory"):
            setattr(stub, name, getattr(PetWindow, name).__get__(stub, _Stub))

        PetWindow._init_companion_memory(stub)

        assert stub._companion_memory is not None
        assert stub._event_stream is not None
        assert stub._scene_memory is not None
        assert stub._status_mapper is not None
        assert stub._proactive.scene_memory is stub._scene_memory
        assert EventBus.subscriber_count("activity_event") == 1
        assert EventBus.subscriber_count("pet_set_mode") == 1
        # 前台变化接线：写活动到旧 JSON + 写事件流（B 情绪由 provider 自动填）
        stub._on_foreground_change_with_memory("app.exe", "development", "标题")
        # 旧 JSON 兼容：close 后存在
        stub._companion_memory.close()
        assert (tmp_path / "harness.json").exists()
        # 事件流文件出现且含情绪标签
        events_path = tmp_path / "harness_events.jsonl"
        assert events_path.exists()
        recs = stub._event_stream.read_all()
        assert recs and recs[0]["category"] == "development"
        assert recs[0]["emotion"] == "happy"
        assert recs[0]["source"] == "foreground"
        # 退订（closeEvent 路径）
        PetWindow.closeEvent  # 仅确认存在
        EventBus.off("activity_event", stub._on_activity_event)
        EventBus.off("pet_set_mode", stub._on_pet_set_mode)
        assert EventBus.subscriber_count("activity_event") == 0
    finally:
        EventBus.clear()
