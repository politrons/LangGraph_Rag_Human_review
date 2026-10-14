from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from ..governance.audit import AuditLedger
from ..governance.auth import (
    AuthenticationError,
    DevelopmentIdentityVerifier,
    IdentityVerifier,
    OidcJwtVerifier,
    authorize_review,
    authorize_sanitized_submission,
    authorize_submission,
)
from ..integrations.clients import (
    HttpCaseRouter,
    IntegrationUnavailableError,
    LocalCaseRouter,
    OllamaDecisionMaker,
    OllamaRiskScorer,
    RemoteRagRetriever,
    UnavailableCaseRouter,
)
from ..shared.config import Settings, StateBackend
from ..shared.models import (
    DecisionResponse,
    HumanReviewRequest,
    PolicyResult,
    ReviewDecisionRequest,
    RoutingResult,
    SanitizedSubmission,
    StartDecisionRequest,
)
from .evaluation import DecisionEvaluator
from .policy import PolicyGate
from .workflow import WorkflowServices, build_decision_graph


def build_services(settings: Settings) -> WorkflowServices:
    audit = AuditLedger(
        database_url=settings.audit_database_url,
        hmac_key=settings.audit_hmac_key.get_secret_value(),
    )
    retriever = RemoteRagRetriever(
        base_url=str(settings.rag_service_url),
        timeout_seconds=settings.request_timeout_seconds,
    )

    scorer = OllamaRiskScorer(
        base_url=str(settings.ollama_base_url),
        model=settings.ollama_scoring_model or settings.ollama_decision_model,
        timeout_seconds=settings.request_timeout_seconds,
    )
    decision_maker = OllamaDecisionMaker(
        base_url=str(settings.ollama_base_url),
        model=settings.ollama_decision_model,
        timeout_seconds=settings.request_timeout_seconds,
    )

    router = (
        LocalCaseRouter()
        if settings.app_env.lower() == "development"
        else UnavailableCaseRouter()
    )
    if (
        settings.app_env.lower() != "development"
        and settings.case_routing_url
        and settings.case_routing_api_key
    ):
        router = HttpCaseRouter(
            url=str(settings.case_routing_url),
            api_key=settings.case_routing_api_key.get_secret_value(),
            timeout_seconds=settings.request_timeout_seconds,
        )
    return WorkflowServices(
        policy_gate=PolicyGate(),
        retriever=retriever,
        scorer=scorer,
        decision_maker=decision_maker,
        router=router,
        evaluator=DecisionEvaluator(),
        audit=audit,
    )


def build_identity_verifier(settings: Settings) -> IdentityVerifier:
    if settings.app_env.lower() in {"production", "staging"}:
        return OidcJwtVerifier(
            jwks_url=str(settings.oidc_jwks_url),
            issuer=str(settings.oidc_issuer),
            audience=str(settings.oidc_audience),
        )
    return DevelopmentIdentityVerifier()


def _to_response(thread_id: str, state: dict[str, Any]) -> DecisionResponse:
    policy = PolicyResult.model_validate(state["policy"]) if state.get("policy") else None
    interrupts = state.get("__interrupt__", ())
    if interrupts:
        interrupt_value = getattr(interrupts[0], "value", interrupts[0])
        return DecisionResponse(
            thread_id=thread_id,
            status="awaiting_human",
            policy=policy,
            proposal=state.get("proposal"),
            review_request=HumanReviewRequest.model_validate(interrupt_value),
        )
    workflow_status = state.get("status", "completed")
    return DecisionResponse(
        thread_id=thread_id,
        status=workflow_status,
        policy=policy,
        proposal=state.get("proposal"),
        routing=RoutingResult.model_validate(state["routing"]) if state.get("routing") else None,
        evaluation=state.get("evaluation"),
    )


def _postgres_checkpointer(stack: ExitStack, settings: Settings) -> object:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - depends on optional production extra
        raise RuntimeError("Install ai-architect-insurance[postgres] for PostgreSQL state") from exc
    connection_uri = settings.postgres_connection_uri
    if connection_uri is None:  # pragma: no cover - Settings validation prevents this
        raise RuntimeError("POSTGRES_CONNECTION_URI is required")
    connection = PostgresSaver.from_conn_string(connection_uri.get_secret_value())
    checkpointer = stack.enter_context(connection)
    checkpointer.setup()
    return checkpointer


def create_app(
    settings: Settings,
    *,
    services: WorkflowServices | None = None,
    checkpointer: object | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    """Create the API with injectable services/checkpointer for deterministic tests."""

    settings.ensure_state_backend_is_configured()
    settings.ensure_identity_provider_is_configured()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ExitStack() as stack:
            app_services = services or build_services(settings)
            app.state.identity_verifier = identity_verifier or build_identity_verifier(settings)
            app_checkpointer = checkpointer
            if app_checkpointer is None:
                app_checkpointer = (
                    InMemorySaver()
                    if settings.state_backend is StateBackend.MEMORY
                    else _postgres_checkpointer(stack, settings)
                )
            app.state.services = app_services
            app.state.graph = build_decision_graph(app_services, app_checkpointer)
            yield

    app = FastAPI(
        title="Governed Insurance Decision Support",
        version="0.1.0",
        lifespan=lifespan,
    )

    def workflow_graph(request: Request):
        return request.app.state.graph

    def workflow_services(request: Request) -> WorkflowServices:
        return request.app.state.services

    def authenticate(request: Request):
        try:
            return request.app.state.identity_verifier.verify(request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    @app.post(
        "/v1/cases/decisions",
        response_model=DecisionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_decision(payload: StartDecisionRequest, request: Request) -> DecisionResponse:
        try:
            authorize_submission(authenticate(request), payload.submission)
        except AuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        services = workflow_services(request)
        thread_id = str(payload.thread_id)
        sanitized = services.policy_gate.sanitize(payload.submission)
        services.audit.append(
            thread_id=thread_id,
            case_id=sanitized.case_id,
            event_type="case_received",
            payload=sanitized.model_dump(mode="json"),
        )
        try:
            state = workflow_graph(request).invoke(
                {"thread_id": thread_id, "submission": sanitized.model_dump(mode="json")},
                config={"configurable": {"thread_id": thread_id}},
            )
            return _to_response(thread_id, state)
        except IntegrationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="Required external integration failed",
            ) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/cases/{case_id}/decisions/{thread_id}/review", response_model=DecisionResponse)
    def review_decision(
        case_id: str,
        thread_id: str,
        payload: ReviewDecisionRequest,
        request: Request,
    ) -> DecisionResponse:
        config = {"configurable": {"thread_id": thread_id}}
        graph = workflow_graph(request)
        snapshot = graph.get_state(config)
        stored_submission = snapshot.values.get("submission")
        stored_case_id = (stored_submission or {}).get("case_id")
        if stored_case_id is None:
            raise HTTPException(status_code=404, detail="Decision thread was not found")
        if stored_case_id != case_id:
            raise HTTPException(status_code=409, detail="Case ID does not match decision thread")
        try:
            authorize_sanitized_submission(
                authenticate(request),
                SanitizedSubmission.model_validate(stored_submission),
            )
            authorize_review(authenticate(request), payload.review)
        except AuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            command = Command(resume=payload.review.model_dump(mode="json"))
            state = graph.invoke(command, config=config)
            return _to_response(thread_id, state)
        except IntegrationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Case-management routing failed") from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/metrics")
    def metrics(request: Request) -> dict[str, dict[str, float]]:
        if not authenticate(request).is_admin:
            raise HTTPException(status_code=403, detail="AI administrator role is required")
        return workflow_services(request).audit.metrics_summary()

    @app.get("/v1/audit/{thread_id}")
    def audit(thread_id: str, request: Request) -> list[dict[str, Any]]:
        snapshot = workflow_graph(request).get_state({"configurable": {"thread_id": thread_id}})
        stored_submission = snapshot.values.get("submission")
        if stored_submission is None:
            raise HTTPException(status_code=404, detail="Decision thread was not found")
        try:
            authorize_sanitized_submission(
                authenticate(request),
                SanitizedSubmission.model_validate(stored_submission),
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return workflow_services(request).audit.events_for_thread(thread_id)

    return app


def run() -> None:
    import uvicorn

    configuration = Settings()
    uvicorn.run(create_app(configuration), host="127.0.0.1", port=8080)


if __name__ == "__main__":
    run()
