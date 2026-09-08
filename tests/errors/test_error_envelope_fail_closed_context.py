from __future__ import annotations

from tstdx.error_envelope import to_error_envelope
from tstdx.errors import SourceUnavailable


def test_legacy_fallback_flags_cannot_contradict_canonical_envelope() -> None:
    envelope = to_error_envelope(
        SourceUnavailable(
            "legacy source unavailable",
            context={
                "provider": "tdx",
                "channel": "quotation",
                "capability": "quotes",
                "fallback": True,
                "fallback_allowed": True,
                "provider_switch_allowed": True,
            },
        )
    ).to_dict()

    assert envelope["fallback_allowed"] is False
    assert envelope["provider_switch_allowed"] is False
    assert envelope["context"]["fallback"] is False
    assert envelope["context"]["fallback_allowed"] is False
    assert envelope["context"]["provider_switch_allowed"] is False
