# Root files, knowledge assets, and documentation structure

## `README.md`

The root README is the quick-start guide. It explains local Ollama setup,
indexing, server startup, the localhost API lifecycle, formatted manual review,
audit/metrics queries, prompt-injection experiment, learning-mode boundaries,
and verification commands. It should be used to run the pattern; this component
reference explains why each file exists.

## `pyproject.toml`

This file defines a Python 3.12+ package named `ai-architect-insurance`. Its
runtime dependencies are FastAPI, HTTPX, LangGraph, Pydantic/settings, PyJWT,
and Uvicorn. PostgreSQL checkpoint/audit support is opt-in. Development tools are
Pytest, pytest-asyncio, and Ruff.

It declares the two importable server commands:

| Command | Entry point | Function |
| --- | --- | --- |
| `ai-architect` | `decision.api.run` | Starts local FastAPI server on port 8080 |
| `ai-architect-rag` | `rag.service.run` | Starts separate RAG FastAPI/LangGraph service on port 8081 |

Both server entry modules also support `python3 -m ai_architect.rag.service` and
`python3 -m ai_architect.decision.api`.

The executable tools stay outside `src/` because they are not application
features loaded by either server:

| Path | Command | Function |
| --- | --- | --- |
| `scripts/build_rag_index.py` | `python3 scripts/build_rag_index.py …` | Builds the local JSONL index offline |
| `sanity_build/smoke.py` | `python3 sanity_build/smoke.py` | Verifies the running services end to end |

## `src/ai_architect/__init__.py`

This one-line package module provides the package-level description, “Governed
insurance decision-support service.” It contains no runtime composition or
business behavior; the importable implementation begins in the other modules
documented in this reference.

## `.env.example`

The example file documents the minimal local configuration and the optional
non-development seams. It has no OpenAI key, Databricks value, or local API key.
It does contain an audit-secret placeholder, which must be replaced with a long
random local value. `.env` is user-owned runtime configuration and should not be
committed with real secrets.

## `data/knowledge/underwriting_referral_guidelines.md`

This is the included source knowledge for the tutorial. It says when property
cases should be referred, requires evidence/version traceability, and requires a
human outcome before downstream routing. `scripts/build_rag_index.py` turns it into the JSONL
artifact under `data/index/`. Updating the guideline should trigger a controlled
reindex with a new version.

## Generated local data

| Path | Produced by | Meaning | Retention note |
| --- | --- | --- | --- |
| `data/index/policy_chunks.jsonl` | `scripts/build_rag_index.py` | Versioned text/vector RAG index | Derived knowledge artifact |
| `data/audit.sqlite3` | Running audit ledger | Local audit/metrics database | Contains sanitized execution metadata; delete only intentionally |

## Existing high-level documentation

The parent `docs/` pages describe the architecture, lifecycle, security, and
operations at system level. `docs/components/` adds a file-by-file learning path
and references the detailed diagrams used here.

## Root-level `auth.py` note

The root-level `auth.py` is not included in `pyproject.toml` package discovery
and is not imported by the running service. The active module is
`src/ai_architect/governance/auth.py`. The root file is an older standalone copy with a
different local API-key behavior and a relative import that is not valid as a
top-level module. Treat it as an orphaned historical artifact; do not use it as
the reference for the current local no-API-key pattern.

This documentation records the distinction without deleting user files. If the
project is later cleaned up, remove or archive that orphan only after confirming
no external tooling imports it.
