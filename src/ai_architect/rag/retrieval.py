from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..integrations.clients import EmbeddingClient, EvidenceRetriever, IntegrationUnavailableError
from ..shared.models import RetrievedEvidence

_TOKEN = re.compile(r"\w+", flags=re.UNICODE)


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source: str
    title: str
    version: str
    text: str
    embedding: tuple[float, ...]
    allowed_roles: frozenset[str]
    url: str | None = None


class HybridRetriever(EvidenceRetriever):
    """Combines BM25 lexical ranking and embedding cosine similarity with role filtering."""

    def __init__(self, chunks: Iterable[IndexedChunk], embedding_client: EmbeddingClient) -> None:
        self._chunks = list(chunks)
        if not self._chunks:
            raise ValueError("Hybrid RAG index contains no chunks")
        self._embedding_client = embedding_client
        self._tokens = [self._tokenize(chunk.text) for chunk in self._chunks]
        self._frequencies = [Counter(tokens) for tokens in self._tokens]
        self._lengths = [max(len(tokens), 1) for tokens in self._tokens]
        self._average_length = sum(self._lengths) / len(self._lengths)
        document_frequency: Counter[str] = Counter()
        for tokens in self._tokens:
            document_frequency.update(set(tokens))
        self._document_frequency = document_frequency

    @classmethod
    def from_jsonl(cls, path: Path, embedding_client: EmbeddingClient) -> HybridRetriever:
        if not path.is_file():
            raise FileNotFoundError(
                f"Hybrid RAG index not found: {path}. Run scripts/build_rag_index.py first."
            )
        chunks: list[IndexedChunk] = []
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            try:
                embedding = tuple(float(value) for value in row["embedding"])
                if not embedding:
                    raise ValueError("embedding was empty")
                chunks.append(
                    IndexedChunk(
                        chunk_id=str(row["chunk_id"]),
                        source=str(row["source"]),
                        title=str(row["title"]),
                        version=str(row["version"]),
                        text=str(row["text"]),
                        embedding=embedding,
                        allowed_roles=frozenset(
                            str(role).lower() for role in row.get("allowed_roles", [])
                        ),
                        url=str(row["url"]) if row.get("url") else None,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid RAG index line {line_number}: {exc}") from exc
        return cls(chunks, embedding_client)

    def retrieve(
        self,
        query: str,
        actor_roles: list[str],
        top_k: int = 5,
        correlation_id: str | None = None,
    ) -> list[RetrievedEvidence]:
        del correlation_id
        query_embedding = self._embedding_client.embed(query)
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        roles = {role.lower() for role in actor_roles}
        candidates: list[tuple[int, float, float]] = []
        for index, chunk in enumerate(self._chunks):
            if chunk.allowed_roles and not roles.intersection(chunk.allowed_roles):
                continue
            lexical = self._bm25(query_tokens, index)
            semantic = self._cosine(query_embedding, chunk.embedding)
            candidates.append((index, lexical, semantic))

        if not candidates:
            return []
        maximum_lexical = max(item[1] for item in candidates) or 1.0
        evidence: list[RetrievedEvidence] = []
        for index, lexical, semantic in candidates:
            normalized_lexical = lexical / maximum_lexical
            normalized_semantic = max(semantic, 0.0)
            hybrid = 0.45 * normalized_lexical + 0.55 * normalized_semantic
            chunk = self._chunks[index]
            evidence.append(
                RetrievedEvidence(
                    chunk_id=chunk.chunk_id,
                    source=chunk.source,
                    title=chunk.title,
                    version=chunk.version,
                    url=chunk.url,
                    excerpt=chunk.text[:2_000],
                    lexical_score=lexical,
                    semantic_score=semantic,
                    hybrid_score=hybrid,
                )
            )
        return sorted(evidence, key=lambda item: item.hybrid_score, reverse=True)[:top_k]

    def _bm25(self, query_tokens: list[str], index: int) -> float:
        score = 0.0
        document_count = len(self._chunks)
        term_frequency = self._frequencies[index]
        document_length = self._lengths[index]
        k1 = 1.5
        b = 0.75
        for token in set(query_tokens):
            frequency = term_frequency.get(token, 0)
            if frequency == 0:
                continue
            document_frequency = self._document_frequency.get(token, 0)
            inverse_document_frequency = math.log(
                1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + k1 * (1 - b + b * document_length / self._average_length)
            score += inverse_document_frequency * (frequency * (k1 + 1) / denominator)
        return score

    @staticmethod
    def _cosine(left: list[float], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            raise ValueError("Query embedding dimension differs from indexed document embeddings")
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token.lower() for token in _TOKEN.findall(text)]


class UnavailableRetriever(EvidenceRetriever):
    def retrieve(
        self,
        query: str,
        actor_roles: list[str],
        top_k: int = 5,
        correlation_id: str | None = None,
    ) -> list[RetrievedEvidence]:
        del query, actor_roles, top_k, correlation_id
        raise IntegrationUnavailableError(
            "Hybrid RAG index or embedding integration is not configured"
        )
