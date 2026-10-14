# `evaluation.py` — deterministic post-decision checks

## Purpose

`src/ai_architect/decision/evaluation.py` measures whether a completed proposal followed
the evidence and policy rules already established by the workflow. It is a small,
deterministic online evaluator rather than another LLM judge. Running it after
route/close means approved, rejected, and escalated cases all receive a
consistent quality record.

## Inputs and outputs

| Input | Why it is required |
| --- | --- |
| `PolicyResult` | Detects an impossible proposal from a blocked path |
| `DecisionProposal` | Provides citations, recommendation, and escalation reason |
| Retrieved evidence | Defines the allowed citation set |

The output is `EvaluationResult`: a pass/fail boolean, groundedness from zero to
one, policy compliance from zero to one, and concrete reasons when a check fails.
`audit.py` persists it as both an audit event and three metric measurements.

## Checks performed today

| Check | Calculation | Failure meaning |
| --- | --- | --- |
| Groundedness | Proposal citation IDs intersected with evidence IDs, divided by citation count | A proposal cited something the retriever did not return |
| Referral completeness | `refer` must contain an escalation reason | A reviewer lacks the stated reason for referral |
| Policy compliance | Zero when policy is `block`; otherwise one | A blocked case somehow reached a proposal |

The decision adapter already rejects citations outside retrieved evidence, so the
groundedness check is defense in depth and creates an observable metric. The
blocked-path check is also a workflow-invariant monitor: it should remain one in
a healthy implementation because the graph routes a block to `END`.

## What evaluation does not claim

It does not assess actuarial correctness, fairness, factual truth beyond the
provided evidence IDs, or whether a human made the right commercial decision. It
also does not authorize or revoke a review. It is a transparent guardrail and
monitoring signal, which makes it suitable for a learning pattern and a useful
base for versioned golden-case checks later.

## Extending it

Add checks that are deterministic, explainable, and measurable. Examples are
required policy version presence, prohibited language detection, minimum
evidence diversity, or known business thresholds. Every new check should return
a reason, be added to test cases, and have a clear decision on whether it is
observational or blocks a downstream action.
