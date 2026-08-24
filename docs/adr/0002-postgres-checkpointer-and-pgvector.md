# 0002: Use Postgres for both the LangGraph checkpointer and the RAG vector store (pgvector)

## Status

Accepted.

## Context

Two independent stateful needs exist in this system:

1. **LangGraph checkpointing** -- durable, per-`thread_id` (= `incident_id`)
   storage of `IncidentState` so a run can pause at the approval gate and
   resume later (ADR 0001). `src/sre_agent/graph/checkpointer.py` wraps
   `langgraph.checkpoint.postgres.PostgresSaver`.
2. **RAG storage** -- embeddings for the runbook/postmortem corpus
   (`runbook_chunks` table), queried by the Diagnostic Agent on every RCA
   pass via cosine similarity (`rag/vector_store.py`,
   `rag/retriever.py`).

The obvious alternative pairing is **Redis for checkpointing** (fast,
LangGraph has a Redis checkpoint saver in the ecosystem) and **a dedicated
vector database like Qdrant/Pinecone for RAG** (purpose-built ANN indexes).
We evaluated that pairing and rejected it in favor of a single Postgres
instance backing both.

## Decision

Use Postgres for both:

- `PostgresSaver` (via `langgraph.checkpoint.postgres`) for the LangGraph
  checkpointer, with `.setup()` run once per environment to create the
  checkpoint tables.
- `pgvector` for the RAG store: a `runbook_chunks(chunk_id, doc_id, text,
  metadata jsonb, embedding vector(256))` table with an HNSW index
  (`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`), populated by
  `rag/ingest.py` and queried by `PgVectorStore.similarity_search()`
  (`ORDER BY embedding <=> query LIMIT k`, optionally filtered on
  `metadata->>'service'`).

### Why not Redis for the checkpointer

An approval can sit pending for `APPROVAL_QUEUE_STALE_MINUTES` (15 minutes by
default, and in practice on-call engineers routinely take longer than that
before `expire_stale()` gives up on it) -- so checkpoint durability has to
survive well past Redis's typical operating assumptions. Redis *can* be
configured for durability (AOF, RDB snapshots with careful `fsync` policy),
but that's extra operational surface to get right, and its default eviction
policies are tuned for caching, not for "this is the only copy of an
in-flight incident's state." Postgres's WAL-backed durability is the default
behavior, not a mode you have to opt into and monitor.

### Why not a standalone vector DB (Qdrant, Pinecone, etc.)

This reasoning already lives in `src/sre_agent/rag/vector_store.py`'s
docstring and is repeated here because it is genuinely the deciding factor:

- The system already requires Postgres for the checkpointer. Adding Qdrant
  means a second stateful service to provision, back up, monitor, and grant
  IRSA/network-policy access to (see `SECURITY.md`'s `NetworkPolicy` egress
  list) -- for a corpus that is realistically thousands of runbook/postmortem
  chunks, not billions of vectors. HNSW via pgvector is fast enough at that
  scale; we are not solving a large-scale ANN problem.
- Keeping chunk `metadata` (service, doc_type, source_path) in the same
  database as everything else means it's joinable with incident records in
  ordinary SQL -- e.g. "which runbooks were retrieved for sev1 incidents in
  the last 30 days" is a join, not a cross-service query spanning two
  databases with two different query languages.
- One fewer moving part in `infrastructure/` (one fewer Helm subchart,
  Terraform module, and NetworkPolicy egress rule) for a portfolio-scale
  reference implementation whose credibility rests on being operable, not on
  demonstrating maximum architectural surface area.

## Consequences

**Positive**

- Single backup/restore, single connection-pooling story
  (`POSTGRES_POOL_MIN`/`POSTGRES_POOL_MAX` in `.env.example`), single
  credential to rotate via Secrets Manager.
- RAG metadata and incident/audit data are queryable together with plain SQL
  joins, useful for building "which runbook actually got retrieved and used"
  analytics later.
- The checkpointer's durability guarantee (survives pod restart, survives an
  approval pending for hours) comes from Postgres's default behavior rather
  than a Redis persistence configuration someone has to remember to turn on
  correctly.

**Negative / tradeoffs**

- Postgres becomes a single point of failure for *two* concerns at once
  (see `docs/incidents/` for how the team reasons about that) -- a Postgres
  outage stalls both new checkpoint writes and RAG retrieval simultaneously,
  where a split architecture would degrade RAG independently of graph
  execution.
- pgvector HNSW is not as fast as a purpose-built ANN engine at very large
  scale; if the runbook corpus ever grows past the "thousands of chunks"
  assumption (e.g. ingesting every postmortem the org has ever written,
  multi-tenant across many services), this decision should be revisited.
- Checkpoint tables and `runbook_chunks` share write/connection pool capacity
  with everything else hitting `POSTGRES_DSN`, so connection pool sizing
  (`POSTGRES_POOL_MAX=10` by default) has to account for both workloads, not
  just one.
