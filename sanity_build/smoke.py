from __future__ import annotations

import argparse
import json
from typing import Any
from uuid import uuid4

import httpx


class SmokeTestFailure(RuntimeError):
    """Raised when the running local service does not follow its documented flow."""


def _headers(*, actor_id: str, tenant_id: str, roles: str) -> dict[str, str]:
    return {
        "X-Development-Actor-ID": actor_id,
        "X-Development-Tenant-ID": tenant_id,
        "X-Development-Roles": roles,
    }


def run_smoke(
    *,
    base_url: str,
    actor_id: str,
    tenant_id: str,
    outcome: str,
) -> dict[str, Any]:
    """Exercise health, decision, human review, routing, and audit over real HTTP."""

    case_id = f"LOCAL-SMOKE-{uuid4().hex[:12]}"
    headers = _headers(
        actor_id=actor_id,
        tenant_id=tenant_id,
        roles="underwriter",
    )
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=90) as client:
        health = client.get("/health")
        health.raise_for_status()
        start = client.post(
            "/v1/cases/decisions",
            headers=headers,
            json={
                "submission": {
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "actor_roles": ["underwriter"],
                    "requested_action": "recommend",
                    "product": "property",
                    "narrative": (
                        "Commercial property has a history of water damage claims "
                        "in a flood-prone district."
                    ),
                    "exposure_amount": 300_000,
                }
            },
        )
        start.raise_for_status()
        start_body = start.json()
        if start_body.get("status") != "awaiting_human":
            raise SmokeTestFailure(f"Expected awaiting_human, received {start_body}")

        thread_id = str(start_body["thread_id"])
        review = client.post(
            f"/v1/cases/{case_id}/decisions/{thread_id}/review",
            headers=headers,
            json={
                "review": {
                    "reviewer_id": actor_id,
                    "outcome": outcome,
                    "rationale": f"Local smoke test recorded outcome: {outcome}.",
                }
            },
        )
        review.raise_for_status()
        review_body = review.json()
        expected_routing = "recorded" if outcome == "approve" else "rejected"
        routing = review_body.get("routing", {})
        if review_body.get("status") != "completed" or routing.get("status") != expected_routing:
            raise SmokeTestFailure(f"Unexpected review result: {review_body}")

        audit = client.get(f"/v1/audit/{thread_id}", headers=headers)
        audit.raise_for_status()
        events = audit.json()
        event_types = [event["event_type"] for event in events]
        required_events = {
            "case_received",
            "policy_evaluated",
            "evidence_retrieved",
            "ml_score_received",
            "decision_proposed",
            "human_review_recorded",
            "decision_evaluated",
        }
        if outcome == "approve":
            required_events.add("case_recorded_locally")
        else:
            required_events.add("case_rejected")
        missing_events = required_events.difference(event_types)
        if missing_events:
            raise SmokeTestFailure(f"Audit is missing expected events: {sorted(missing_events)}")

    return {
        "health": health.json(),
        "case_id": case_id,
        "thread_id": thread_id,
        "initial_decision": start_body,
        "manual_review_response": review_body,
        "audit_event_types": event_types,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an end-to-end smoke test against a running local AI Architect API."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--actor-id", default="underwriter-smoke")
    parser.add_argument("--tenant-id", default="tenant-local")
    parser.add_argument("--outcome", choices=("approve", "reject"), default="approve")
    args = parser.parse_args()

    try:
        result = run_smoke(
            base_url=args.base_url,
            actor_id=args.actor_id,
            tenant_id=args.tenant_id,
            outcome=args.outcome,
        )
    except (httpx.HTTPError, KeyError, SmokeTestFailure) as exc:
        parser.exit(status=1, message=f"Smoke test failed: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
