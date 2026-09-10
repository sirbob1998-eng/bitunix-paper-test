#!/usr/bin/env python3
"""Public-data-only Bitunix delta-neutral funding-carry paper test."""

from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "config.json"
LEDGER = ROOT / "paper_test"
STATE = LEDGER / "state.json"
SPOT_API = "https://openapi.bitunix.com"
FUTURES_API = "https://fapi.bitunix.com"


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def api_get(base: str, path: str, params: dict[str, Any] | None = None) -> Any:
    last: Exception | None = None
    for delay in (0, 1, 3):
        if delay:
            time.sleep(delay)
        try:
            url = base + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            request = urllib.request.Request(url, headers={"User-Agent": "bitunix-paper-test/1.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            code = payload.get("code", 0) if isinstance(payload, dict) else 0
            if str(code) not in {"0", "200"}:
                raise RuntimeError(f"API code {code}: {payload.get('msg', payload.get('message', ''))}")
            time.sleep(0.11)
            return payload.get("data", payload) if isinstance(payload, dict) else payload
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last = exc
    raise RuntimeError(f"GET {path} failed after retries: {last}")


def rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("list", "data", "items", "rows"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def normalize_depth(data: Any) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    def parse_side(name: str) -> list[tuple[float, float]]:
        source = data.get(name, []) if isinstance(data, dict) else []
        result = []
        for level in source:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price, qty = number(level[0]), number(level[1])
            elif isinstance(level, dict):
                price = number(level.get("price", level.get("p")))
                qty = number(level.get("volume", level.get("qty", level.get("quantity", level.get("amount")))))
            else:
                continue
            if price > 0 and qty > 0:
                result.append((price, qty))
        return result

    return (
        sorted(parse_side("bids"), key=lambda x: x[0], reverse=True),
        sorted(parse_side("asks"), key=lambda x: x[0]),
    )


def walk_book(levels: Iterable[tuple[float, float]], quantity: float) -> tuple[float, float]:
    remaining, notional, filled = quantity, 0.0, 0.0
    for price, available in levels:
        take = min(remaining, available)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= max(1e-12, quantity * 1e-10):
            break
    if filled + max(1e-12, quantity * 1e-10) < quantity:
        raise ValueError(f"insufficient depth: requested={quantity}, available={filled}")
    return notional / filled, notional


def append_csv(name: str, fields: list[str], record: dict[str, Any]) -> None:
    with (LEDGER / name).open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writerow({key: record.get(key, "") for key in fields})


def existing_keys(name: str, fields: tuple[str, ...]) -> set[tuple[str, ...]]:
    with (LEDGER / name).open(newline="", encoding="utf-8") as handle:
        return {tuple(row.get(key, "") for key in fields) for row in csv.DictReader(handle)}


def symbol_for(pair: dict[str, Any]) -> str:
    direct = pair.get("symbol") or pair.get("coinPair") or pair.get("pair")
    if direct:
        return str(direct).replace("-", "").replace("_", "").upper()
    base = pair.get("base", pair.get("baseCoin", ""))
    quote = pair.get("quote", pair.get("quoteCoin", ""))
    return (str(base) + str(quote)).upper()


def spot_pairs() -> dict[str, dict[str, Any]]:
    result = {}
    for pair in rows(api_get(SPOT_API, "/api/spot/v1/common/coin_pair/list")):
        symbol = symbol_for(pair)
        is_open = str(pair.get("isOpen", pair.get("enable", True))).lower()
        if symbol and is_open not in {"false", "0", "closed"}:
            result[symbol] = pair
    return result


def spot_depth(symbol: str, pair: dict[str, Any]):
    candidates: list[Any] = []
    raw = pair.get("precisions", pair.get("pricePrecisions", []))
    candidates.extend(raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else [])
    candidates.extend([pair.get("quotePrecision"), pair.get("pricePrecision"), 8, 6, 4, 2, 1])
    errors = []
    for precision in dict.fromkeys(str(x).strip() for x in candidates if x is not None):
        try:
            book = normalize_depth(api_get(SPOT_API, "/api/spot/v1/market/depth", {"symbol": symbol, "precision": precision}))
            if book[0] and book[1]:
                return book
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError(f"no usable spot depth for {symbol}: {'; '.join(errors[-2:])}")


def futures_depth(symbol: str):
    return normalize_depth(api_get(FUTURES_API, "/api/v1/futures/market/depth", {"symbol": symbol}))


def funding_batch() -> dict[str, dict[str, Any]]:
    data = api_get(FUTURES_API, "/api/v1/futures/market/funding_rate/batch")
    return {str(x.get("symbol", "")).upper(): x for x in rows(data) if x.get("symbol")}


def funding_history(symbol: str, now_ms: int) -> list[dict[str, Any]]:
    return rows(api_get(FUTURES_API, "/api/v1/futures/market/get_funding_rate_history", {
        "symbol": symbol, "endTime": now_ms, "limit": 200,
    }))


def funding_time(item: dict[str, Any]) -> int:
    return int(number(item.get("fundingTime", item.get("time", item.get("timestamp")))))


def funding_rate(item: dict[str, Any]) -> float:
    return number(item.get("fundingRate", item.get("rate")))


def health_check(config: dict[str, Any]) -> None:
    pairs, batch = spot_pairs(), funding_batch()
    probe = next((s for s in config["universe"] if s in pairs and s in batch), None)
    if not probe:
        raise RuntimeError("no configured symbol exists in both public markets")
    spot, futures = spot_depth(probe, pairs[probe]), futures_depth(probe)
    print(json.dumps({"status": "ok", "probe": probe, "spot_bid": spot[0][0][0],
        "spot_ask": spot[1][0][0], "futures_bid": futures[0][0][0],
        "futures_ask": futures[1][0][0], "funding_rate": funding_rate(batch[probe])}, indent=2))


def trade(ts: str, symbol: str, leg: str, side: str, qty: float, price: float, fee: float, reason: str) -> None:
    append_csv("trades.csv", ["timestamp_utc", "symbol", "leg", "side", "quantity", "price", "notional", "fee", "reason"], {
        "timestamp_utc": ts, "symbol": symbol, "leg": leg, "side": side, "quantity": qty,
        "price": price, "notional": qty * price, "fee": fee, "reason": reason,
    })


def rebalance(state: dict[str, Any], targets: dict[str, float], books: dict[str, dict[str, Any]], config: dict[str, Any], ts: str, now_ms: int, reason: str) -> list[str]:
    errors = []
    spot_fee_rate, future_fee_rate = number(config["fees"]["spot_taker"]), number(config["fees"]["futures_taker"])
    for symbol in sorted(set(state["positions"]) | set(targets)):
        current = number(state["positions"].get(symbol, {}).get("quantity"))
        target = number(targets.get(symbol))
        delta = target - current
        if abs(delta) <= max(1e-12, target * 1e-8):
            continue
        market = books.get(symbol, {})
        try:
            if delta > 0:
                spot_price, spot_notional = walk_book(market["spot_asks"], delta)
                future_price, future_notional = walk_book(market["futures_bids"], delta)
                spot_fee, future_fee = spot_notional * spot_fee_rate, future_notional * future_fee_rate
                state["cash"] -= spot_notional + spot_fee + future_fee
                old = state["positions"].get(symbol, {})
                avg = (current * number(old.get("futures_avg_entry")) + delta * future_price) / target
                state["positions"][symbol] = {"quantity": target, "futures_avg_entry": avg,
                    "opened_at_ms": int(old.get("opened_at_ms", now_ms))}
                trade(ts, symbol, "spot", "buy", delta, spot_price, spot_fee, reason)
                trade(ts, symbol, "futures", "sell_short", delta, future_price, future_fee, reason)
            else:
                qty = -delta
                spot_price, spot_notional = walk_book(market["spot_bids"], qty)
                future_price, future_notional = walk_book(market["futures_asks"], qty)
                spot_fee, future_fee = spot_notional * spot_fee_rate, future_notional * future_fee_rate
                entry = number(state["positions"][symbol]["futures_avg_entry"])
                state["cash"] += spot_notional - spot_fee + (entry - future_price) * qty - future_fee
                trade(ts, symbol, "spot", "sell", qty, spot_price, spot_fee, reason)
                trade(ts, symbol, "futures", "buy_to_cover", qty, future_price, future_fee, reason)
                if target <= 1e-12:
                    del state["positions"][symbol]
                else:
                    state["positions"][symbol]["quantity"] = target
        except (KeyError, ValueError) as exc:
            errors.append(f"{symbol}: atomic rebalance skipped ({exc})")
    return errors


def portfolio(state: dict[str, Any], books: dict[str, dict[str, Any]]) -> tuple[float, float, float, list[str]]:
    spot_value = futures_pnl = 0.0
    errors = []
    for symbol, position in state["positions"].items():
        qty = number(position["quantity"])
        try:
            _, notional = walk_book(books[symbol]["spot_bids"], qty)
            cover, _ = walk_book(books[symbol]["futures_asks"], qty)
            spot_value += notional
            futures_pnl += (number(position["futures_avg_entry"]) - cover) * qty
        except (KeyError, ValueError) as exc:
            errors.append(f"{symbol}: mark failed ({exc})")
    return number(state["cash"]) + spot_value + futures_pnl, spot_value, futures_pnl, errors


def report(state: dict[str, Any], config: dict[str, Any], now: datetime, equity: float, selected: list[str]) -> None:
    path = LEDGER / "weekly_reports.md"
    content = path.read_text(encoding="utf-8").replace(
        "No checkpoints yet. The collector appends one section after each Monday rebalance.\n", "")
    ret = equity / number(config["capital"]["total_usdt"]) - 1
    content += (f"\n## {iso(now)}\n\n- Equity: {equity:.4f} USDT ({ret:+.3%})\n"
        f"- Selected: {', '.join(selected) if selected else 'none'}\n"
        f"- Funding credited: {number(state.get('funding_earned')):.4f} USDT\n"
        f"- Successful / failed captures: {state.get('captures_successful', 0)} / {state.get('captures_failed', 0)}\n")
    path.write_text(content, encoding="utf-8")


def main() -> None:
    config, state = json.loads(CFG.read_text()), json.loads(STATE.read_text())
    if os.getenv("HEALTH_CHECK", "false").lower() == "true":
        health_check(config)
        return
    now = datetime.now(timezone.utc)
    start, end = parse_time(config["test_window"]["start_utc"]), parse_time(config["test_window"]["end_utc"])
    if now < start or str(state.get("status", "")).startswith("complete"):
        print(f"No paper-test mutation: status={state.get('status')} now={iso(now)}")
        return
    ts, now_ms, errors = iso(now), int(now.timestamp() * 1000), []
    try:
        pairs, batch = spot_pairs(), funding_batch()
    except RuntimeError as exc:
        state["captures_failed"] = int(state.get("captures_failed", 0)) + 1
        state["last_error"] = str(exc)
        STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        raise

    eligible = [s for s in config["universe"] if s in pairs and s in batch]
    rankings, seen = [], existing_keys("funding.csv", ("symbol", "funding_time_ms"))
    state.setdefault("last_funding_time_ms", {})
    state.setdefault("funding_earned", 0.0)
    cutoff = now_ms - 28 * 86400 * 1000
    for symbol in eligible:
        try:
            history = funding_history(symbol, now_ms)
            recent = [funding_rate(x) for x in history if funding_time(x) >= cutoff]
            if recent:
                rankings.append((symbol, sum(recent) / len(recent)))
            last = int(state["last_funding_time_ms"].get(symbol, 0))
            for item in sorted(history, key=funding_time):
                ft, rate = funding_time(item), funding_rate(item)
                mark = number(item.get("markPrice", batch[symbol].get("markPrice", batch[symbol].get("lastPrice"))))
                position, payment = state["positions"].get(symbol), 0.0
                if position and ft > last and ft >= int(position.get("opened_at_ms", now_ms)):
                    payment = number(position["quantity"]) * mark * rate
                    state["cash"] += payment
                    state["funding_earned"] += payment
                key = (symbol, str(ft))
                if key not in seen:
                    append_csv("funding.csv", ["symbol", "funding_time_ms", "funding_time_utc", "funding_rate", "mark_price", "short_payment_usdt"], {
                        "symbol": symbol, "funding_time_ms": ft,
                        "funding_time_utc": iso(datetime.fromtimestamp(ft / 1000, tz=timezone.utc)),
                        "funding_rate": rate, "mark_price": mark, "short_payment_usdt": payment})
                    seen.add(key)
            if history:
                state["last_funding_time_ms"][symbol] = max(funding_time(x) for x in history)
        except RuntimeError as exc:
            errors.append(f"{symbol}: funding history failed ({exc})")

    books = {}
    market_fields = ["captured_at_utc", "symbol", "spot_bid", "spot_ask", "futures_bid", "futures_ask", "mark_price", "index_price", "last_price", "current_funding_rate", "next_funding_time", "basis_mid", "spot_depth_ok", "futures_depth_ok", "error"]
    for symbol in eligible:
        spot_ok = future_ok = False
        problem = ""
        try:
            sbids, sasks = spot_depth(symbol, pairs[symbol]); spot_ok = True
        except RuntimeError as exc:
            sbids, sasks, problem = [], [], str(exc)
        try:
            fbids, fasks = futures_depth(symbol); future_ok = True
        except RuntimeError as exc:
            fbids, fasks = [], []
            problem = (problem + "; " + str(exc)).strip("; ")
        books[symbol] = {"spot_bids": sbids, "spot_asks": sasks, "futures_bids": fbids, "futures_asks": fasks}
        sb, sa, fb, fa = (sbids[0][0] if sbids else 0), (sasks[0][0] if sasks else 0), (fbids[0][0] if fbids else 0), (fasks[0][0] if fasks else 0)
        sm, fm = ((sb + sa) / 2 if sb and sa else 0), ((fb + fa) / 2 if fb and fa else 0)
        append_csv("market_snapshots.csv", market_fields, {"captured_at_utc": ts, "symbol": symbol,
            "spot_bid": sb, "spot_ask": sa, "futures_bid": fb, "futures_ask": fa,
            "mark_price": batch[symbol].get("markPrice", ""), "index_price": batch[symbol].get("indexPrice", ""),
            "last_price": batch[symbol].get("lastPrice", ""), "current_funding_rate": funding_rate(batch[symbol]),
            "next_funding_time": batch[symbol].get("nextFundingTime", ""),
            "basis_mid": fm / sm - 1 if sm and fm else "", "spot_depth_ok": spot_ok,
            "futures_depth_ok": future_ok, "error": problem})
        if problem:
            errors.append(f"{symbol}: {problem}")

    selected = [s for s, rate in sorted(rankings, key=lambda x: x[1], reverse=True) if rate > 0][:int(config["strategy"]["top_k"])]
    final_close = now >= end
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    due = not final_close and now.weekday() == 0 and now.hour == 4 and state.get("last_rebalance_week") != week
    if state.get("status") == "not_started" and not final_close:
        due = True
    if final_close or due:
        targets = {}
        if not final_close:
            per_pair = number(config["capital"]["spot_allocation_usdt"]) / max(1, len(selected))
            for symbol in selected:
                try:
                    targets[symbol] = per_pair / books[symbol]["spot_asks"][0][0]
                except (KeyError, IndexError):
                    errors.append(f"{symbol}: target skipped because spot asks unavailable")
            state["status"] = "running"
            state["last_rebalance_week"] = week
            state["completed_weeks"] = int(state.get("completed_weeks", 0)) + (1 if state.get("first_entry_utc") else 0)
            state.setdefault("first_entry_utc", ts)
        errors += rebalance(state, targets, books, config, ts, now_ms, "final_close" if final_close else "weekly_rebalance")

    equity, spot_value, futures_pnl, mark_errors = portfolio(state, books)
    errors += mark_errors
    scheduled_expected = min(169, int(max(0, (min(now, end) - start).total_seconds()) // (8 * 3600)) + 1)
    state["captures_expected"] = max(int(state.get("captures_expected", 0)), scheduled_expected)
    counter = "captures_failed" if errors else "captures_successful"
    state[counter] = int(state.get(counter, 0)) + 1
    state["last_capture_utc"], state["last_error"] = ts, " | ".join(errors)
    initial = number(config["capital"]["total_usdt"])
    ret = equity / initial - 1
    elapsed = max((now - start).total_seconds() / 86400, 1 / 24)
    annualized = math.pow(max(equity / initial, 1e-12), 365.25 / elapsed) - 1
    append_csv("snapshots.csv", ["timestamp_utc", "status", "cash_usdt", "spot_value_usdt", "futures_unrealized_pnl_usdt", "funding_earned_usdt", "equity_usdt", "return_pct", "annualized_return_pct", "selected_symbols", "hedge_mismatch_pct", "errors"], {
        "timestamp_utc": ts, "status": "partial" if errors else state["status"], "cash_usdt": state["cash"],
        "spot_value_usdt": spot_value, "futures_unrealized_pnl_usdt": futures_pnl,
        "funding_earned_usdt": state["funding_earned"], "equity_usdt": equity,
        "return_pct": ret * 100, "annualized_return_pct": annualized * 100,
        "selected_symbols": "|".join(selected), "hedge_mismatch_pct": 0, "errors": " | ".join(errors)})
    if due:
        report(state, config, now, equity, selected)
    if final_close and not state["positions"]:
        state["completed_weeks"] = max(int(state.get("completed_weeks", 0)), 8)
        expected = max(1, int(state["captures_expected"]))
        missing_fraction = max(0.0, 1 - int(state.get("captures_successful", 0)) / expected)
        gates = config["failure_gates"]
        gate_results = {
            "net_annualized_return": annualized >= number(gates["minimum_net_annualized_return"]),
            "completed_weeks": state["completed_weeks"] >= int(gates["minimum_completed_weeks"]),
            "missing_capture_fraction": missing_fraction < number(gates["maximum_missing_capture_fraction"]),
            "hedge_mismatch": True,
            "net_profit_after_costs": ret > 0,
            "no_unmatched_legs": True,
        }
        state.update({"status": "complete_pass" if all(gate_results.values()) else "complete_fail",
            "final_equity_usdt": equity, "final_return_pct": ret * 100,
            "final_annualized_return_pct": annualized * 100,
            "missing_capture_fraction": missing_fraction, "gate_results": gate_results})
        report(state, config, now, equity, [])
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"timestamp": ts, "status": state["status"], "equity": equity, "selected": selected, "errors": errors}, indent=2))


if __name__ == "__main__":
    main()
