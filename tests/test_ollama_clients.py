from __future__ import annotations

import json as json_module

import httpx
import pytest
from pydantic import ValidationError

from ai_architect.integrations import clients
from ai_architect.integrations.clients import (
    CaseRouter,
    DecisionMaker,
    EmbeddingClient,
    EvidenceRetriever,
    LocalCaseRouter,
    OllamaDecisionMaker,
    OllamaEmbeddingClient,
    OllamaRiskScorer,
    RemoteRagRetriever,
    RiskScorer,
)
from ai_architect.shared.models import (
    DecisionProposal,
    HumanReview,
    Recommendation,
    RetrievedEvidence,
    ReviewOutcome,
    RiskScore,
    SanitizedSubmission,
)


def _response(payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "http://ollama.test/api"),
    )


def _submission() -> SanitizedSubmission:
    return SanitizedSubmission(
        case_id="CASE-OLLAMA-001",
        tenant_id="tenant-a",
        actor_id="underwriter-1",
        actor_roles=["underwriter"],
        requested_action="recommend",
        product="property",
        narrative="Sanitized commercial property case narrative.",
        exposure_amount=300_000,
        pii_redactions=0,
    )


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id="policy-001",
        source="guideline.md",
        title="Guideline",
        version="2026.10",
        excerpt="Refer high-risk property cases to an underwriter.",
        lexical_score=1.0,
        semantic_score=0.9,
        hybrid_score=0.945,
    )


def test_adapters_explicitly_inherit_their_navigation_contracts() -> None:
    assert EmbeddingClient in OllamaEmbeddingClient.__mro__
    assert RiskScorer in OllamaRiskScorer.__mro__
    assert DecisionMaker in OllamaDecisionMaker.__mro__
    assert CaseRouter in LocalCaseRouter.__mro__
    assert EvidenceRetriever in RemoteRagRetriever.__mro__


def test_ollama_embedding_client_uses_native_embedding_api(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: object, timeout: float) -> httpx.Response:
        """Test double: emulates Ollama's native /api/embed response."""
        captured.update(url=url, json=json, timeout=timeout)
        return _response({"embeddings": [[0.1, -0.2, 0.3]]})

    monkeypatch.setattr(clients.httpx, "post", fake_post)

    vector = OllamaEmbeddingClient(
        base_url="http://ollama.test",
        model="nomic-embed-text",
        timeout_seconds=12,
    ).embed("sanitized text")

    assert vector == [0.1, -0.2, 0.3]
    assert captured == {
        "url": "http://ollama.test/api/embed",
        "json": {"model": "nomic-embed-text", "input": "sanitized text"},
        "timeout": 12,
    }


def test_remote_rag_retriever_calls_the_narrow_service_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: object, timeout: float) -> httpx.Response:
        """Test double: represents the separately deployed RAG service."""
        captured.update(url=url, json=json, timeout=timeout)
        return _response(
            {
                "correlation_id": "decision-thread-1",
                "rag_thread_id": "rag-request-1",
                "evidence": [_evidence().model_dump(mode="json")],
            }
        )

    monkeypatch.setattr(clients.httpx, "post", fake_post)

    result = RemoteRagRetriever(
        base_url="http://rag.test",
        timeout_seconds=12,
    ).retrieve(
        "property\nflood exposure",
        ["underwriter"],
        correlation_id="decision-thread-1",
    )

    assert [item.chunk_id for item in result] == ["policy-001"]
    assert captured["url"] == "http://rag.test/v1/rag/retrieve"
    assert captured["timeout"] == 12
    assert isinstance(captured["json"], dict)
    assert captured["json"]["query"] == "property\nflood exposure"
    assert captured["json"]["actor_roles"] == ["underwriter"]
    assert captured["json"]["correlation_id"] == "decision-thread-1"


def test_ollama_risk_scorer_marks_estimate_as_local(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, json: object, timeout: float) -> httpx.Response:
        """Test double: emulates Ollama's structured chat response for scoring."""
        captured.update(url=url, json=json, timeout=timeout)
        return _response(
            {
                "message": {
                    "content": json_module.dumps(
                        {"score": 0.73, "factors": ["flood zone"]}
                    )
                }
            }
        )

    monkeypatch.setattr(clients.httpx, "post", fake_post)

    result = OllamaRiskScorer(
        base_url="http://ollama.test",
        model="llama3.1:8b",
        timeout_seconds=12,
    ).score(_submission())

    assert result.model_name == "ollama/llama3.1:8b"
    assert result.model_version == "local-development"
    assert result.score == 0.73
    assert captured["url"] == "http://ollama.test/api/chat"
    assert isinstance(captured["json"], dict)
    assert captured["json"]["stream"] is False
    assert captured["json"]["format"]["title"] == "LocalRiskEstimate"


def test_ollama_decision_maker_validates_native_structured_response(monkeypatch) -> None:
    evidence = _evidence()
    proposal = DecisionProposal(
        recommendation=Recommendation.REFER,
        rationale="The retrieved policy requires an underwriter review for this risk profile.",
        confidence=0.81,
        cited_chunk_ids=[evidence.chunk_id],
        escalation_reason="Risk requires underwriting review.",
    )

    def fake_post(url: str, *, json: object, timeout: float) -> httpx.Response:
        """Test double: emulates Ollama's native /api/chat decision response."""
        del url, json, timeout
        return _response({"message": {"content": proposal.model_dump_json()}})

    monkeypatch.setattr(clients.httpx, "post", fake_post)
    score = RiskScore(
        model_name="ollama/llama3.1:8b",
        model_version="local-development",
        score=0.73,
        factors=["flood zone"],
    )

    result = OllamaDecisionMaker(
        base_url="http://ollama.test",
        model="llama3.1:8b",
        timeout_seconds=12,
    ).propose(_submission(), score, [evidence])

    assert result == proposal


def test_ollama_decision_maker_rejects_referral_without_escalation_reason(monkeypatch) -> None:
    evidence = _evidence()
    response_content = {
        "recommendation": "refer",
        "rationale": "The risk score requires an underwriter review under the retrieved guideline.",
        "confidence": 0.81,
        "cited_chunk_ids": [evidence.chunk_id],
        "escalation_reason": None,
    }

    def fake_post(url: str, *, json: object, timeout: float) -> httpx.Response:
        """Test double: returns an incomplete referral proposal from Ollama."""
        del url, json, timeout
        return _response({"message": {"content": json_module.dumps(response_content)}})

    monkeypatch.setattr(clients.httpx, "post", fake_post)
    score = RiskScore(
        model_name="ollama/llama3.1:8b",
        model_version="local-development",
        score=0.73,
        factors=["flood zone"],
    )

    with pytest.raises(ValidationError, match="escalation_reason is required"):
        OllamaDecisionMaker(
            base_url="http://ollama.test",
            model="llama3.1:8b",
            timeout_seconds=12,
        ).propose(_submission(), score, [evidence])


def test_local_case_router_records_without_external_http() -> None:
    result = LocalCaseRouter().route(
        _submission(),
        DecisionProposal(
            recommendation=Recommendation.ACCEPT,
            rationale="The available evidence supports acceptance under the documented conditions.",
            confidence=0.8,
            cited_chunk_ids=["policy-001"],
        ),
        HumanReview(
            reviewer_id="underwriter-1",
            outcome=ReviewOutcome.APPROVE,
            rationale="Approved for local workflow validation.",
        ),
    )

    assert result.status == "recorded"
    assert result.destination == "local-only"
    assert result.external_reference is None
