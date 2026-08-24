"""Retrieval tool consumed by the Diagnostic/RCA Agent.

Wraps embedding + similarity search behind the same typed schema
(`RunbookSearchInput` / `RunbookSearchOutput`) used by the LangGraph tool
node and the MCP server, so both call paths return identical shapes.
"""

from __future__ import annotations

from sre_agent.observability.tracing import traced_tool_call
from sre_agent.rag.embeddings import EmbeddingClientProtocol, FakeEmbeddingClient
from sre_agent.rag.vector_store import InMemoryVectorStore, VectorStoreProtocol
from sre_agent.tools.schemas import RunbookSearchInput, RunbookSearchOutput


class RunbookRetriever:
    def __init__(
        self,
        vector_store: VectorStoreProtocol | None = None,
        embedding_client: EmbeddingClientProtocol | None = None,
    ) -> None:
        self.vector_store = vector_store or InMemoryVectorStore()
        self.embedding_client = embedding_client or FakeEmbeddingClient()

    @traced_tool_call("rag_runbook_search")
    def search(self, inp: RunbookSearchInput) -> RunbookSearchOutput:
        [query_vector] = self.embedding_client.embed([inp.query])
        hits = self.vector_store.similarity_search(
            query_vector, top_k=inp.top_k, service_filter=inp.service_filter
        )
        results = [
            {
                "chunk_id": hit.chunk.chunk_id,
                "doc_id": hit.chunk.doc_id,
                "text": hit.chunk.text,
                "score": round(hit.score, 4),
                "metadata": hit.chunk.metadata,
            }
            for hit in hits
        ]
        return RunbookSearchOutput(results=results)
