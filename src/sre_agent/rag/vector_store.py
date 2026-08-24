"""pgvector-backed store for runbook/postmortem chunks (long-term memory).

Why pgvector over a standalone vector DB (Qdrant, etc.): the system already
requires Postgres for the LangGraph checkpointer (see docs/adr/0002); reusing
the same instance for embeddings avoids a second stateful service, keeps RAG
metadata joinable with incident records in ordinary SQL, and Postgres HNSW
via pgvector is fast enough at this corpus size (thousands, not billions, of
chunks). See docs/adr/0002-postgres-checkpointer-and-pgvector.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RunbookChunk:
    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    chunk: RunbookChunk
    score: float


class VectorStoreProtocol(Protocol):
    def upsert(self, chunks: list[RunbookChunk], vectors: list[list[float]]) -> None: ...
    def similarity_search(
        self, query_vector: list[float], top_k: int, service_filter: str | None = None
    ) -> list[RetrievedChunk]: ...


class PgVectorStore:
    """Real store: `runbook_chunks(chunk_id text pk, doc_id text, text text,
    metadata jsonb, embedding vector(256))` with an HNSW index on `embedding`.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ensure_schema(self) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runbook_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}',
                    embedding VECTOR(256) NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS runbook_chunks_embedding_hnsw "
                "ON runbook_chunks USING hnsw (embedding vector_cosine_ops)"
            )
            conn.commit()

    def upsert(self, chunks: list[RunbookChunk], vectors: list[list[float]]) -> None:
        import json

        import psycopg

        with psycopg.connect(self.dsn) as conn:
            for chunk, vector in zip(chunks, vectors, strict=True):
                conn.execute(
                    """
                    INSERT INTO runbook_chunks (chunk_id, doc_id, text, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET text = EXCLUDED.text, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding
                    """,
                    (chunk.chunk_id, chunk.doc_id, chunk.text, json.dumps(chunk.metadata), vector),
                )
            conn.commit()

    def similarity_search(
        self, query_vector: list[float], top_k: int, service_filter: str | None = None
    ) -> list[RetrievedChunk]:
        import psycopg

        sql = "SELECT chunk_id, doc_id, text, metadata, 1 - (embedding <=> %s) AS score FROM runbook_chunks"
        params: list = [query_vector]
        if service_filter:
            sql += " WHERE metadata->>'service' = %s"
            params.append(service_filter)
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.extend([query_vector, top_k])

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            RetrievedChunk(
                chunk=RunbookChunk(chunk_id=r[0], doc_id=r[1], text=r[2], metadata=r[3]), score=r[4]
            )
            for r in rows
        ]


class InMemoryVectorStore:
    """Fake store for tests/local demos: cosine similarity over python lists."""

    def __init__(self) -> None:
        self._rows: list[tuple[RunbookChunk, list[float]]] = []

    def upsert(self, chunks: list[RunbookChunk], vectors: list[list[float]]) -> None:
        existing_ids = {c.chunk_id for c, _ in self._rows}
        for chunk, vector in zip(chunks, vectors, strict=True):
            if chunk.chunk_id in existing_ids:
                self._rows = [
                    (c, v) if c.chunk_id != chunk.chunk_id else (chunk, vector)
                    for c, v in self._rows
                ]
            else:
                self._rows.append((chunk, vector))

    def similarity_search(
        self, query_vector: list[float], top_k: int, service_filter: str | None = None
    ) -> list[RetrievedChunk]:
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            norm_a = sum(x * x for x in a) ** 0.5 or 1e-9
            norm_b = sum(y * y for y in b) ** 0.5 or 1e-9
            return dot / (norm_a * norm_b)

        candidates = self._rows
        if service_filter:
            candidates = [
                (c, v) for c, v in candidates if c.metadata.get("service") == service_filter
            ]
        scored = [RetrievedChunk(chunk=c, score=cosine(query_vector, v)) for c, v in candidates]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]
