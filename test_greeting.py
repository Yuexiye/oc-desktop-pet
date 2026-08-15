"""单元测试 — core/greeting.py

运行: python -m pytest test_greeting.py -q
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.greeting import (
    build_greeting_text,
    check_first_launch,
    is_llm_unconfigured,
    mark_greeted,
    maybe_greet,
    _GREETING_CANDIDATES,
)


# ════════════════════════════════════════════════════════════
#  is_llm_unconfigured
# ════════════════════════════════════════════════════════════

class TestIsLlmUnconfigured:

    def test_no_env_key_returns_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert is_llm_unconfigured() is True

    def test_empty_string_key_returns_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_API_KEY", "")
        assert is_llm_unconfigured() is True

    def test_placeholder_xxx_returns_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_API_KEY", "xxx")
        assert is_llm_unconfigured() is True

    def test_placeholder_your_key_returns_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_API_KEY", "your_key")
        assert is_llm_unconfigured() is True

    def test_real_key_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-real-key-12345")
        assert is_llm_unconfigured() is False

    def test_env_with_spaces_stripped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_API_KEY", "  sk-abc  ")
        assert is_llm_unconfigured() is False


# ════════════════════════════════════════════════════════════
#  check_first_launch
# ════════════════════════════════════════════════════════════

class TestCheckFirstLaunch:

    @pytest.fixture
    def empty_config(self):
        return {"dialog": {"agent_id": ""}}

    def test_no_marker_no_llm_enabled_true(self, empty_config, tmp_path):
        marker = tmp_path / "greet_test.json"
        assert check_first_launch(empty_config, str(marker)) is True

    def test_already_marked_returns_false(self, empty_config, tmp_path):
        marker = tmp_path / "greet_test.json"
        marker.write_text("{}")
        assert check_first_launch(empty_config, str(marker)) is False

    def test_enabled_false_returns_false(self, empty_config, tmp_path):
        cfg = {**empty_config, "greeting": {"enabled": False}}
        marker = tmp_path / "greet_test.json"
        assert check_first_launch(cfg, str(marker)) is False

    def test_enabled_missing_defaults_true(self, empty_config, tmp_path):
        """greeting 键不存在时默认 enabled=True"""
        marker = tmp_path / "greet_test.json"
        assert check_first_launch(empty_config, str(marker)) is True

    def test_agent_id_set_returns_false(
        self, empty_config, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """agent_id 非空时即使 env 无 key 也不触发（说明有 Hanako 绑定）"""
        cfg = {**empty_config, "dialog": {"agent_id": "aimis"}}
        # 把 is_llm_unconfigured 强制返回 True（模拟无 .env key），
        # 但 agent_id 非空，应返回 False
        with patch("core.greeting.is_llm_unconfigured", return_value=True):
            marker = tmp_path / "greet_test.json"
            assert check_first_launch(cfg, str(marker)) is False

    def test_env_key_set_returns_false(
        self, empty_config, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """env 有真实 API key 时不触发"""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
        marker = tmp_path / "greet_test.json"
        assert check_first_launch(empty_config, str(marker)) is False


# ════════════════════════════════════════════════════════════
#  maybe_greet
# ════════════════════════════════════════════════════════════

class TestMaybeGreet:

    @pytest.fixture
    def base_config(self):
        return {"dialog": {"agent_id": ""}}

    def test_returns_tuple_on_first_launch(
        self, base_config, tmp_path
    ):
        marker = tmp_path / "greet_test.json"
        result = maybe_greet(base_config, str(marker), "月薪喵")
        assert result is not None
        text, emotion = result
        assert isinstance(text, str)
        assert emotion == "happy"
        assert "月薪喵" in text

    def test_marker_file_created_after_call(
        self, base_config, tmp_path
    ):
        marker = tmp_path / "greet_test.json"
        maybe_greet(base_config, str(marker), "月薪喵")
        assert marker.exists()
        data = json.loads(marker.read_text("utf-8"))
        assert "greeted_at" in data

    def test_second_call_returns_none(
        self, base_config, tmp_path
    ):
        marker = tmp_path / "greet_test.json"
        maybe_greet(base_config, str(marker), "月薪喵")
        assert maybe_greet(base_config, str(marker), "月薪喵") is None

    def test_enabled_false_returns_none(
        self, base_config, tmp_path
    ):
        cfg = {**base_config, "greeting": {"enabled": False}}
        marker = tmp_path / "greet_test.json"
        assert maybe_greet(cfg, str(marker), "月薪喵") is None


# ════════════════════════════════════════════════════════════
#  build_greeting_text
# ════════════════════════════════════════════════════════════

class TestBuildGreetingText:

    def test_returns_two_elements(self):
        text, emotion = build_greeting_text("测试猫")
        assert len((text, emotion)) == 2

    def test_emotion_is_happy(self):
        _, emotion = build_greeting_text("任意名")
        assert emotion == "happy"

    def test_text_contains_agent_name(self):
        text, _ = build_greeting_text("月薪喵")
        assert "月薪喵" in text

    def test_no_empty_candidate_strings(self):
        """文案池没有空字符串"""
        for candidate in _GREETING_CANDIDATES:
            assert len(candidate) > 0

    def test_all_candidates_have_placeholder(self):
        """每条候选文案都含 {myname} 占位符"""
        for candidate in _GREETING_CANDIDATES:
            assert "{myname}" in candidate
