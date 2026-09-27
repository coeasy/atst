"""F4 feedback 包单测（该包此前零测试）：环形缓冲上限、遥测深拷贝与
丢弃计数、上报脱敏键名与混合类型集合排序。全部离线。"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from atst.errors import ConnectionFailed, RetryAdvice, TdxError  # noqa: E402
from atst.feedback.reporter import (  # noqa: E402
    FeedbackReporter,
    _sanitize_value,
)
from atst.feedback.stats import UserStats  # noqa: E402
from atst.feedback.telemetry import TelemetryCollector  # noqa: E402


# --------------------------------------------------------------------------- #
# UserStats：环形缓冲（修复死分支 / max 生效）
# --------------------------------------------------------------------------- #
class TestUserStatsRingBuffer:
    def test_ring_buffer_capped_at_max(self):
        stats = UserStats(max_latency_samples=5)
        for i in range(20):
            stats.record_command("0x0530", float(i))
        assert len(stats._latency_samples) == 5  # 不再无界增长

    def test_ring_buffer_overwrite_keeps_latest(self):
        """容量 5 写入 0..9 → 覆盖写保留最后 5 条 [5,6,7,8,9]。"""
        stats = UserStats(max_latency_samples=5)
        for i in range(10):
            stats.record_command("cmd", float(i))
        assert stats._latency_samples == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_max_latency_samples_validation(self):
        with pytest.raises(ValueError):
            UserStats(max_latency_samples=0)

    def test_stats_snapshot_shape(self):
        stats = UserStats()
        stats.record_command("0x0530", 12.5)
        stats.record_error("ConnectionFailed")
        snap = stats.snapshot()
        assert snap["total_commands"] == 1
        assert snap["total_errors"] == 1
        assert snap["errors_by_type"] == {"ConnectionFailed": 1}
        assert snap["average_latency_ms"] == 12.5
        json.dumps(snap)

    def test_error_counting(self):
        stats = UserStats()
        stats.record_error("E2010")
        stats.record_error("E2010")
        assert stats.total_errors == 2
        assert stats.errors_by_type["E2010"] == 2


# --------------------------------------------------------------------------- #
# TelemetryCollector：深拷贝 / 禁用丢弃计数 / 上限
# --------------------------------------------------------------------------- #
class TestTelemetryCollector:
    def test_events_returns_deep_copy(self):
        """events() 返回的 dict 与内层 properties 均为拷贝（修共享引用假拷贝）。"""
        c = TelemetryCollector(max_size=10)
        c.enable()
        c.record_event("command", {"k": "orig"})
        ev = c.events()[0]
        ev["properties"]["k"] = "mutated"
        ev["type"] = "hacked"
        assert c.events()[0]["properties"]["k"] == "orig"
        assert c.events()[0]["type"] == "command"

    def test_events_filtered_type_also_deep_copy(self):
        c = TelemetryCollector(max_size=10)
        c.enable()
        c.record_event("error", {"code": 1})
        ev = c.events("error")[0]
        ev["properties"]["code"] = 999
        assert c.events("error")[0]["properties"]["code"] == 1

    def test_disabled_records_counted_as_dropped(self):
        """禁用期间 record_event 计入 total_dropped（可观测的静默丢弃）。"""
        c = TelemetryCollector(max_size=10)
        assert c.enabled is False
        for _ in range(3):
            c.record_event("command", {"k": 1})
        assert c.buffer_size == 0
        assert c.total_recorded == 0
        assert c.total_dropped == 3

    def test_max_size_cap_evicts_oldest(self):
        """环形缓冲上限：记录 5 条、容量 3 → 保留最新 3 条，丢弃 2 条。"""
        c = TelemetryCollector(max_size=3)
        c.enable()
        for i in range(5):
            c.record_event("command", {"i": i})
        assert c.buffer_size == 3
        assert c.total_recorded == 5
        assert c.total_dropped == 2
        assert [e["properties"]["i"] for e in c.events()] == [2, 3, 4]

    def test_flush_keeps_counters(self):
        seen: list = []
        c = TelemetryCollector(max_size=3, on_flush=seen.append)
        c.enable()
        for i in range(3):
            c.record_event("command", {"i": i})
        c.flush()
        assert len(seen[0]) == 3
        assert c.buffer_size == 0
        assert c.total_recorded == 3


# --------------------------------------------------------------------------- #
# FeedbackReporter：脱敏键名 / 混合类型集合 / payload 形态
# --------------------------------------------------------------------------- #
class TestReporterSanitize:
    def test_dict_keys_are_sanitized(self):
        """键名含路径/主机名 PII → 键被脱敏（此前漏 key）。"""
        out = _sanitize_value(
            {
                "C:\\Users\\john\\secret.txt": "v",
                "mail.example.com": 2,
                "safe_key": 3,
            }
        )
        assert set(out) == {"[REDACTED_PATH]", "[REDACTED_HOST]", "safe_key"}
        assert out["safe_key"] == 3

    def test_nested_keys_sanitized(self):
        out = _sanitize_value({"ctx": {"C:\\Users\\a\\b.log": 1}})
        assert list(out["ctx"]) == ["[REDACTED_PATH]"]

    def test_mixed_type_set_sorted_without_typeerror(self):
        """{1, 'a', 3} 混合类型集合不再 sorted TypeError：数值在前，str 归一。"""
        out = _sanitize_value({3, "a", 1})
        assert out == [1, 3, "a"]

    def test_homogeneous_set_still_sorted(self):
        assert _sanitize_value({"b", "a"}) == ["a", "b"]
        assert _sanitize_value({3, 1, 2}) == [1, 2, 3]

    def test_error_payload_shape_and_redaction(self):
        reporter = FeedbackReporter()
        exc = ConnectionFailed("主站 119.147.15.13 超时")
        payload = reporter._build_error_payload(
            exc, context={"host": "push.example.com", "port": 7709}
        )
        # 形态：事件骨架 + 7 步元数据
        for key in (
            "event",
            "error_type",
            "error_code",
            "message",
            "advice",
            "context",
            "timestamp",
            "version",
            "platform",
        ):
            assert key in payload
        assert payload["event"] == "error"
        assert payload["error_type"] == "ConnectionFailed"
        assert payload["error_code"] == ConnectionFailed.code
        # 脱敏生效
        text = json.dumps(payload, ensure_ascii=False, default=str)
        assert "119.147.15.13" not in text
        assert "push.example.com" not in text
        assert "[REDACTED_IP]" in text
        assert "[REDACTED_HOST]" in text

    def test_error_payload_keeps_tdx_advice(self):
        reporter = FeedbackReporter()
        exc = TdxError(
            "boom",
            code="E2010",
            advice=RetryAdvice(retryable=True, backoff=1.5),
        )
        payload = reporter._build_error_payload(exc)
        assert payload["advice"] == {"retryable": True, "backoff": 1.5}

    def test_report_disabled_returns_false_no_send(self, monkeypatch):
        monkeypatch.delenv("ATST_FEEDBACK", raising=False)
        monkeypatch.delenv("ATST_FEEDBACK_ENDPOINT", raising=False)
        reporter = FeedbackReporter()
        assert reporter.enabled is False
        assert reporter.report_error(RuntimeError("x")) is False
        assert reporter.report_usage("bars", 1.0, "ok") is False
