# Bitunix funding-carry paper test

An eight-week, simulation-only forward test of a delta-neutral funding-carry strategy.

The collector uses **public Bitunix market-data endpoints only**. It contains no API keys and cannot place, amend, or cancel orders.

## Frozen rules

- Window: 14 September 2026 04:10 UTC through 9 November 2026 04:10 UTC.
- Starting paper capital: 1,000 USDT.
- Every Monday, rank eligible markets by their trailing four-week mean settled funding rate.
- Select the top three positive rates.
- Simulate equal-quantity spot longs and perpetual shorts, targeting 166.67 USDT per pair.
- Use executable public order-book depth, VIP-0 taker fees, actual settled funding and marked basis P&L.
- Never alter the rules during the test.

## Outputs

- `paper_test/state.json`: authoritative simulated account state.
- `paper_test/snapshots.csv`: eight-hour portfolio and market checkpoints.
- `paper_test/funding.csv`: deduplicated settled funding observations.
- `paper_test/trades.csv`: simulated fills and fees.
- `paper_test/weekly_reports.md`: weekly checkpoints and final gate result.

The scheduled workflow stops doing work outside the test window. Passing the eight-week gate supports only a longer paper test—not live deployment.
