from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class RequestedAction(StrEnum):
    RECOMMEND = "recommend"
    BIND = "bind"
    PAY_CLAIM = "pay_claim"


class PolicyDisposition(StrEnum):
    ALLOW = "allow"
    REQUIRE_REVIEW = "require_review"
    BLOCK = "block"


class ReviewOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class Recommendation(StrEnum):
    REFER = "refer"
    ACCEPT = "accept"
    DECLINE = "decline"


class CaseSubmission(BaseModel):
    """Input accepted from the API before the PII boundary is applied."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(min_length=3, max_length=96, pattern=r"^[A-Za-z0-9._-]+$")
    tenant_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    actor_id: str = Field(min_length=3, max_length=128)
    actor_roles: Annotated[list[str], Field(min_length=1, max_length=10)]
    requested_action: RequestedAction
    product: str = Field(min_length=2, max_length=80)
    narrative: str = Field(min_length=10, max_length=12_000)
    exposure_amount: float = Field(ge=0, le=1_000_000_000)
    applicant_email: str | None = Field(default=None, max_length=320)
    applicant_phone: str | None = Field(default=None, max_length=48)
    customer_reference: str | None = Field(default=None, max_length=128)

    @field_validator("actor_roles")
    @classmethod
    def normalize_roles(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().lower() for value in values if value.strip()})
        if not normalized:
            raise ValueError("at least one non-empty actor role is required")
        return normalized


class SanitizedSubmission(BaseModel):
    """Data allowed to enter state persistence, LLM prompts and audit records."""

    case_id: str
    tenant_id: str
    actor_id: str
    actor_roles: list[str]
    requested_action: RequestedAction
    product: str
    narrative: str
    exposure_amount: float
    pii_redactions: int = Field(ge=0)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyResult(BaseModel):
    disposition: PolicyDisposition
    reasons: list[str] = Field(default_factory=list)
    prompt_injection_detected: bool = False
    pii_redactions: int = Field(ge=0)


class RetrievedEvidence(BaseModel):
    chunk_id: str
    source: str
    title: str
    version: str
    url: HttpUrl | None = None
    excerpt: str = Field(min_length=1, max_length=2_000)
    lexical_score: float = Field(ge=0)
    semantic_score: float = Field(ge=-1, le=1)
    hybrid_score: float = Field(ge=0)


class RagRetrievalRequest(BaseModel):
    """The narrow, sanitized contract accepted by the separate RAG service."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=12_100)
    actor_roles: Annotated[list[str], Field(min_length=1, max_length=10)]
    top_k: int = Field(default=5, ge=1, le=10)
    correlation_id: str = Field(min_length=1, max_length=128)
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("actor_roles")
    @classmethod
    def normalize_roles(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().lower() for value in values if value.strip()})
        if not normalized:
            raise ValueError("at least one non-empty actor role is required")
        return normalized


class RagRetrievalResponse(BaseModel):
    """Evidence returned by the RAG subgraph to the decision workflow."""

    correlation_id: str
    rag_thread_id: str
    evidence: list[RetrievedEvidence]


class RiskScore(BaseModel):
    model_name: str
    model_version: str
    score: float = Field(ge=0, le=1)
    factors: list[str] = Field(default_factory=list)
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionProposal(BaseModel):
    recommendation: Recommendation
    rationale: str = Field(min_length=20, max_length=3_000)
    confidence: float = Field(ge=0, le=1)
    cited_chunk_ids: list[str] = Field(min_length=1, max_length=10)
    required_conditions: list[str] = Field(default_factory=list, max_length=10)
    escalation_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_escalation_reason_for_referral(self) -> DecisionProposal:
        if self.recommendation is Recommendation.REFER and not (
            self.escalation_reason and self.escalation_reason.strip()
        ):
            raise ValueError(
                "escalation_reason is required when recommendation is 'refer'"
            )
        return self


class HumanReviewRequest(BaseModel):
    case_id: str
    tenant_id: str
    requested_action: RequestedAction
    proposal: DecisionProposal
    risk_score: RiskScore
    evidence: list[RetrievedEvidence]


class HumanReview(BaseModel):
    reviewer_id: str = Field(min_length=3, max_length=128)
    outcome: ReviewOutcome
    rationale: str = Field(min_length=5, max_length=2_000)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoutingResult(BaseModel):
    status: Literal["recorded", "routed", "rejected", "escalated"]
    destination: str
    external_reference: str | None = None


class EvaluationResult(BaseModel):
    passed: bool
    groundedness: float = Field(ge=0, le=1)
    policy_compliance: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class StartDecisionRequest(BaseModel):
    submission: CaseSubmission
    thread_id: UUID = Field(default_factory=uuid4)


class ReviewDecisionRequest(BaseModel):
    review: HumanReview


class DecisionResponse(BaseModel):
    thread_id: UUID
    status: Literal["awaiting_human", "completed", "blocked", "failed"]
    policy: PolicyResult | None = None
    proposal: DecisionProposal | None = None
    review_request: HumanReviewRequest | None = None
    routing: RoutingResult | None = None
    evaluation: EvaluationResult | None = None
    error: str | None = None
