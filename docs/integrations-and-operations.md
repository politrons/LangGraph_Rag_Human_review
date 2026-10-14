# Integrations and operations

## Local runtime

This project is configured for a two-process, single-machine deployment. The
RAG process owns query embeddings; the decision process owns the local risk
estimate and decision proposal. Ollama provides those model operations through
its native API. No OpenAI-compatible endpoint, OpenAI API key, Databricks host,
or external case-management URL is used in development.

| Group | Variables | Purpose |
| --- | --- | --- |
| Local identity | APP_ENV and development identity headers | Enables local learning-mode identity context |
| Parent graph state | STATE_BACKEND | Uses memory for decision graph checkpoints |
| Audit | AUDIT_DATABASE_URL and AUDIT_HMAC_KEY | Stores the local audit chain in SQLite |
| Ollama | OLLAMA_BASE_URL | Native API address; default is http://127.0.0.1:11434 |
| Ollama decision | OLLAMA_DECISION_MODEL | Generates the cited decision proposal |
| Ollama scoring | OLLAMA_SCORING_MODEL | Generates an explicitly non-calibrated local risk estimate |
| Ollama embeddings | OLLAMA_EMBEDDING_MODEL | Generates vectors for RAG indexing and search in port-8081 service |
| Distributed RAG | RAG_SERVICE_URL and RAG_INDEX_PATH | Parent calls port 8081; RAG process reads versioned local JSONL knowledge index |
| Optional routing | CASE_ROUTING_URL and CASE_ROUTING_API_KEY | Reserved for a future non-development external integration |

The defaults in .env.example are enough for the local flow. Existing legacy
variables for OpenAI or Databricks are ignored and can be removed from a local
.env file when convenient.

## Required local models

Ollama must be running and have one chat model plus one embedding model:

~~~bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama list
~~~

The project defaults to llama3.1:8b for both proposal and local score
generation, and to nomic-embed-text for embeddings. A score made by a chat
model is suitable only for exercising the workflow: it is not a calibrated or
validated insurance risk model.

## Native Ollama contracts

### Embeddings

The RAG service and the offline indexer call the native endpoint:

~~~text
POST {OLLAMA_BASE_URL}/api/embed
{
  "model": "nomic-embed-text",
  "input": "sanitized text"
}
~~~

Ollama returns embeddings[0], a numeric vector. The exact same embedding model
must be used to build and query an index; changing the model requires
reindexing because cosine similarity is meaningful only within one vector
space.

### Local risk estimate

The scoring node calls the native chat endpoint with a JSON schema for score
and factors:

~~~text
POST {OLLAMA_BASE_URL}/api/chat
{
  "model": "llama3.1:8b",
  "stream": false,
  "format": { "JSON Schema for score and factors" }
}
~~~

The result is stored as an Ollama model with version local-development. This
provenance makes the limitation visible in the review request and audit record.
It cannot bind a policy, pay a claim, or replace an existing ML model.

### Decision proposal

The decision node uses the same native chat endpoint, but supplies the JSON
schema for DecisionProposal. Ollama receives the sanitized case, local risk
estimate, retrieved evidence, and the allowed evidence IDs. The response is
validated again by the service, including a check that every cited ID was
actually retrieved.

Ollama structured output is used through its native format parameter; the
service does not use an OpenAI client or OpenAI-compatible route. This follows
Ollama's documented structured-output and embedding APIs. [Ollama structured
outputs](https://ollama.com/blog/structured-outputs) and [Ollama embedding
models](https://ollama.com/blog/embedding-models) document these endpoints.

## Building the knowledge index

The indexer accepts Markdown and text documents, splits them into chunks,
requests native Ollama embeddings, and writes JSONL:

~~~bash
python3 scripts/build_rag_index.py --documents data/knowledge --output data/index/policy_chunks.jsonl --version 2026.10 --allowed-role underwriter --allowed-role claims_adjuster
~~~

The command defaults to the configured local address and nomic-embed-text. To
use a different local Ollama instance or embedding model:

~~~bash
python3 scripts/build_rag_index.py --documents data/knowledge --output data/index/policy_chunks.jsonl --version 2026.10 --ollama-base-url http://127.0.0.1:11434 --ollama-embedding-model nomic-embed-text --allowed-role underwriter
~~~

Keep a previous index copy when changing the knowledge base or embedding
model. Test representative queries for each role and verify that restricted
chunks never appear in unauthorized results.

## What local case routing means

Case routing is normally the integration that would send an approved result to
a case-management system: a claims platform, underwriting queue, CRM, ticket
system, or internal workflow API.

There is no such dependency in development. On approve, LocalCaseRouter creates
the result:

~~~json
{
  "status": "recorded",
  "destination": "local-only",
  "external_reference": null
}
~~~

The graph then evaluates and audits that result. No HTTP request is made and no
real business action occurs. Reject and escalate also never invoke routing.

CASE_ROUTING_URL and CASE_ROUTING_API_KEY remain optional for a deliberate
future non-development integration. In that mode, the receiving system must
provide service authentication, TLS, idempotency, a correlation ID based on
thread_id, receipt audit records, and a versioned contract.

## Local security baseline

| Control | Local behavior |
| --- | --- |
| Network exposure | Both FastAPI services bind to 127.0.0.1 by default. |
| API authentication | None; local identity headers are intentionally unauthenticated. |
| Identity isolation | The actor, tenant, and role set in the request must match local headers. |
| Data minimization | PII redaction happens before Ollama, graph persistence, and audit. |
| Service boundary | Parent sends RAG only sanitized query text, roles, bounds, and correlation metadata. |
| Human control | Every graph execution pauses for review before the approval branch. |
| Audit integrity | SQLite events are HMAC-chained per thread. |
| Outbound actions | None in development; approval creates a local-only record. |

Do not expose the application to a network or use real customer data in this
mode. For staging or production, restore OIDC, durable PostgreSQL state,
network controls, calibrated scoring, secret management, and an explicitly
reviewed routing integration.

## Local run and validation

~~~bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,postgres]'
cp .env.example .env
./.venv/bin/python scripts/build_rag_index.py --documents data/knowledge --output data/index/policy_chunks.jsonl --version 2026.10 --allowed-role underwriter
./.venv/bin/ai-architect-rag
./.venv/bin/ai-architect
~~~

Start `ai-architect-rag` before `ai-architect`. The RAG service listens on
`127.0.0.1:8081`; its independent OpenAPI interface is at `/docs`. The decision
API listens on `127.0.0.1:8080` and has its own `/docs` interface. Verify both:

~~~bash
curl --fail --silent \
  http://127.0.0.1:8081/health | python3 -m json.tool

curl --fail --silent \
  http://127.0.0.1:8080/health | python3 -m json.tool
~~~

Before committing changes, run:

~~~bash
./.venv/bin/ruff check src scripts sanity_build tests
./.venv/bin/python -m compileall -q src scripts sanity_build tests
./.venv/bin/pytest
~~~

The unit suite uses explicitly labeled test doubles only under tests. It also
checks the RAG subgraph and parent adapter, native Ollama client payloads, local
score provenance, and the no-network local routing result.
