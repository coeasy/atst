# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""契约测试：PersistentSemanticCache (L2) + ErrorEnvelope。

从 PR #6 (refactor/runtime-integration-v12) 提取的独立工程原语，
在 v14 生态下独立运行，不依赖 v13 UnifiedRuntime。
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from tstdx.cache_persistent import PersistentSemanticCache
from tstdx.error_envelope import ErrorEnvelope, to_error_envelope
from tstdx.errors import RetryAdvice, TdxError, ValidationError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, ProvenanceKind, QueryResult, ResultMeta


def _plan(capability: str = "quotes", symbol: str = "sh600000", **kw: object) -> QueryPlan:
    base: dict[str, object] = {
        "capability": capability,
        "symbols": (symbol,),
        "provider": "tdx",
    }
    if capability == "bars":
        base["count"] = 30
        base["period"] = "day"
    base.update(kw)
    spec = QuerySpec(**base)
    return QueryPlanner().compile(spec)


def _result(plan: QueryPlan, payload: object) -> QueryResult:
    prov = Provenance(
        provider="tdx",
        channel="quotation",
        capability=plan.spec.capability,
        kind=ProvenanceKind.DIRECT,
        observed_at_ns=time.time_ns(),
    )
    meta = ResultMeta(
        provider="tdx",
        channel="quotation",
        capability=plan.spec.capability,
        fingerprint=plan.fingerprint.value,
        provenance=prov,
    )
    return QueryResult(data=payload, meta=meta)


# --------------------------------------------------------------------------- #
# PersistentSemanticCache (L2)
# --------------------------------------------------------------------------- #
class TestPersistentSemanticCache:
    def test_put_get_roundtrip(self) -> None:
        plan = _plan()
        result = _result(plan, [{"code": "sh600000", "price": 10.5}])

        with tempfile.TemporaryDirectory() as tmp:
            cache = PersistentSemanticCache(Path(tmp) / "l2.db")
            assert cache.put(plan, result, ttl=60.0)
            got = cache.get(plan)
            assert got is not None
            assert got.data == [{"code": "sh600000", "price": 10.5}]
            cache.close()

    def test_survives_reopen(self) -> None:
        plan = _plan()
        result = _result(plan, {"ok": True})

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "l2.db"
            cache = PersistentSemanticCache(db)
            assert cache.put(plan, result, ttl=60.0)
            cache.close()

            cache2 = PersistentSemanticCache(db)
            got = cache2.get(plan)
            assert got is not None, "持久化语义缓存重开后必须仍然可读"
            assert got.data == {"ok": True}
            cache2.close()

    def test_ttl_expiry(self) -> None:
        plan = _plan()
        result = _result(plan, {"v": 1})

        with tempfile.TemporaryDirectory() as tmp:
            cache = PersistentSemanticCache(Path(tmp) / "l2.db")
            now = time.time_ns()
            assert cache.put(plan, result, ttl=0.01, now_ns=now)
            # 1s later => expired
            assert cache.get(plan, now_ns=now + 1_000_000_000) is None
            cache.close()

    def test_tier_identity_check_rejects_mismatch(self) -> None:
        plan = _plan()
        # 用不匹配的 fingerprint 构造 result
        mismatched = _result(
            _plan(capability="bars", symbol="sh600000"), {"v": 2}
        )

        with tempfile.TemporaryDirectory() as tmp:
            cache = PersistentSemanticCache(Path(tmp) / "l2.db")
            try:
                # put 在身份不匹配时抛 ValueError，拒绝写入
                try:
                    cache.put(plan, mismatched, ttl=60.0)
                    raise AssertionError("身份不匹配必须抛 ValueError")
                except ValueError as exc:
                    assert "identity does not match" in str(exc)
                assert cache.get(plan) is None
            finally:
                cache.close()

    def test_invalidate(self) -> None:
        plan = _plan()
        result = _result(plan, {"v": 3})

        with tempfile.TemporaryDirectory() as tmp:
            cache = PersistentSemanticCache(Path(tmp) / "l2.db")
            assert cache.put(plan, result, ttl=60.0)
            assert cache.get(plan) is not None
            assert cache.invalidate(plan)
            assert cache.get(plan) is None
            cache.close()


# --------------------------------------------------------------------------- #
# ErrorEnvelope
# --------------------------------------------------------------------------- #
class TestErrorEnvelope:
    def test_from_tdx_error(self) -> None:
        exc = TdxError("boom", code="E5000", advice=RetryAdvice(retryable=False))
        env = to_error_envelope(exc)
        assert isinstance(env, ErrorEnvelope)
        assert env.code == "E5000"
        assert "boom" in str(env.message)
        assert not env.retryable

    def test_validation_error_code(self) -> None:
        exc = ValidationError("bad input")
        env = to_error_envelope(exc)
        assert env.code is not None

    def test_envelope_roundtrip_dict(self) -> None:
        exc = TdxError("boom", code="E5000")
        env = to_error_envelope(exc)
        data = env.to_dict()
        assert data["code"] == "E5000"
        assert "boom" in data["message"]

    def test_redacts_context_secrets(self) -> None:
        exc = TdxError(
            "boom",
            code="E5000",
            context={"token": "secret-value", "url": "https://example.com"},
        )
        env = to_error_envelope(exc)
        data = env.to_dict()
        # context 中的敏感字段不应出现在序列化结果中
        assert "secret-value" not in repr(data)