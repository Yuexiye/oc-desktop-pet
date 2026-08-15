"""SpriteRenderer 自动化测试（offscreen）。

覆盖加载缺口（爱莉丝 B3a 验收指出）：
1. pet.json spritesheet 格式（frameWidth/frameHeight 裁剪 + 动画序列）
2. atlas 格式（8x9 网格 + animations 行映射）
3. 分帧 frames/ 目录（自动发现动画子目录）
4. 损坏/缺失角色目录 → fallback 不崩

用法：QT_QPA_PLATFORM=offscreen python -m pytest test_renderer.py -q
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    return a


@pytest.fixture()
def widget(app):
    return QWidget()


def _make_png(path: Path, w: int, h: int, color=0xFF4488CC):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(color)
    assert img.save(str(path), "PNG")
    return path


def _make_sprite_dir(style="spritesheet"):
    """构造临时角色目录，返回 (dir, pet_json_path)。

    style:
      - spritesheet: 单 sheet + frameWidth/Height + animations
      - atlas: 8x3 网格 + animations(row)
    """
    d = Path(tempfile.mkdtemp(prefix="sprite_test_"))
    if style == "spritesheet":
        # spritesheet: 2 列 x 2 行，每帧 32x32 => 64x64
        _make_png(d / "spritesheet.png", 64, 64)
        pet = {
            "id": "test_sprite",
            "name": "Test",
            "spritesheet": {
                "src": "spritesheet.png",
                "frameWidth": 32,
                "frameHeight": 32,
                "scale": 1.0,
            },
            "animations": {
                "idle": {"start": 0, "count": 2, "fps": 3},
                "walk": {"start": 2, "count": 2, "fps": 4},
            },
        }
        (d / "pet.json").write_text(
            __import__("json").dumps(pet, ensure_ascii=False), encoding="utf-8")
    elif style == "atlas":
        # atlas: 8 列 x 3 行，每格 32x32 => 256x96
        _make_png(d / "atlas.png", 256, 96)
        pet = {
            "id": "test_atlas",
            "name": "TestAtlas",
            "atlas": {"src": "atlas.png", "columns": 8, "rows": 3,
                      "cellWidth": 32, "cellHeight": 32},
            "animations": {
                "idle": {"row": 0, "frames": 4, "fps": 2},
                "happy": {"row": 1, "frames": 2, "fps": 3},
            },
            "emotions": {"happy": {"anim": "happy"}},
        }
        (d / "pet.json").write_text(
            __import__("json").dumps(pet, ensure_ascii=False), encoding="utf-8")
    elif style == "frames":
        # frames/<anim>/ 分帧目录
        for anim, n in (("idle", 3), ("wave", 2)):
            ad = d / "frames" / anim
            ad.mkdir(parents=True)
            for i in range(n):
                _make_png(ad / f"{i:02d}.png", 24, 24, 0xFF112233 + i * 0x10101)
    else:
        raise ValueError(style)
    return d


def test_load_spritesheet(widget, app):
    d = _make_sprite_dir("spritesheet")
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer(widget)
    ok = r.load("test_sprite", sprite_dir=str(d))
    assert ok, "spritesheet 加载失败"
    assert "idle" in r._frames and "walk" in r._frames
    assert len(r._frames["idle"]) == 2, f"idle 应 2 帧, got {len(r._frames['idle'])}"
    assert len(r._frames["walk"]) == 2
    assert r._seq_fps.get("idle") == 3
    # 帧尺寸：32x32
    f = r._frames["idle"][0]
    assert f.width() == 32 and f.height() == 32, f"帧尺寸 {f.width()}x{f.height()}"
    # 播放不崩
    r.play_anim("idle")
    r._anim_tick()
    r.look_at(10, 10)
    print("[ok] spritesheet + 播放")


def test_load_atlas(widget, app):
    d = _make_sprite_dir("atlas")
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer(widget)
    ok = r.load("test_atlas", sprite_dir=str(d))
    assert ok, "atlas 加载失败"
    assert "idle" in r._frames and "happy" in r._frames
    assert len(r._frames["idle"]) == 4
    assert len(r._frames["happy"]) == 2
    # 情绪映射
    assert r._emotion_ranges.get("happy") == "happy"
    f = r._frames["idle"][0]
    assert f.width() == 32 and f.height() == 32
    print("[ok] atlas 网格 + 情绪映射")


def test_load_frames_dir(widget, app):
    d = _make_sprite_dir("frames")
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer(widget)
    ok = r.load("test_frames", sprite_dir=str(d))
    assert ok, "frames/ 加载失败"
    assert "idle" in r._frames and "wave" in r._frames
    assert len(r._frames["idle"]) == 3
    assert len(r._frames["wave"]) == 2
    print("[ok] frames/ 自动发现")


def test_missing_dir_fallback(widget, app):
    """角色目录缺失 → fallback 加载，不抛异常"""
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer(widget)
    # 不存在的角色：load 返回 False（fallback 图），但不应崩溃
    ok = r.load("__no_such_char__")
    # fallback 后至少有一个动画可用（fallback 帧）
    assert "idle" in r._frames or not ok
    r.play_anim("idle")  # 无论 fallback 与否播放不崩
    print("[ok] 缺失目录 fallback 不崩")


def test_corrupt_pet_json(widget, app):
    """pet.json 损坏 → 静默回退 frames/ 或返回 False，不抛异常"""
    d = Path(tempfile.mkdtemp(prefix="sprite_bad_"))
    (d / "pet.json").write_text("{ not valid json !!", encoding="utf-8")
    (d / "frames" / "idle").mkdir(parents=True)
    _make_png(d / "frames" / "idle" / "00.png", 24, 24)
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer(widget)
    ok = r.load("bad_char", sprite_dir=str(d))
    assert "idle" in r._frames
    print("[ok] 损坏 pet.json 回退 frames/")