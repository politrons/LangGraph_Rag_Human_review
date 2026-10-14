# `scripts/build_rag_index.py` — building the local RAG knowledge index

![Offline knowledge preparation and online retrieval](assets/retrieval-scoring.svg)

## Purpose

`scripts/build_rag_index.py` is an offline command-line tool. It turns
versioned Markdown or text reference material into a JSON Lines index that the
port-8081 RAG service's hybrid retriever can search. It is intentionally
separate from both running APIs: case requests never rebuild knowledge or mutate
the index.

## Command and inputs

The `python3 scripts/build_rag_index.py` command accepts a documents directory, output JSONL
path, Ollama base URL and embedding model, index version, and one or more
`--allowed-role` values. The included example indexes
`data/knowledge/underwriting_referral_guidelines.md` for underwriters and claims
adjusters using the local `nomic-embed-text` model.

## Index construction sequence

1. Recursively find only `.md` and `.txt` files, in deterministic sorted order.
2. Read each file as UTF-8 and split it into paragraph-aware fragments.
3. Keep fragments near 1,200 characters, splitting long content with a 160
   character overlap so boundary context is retained.
4. Call `OllamaEmbeddingClient` for each fragment through native `/api/embed`.
5. Write one JSON object per line containing chunk ID, source, human title,
   version, text, vector, and allowed roles.
6. Print a compact JSON summary with document count, chunk count, and output.

## What a single JSONL row means

| Field | Meaning at retrieval time |
| --- | --- |
| `chunk_id` | Stable citation identifier, built from file stem and sequence |
| `source` and `title` | Human traceability back to the knowledge file |
| `version` | Index/content version a proposal can be investigated against |
| `text` | Fragment that may later become a reviewer-visible evidence excerpt |
| `embedding` | Numeric vector for cosine similarity |
| `allowed_roles` | Pre-LLM access filter; empty means no role restriction in current logic |

The vectors and index must be rebuilt if the embedding model changes. A query
vector with a different dimension is rejected by `retrieval.py`; silently mixing
models would make semantic ranking invalid.

## What this tool does not do

It does not call an OpenAI-compatible endpoint, upload documents, create a
vector database, or send case data anywhere. It uses the same local native
Ollama adapter as online retrieval, but its input is administrator-supplied
knowledge rather than a case.

## Operational considerations

Treat index output as a versioned derived artifact. Review source documents,
roles, and model choice before regeneration; regenerate in a controlled step;
then validate a representative retrieval query. The index contains knowledge
text and vectors, so it should receive the same access consideration as the
source guidance even in a local learning project.
