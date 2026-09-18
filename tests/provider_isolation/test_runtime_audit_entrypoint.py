from tstdx.runtime_audit import audit_runtime


def test_runtime_audit_returns_structural_report() -> None:
    report = audit_runtime()

    assert report.providers > 0
    assert report.capabilities > 0
    assert report.bindings > 0
