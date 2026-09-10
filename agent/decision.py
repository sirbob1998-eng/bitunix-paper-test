"""Decision contract and deterministic guardrails for the AI agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


ALLOWED_ACTIONS = {"hold", "rebalance", "exit_all"}


@dataclass(frozen=True)
class RiskPolicy:
    allowed_symbols: frozenset[str]
    maximum_positions: int = 3
    maximum_total_allocation_usdt: float = 500.0
    maximum_symbol_allocation_usdt: float = 200.0
    minimum_confidence: float = 0.55
    maximum_abs_basis_fraction: float = 0.03
    maximum_spread_fraction: float = 0.005
    maximum_context_age_minutes: int = 30
    require_positive_28d_funding: bool = True


def validate_shape(decision: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if decision.get("action") not in ALLOWED_ACTIONS:
        errors.append("action must be hold, rebalance, or exit_all")
    if not isinstance(decision.get("portfolio_reason"), str) or not decision.get("portfolio_reason", "").strip():
        errors.append("portfolio_reason is required")
    if not isinstance(decision.get("risk_notes"), list):
        errors.append("risk_notes must be a list")
    positions = decision.get("positions")
    if not isinstance(positions, list):
        errors.append("positions must be a list")
        return errors
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            errors.append(f"positions[{index}] must be an object")
            continue
        if not isinstance(position.get("symbol"), str):
            errors.append(f"positions[{index}].symbol is required")
        allocation = position.get("allocation_usdt")
        if not isinstance(allocation, (int, float)) or isinstance(allocation, bool) or allocation < 0:
            errors.append(f"positions[{index}].allocation_usdt must be non-negative")
        confidence = position.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            errors.append(f"positions[{index}].confidence must be between 0 and 1")
        if not isinstance(position.get("reason"), str) or not position.get("reason", "").strip():
            errors.append(f"positions[{index}].reason is required")
    return errors


def validate_risk(
    decision: dict[str, Any], context: dict[str, Any], policy: RiskPolicy,
    now: datetime | None = None,
) -> list[str]:
    errors = validate_shape(decision)
    if errors:
        return errors
    now = now or datetime.now(timezone.utc)
    try:
        captured = datetime.fromisoformat(str(context["captured_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return ["market context timestamp is missing or invalid"]
    age_minutes = (now.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() / 60
    if age_minutes < -1 or age_minutes > policy.maximum_context_age_minutes:
        errors.append(f"market context age {age_minutes:.1f} minutes exceeds limit")

    positions = decision["positions"]
    if decision["action"] in {"hold", "exit_all"} and positions:
        errors.append(f"{decision['action']} must not include target positions")
    if len(positions) > policy.maximum_positions:
        errors.append("position count exceeds policy")
    symbols = [str(p.get("symbol", "")).upper() for p in positions]
    if len(symbols) != len(set(symbols)):
        errors.append("duplicate symbols are not allowed")
    total = sum(float(p.get("allocation_usdt", 0)) for p in positions)
    if total > policy.maximum_total_allocation_usdt + 1e-9:
        errors.append("total allocation exceeds policy")

    markets = {str(m.get("symbol", "")).upper(): m for m in context.get("markets", [])}
    for position, symbol in zip(positions, symbols):
        allocation = float(position["allocation_usdt"])
        confidence = float(position["confidence"])
        if symbol not in policy.allowed_symbols:
            errors.append(f"{symbol}: symbol is not allowed")
            continue
        market = markets.get(symbol)
        if not market:
            errors.append(f"{symbol}: no market context")
            continue
        if allocation <= 0 or allocation > policy.maximum_symbol_allocation_usdt:
            errors.append(f"{symbol}: allocation is outside policy")
        if confidence < policy.minimum_confidence:
            errors.append(f"{symbol}: confidence is below policy")
        if not market.get("spot_depth_ok") or not market.get("futures_depth_ok"):
            errors.append(f"{symbol}: both order books must be available")
        if policy.require_positive_28d_funding and float(market.get("funding_mean_28d", 0)) <= 0:
            errors.append(f"{symbol}: 28-day mean funding is not positive")
        if abs(float(market.get("basis_fraction", 0))) > policy.maximum_abs_basis_fraction:
            errors.append(f"{symbol}: absolute basis exceeds policy")
        if float(market.get("spot_spread_fraction", 1)) > policy.maximum_spread_fraction:
            errors.append(f"{symbol}: spot spread exceeds policy")
        if float(market.get("futures_spread_fraction", 1)) > policy.maximum_spread_fraction:
            errors.append(f"{symbol}: futures spread exceeds policy")
        if float(market.get("depth_capacity_usdt", 0)) + 1e-9 < allocation:
            errors.append(f"{symbol}: insufficient two-leg depth")
    return errors


DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "positions", "portfolio_reason", "risk_notes"],
    "properties": {
        "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
        "positions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["symbol", "allocation_usdt", "confidence", "reason"],
                "properties": {
                    "symbol": {"type": "string"},
                    "allocation_usdt": {"type": "number", "minimum": 0, "maximum": 200},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
            },
        },
        "portfolio_reason": {"type": "string"},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
    },
}
