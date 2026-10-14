from __future__ import annotations

import re

from ..shared.models import CaseSubmission, PolicyDisposition, PolicyResult, SanitizedSubmission

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_INJECTION = re.compile(
    r"(?i)ignore (?:all |any |the )?(?:previous|prior) instructions|"
    r"system prompt|reveal (?:your )?(?:instructions|prompt)|"
    r"act as (?:the )?(?:system|developer)"
)


class PolicyGate:
    """Removes direct PII before durable state or model calls, then applies routing policy."""

    def sanitize(self, submission: CaseSubmission) -> SanitizedSubmission:
        narrative, narrative_redactions = self._redact(submission.narrative)
        direct_pii = sum(
            value is not None
            for value in (
                submission.applicant_email,
                submission.applicant_phone,
                submission.customer_reference,
            )
        )
        return SanitizedSubmission(
            case_id=submission.case_id,
            tenant_id=submission.tenant_id,
            actor_id=submission.actor_id,
            actor_roles=submission.actor_roles,
            requested_action=submission.requested_action,
            product=submission.product,
            narrative=narrative,
            exposure_amount=submission.exposure_amount,
            pii_redactions=narrative_redactions + direct_pii,
        )

    def evaluate(self, submission: SanitizedSubmission) -> PolicyResult:
        reasons: list[str] = []
        injection_detected = bool(_INJECTION.search(submission.narrative))
        if injection_detected:
            reasons.append("Possible prompt-injection content requires specialist review.")
        if submission.exposure_amount >= 5_000_000:
            reasons.append("High exposure amount requires specialist review.")
        if submission.requested_action.value in {"bind", "pay_claim"}:
            reasons.append(
                "The service may recommend only; a human must authorize the requested action."
            )

        disposition = PolicyDisposition.REQUIRE_REVIEW if reasons else PolicyDisposition.ALLOW
        return PolicyResult(
            disposition=disposition,
            reasons=reasons,
            prompt_injection_detected=injection_detected,
            pii_redactions=submission.pii_redactions,
        )

    @staticmethod
    def _redact(value: str) -> tuple[str, int]:
        redactions = 0

        def replace(match: re.Match[str], label: str) -> str:
            nonlocal redactions
            redactions += 1
            return f"[{label}_REDACTED]"

        value = _EMAIL.sub(lambda match: replace(match, "EMAIL"), value)
        value = _PHONE.sub(lambda match: replace(match, "PHONE"), value)
        value = _IBAN.sub(lambda match: replace(match, "IBAN"), value)
        return value, redactions
