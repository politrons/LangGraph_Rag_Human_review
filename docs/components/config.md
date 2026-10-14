# `config.py` — explicit runtime choices and environment guardrails

## Purpose

`src/ai_architect/shared/config.py` is the single configuration schema. It uses
`pydantic-settings` to read environment variables and `.env`, validate their
types, and stop startup when a chosen deployment mode lacks the required
infrastructure. It keeps environment branching out of business logic.

## Configuration groups

| Group | Settings | Local default | What consumes them |
| --- | --- | --- | --- |
| Environment and identity | `APP_ENV`, OIDC JWKS/issuer/audience | `development`, no OIDC | `api.py`, `auth.py` |
| Graph state | `STATE_BACKEND`, `POSTGRES_CONNECTION_URI` | `memory` | `api.py` lifespan |
| Audit persistence | `AUDIT_DATABASE_URL`, `AUDIT_HMAC_KEY` | SQLite under `data/` | `audit.py` |
| Ollama | Base URL, embedding/decision/scoring model names | Loopback Ollama, `nomic-embed-text`, `llama3.1:8b` | `integrations/clients.py`, `decision/api.py`, `rag/service.py` |
| Distributed RAG | `RAG_SERVICE_URL`, `RAG_INDEX_PATH` | `http://127.0.0.1:8081`, generated JSONL index | parent adapter and `rag/service.py` |
| External routing | URL, API key, timeout | unset | `clients.py` via non-development `api.py` |

## Startup validation

Two checks make unsafe partial deployments visible:

1. Choosing `STATE_BACKEND=postgres` without a PostgreSQL connection URI fails
   before serving requests.
2. Choosing `staging` or `production` without all three OIDC values fails before
   serving requests.

The audit database URL must use `sqlite:///` or `postgresql://`. The audit HMAC
key is treated as a secret and is enforced by the ledger to be at least 24
characters. In local mode, `.env.example` gives the structure but intentionally
uses a placeholder; replace it before starting.

## Local mode explained

The learning configuration is intentionally small:

- LangGraph checkpoints live in memory and disappear on process restart.
- Audit events live in the local SQLite file under `data/`.
- Ollama is reached at `http://127.0.0.1:11434` through its native APIs.
- The parent graph calls a separate local RAG service at
  `http://127.0.0.1:8081`; that service opens the local JSONL index.
- Routing is forced to a local record, even if obsolete external-routing values
  exist in `.env`.

This is enough to study the architecture without an OpenAI account, Databricks,
a vector database, or a case-management system.

## Non-development behavior

Configuration does not itself make the system production-ready. It only exposes
the required seams: OIDC is mandatory, PostgreSQL graph state can be selected,
and an HTTP case router can be enabled if both its URL and secret are supplied.
Production still needs network controls, secret management, calibrated scoring,
operational monitoring, review of data classification, and tests against its
real integrations.

## Relationship to `pyproject.toml` and `.env.example`

`pyproject.toml` declares the dependencies and four commands; `.env.example`
documents the local values and which settings are optional or environment-only.
The schema is the source of truth for accepted variable names and types. Unknown
values are ignored deliberately to avoid a stale local `.env` preventing the
tutorial from running; do not rely on that behavior to manage production
configuration drift.

## How to validate configuration

Run `ai-architect-rag` and then `ai-architect` after copying `.env.example` to
`.env` and setting a long local audit key. An invalid audit URL, a missing audit
key, an unavailable RAG URL, or a selected PostgreSQL/OIDC mode without its
required fields should stop or visibly fail rather than cause a partially
governed request later.
