from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from ..governance.audit import AuditLedger
from ..integrations.clients import CaseRouter, DecisionMaker, EvidenceRetriever, RiskScorer
from ..shared.models import (
    DecisionProposal,
    EvaluationResult,
    HumanReview,
    HumanReviewRequest,
    PolicyDisposition,
    PolicyResult,
    RetrievedEvidence,
    RiskScore,
    RoutingResult,
    SanitizedSubmission,
)
from .evaluation import DecisionEvaluator
from .policy import PolicyGate


class DecisionState(TypedDict, total=False):
    thread_id: str
    submission: dict
    policy: dict
    evidence: list[dict]
    score: dict
    proposal: dict
    human_review: dict
    routing: dict
    evaluation: dict
    status: str


@dataclass(frozen=True)
class WorkflowServices:
    policy_gate: PolicyGate
    retriever: EvidenceRetriever
    scorer: RiskScorer
    decision_maker: DecisionMaker
    router: CaseRouter
    evaluator: DecisionEvaluator
    audit: AuditLedger


def build_decision_graph(
    services: WorkflowServices,
    checkpointer: object,
) -> CompiledStateGraph:
    """Build the durable decision graph. Checkpointer is injected by the API lifespan."""

    def submission_from(state: DecisionState) -> SanitizedSubmission:
        return SanitizedSubmission.model_validate(state["submission"])

    def policy_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        result = services.policy_gate.evaluate(submission)
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="policy_evaluated",
            payload=result.model_dump(mode="json"),
        )
        return {"policy": result.model_dump(mode="json")}

    def after_policy(state: DecisionState) -> Literal["blocked", "retrieve"]:
        policy = PolicyResult.model_validate(state["policy"])
        return "blocked" if policy.disposition is PolicyDisposition.BLOCK else "retrieve"

    def blocked_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="case_blocked",
            payload={"policy": state["policy"]},
        )
        return {"status": "blocked"}

    def retrieve_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        evidence = services.retriever.retrieve(
            query=f"{submission.product}\n{submission.narrative}",
            actor_roles=submission.actor_roles,
            correlation_id=state["thread_id"],
        )
        if not evidence:
            raise ValueError("No authorized evidence was retrieved; referral is required")
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="evidence_retrieved",
            payload={
                "chunk_ids": [item.chunk_id for item in evidence],
                "count": len(evidence),
                "sources": [item.source for item in evidence],
            },
        )
        return {"evidence": [item.model_dump(mode="json") for item in evidence]}

    def score_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        score = services.scorer.score(submission)
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="ml_score_received",
            payload=score.model_dump(mode="json"),
        )
        return {"score": score.model_dump(mode="json")}

    def decision_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        score = RiskScore.model_validate(state["score"])
        evidence = [RetrievedEvidence.model_validate(item) for item in state["evidence"]]
        proposal = services.decision_maker.propose(submission, score, evidence)
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="decision_proposed",
            payload=proposal.model_dump(mode="json"),
        )
        return {"proposal": proposal.model_dump(mode="json")}

    def human_review_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        request = HumanReviewRequest(
            case_id=submission.case_id,
            tenant_id=submission.tenant_id,
            requested_action=submission.requested_action,
            proposal=DecisionProposal.model_validate(state["proposal"]),
            risk_score=RiskScore.model_validate(state["score"]),
            evidence=[RetrievedEvidence.model_validate(item) for item in state["evidence"]],
        )
        review_payload = interrupt(request.model_dump(mode="json"))
        review = HumanReview.model_validate(review_payload)
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="human_review_recorded",
            payload=review.model_dump(mode="json"),
        )
        return {"human_review": review.model_dump(mode="json")}

    def after_review(state: DecisionState) -> Literal["route", "close"]:
        review = HumanReview.model_validate(state["human_review"])
        return "route" if review.outcome.value == "approve" else "close"

    def route_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        result = services.router.route(
            submission,
            DecisionProposal.model_validate(state["proposal"]),
            HumanReview.model_validate(state["human_review"]),
        )
        event_type = "case_routed" if result.status == "routed" else "case_recorded_locally"
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type=event_type,
            payload=result.model_dump(mode="json"),
        )
        return {"routing": result.model_dump(mode="json")}

    def close_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        review = HumanReview.model_validate(state["human_review"])
        status = "rejected" if review.outcome.value == "reject" else "escalated"
        result = RoutingResult(status=status, destination="human-review")
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type=f"case_{status}",
            payload={"review": review.model_dump(mode="json")},
        )
        return {"routing": result.model_dump(mode="json")}

    def evaluate_node(state: DecisionState) -> DecisionState:
        submission = submission_from(state)
        result: EvaluationResult = services.evaluator.evaluate(
            policy=PolicyResult.model_validate(state["policy"]),
            proposal=DecisionProposal.model_validate(state["proposal"]),
            evidence=[RetrievedEvidence.model_validate(item) for item in state["evidence"]],
        )
        services.audit.record_evaluation(
            thread_id=state["thread_id"], case_id=submission.case_id, result=result
        )
        services.audit.append(
            thread_id=state["thread_id"],
            case_id=submission.case_id,
            event_type="decision_evaluated",
            payload=result.model_dump(mode="json"),
        )
        return {"evaluation": result.model_dump(mode="json"), "status": "completed"}

    graph = StateGraph(DecisionState)
    graph.add_node("policy", policy_node)
    graph.add_node("blocked", blocked_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("score", score_node)
    graph.add_node("decision", decision_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("route", route_node)
    graph.add_node("close", close_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_edge(START, "policy")
    graph.add_conditional_edges(
        "policy",
        after_policy,
        {"blocked": "blocked", "retrieve": "retrieve"},
    )
    graph.add_edge("blocked", END)
    graph.add_edge("retrieve", "score")
    graph.add_edge("score", "decision")
    graph.add_edge("decision", "human_review")
    graph.add_conditional_edges("human_review", after_review, {"route": "route", "close": "close"})
    graph.add_edge("route", "evaluate")
    graph.add_edge("close", "evaluate")
    graph.add_edge("evaluate", END)
    return graph.compile(checkpointer=checkpointer)
