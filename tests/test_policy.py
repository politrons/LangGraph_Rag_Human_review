from ai_architect.decision.policy import PolicyGate
from ai_architect.shared.models import CaseSubmission, RequestedAction


def test_policy_gate_redacts_pii_before_state() -> None:
    submission = CaseSubmission(
        case_id="CASE-101",
        tenant_id="tenant-a",
        actor_id="underwriter-1",
        actor_roles=["underwriter"],
        requested_action=RequestedAction.BIND,
        product="Property",
        narrative="Contact jane.doe@example.com or +34 600 123 123 about flood exposure.",
        exposure_amount=500_000,
        applicant_email="jane.doe@example.com",
        applicant_phone="+34 600 123 123",
        customer_reference="customer-456",
    )

    sanitized = PolicyGate().sanitize(submission)
    policy = PolicyGate().evaluate(sanitized)

    assert "jane.doe@example.com" not in sanitized.narrative
    assert "+34 600 123 123" not in sanitized.narrative
    assert sanitized.pii_redactions == 5
    assert policy.disposition.value == "require_review"
