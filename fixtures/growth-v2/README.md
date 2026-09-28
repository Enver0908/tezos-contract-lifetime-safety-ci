# Explicit growth corpus v2

This corpus has 61 explicit cases across six synthetic controls, one TzSafe entrypoint, one FA2 transfer-batch growth path, and one FA2 unrelated-ledger negative control. The TzSafe snapshots are produced by sequential calls to the pinned Octez mockup `run_code`; internal operations are not applied. The FA2 cases begin from independent storage/context states.

`size` is the declared collection cardinality or prior signer count described by the scenario; it is not a user count or a production-state claim. `expected_status: protocol_limit_exceeded` means the pinned Octez mockup returned a structured gas-exhaustion result at the protocol ceiling. It does not by itself establish a deployed incident, affected user, demand, willingness to pay, grant acceptance, or revenue. `constructed_state` means a fixture state; the TzSafe sequence label is limited to the recorded `run_code` transition chain.

The TzSafe sequence uses the minimal proposal action in `templates/tzsafe-sign-proposal-content.json`, derived from the pinned integration's `sign_proposal` parameter. The builder sets its transfer amount to one mutez. The template and source/compiler provenance are recorded in `sources.json`; no private state or user data is included.

Upstream source commits, compiler provenance, compiled Michelson hashes and MIT notices are in `sources.json` and `contracts/`.
