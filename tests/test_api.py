from fastapi.testclient import TestClient


def identity_headers(actor_id: str, roles: str = "underwriter") -> dict[str, str]:
    return {
        "X-Development-Actor-ID": actor_id,
        "X-Development-Tenant-ID": "tenant-a",
        "X-Development-Roles": roles,
    }


def test_approved_case_requires_review_then_routes_and_audits_without_pii(app_parts) -> None:
    app, services, router = app_parts
    thread_id = "00000000-0000-0000-0000-000000000001"
    body = {
        "thread_id": thread_id,
        "submission": {
            "case_id": "CASE-900",
            "tenant_id": "tenant-a",
            "actor_id": "underwriter-1",
            "actor_roles": ["underwriter"],
            "requested_action": "bind",
            "product": "Property",
            "narrative": "Request is for a high flood exposure property; email jane@example.com.",
            "exposure_amount": 750000,
            "applicant_email": "jane@example.com",
        },
    }
    with TestClient(app) as client:
        initial = client.post(
            "/v1/cases/decisions",
            json=body,
            headers=identity_headers("underwriter-1"),
        )
        assert initial.status_code == 202
        assert initial.json()["status"] == "awaiting_human"
        assert initial.json()["review_request"]["proposal"]["recommendation"] == "refer"

        completed = client.post(
            f"/v1/cases/CASE-900/decisions/{thread_id}/review",
            json={
                "review": {
                    "reviewer_id": "senior-underwriter-2",
                    "outcome": "approve",
                    "rationale": "Referral is appropriate; proceed to specialist queue.",
                }
            },
            headers=identity_headers("senior-underwriter-2"),
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["routing"]["external_reference"] == "route-123"

        audit = client.get(f"/v1/audit/{thread_id}", headers=identity_headers("underwriter-1"))
        assert audit.status_code == 200
        assert "jane@example.com" not in audit.text
        assert router.routed_case_ids == ["CASE-900"]
        assert services.audit.metrics_summary()["evaluation_passed"]["average"] == 1.0


def test_rejected_case_does_not_call_case_routing(app_parts) -> None:
    app, _, router = app_parts
    thread_id = "00000000-0000-0000-0000-000000000002"
    body = {
        "thread_id": thread_id,
        "submission": {
            "case_id": "CASE-901",
            "tenant_id": "tenant-a",
            "actor_id": "underwriter-1",
            "actor_roles": ["underwriter"],
            "requested_action": "recommend",
            "product": "Property",
            "narrative": "Property needs an underwriting referral due to flood exposure.",
            "exposure_amount": 750000,
        },
    }
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/cases/decisions",
                json=body,
                headers=identity_headers("underwriter-1"),
            ).status_code
            == 202
        )
        rejected = client.post(
            f"/v1/cases/CASE-901/decisions/{thread_id}/review",
            json={
                "review": {
                    "reviewer_id": "senior-underwriter-2",
                    "outcome": "reject",
                    "rationale": "Insufficient supporting information.",
                }
            },
            headers=identity_headers("senior-underwriter-2"),
        )
    assert rejected.status_code == 200
    assert rejected.json()["routing"]["status"] == "rejected"
    assert router.routed_case_ids == []


def test_identity_claims_must_match_case_submission_and_metrics_need_admin(app_parts) -> None:
    app, _, _ = app_parts
    body = {
        "submission": {
            "case_id": "CASE-902",
            "tenant_id": "tenant-a",
            "actor_id": "underwriter-1",
            "actor_roles": ["underwriter"],
            "requested_action": "recommend",
            "product": "Property",
            "narrative": "Property needs an underwriting referral due to flood exposure.",
            "exposure_amount": 750000,
        }
    }
    with TestClient(app) as client:
        forbidden = client.post(
            "/v1/cases/decisions",
            json=body,
            headers=identity_headers("different-user"),
        )
        assert forbidden.status_code == 403

        assert (
            client.get("/v1/metrics", headers=identity_headers("underwriter-1")).status_code == 403
        )
        admin = client.get("/v1/metrics", headers=identity_headers("ai-admin", "ai_admin"))
        assert admin.status_code == 200
