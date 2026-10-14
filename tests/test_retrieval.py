from ai_architect.rag.retrieval import HybridRetriever, IndexedChunk


class TestDoubleEmbeddingClient:
    """Test double: fixed embedding keeps the hybrid rank calculation deterministic."""

    def embed(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]


def test_hybrid_retrieval_applies_role_filter_and_combines_scores() -> None:
    retriever = HybridRetriever(
        [
            IndexedChunk(
                chunk_id="underwriter-1",
                source="guideline.md",
                title="Underwriting guideline",
                version="v1",
                text="Flood exposure above threshold needs referral.",
                embedding=(1.0, 0.0),
                allowed_roles=frozenset({"underwriter"}),
            ),
            IndexedChunk(
                chunk_id="claims-1",
                source="claims.md",
                title="Claims guideline",
                version="v1",
                text="Flood exposure above threshold needs referral.",
                embedding=(0.0, 1.0),
                allowed_roles=frozenset({"claims_adjuster"}),
            ),
        ],
        TestDoubleEmbeddingClient(),
    )

    results = retriever.retrieve("flood exposure threshold", ["underwriter"])

    assert [result.chunk_id for result in results] == ["underwriter-1"]
    assert results[0].hybrid_score > 0.5
