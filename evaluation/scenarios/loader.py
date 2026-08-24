"""Loads the golden-dataset YAML fixtures into typed scenario objects.

Each fixture under `evaluation/fixtures/*.yaml` describes one synthetic
incident: the inbound alert (fields matching `RawAlert` exactly) plus the
expected triage/RCA/remediation outcomes the graph should produce for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sre_agent.core.models import RawAlert

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass
class EvalScenario:
    scenario_id: str
    description: str
    alert: RawAlert
    expected_severity: str | None
    expected_action_type: str | None
    expected_requires_approval: bool
    expect_diagnostic_run: bool
    notes: str = ""
    simulate_duplicate: bool = False
    action_parameters: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""


def _load_one(path: Path) -> EvalScenario:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    alert = RawAlert(**raw["alert"])
    return EvalScenario(
        scenario_id=raw["scenario_id"],
        description=raw.get("description", ""),
        alert=alert,
        expected_severity=raw.get("expected_severity"),
        expected_action_type=raw.get("expected_action_type"),
        expected_requires_approval=bool(raw.get("expected_requires_approval", False)),
        expect_diagnostic_run=bool(raw.get("expect_diagnostic_run", False)),
        notes=raw.get("notes", ""),
        simulate_duplicate=bool(raw.get("simulate_duplicate", False)),
        action_parameters=raw.get("action_parameters") or {},
        source_file=path.name,
    )


def load_scenarios(fixtures_dir: Path | None = None) -> list[EvalScenario]:
    """Loads every `*.yaml` fixture in `fixtures_dir`, sorted by filename so
    the printed report is stable across runs."""
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    paths = sorted(fixtures_dir.glob("*.yaml"))
    if not paths:
        raise FileNotFoundError(f"no scenario fixtures found under {fixtures_dir}")
    return [_load_one(p) for p in paths]
