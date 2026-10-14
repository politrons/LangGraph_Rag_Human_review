from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from ai_architect.rag.service import create_rag_app
from ai_architect.shared.config import Settings
from ai_architect.shared.models import RetrievedEvidence


class RagRetrieverTestDouble:
    """Test double: deterministic RAG evidence with no Ollama or network call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(
        self,
        query: str,
        actor_roles: list[str],
        top_k: int = 5,
        correlation_id: str | None = None,
    ) -> list[RetrievedEvidence]:
        self.calls.append(
            {
                "query": query,
                "actor_roles": actor_roles,
                "top_k": top_k,
                "correlation_id": correlation_id,
            }
        )
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


def test_rag_service_runs_its_subgraph_and_returns_typed_evidence() -> None:
    settings = Settings(audit_hmac_key=SecretStr("test-audit-key-that-is-long-enough"))
    retriever = RagRetrieverTestDouble()
    app = create_rag_app(settings, retriever=retriever, checkpointer=InMemorySaver())

    with TestClient(app) as client:
        assert client.get("/health").json()["service"] == "rag"
        response = client.post(
            "/v1/rag/retrieve",
            json={
                "query": "property\nflood exposure",
                "actor_roles": ["underwriter"],
                "top_k": 3,
                "correlation_id": "decision-thread-1",
                "request_id": "00000000-0000-0000-0000-000000000003",
            },
        )

    assert response.status_code == 200
    assert response.json()["rag_thread_id"] == "rag-00000000-0000-0000-0000-000000000003"
    assert response.json()["evidence"][0]["chunk_id"] == "policy-001"
    assert retriever.calls == [
        {
            "query": "property\nflood exposure",
            "actor_roles": ["underwriter"],
            "top_k": 3,
            "correlation_id": "decision-thread-1",
        }
    ]
