# `policy.py` — PII gate and deterministic governance rules

![Untrusted input is reduced to a safe internal case](assets/data-contracts.svg)

## Purpose

`src/ai_architect/decision/policy.py` is the first domain boundary after API validation.
It converts the rich external case into a PII-minimized internal case and then
creates a policy result that travels with the workflow. It is intentionally
deterministic: the safety decision is made by code, not by a language model.

## Two separate operations

| Operation | Input | Output | Why it is separate |
| --- | --- | --- | --- |
| `sanitize()` | `CaseSubmission` | `SanitizedSubmission` | Data minimization must happen before persistence or model work |
| `evaluate()` | `SanitizedSubmission` | `PolicyResult` | Policy can be inspected and audited without raw values |

The API calls `sanitize()` before it appends `case_received` or invokes
LangGraph. The graph then calls `evaluate()` in its first node. This ordering
ensures that graph state, RAG, Ollama, audit, and routing see only the safe form.

## What `sanitize()` removes or transforms

| Data location | Treatment | Downstream result |
| --- | --- | --- |
| `applicant_email` field | Removed from the safe model | Only increments redaction count |
| `applicant_phone` field | Removed from the safe model | Only increments redaction count |
| `customer_reference` field | Removed from the safe model | Only increments redaction count |
| Email in narrative | Replaced with `[EMAIL_REDACTED]` | Marker may appear; original may not |
| Phone-like number in narrative | Replaced with `[PHONE_REDACTED]` | Marker may appear; original may not |
| IBAN-like value in narrative | Replaced with `[IBAN_REDACTED]` | Marker may appear; original may not |

The redaction count is useful for visibility without retaining the sensitive
value. The pattern set is intentionally limited and documented; it is a
concrete learning control, not a claim of universal PII detection.

## Policy rules currently implemented

`evaluate()` produces `allow` when no listed condition is present and
`require_review` when one or more is present:

| Condition | Detection | Policy reason | Practical effect |
| --- | --- | --- | --- |
| Prompt-injection signal | Expressions such as ignoring prior instructions, revealing a prompt, or acting as system/developer | Possible hostile content needs specialist review | Flag stays visible and case still uses only sanitized data |
| High exposure | Exposure at least 5,000,000 | Specialist review required | Proposal cannot bypass the human interrupt |
| Bind or pay-claim request | Requested action is `bind` or `pay_claim` | Service may recommend only | Makes the non-autonomy boundary explicit |

The current code never returns `block`, but the workflow supports that
disposition as a ready fail-closed branch for future absolute prohibitions. A
new block rule would end before RAG, score, model proposal, review, or routing.

## Injection handling in this pattern

The gate does not execute or obey narrative text. It labels certain patterns as
untrusted and creates a review reason. Independently, both Ollama system prompts
tell the model that case data and evidence are untrusted data, not instructions.
The two measures serve different purposes: the gate produces a governed signal;
the prompt reduces model susceptibility. Neither replaces careful model and data
security work in a real deployment.

## How this component relates to the rest of the flow

1. `api.py` authorizes the caller, calls `sanitize()`, and audits the safe case.
2. `workflow.py` calls `evaluate()` and stores/audits `PolicyResult`.
3. The parent `retrieve` node passes only a minimized sanitized query and roles
   to `rag/service.py`; `rag/retrieval.py`, `integrations/clients.py`, and
   `governance/audit.py` then work
   only with the safe contract.
4. `evaluation.py` records whether a blocked policy ever reached a proposal.

## Change safely

When adding PII categories, update detection, markers, tests, data-retention
documentation, and any audit safeguard together. When adding policy rules,
return a human-readable reason and decide whether the right disposition is
`require_review` or `block`. Never add a raw original field to
`SanitizedSubmission` simply to make it easy for a prompt to use.

## Validation evidence

`tests/test_policy.py` supplies email and phone data in both fields and free
text. It verifies that those values are absent from the sanitized narrative, the
redaction count is correct, and a sensitive bind request requires review.
