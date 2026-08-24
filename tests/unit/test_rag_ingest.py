"""Tests for rag.ingest (chunking, frontmatter extraction, directory ingestion)
and rag.retriever.RunbookRetriever.search built on top of it.
"""

from __future__ import annotations

from pathlib import Path

from sre_agent.rag.embeddings import FakeEmbeddingClient
from sre_agent.rag.ingest import (
    ChunkingConfig,
    chunk_markdown,
    extract_frontmatter_metadata,
    ingest_directory,
)
from sre_agent.rag.retriever import RunbookRetriever
from sre_agent.rag.vector_store import InMemoryVectorStore
from sre_agent.tools.schemas import RunbookSearchInput

_MULTI_HEADING_DOC = """# Checkout Service Runbook
service: checkout
type: runbook

## Symptom: CrashLoopBackOff

Pods enter CrashLoopBackOff when the readiness probe fails repeatedly after
a bad deploy. Check `kubectl describe pod` for OOMKilled or a failing
liveness probe before doing anything else -- restarting will not fix a
config error.

## Diagnosis

Correlate with the deploy timeline in the release channel. If the crash
started within minutes of a rollout, the fix is almost always a rollback,
not a restart.

## Remediation

1. Roll back to the previous ReplicaSet revision.
2. Confirm error rate recovers within 5 minutes.
3. If it does not, escalate to the service owner.
"""


def test_chunk_markdown_splits_on_headings():
    chunks = chunk_markdown(_MULTI_HEADING_DOC, doc_id="checkout-runbook")
    assert len(chunks) >= 3  # title section + 3 ## sections
    assert all(c.doc_id == "checkout-runbook" for c in chunks)
    # Every original heading text should appear in some chunk.
    joined = "\n".join(c.text for c in chunks)
    assert "Symptom: CrashLoopBackOff" in joined
    assert "Diagnosis" in joined
    assert "Remediation" in joined


def test_chunk_markdown_respects_max_chars_with_hard_wrap():
    long_section = "# Title\n\n" + ("word " * 2000)  # one big section, no sub-headings
    config = ChunkingConfig(max_chars=500, overlap_chars=50)
    chunks = chunk_markdown(long_section, doc_id="long-doc", config=config)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert len(chunk.text) <= 500


def test_chunk_markdown_overlap_shares_content_between_adjacent_chunks():
    long_section = "# Title\n\n" + ("alpha bravo charlie delta echo " * 100)
    config = ChunkingConfig(max_chars=300, overlap_chars=60)
    chunks = chunk_markdown(long_section, doc_id="overlap-doc", config=config)

    assert len(chunks) >= 2
    # The tail of chunk[i] should reappear at the head of chunk[i+1] because
    # of the overlap window.
    tail = chunks[0].text[-40:]
    assert tail in chunks[1].text


def test_chunk_markdown_single_section_no_headings():
    text = "just a plain paragraph with no markdown headings at all."
    chunks = chunk_markdown(text, doc_id="plain")
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_extract_frontmatter_metadata_title_service_type():
    metadata = extract_frontmatter_metadata(_MULTI_HEADING_DOC)
    assert metadata["title"] == "Checkout Service Runbook"
    assert metadata["service"] == "checkout"
    assert metadata["doc_type"] == "runbook"


def test_extract_frontmatter_metadata_missing_fields_are_omitted():
    text = "no title line here\nservice: payments\n"
    metadata = extract_frontmatter_metadata(text)
    assert "title" not in metadata
    assert metadata["service"] == "payments"
    assert "doc_type" not in metadata


def test_extract_frontmatter_metadata_empty_text():
    assert extract_frontmatter_metadata("") == {}


def _write_doc(dir_path: Path, name: str, text: str) -> None:
    (dir_path / name).write_text(text, encoding="utf-8")


def test_ingest_directory_populates_vector_store_with_expected_chunk_count(tmp_path: Path):
    doc_a = "# Database Runbook\nservice: payments\ntype: runbook\n\n## Symptom\n\nConnection pool exhausted talking to the payments database under load.\n"
    doc_b = "# Cache Runbook\nservice: catalog\ntype: runbook\n\n## Symptom\n\nCache eviction storm causes memory pressure and OOM on the catalog pods.\n"
    _write_doc(tmp_path, "database-runbook.md", doc_a)
    _write_doc(tmp_path, "cache-runbook.md", doc_b)

    store = InMemoryVectorStore()
    total_chunks = ingest_directory(tmp_path, store, embedding_client=FakeEmbeddingClient())

    expected_a = len(chunk_markdown(doc_a, doc_id="database-runbook"))
    expected_b = len(chunk_markdown(doc_b, doc_id="cache-runbook"))
    assert total_chunks == expected_a + expected_b
    assert len(store._rows) == total_chunks  # whitebox: confirms the store was actually populated

    # Metadata from the frontmatter should have been attached to every chunk.
    services = {chunk.metadata.get("service") for chunk, _ in store._rows}
    assert services == {"payments", "catalog"}


def test_runbook_retriever_ranks_matching_doc_higher(tmp_path: Path):
    doc_db = "# Database Runbook\nservice: payments\ntype: runbook\n\n## Symptom\n\nDatabase connection pool exhausted timeout errors under heavy load.\n"
    doc_cache = "# Cache Runbook\nservice: catalog\ntype: runbook\n\n## Symptom\n\nCache eviction memory pressure oom kill spike on catalog pods.\n"
    _write_doc(tmp_path, "database-runbook.md", doc_db)
    _write_doc(tmp_path, "cache-runbook.md", doc_cache)

    store = InMemoryVectorStore()
    ingest_directory(tmp_path, store, embedding_client=FakeEmbeddingClient())
    retriever = RunbookRetriever(vector_store=store, embedding_client=FakeEmbeddingClient())

    result = retriever.search(RunbookSearchInput(query="database connection pool timeout", top_k=4))

    assert result.results, "expected at least one retrieved chunk"
    top_doc_id = result.results[0]["doc_id"]
    assert top_doc_id == "database-runbook"


def test_runbook_retriever_service_filter_restricts_results(tmp_path: Path):
    doc_db = "# Database Runbook\nservice: payments\ntype: runbook\n\n## Symptom\n\nConnection pool exhausted.\n"
    doc_cache = (
        "# Cache Runbook\nservice: catalog\ntype: runbook\n\n## Symptom\n\nCache eviction storm.\n"
    )
    _write_doc(tmp_path, "database-runbook.md", doc_db)
    _write_doc(tmp_path, "cache-runbook.md", doc_cache)

    store = InMemoryVectorStore()
    ingest_directory(tmp_path, store, embedding_client=FakeEmbeddingClient())
    retriever = RunbookRetriever(vector_store=store, embedding_client=FakeEmbeddingClient())

    result = retriever.search(
        RunbookSearchInput(query="pool exhausted", top_k=4, service_filter="catalog")
    )

    assert all(r["metadata"].get("service") == "catalog" for r in result.results)
