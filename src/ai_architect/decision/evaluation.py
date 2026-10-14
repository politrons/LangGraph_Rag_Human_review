from __future__ import annotations

from ..shared.models import DecisionProposal, EvaluationResult, PolicyResult, RetrievedEvidence


class DecisionEvaluator:
    """Online safety evaluation; CI uses the same checks against versioned golden cases."""

    def evaluate(
        self,
        *,
        policy: PolicyResult,
        proposal: DecisionProposal,
        evidence: list[RetrievedEvidence],
    ) -> EvaluationResult:
        reasons: list[str] = []
        evidence_ids = {item.chunk_id for item in evidence}
        cited = set(proposal.cited_chunk_ids)
        groundedness = len(cited.intersection(evidence_ids)) / max(len(cited), 1)
        if groundedness < 1.0:
            reasons.append("One or more decision citations were not returned by the retriever.")
        if proposal.recommendation.value == "refer" and not proposal.escalation_reason:
            reasons.append("Referral recommendations must explain the escalation reason.")

        policy_compliance = 1.0
        if policy.disposition.value == "block":
            policy_compliance = 0.0
            reasons.append("A blocked case must not receive a decision proposal.")

        return EvaluationResult(
            passed=not reasons,
            groundedness=groundedness,
            policy_compliance=policy_compliance,
            reasons=reasons,
        )
