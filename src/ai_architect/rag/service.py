from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TypedDict

from fastapi import FastAPI, HTTPException, Request, status
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ..integrations.clients import (
    EvidenceRetriever,
    IntegrationUnavailableError,
    OllamaEmbeddingClient,
)
from ..shared.config import Settings
from ..shared.models import RagRetrievalRequest, RagRetrievalResponse, RetrievedEvidence
from .retrieval import HybridRetriever


class RagState(TypedDict, total=False):
    """Private state owned by the RAG service's small LangGraph subgraph."""

    request: dict[str, object]
    evidence: list[dict[str, object]]


def build_retrieval_subgraph(retriever: EvidenceRetriever):
    """Compile the child graph that owns the actual hybrid-retrieval step."""

    def retrieve_node(state: RagState) -> RagState:
        request = RagRetrievalRequest.model_validate(state["request"])
        evidence = retriever.retrieve(
            query=request.query,
            actor_roles=request.actor_roles,
            top_k=request.top_k,
            correlation_id=request.correlation_id,
        )
        return {"evidence": [item.model_dump(mode="json") for item in evidence]}

    graph = StateGraph(RagState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", END)
    return graph.compile()


def build_rag_subgraph(retriever: EvidenceRetriever, checkpointer: object):
    """Compile the remote RAG graph with a child retrieval subgraph as its node."""

    graph = StateGraph(RagState)
    graph.add_node("retrieval_subgraph", build_retrieval_subgraph(retriever))
    graph.add_edge(START, "retrieval_subgraph")
    graph.add_edge("retrieval_subgraph", END)
    return graph.compile(checkpointer=checkpointer)


def build_rag_retriever(settings: Settings) -> HybridRetriever:
    """Build the only component that owns the RAG index and Ollama embedding client."""

    embedding_client = OllamaEmbeddingClient(
        base_url=str(settings.ollama_base_url),
        model=settings.ollama_embedding_model,
        timeout_seconds=settings.request_timeout_seconds,
    )
    return HybridRetriever.from_jsonl(settings.rag_index_path, embedding_client)


def create_rag_app(
    settings: Settings,
    *,
    retriever: EvidenceRetriever | None = None,
    checkpointer: object | None = None,
) -> FastAPI:
    """Expose the RAG subgraph through a deliberately narrow local HTTP boundary."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_retriever = retriever or build_rag_retriever(settings)
        app_checkpointer = checkpointer or InMemorySaver()
        app.state.retriever = app_retriever
        app.state.graph = build_rag_subgraph(app_retriever, app_checkpointer)
        yield

    app = FastAPI(
        title="Local RAG LangGraph Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "rag", "environment": settings.app_env}

    @app.post(
        "/v1/rag/retrieve",
        response_model=RagRetrievalResponse,
        status_code=status.HTTP_200_OK,
    )
    def retrieve(payload: RagRetrievalRequest, request: Request) -> RagRetrievalResponse:
        rag_thread_id = f"rag-{payload.request_id}"
        try:
            state = request.app.state.graph.invoke(
                {"request": payload.model_dump(mode="json")},
                config={"configurable": {"thread_id": rag_thread_id}},
            )
            raw_evidence = state.get("evidence", [])
            evidence = [RetrievedEvidence.model_validate(item) for item in raw_evidence]
            return RagRetrievalResponse(
                correlation_id=payload.correlation_id,
                rag_thread_id=rag_thread_id,
                evidence=evidence,
            )
        except IntegrationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def run() -> None:
    import uvicorn

    configuration = Settings()
    uvicorn.run(create_rag_app(configuration), host="127.0.0.1", port=8081)


if __name__ == "__main__":
    run()
