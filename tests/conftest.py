from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from ai_architect.decision.api import create_app
from ai_architect.decision.evaluation import DecisionEvaluator
from ai_architect.decision.policy import PolicyGate
from ai_architect.decision.workflow import WorkflowServices
from ai_architect.governance.audit import AuditLedger
from ai_architect.shared.config import Settings
from ai_architect.shared.models import (
    DecisionProposal,
    HumanReview,
    Recommendation,
    RetrievedEvidence,
    RiskScore,
    RoutingResult,
    SanitizedSubmission,
)


class TestDoubleRetriever:
    """Test double: deterministic authorized evidence; no external RAG service is called."""

    def retrieve(
        self,
        query: str,
        actor_roles: list[str],
        top_k: int = 5,
        correlation_id: str | None = None,
    ) -> list[RetrievedEvidence]:
        del query, actor_roles, top_k, correlation_id
        return [
            RetrievedEvidence(
                chunk_id="policy-001",
                source="policy-guidelines.md",
                title="Policy Guidelines",
                version="2026.10",
                excerpt="Refer cases with a risk score above 0.7 to an underwriter.",
                lexical_score=1.0,
                semantic_score=0.9,
                hybrid_score=0.945,
            )
        ]


class TestDoubleScorer:
    """Test double: represents an existing ML endpoint response."""

    def score(self, submission: SanitizedSubmission) -> RiskScore:
        del submission
        return RiskScore(
            model_name="risk-model",
            model_version="2026.10.1",
            score=0.72,
            factors=["flood zone", "high insured value"],
        )


class TestDoubleDecisionMaker:
    """Test double: returns an LLM-style structured proposal without an LLM network call."""

    def propose(
        self,
        submission: SanitizedSubmission,
        score: RiskScore,
        evidence: list[RetrievedEvidence],
    ) -> DecisionProposal:
        del submission, score
        return DecisionProposal(
            recommendation=Recommendation.REFER,
            rationale=(
                "The risk score exceeds the referral threshold stated in the retrieved guideline."
            ),
            confidence=0.88,
            cited_chunk_ids=[evidence[0].chunk_id],
            escalation_reason="Risk score exceeds the automated referral threshold.",
        )


@dataclass
class TestDoubleRouter:
    """Test double: captures only the approved case-management handoff."""

    routed_case_ids: list[str] = field(default_factory=list)

    def route(
        self,
        submission: SanitizedSubmission,
        proposal: DecisionProposal,
        review: HumanReview,
    ) -> RoutingResult:
        del proposal, review
        self.routed_case_ids.append(submission.case_id)
        return RoutingResult(
            status="routed",
            destination="test-case-management",
            external_reference="route-123",
        )


@pytest.fixture
def app_parts(tmp_path):
    settings = Settings(
        audit_hmac_key=SecretStr("test-audit-key-that-is-long-enough"),
        audit_database_url=f"sqlite:///{tmp_path / 'audit.sqlite3'}",
    )
    router = TestDoubleRouter()
    services = WorkflowServices(
        policy_gate=PolicyGate(),
        retriever=TestDoubleRetriever(),
        scorer=TestDoubleScorer(),
        decision_maker=TestDoubleDecisionMaker(),
        router=router,
        evaluator=DecisionEvaluator(),
        audit=AuditLedger(settings.audit_database_url, settings.audit_hmac_key.get_secret_value()),
    )
    app = create_app(settings, services=services, checkpointer=InMemorySaver())
    return app, services, router
