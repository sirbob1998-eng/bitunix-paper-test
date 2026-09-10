"""Build a compact decision context from public Bitunix market data."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from collector import funding_batch, funding_history, funding_rate, funding_time, futures_depth, iso, spot_depth, spot_pairs


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def standard_deviation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def spread_fraction(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else 1.0


def side_capacity_usdt(levels: list[tuple[float, float]], levels_to_use: int = 10) -> float:
    return sum(price * quantity for price, quantity in levels[:levels_to_use])


def build_context(config: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    pairs, batch = spot_pairs(), funding_batch()
    markets: list[dict[str, Any]] = []
    for symbol in config["universe"]:
        if symbol not in pairs or symbol not in batch:
            continue
        try:
            history = funding_history(symbol, now_ms)
            sbids, sasks = spot_depth(symbol, pairs[symbol])
            fbids, fasks = futures_depth(symbol)
            if not (sbids and sasks and fbids and fasks):
                continue
            rates_7d = [funding_rate(x) for x in history if funding_time(x) >= now_ms - 7 * 86_400_000]
            rates_28d = [funding_rate(x) for x in history if funding_time(x) >= now_ms - 28 * 86_400_000]
            spot_mid = (sbids[0][0] + sasks[0][0]) / 2
            futures_mid = (fbids[0][0] + fasks[0][0]) / 2
            markets.append({
                "symbol": symbol,
                "current_funding_rate": funding_rate(batch[symbol]),
                "funding_mean_7d": mean(rates_7d),
                "funding_mean_28d": mean(rates_28d),
                "funding_std_28d": standard_deviation(rates_28d),
                "funding_samples_28d": len(rates_28d),
                "basis_fraction": futures_mid / spot_mid - 1,
                "spot_spread_fraction": spread_fraction(sbids[0][0], sasks[0][0]),
                "futures_spread_fraction": spread_fraction(fbids[0][0], fasks[0][0]),
                "depth_capacity_usdt": min(side_capacity_usdt(sasks), side_capacity_usdt(fbids)),
                "spot_depth_ok": True,
                "futures_depth_ok": True,
            })
        except RuntimeError:
            continue
    return {
        "captured_at_utc": iso(now),
        "mandate": "paper-only delta-neutral funding carry",
        "maximum_total_allocation_usdt": 500.0,
        "maximum_positions": 3,
        "markets": markets,
    }
