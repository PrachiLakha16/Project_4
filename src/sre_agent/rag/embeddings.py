"""Embedding client used for both ingestion and retrieval, so vectors are comparable.

Real implementation calls Anthropic-compatible or OpenAI embeddings; tests use
`FakeEmbeddingClient`, a deterministic hash-based embedding so retrieval logic
(cosine similarity ranking) can be tested without network access.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

EMBEDDING_DIM = 256


class EmbeddingClientProtocol(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """Uses OpenAI's text-embedding-3-small (1536d) via the provider-agnostic settings.

    Kept separate from LLMClient because embeddings and chat completions
    scale independently and often use different providers even when chat is
    on Anthropic (Anthropic does not offer a first-party embeddings endpoint).
    """

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        client = openai.OpenAI(api_key=self.api_key)
        resp = client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]


class FakeEmbeddingClient:
    """Deterministic bag-of-words hash embedding: good enough to rank
    semantically similar canned fixtures in tests, no external calls."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode()).digest()
            idx = int.from_bytes(digest[:2], "big") % EMBEDDING_DIM
            vec[idx] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]
