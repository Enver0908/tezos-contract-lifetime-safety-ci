# Bounded valid-state follow-up v1 — 28 September 2026

## Why this is a separate run

The immutable `growth-v2` run measured the bounded-last-64 Michelson fixture with pre-call storage lengths 0, 1, 8, 64, 256, 1,024 and 4,096. The same script retains at most 64 output elements, but it iterates the entire pre-call list. Therefore the 256/1,024/4,096 starting states exceed the fixture's own 64-item postcondition and are not representative of states produced by its normal transition. Those exploratory points cost 576, 938 and 2,382 minimum-budget units; the valid size-64 point cost 490. The result is preserved unchanged and the original growth-v2 acceptance remains **NO-GO**.

This follow-up is deliberately labelled **post-hoc diagnostic**, not blind preregistration or independent confirmation. It narrows only the starting-state domain to 0, 1, 8 and 64, keeps the same pinned runtime, the same two-pass measurement method, and the already declared project signal rule (`delta > 50` gas **and** `increase > 15%`). It does not change, erase or retroactively pass the original acceptance result.

## Locked follow-up protocol

- Manifest: `fixtures/growth-v2/bounded-valid-state-followup-v1.json`.
- Exact script: the pinned `contracts/synthetic/bounded_last64_append.json` fixture from the parent corpus.
- Valid pre-call lengths: exactly 0, 1, 8 and 64; no size above the 64-entry invariant is admitted to this follow-up.
- Runtime: Octez 25.2 pinned digest and Ushuaia protocol from `runtime.lock.json`; protocol operation ceiling 1,040,000.
- Repetitions: exactly two complete runs per case. Any run mismatch, invalid result, runtime error, missing case or semantic-output mismatch fails the follow-up.
- Semantic postcondition: output storage equals `[1] + input storage`, truncated to its first 64 items; generated internal operations must be empty. The pre-existing Octez acceptance test separately verifies exact latest-64 values at lengths 0, 1, 63, 64, 65 and 256, including a repeated call.
- Growth comparison: size 0 versus size 64. The project signal is crossed only if **both** the absolute delta is greater than 50 and relative growth is greater than 15%. This is an internal experiment rule, not a Tezos protocol or Foundation rule.
- Interpretation: a valid follow-up can support this fixture's measured behavior only. It cannot prove general bounded-cost behavior, production reachability, user harm, demand, willingness to pay, a grant award or revenue.

## Commands

```text
python -m tlsci --runtime runtime.lock.json validate --manifest fixtures/growth-v2/bounded-valid-state-followup-v1.json
python -m tlsci --runtime runtime.lock.json run --manifest fixtures/growth-v2/bounded-valid-state-followup-v1.json --policy policy.json --output-dir outputs/strategy-2026-09-28/bounded-valid-state-followup-v1 --timeout 60
```

The output directory must be absent before the run. Do not overwrite the growth-v2 report or its acceptance JSON.
