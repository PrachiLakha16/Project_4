"""Ingestion pipeline for runbooks/postmortems into the pgvector store.

Pipeline: read markdown -> chunk by heading with overlap -> embed -> upsert
into the `runbook_chunks` pgvector table. Run via:

    python -m sre_agent.rag.ingest --docs-dir docs/runbooks

Long-term memory (this store) is populated offline/on a schedule, distinct
from the per-incident short-term state carried in the LangGraph checkpointer.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from sre_agent.rag.embeddings import EmbeddingClientProtocol, FakeEmbeddingClient
from sre_agent.rag.vector_store import RunbookChunk, VectorStoreProtocol

_HEADING_RE = re.compile(r"^#{1,3}\s+(.*)$", re.MULTILINE)


@dataclass
class ChunkingConfig:
    max_chars: int = 1200
    overlap_chars: int = 150


def chunk_markdown(
    text: str, doc_id: str, config: ChunkingConfig | None = None
) -> list[RunbookChunk]:
    """Splits on headings first, then hard-wraps any section still over max_chars,
    carrying a small char overlap between adjacent chunks so RCA context isn't
    cut mid-sentence at a chunk boundary."""
    config = config or ChunkingConfig()
    sections: list[str] = []
    headings = list(_HEADING_RE.finditer(text))
    boundaries = [m.start() for m in headings] + [len(text)]
    if boundaries and boundaries[0] != 0:
        boundaries = [0] + boundaries
    for start, end in pairwise(boundaries):
        section = text[start:end].strip()
        if section:
            sections.append(section)
    if not sections:
        sections = [text]

    chunks: list[RunbookChunk] = []
    for section in sections:
        start = 0
        while start < len(section):
            piece = section[start : start + config.max_chars]
            chunk_id = hashlib.sha256(f"{doc_id}:{start}:{piece[:32]}".encode()).hexdigest()[:16]
            chunks.append(RunbookChunk(chunk_id=chunk_id, doc_id=doc_id, text=piece, metadata={}))
            if start + config.max_chars >= len(section):
                break
            start += config.max_chars - config.overlap_chars
    return chunks


def extract_frontmatter_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if first_line.startswith("# "):
        metadata["title"] = first_line[2:].strip()
    service_match = re.search(r"^service:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if service_match:
        metadata["service"] = service_match.group(1).strip()
    doc_type_match = re.search(r"^type:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if doc_type_match:
        metadata["doc_type"] = doc_type_match.group(1).strip()
    return metadata


def ingest_directory(
    docs_dir: Path,
    vector_store: VectorStoreProtocol,
    embedding_client: EmbeddingClientProtocol | None = None,
) -> int:
    embedding_client = embedding_client or FakeEmbeddingClient()
    total_chunks = 0
    for md_path in sorted(docs_dir.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        doc_metadata = extract_frontmatter_metadata(text)
        doc_metadata["source_path"] = str(md_path)
        chunks = chunk_markdown(text, doc_id=md_path.stem)
        for chunk in chunks:
            chunk.metadata.update(doc_metadata)
        vectors = embedding_client.embed([c.text for c in chunks])
        vector_store.upsert(chunks, vectors)
        total_chunks += len(chunks)
    return total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest runbooks/postmortems into the pgvector RAG store"
    )
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/runbooks"))
    args = parser.parse_args()

    from sre_agent.core.config import get_settings
    from sre_agent.rag.vector_store import PgVectorStore

    settings = get_settings()
    store = PgVectorStore(settings.postgres_dsn)
    store.ensure_schema()
    count = ingest_directory(args.docs_dir, store)
    print(f"Ingested {count} chunks from {args.docs_dir}")


if __name__ == "__main__":
    main()
