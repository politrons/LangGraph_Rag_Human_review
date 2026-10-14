from __future__ import annotations

import httpx

from sanity_build import smoke


def test_smoke_exercises_the_documented_local_approval_flow(monkeypatch) -> None:
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        """Test double: emulates only the documented running API responses."""
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if request.url.path == "/v1/cases/decisions":
            return httpx.Response(
                202,
                json={
                    "thread_id": "thread-local-001",
                    "status": "awaiting_human",
                    "review_request": {"case_id": "LOCAL-SMOKE-test"},
                },
                request=request,
            )
        if request.url.path.endswith("/review"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "routing": {"status": "recorded", "destination": "local-only"},
                },
                request=request,
            )
        if request.url.path == "/v1/audit/thread-local-001":
            events = [
                "case_received",
                "policy_evaluated",
                "evidence_retrieved",
                "ml_score_received",
                "decision_proposed",
                "human_review_recorded",
                "case_recorded_locally",
                "decision_evaluated",
            ]
            return httpx.Response(
                200,
                json=[{"event_type": event} for event in events],
                request=request,
            )
        return httpx.Response(404, request=request)

    def test_double_client(*, base_url: str, timeout: float) -> httpx.Client:
        """Test double: routes smoke-test HTTP calls to the in-memory transport."""
        return real_client(
            base_url=base_url,
            timeout=timeout,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(smoke.httpx, "Client", test_double_client)

    result = smoke.run_smoke(
        base_url="http://local.test",
        actor_id="underwriter-smoke",
        tenant_id="tenant-local",
        outcome="approve",
    )

    assert result["initial_decision"]["status"] == "awaiting_human"
    assert result["manual_review_response"]["status"] == "completed"
    assert result["manual_review_response"]["routing"]["status"] == "recorded"
    assert "case_recorded_locally" in result["audit_event_types"]
