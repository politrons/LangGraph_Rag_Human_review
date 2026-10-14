# `sanity_build/smoke.py` — real localhost verification and readable output

![The smoke test runs the same two-request lifecycle](assets/api-lifecycle.svg)

## Purpose

`sanity_build/smoke.py` is a developer-facing executable verification of the
full local pattern. Unlike the unit tests, it uses real HTTP against the running
decision FastAPI service on port 8080 and therefore exercises the live wiring to
the parent LangGraph, the separate RAG LangGraph service on port 8081, SQLite
audit storage, the generated RAG index, and local Ollama.

It is invoked with `python3 sanity_build/smoke.py`; it is intentionally not an
installed application command.

## What one run does

1. Creates a unique non-PII case ID beginning `LOCAL-SMOKE-`.
2. Calls parent `/health` and stops on an unavailable decision server.
3. Starts a normal property recommendation using valid development identity
   headers and waits for the graph to reach its review interrupt.
4. Verifies that the initial response is `awaiting_human` and retains the graph
   thread ID returned by the service.
5. Calls the manual-review endpoint with either `approve` or `reject`.
6. Verifies the completed status and expected routing: `recorded` for approve,
   `rejected` for reject.
7. Reads the audit endpoint and confirms all expected lifecycle events exist.
8. Prints one pretty-indented JSON object to standard output.

## Readable JSON result

The output contains the full initial response and the **complete response from
the manual decision request**, rather than only a one-line status summary:

| Field | Why it is useful |
| --- | --- |
| `health` | Confirms the service/environment used |
| `case_id` and `thread_id` | Identifiers for later audit investigation |
| `initial_decision` | Full policy, model proposal, evidence, and review request returned before pause |
| `manual_review_response` | Full final proposal, routing, and evaluation after approve/reject |
| `audit_event_types` | Compact proof of every expected state transition |

The final print uses Python JSON indentation, so nested `routing` and
`evaluation` structures remain readable in a terminal. The README’s manual curl
review example also pipes its response through `python3 -m json.tool` for the
same reason.

## CLI options

| Option | Default | Use |
| --- | --- | --- |
| `--base-url` | `http://127.0.0.1:8080` | Target a different local host/port |
| `--actor-id` | `underwriter-smoke` | Change development actor/header identity |
| `--tenant-id` | `tenant-local` | Change tenant for a local test namespace |
| `--outcome` | `approve` | Exercise `approve` or `reject` branch |

The command deliberately has no API-key option because the current development
mode uses local identity headers only. Do not point it at an unaudited remote
environment; it is a learning-mode test client.

## Failure interpretation

An HTTP error means the parent server, RAG service, Ollama, or another required
integration was not available. A
`SmokeTestFailure` means the service replied but violated a documented lifecycle
expectation, such as failing to pause for human review, reporting an incorrect
routing status, or omitting an audit event. In both cases the command exits with
status one and a concise error message suitable for a terminal or CI log.

## Relationship to the unit suite

`tests/test_smoke.py` uses a minimal, explicitly named `httpx.MockTransport`
test double to verify the client’s expected HTTP sequence without a live server.
That test proves the smoke command logic; an actual command run proves the
installed local system wiring.
