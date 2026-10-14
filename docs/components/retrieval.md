# `retrieval.py` — role-filtered hybrid RAG

![Hybrid rank calculation and evidence handoff](assets/retrieval-scoring.svg)

## Purpose

`src/ai_architect/rag/retrieval.py` implements the RAG part of the pattern inside
the separate RAG service, without a remote vector database. The service loads
the generated JSONL index into memory, filters chunks by caller role, and
combines lexical BM25 relevance with semantic cosine similarity. Its output is
typed evidence, not an unbounded prompt string.

## Runtime sequence

1. `from_jsonl()` validates every non-empty index row and turns it into an
   immutable `IndexedChunk`.
2. Initialization tokenizes texts, calculates document frequencies, and retains
   term counts/lengths required for BM25.
3. The parent graph constructs a query from sanitized product plus sanitized
   narrative and sends it with roles to port `8081`.
4. The RAG subgraph's `retrieve` node calls this class; its embedding client
   creates a query vector locally through Ollama.
5. Candidate chunks are removed if the caller roles do not intersect their
   `allowed_roles` set.
6. Remaining chunks receive BM25, cosine, and weighted hybrid scores.
7. The highest-ranked five evidence objects are returned by default.

## Ranking formula

BM25 supplies exact-term relevance with `k1 = 1.5` and `b = 0.75`. Cosine
similarity supplies semantic proximity. For each authorized candidate, the
implementation computes:

`hybrid = 0.45 × normalized BM25 + 0.55 × max(cosine, 0)`

Normalizing BM25 against the best candidate makes the two signals comparable;
clamping negative cosine avoids rewarding semantic opposition. The exact weights
are a learning-project choice, not an empirically tuned claim.

## Access control happens before generation

Role filtering is performed before results become `RetrievedEvidence` and before
the decision-maker prompt is built. A prompt cannot recover a chunk excluded by
this layer. This is materially safer than sending every document to the model and
asking it to respect role instructions in natural language.

## Evidence result contents

Every returned object includes a stable chunk ID, source/title/version, bounded
excerpt, optional URL, lexical score, semantic score, and hybrid score. The
decision maker receives these objects and must cite only their chunk IDs. It
cannot cite a chunk that was not retrieved; `clients.py` validates that rule.

## Failure behavior

The RAG service fails at startup if its index file is missing. An empty index,
malformed JSONL row, missing embedding, mismatched vector dimension, or zero
authorized evidence results in a visible failure. The parent receives an HTTP
dependency failure and does not turn a missing knowledge base into an invented
answer.

## Technology boundary

This is plain Python BM25 and cosine math plus a local JSONL file. It runs inside
the `StateGraph` compiled by [rag/service.py](rag-service.md), but it does not
use LangChain, a hosted vector store, or an OpenAI API. Replacing it with a
production retrieval service is an extension point, but the pre-retrieval
authorization property must be retained.

## Validation evidence

`tests/test_retrieval.py` uses a clearly labeled test-double embedding client
and two equal-text chunks with different allowed roles. It proves that only the
underwriter-permitted chunk is returned and that its combined score remains
meaningful.
