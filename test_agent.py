import json
import unittest
from datetime import datetime, timezone

from agent.decision import RiskPolicy, validate_risk
from agent.provider import extract_output_text


NOW = datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc)


def context(**overrides):
    market = {
        "symbol": "BTCUSDT",
        "funding_mean_28d": 0.0001,
        "basis_fraction": 0.001,
        "spot_spread_fraction": 0.0002,
        "futures_spread_fraction": 0.0002,
        "depth_capacity_usdt": 10_000,
        "spot_depth_ok": True,
        "futures_depth_ok": True,
    }
    market.update(overrides)
    return {"captured_at_utc": "2026-09-10T02:00:00Z", "markets": [market]}


def decision(**position_overrides):
    position = {"symbol": "BTCUSDT", "allocation_usdt": 166.0, "confidence": 0.8, "reason": "positive stable carry"}
    position.update(position_overrides)
    return {"action": "rebalance", "positions": [position], "portfolio_reason": "best risk-adjusted carry", "risk_notes": []}


class AgentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = RiskPolicy(allowed_symbols=frozenset({"BTCUSDT"}))

    def test_accepts_safe_delta_neutral_target(self):
        self.assertEqual(validate_risk(decision(), context(), self.policy, now=NOW), [])

    def test_rejects_negative_carry(self):
        errors = validate_risk(decision(), context(funding_mean_28d=-0.0001), self.policy, now=NOW)
        self.assertTrue(any("not positive" in error for error in errors))

    def test_rejects_excess_allocation(self):
        errors = validate_risk(decision(allocation_usdt=250), context(), self.policy, now=NOW)
        self.assertTrue(any("allocation is outside" in error for error in errors))

    def test_rejects_stale_market_context(self):
        later = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
        errors = validate_risk(decision(), context(), self.policy, now=later)
        self.assertTrue(any("context age" in error for error in errors))

    def test_hold_cannot_smuggle_positions(self):
        candidate = decision()
        candidate["action"] = "hold"
        errors = validate_risk(candidate, context(), self.policy, now=NOW)
        self.assertTrue(any("must not include" in error for error in errors))

    def test_extracts_structured_response_text(self):
        expected = decision()
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(expected)}]}]}
        self.assertEqual(json.loads(extract_output_text(payload)), expected)


if __name__ == "__main__":
    unittest.main()
