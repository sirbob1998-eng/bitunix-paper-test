#!/usr/bin/env python3
"""Run one paper-only shadow decision and record an immutable audit event."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.context import build_context
from agent.decision import RiskPolicy, validate_risk
from agent.provider import request_decision


ROOT = Path(__file__).resolve().parent


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def run(context: dict[str, Any], decision: dict[str, Any], config: dict[str, Any], response_id: str = "") -> dict[str, Any]:
    limits = config["agent"]["risk_policy"]
    policy = RiskPolicy(
        allowed_symbols=frozenset(config["universe"]),
        maximum_positions=int(limits["maximum_positions"]),
        maximum_total_allocation_usdt=float(limits["maximum_total_allocation_usdt"]),
        maximum_symbol_allocation_usdt=float(limits["maximum_symbol_allocation_usdt"]),
        minimum_confidence=float(limits["minimum_confidence"]),
        maximum_abs_basis_fraction=float(limits["maximum_abs_basis_fraction"]),
        maximum_spread_fraction=float(limits["maximum_spread_fraction"]),
        maximum_context_age_minutes=int(limits["maximum_context_age_minutes"]),
    )
    now = datetime.now(timezone.utc)
    errors = validate_risk(decision, context, policy, now=now)
    return {
        "recorded_at_utc": now.isoformat().replace("+00:00", "Z"),
        "mode": "shadow_only",
        "approved": not errors,
        "rejection_reasons": errors,
        "model_response_id": response_id,
        "context": context,
        "decision": decision,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", help="Use a captured context instead of live public data")
    parser.add_argument("--decision-file", help="Use a fixture decision instead of calling a model")
    parser.add_argument("--output", default="agent_paper/decisions.jsonl")
    args = parser.parse_args()
    config = load_json(ROOT / "agent_config.json")
    base_config = load_json(ROOT / "config.json")
    context = load_json(args.context_file) if args.context_file else build_context(base_config)
    if args.decision_file:
        decision, response_id = load_json(args.decision_file), "fixture"
    else:
        decision, response_id = request_decision(
            os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_MODEL", ""), context)
    event = run(context, decision, config, response_id)
    append_event(ROOT / args.output, event)
    print(json.dumps({"approved": event["approved"], "rejection_reasons": event["rejection_reasons"],
        "action": decision.get("action"), "positions": decision.get("positions", [])}, indent=2))
    if not event["approved"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
