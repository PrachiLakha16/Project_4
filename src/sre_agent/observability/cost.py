"""Per-incident token budget enforcement and cost logging.

Kept process-local (in-memory) and keyed by incident_id, mirroring how the
LangGraph checkpointer scopes state per thread. A single incident never spans
processes within its synchronous graph run, so this is sufficient without a
shared store; if it needs to be, the ledger is the one place to swap in Redis.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

# USD per 1K tokens, illustrative list prices for cost logging only.
_PRICING_PER_1K = {
    "claude-sonnet-5": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5": {"input": 0.0008, "output": 0.004},
}
_DEFAULT_PRICE = {"input": 0.002, "output": 0.010}


@dataclass
class CostEntry:
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def usd(self) -> float:
        price = _PRICING_PER_1K.get(self.model, _DEFAULT_PRICE)
        return (self.input_tokens / 1000) * price["input"] + (self.output_tokens / 1000) * price[
            "output"
        ]


@dataclass
class _IncidentLedger:
    entries: list[CostEntry] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(e.input_tokens + e.output_tokens for e in self.entries)

    @property
    def total_usd(self) -> float:
        return sum(e.usd for e in self.entries)


class CostLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_incident: dict[str, _IncidentLedger] = {}

    def record(
        self, *, incident_id: str, model: str, input_tokens: int, output_tokens: int
    ) -> CostEntry:
        entry = CostEntry(model=model, input_tokens=input_tokens, output_tokens=output_tokens)
        with self._lock:
            self._by_incident.setdefault(incident_id, _IncidentLedger()).entries.append(entry)
        return entry

    def remaining_budget(self, incident_id: str, token_budget: int) -> int:
        with self._lock:
            used = self._by_incident.get(incident_id, _IncidentLedger()).total_tokens
        return token_budget - used

    def summary(self, incident_id: str) -> dict[str, float | int]:
        with self._lock:
            ledger = self._by_incident.get(incident_id, _IncidentLedger())
            return {
                "total_tokens": ledger.total_tokens,
                "total_usd": round(ledger.total_usd, 4),
                "calls": len(ledger.entries),
            }
