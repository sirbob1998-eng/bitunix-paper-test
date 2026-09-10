# Experimental AI funding-carry agent

This branch adds an adaptive decision layer alongside the frozen eight-week baseline. It does not modify the baseline strategy or its ledger.

## Current mode: shadow only

The agent may independently choose to hold cash, exit, or propose up to three delta-neutral spot-long/perpetual-short targets. It cannot place real orders and does not yet mutate a simulated portfolio. Each proposal is saved as an audit event for later scoring.

The model receives only public Bitunix market summaries:

- current, 7-day, and 28-day funding statistics;
- spot/perpetual basis;
- spot and futures spreads;
- two-leg order-book depth capacity.

The model returns a strict structured decision. A deterministic policy then rejects stale data, unsupported symbols, negative trailing carry, weak confidence, excessive basis or spread, inadequate depth, duplicate symbols, too many positions, or allocations above 500 USDT total.

## Safety boundary

- Paper-only and public-data-only.
- No Bitunix API key, private endpoint, or order code.
- The model cannot change risk limits or approve its own output.
- Rejected decisions exit non-zero and remain auditable.
- The baseline on `main` remains the control group.

## Run offline tests

```bash
python -m unittest discover -s tests -v
```

## Run one shadow decision

Set `OPENAI_API_KEY` and `OPENAI_MODEL`, then run:

```bash
python agent_runner.py
```

For GitHub Actions, store the API key as the `OPENAI_API_KEY` repository secret and the chosen model name as the `OPENAI_MODEL` repository variable. Do not commit either value. The experimental workflow has no schedule and only uploads a 30-day decision artifact.

## Promotion gates

Do not add a paper execution simulator until the branch has at least 24 shadow decisions, no policy breaches, no more than 10% malformed/rejected model outputs, and an evaluation method that measures net performance against the frozen baseline after fees, slippage, and basis changes.
