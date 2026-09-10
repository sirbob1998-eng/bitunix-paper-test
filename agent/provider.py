"""Minimal OpenAI Responses API client with strict structured output."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from agent.decision import DECISION_SCHEMA


SYSTEM_PROMPT = """You manage a paper-only, delta-neutral crypto funding-carry portfolio.
Choose whether to hold, exit, or rebalance into at most three spot-long/perpetual-short pairs.
Optimize expected net carry after fees, spread, slippage, funding variability, and basis risk.
You may choose cash instead of forcing a trade. Never propose a directional or unmatched position.
Use only supplied market data. Your output will be rejected unless it obeys the JSON schema and
deterministic risk policy. Keep reasons concise and evidence-based."""


def extract_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("model response contained no output_text")


def request_decision(api_key: str, model: str, context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    if not model:
        raise RuntimeError("OPENAI_MODEL is required")
    body = {
        "model": model,
        "store": False,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(context, separators=(",", ":"))}]},
        ],
        "text": {"format": {"type": "json_schema", "name": "paper_decision", "strict": True, "schema": DECISION_SCHEMA}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {detail}") from exc
    text = extract_output_text(payload)
    return json.loads(text), str(payload.get("id", ""))
