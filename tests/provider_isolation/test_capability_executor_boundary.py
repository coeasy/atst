from tstdx.capability_audit import audit_capability_bindings
from tstdx.provider_audit import audit_provider_registry


def test_provider_and_capability_audits_are_available() -> None:
    provider_report = audit_provider_registry()
    capability_report = audit_capability_bindings()

    assert provider_report.providers > 0
    assert provider_report.bindings > 0
    assert capability_report.migrated_capabilities > 0
    assert capability_report.executable_bindings > 0
