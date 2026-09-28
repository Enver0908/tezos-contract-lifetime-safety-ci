# MVP limitations

- A passing result is not a security audit or a proof of future callability.
- The current runner measures one script execution. It does not execute internal operations returned by the script.
- A public mainnet contract code snapshot proves deployed code identity at a block; generated storage growth cases are not historical user activity.
- Storage JSON byte length is a diagnostic measurement, not the protocol's exact storage accounting.
- Gas policy thresholds are project policy. A failure at a lower policy cap is not a protocol hard-limit failure.
- Minimum successful integer gas budget is not exact gas consumed. At a minimum of one, the runner has no observed result below it.
- Semantic equality is checked at observed successful budgets, including the minimum and selected cap. It does not prove monotonic gas behavior at every untested budget.
- `STEPS_TO_QUOTA` is rejected during manifest validation because the automatic minimum-budget search does not model its gas-sensitive semantics. Other unsupported gas-sensitive instructions must be added to the same validation set before they are accepted.
- The MVP does not prove a user incident, willingness to pay, grant acceptance, or post-grant revenue.
- A schema-v2 case declared `protocol_limit_exceeded` records the expected structured gas-exhaustion boundary of this pinned single-script experiment; it is not evidence that a deployed contract failed for a user.
- The TzSafe sequence uses 257 deterministic mockup owner addresses and one proposal containing a one-mutez transfer action. Signatures validate state growth through the chosen entrypoint; the proposal is not executed and no funds move.
- The FA2 batch fixture repeats the same one-unit transfer within one entrypoint call; the negative control grows unrelated zero-balance ledger entries while keeping the transfer fixed. These tests are protocol-pinned examples, not evidence of production transaction distributions or operator/user demand.
- The bounded-last-64 fixture retains at most 64 output elements but iterates every element in its supplied pre-call list. The growth-v2 points above size 64 are directly constructed states outside the fixture's own postcondition; their higher budgets do not establish the normal reachable-state curve. The separate size-0/1/8/64 diagnostic run is post-hoc and does not retroactively pass the full v2 acceptance.
- Semantic outputs are stored in the report for independent review. They may increase report size; they do not replace independent execution or a chain-state replay.
- `bounded-sequence-v1` can support only the named synthetic script, one `Unit` input, one fixed context, and the pinned local mockup across the recorded 4,096 calls. It does not prove constant gas for all bounded contracts, all list element sizes, arbitrary call parameters, mainnet history, user harm, or demand.
