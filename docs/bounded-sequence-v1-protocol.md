# Bounded last-64 lifetime sequence v1

## Purpose

Resolve the growth-v2 bounded-control ambiguity by measuring states reached through actual consecutive mockup calls. The question is whether the pinned bounded Michelson script, starting from an empty list and receiving `Unit` on every call, keeps at most 64 items and has an identical minimum successful gas budget at the predeclared post-saturation checkpoints.

This is a technical mockup experiment for one synthetic script, fixed input, fixed context, and pinned runtime. It does not establish a mainnet incident, arbitrary-contract behaviour, user demand, willingness to pay, a grant award, or revenue.

## Locked inputs

The machine-readable authority is `fixtures/bounded-sequence-v1/protocol.json`. It pins:

- Existing code: `fixtures/growth-v2/contracts/synthetic/bounded_last64_append.json`, SHA-256 `ab8d4acd5890fd9d5b7db7cdb1bfddfa5c56fb37d2bbd1c443e1f10226ba6c8e`.
- Existing `Unit` input and execution context, with their file hashes recorded in the protocol JSON.
- Empty initial storage, 4,096 successful sequential calls, a 64-item storage limit, and checkpoints at 0, 1, 8, 63, 64, 65, 256, 1,024, and 4,096 completed calls.
- Octez 25.2, the pinned image digest, Ushuaia protocol, the recorded chain id, and the 1,040,000 per-operation measurement ceiling from `runtime.lock.json`.
- A 60-second per-call timeout and a six-hour maximum for constructing one transition chain.

Changing a source hash, input, context, runtime, checkpoint, storage limit, repetition count, timeout, or acceptance rule invalidates this experiment version. A changed protocol requires a new version and a new output directory.

## State transition procedure

Let `S_0 = []`. For each integer `n` from 0 through 4,095, invoke the pinned script with `S_n`, the pinned `Unit`, the same execution context, and the pinned protocol hard gas ceiling. Require a successful result `R_n`; set `S_(n+1)` to the actual storage returned by Octez. Never synthesize a later state from the expected formula or skip calls after the list reaches 64.

At call `k`, the expected storage is the list containing `min(k, 64)` Michelson naturals with value 1. Every call must return that ordered list, at most 64 items, no internal operations, no events, and no lazy-storage diff. Since no internal operations or other effects are produced, the next state in this fixture is precisely the preceding call's returned storage.

The trace records all 4,096 calls, their input-state and output-state hashes, the complete output storage for each step, completion counters, cardinalities, input/context hashes, status, and effect counts. The nine checkpoint fixtures refer to snapshots from this trace. Manifest `size` means **completed calls** for this experiment; `storage_cardinality` is separately recorded in the trace and acceptance report. Measurement executes one additional script invocation from each frozen checkpoint input and does not feed that probe's output into the 4,096-call history. Thus the measured output at checkpoint `k` is `S_(k+1)`. In particular, the 4,096 checkpoint probes once from `S_4096`; this is a gas/semantics measurement only, not a 4,097th history transition.

## Gas measurement and acceptance

The existing schema-v2 integer-budget search measures the next invocation from each of the nine checkpoint states. Each scenario requires exactly two identical measurement passes. For each checkpoint, an independent direct replay must succeed at the recorded minimum. If the minimum is greater than one, a direct replay at minimum minus one must return the structured `gas_exhausted` status. All outputs at the minimum must match the expected `S_(k+1)` semantics and, for checkpoints before 4,096, the corresponding next transition in the recorded trace.

The 0, 1, 8, and 63-call checkpoints describe the fill-up period. The plateau rule applies to the five checkpoints after saturation: 64, 65, 256, 1,024, and 4,096 completed calls. Their minimum successful integer gas budgets must be exactly equal within a run and across both clean local runs. The full state trace, checkpoint storage, and measured budgets must also match across runs.

Exact equality after saturation is a new, predeclared condition derived from this script's fixed `Unit` input, fixed context, and repeated value `1`; it is not a Tezos protocol rule or Foundation requirement. The growth-v2 positive controls and unrelated-ledger negative control keep their original thresholds and must still pass. The old growth-v2 report and its historical bounded comparison remain unchanged.

## Hosted confirmation

The locked protocol was independently reproduced on three clean Ubuntu hosted runners from public `main` commit `65e7f021416efeb9f66dfa0af141d80c2cedb23b` in workflow run `36602735671`, attempt `1`. Runner IDs were `one`, `two`, and `three`. Each runner produced two fresh 4,096-transition chains; the fail-closed comparator found matching bounded summaries and normalized `run-a`/`run-b` reports across all three runners. The published acceptance summary reports `hosted_technical_go: go`, and its `evidence_sha256` is `d1ca275b62ff500f5b7d14abee30956d8ded7744c7bdc95ad2f93e21e428a2df`.

The immutable evidence archive is published at <https://github.com/Enver0908/tezos-contract-lifetime-safety-ci/releases/tag/technical-mvp-go-65e7f021416e-run-36602735671-a1> with SHA-256 `2e07e7ba3c2b9395108df6492a5679aa1e8277635a975cc33eab23d71bca9ecc`. This confirms repeatability of the named fixture under the locked mockup protocol. It does not establish a mainnet incident, arbitrary-contract behaviour, independent-user demand, willingness to pay, a grant award, payment, or revenue. The historical growth-v2 acceptance result remains `measured_technical_criteria_not_met`; this hosted result is a separate bounded-sequence-v1 technical result.

## Failure handling

- A missing/duplicate transition, broken state link, wrong output order, item count above 64, effect outside this sequence, changed input/runtime hash, missing checkpoint, malformed report, or mismatched direct replay invalidates the run.
- A timeout, Docker failure, interrupted run, or missing artifact produces an incomplete/invalid result. It cannot produce GO.
- If all trace and measurement integrity checks pass but post-saturation budgets differ, the technical result is NO-GO, and the acceptance report records the differing checkpoints and budgets.
- The generator and acceptance writer refuse to overwrite existing run artifacts.
- Large constructed initial states above 64 remain separate stress cases. Their gas result is not compared with this reached-state plateau.

## Local execution

Create a unique run id and an absent output directory under the task output root. For each of `run-a` and `run-b`, use a new corpus directory and a new Octez container:

```text
python tools/build_bounded_sequence.py --output-dir <run-root>/run-a/corpus
python -m tlsci --runtime runtime.lock.json run --manifest <run-root>/run-a/corpus/manifest.json --policy policy.json --output-dir <run-root>/run-a/measurement --timeout 60 --skip-doctor

python tools/build_bounded_sequence.py --output-dir <run-root>/run-b/corpus
python -m tlsci --runtime runtime.lock.json run --manifest <run-root>/run-b/corpus/manifest.json --policy policy.json --output-dir <run-root>/run-b/measurement --timeout 60 --skip-doctor

python tools/verify_technical_go_v3.py --growth-report <fresh-growth-v2-report.json> --run-a-corpus <run-root>/run-a/corpus --run-a-report <run-root>/run-a/measurement/report.json --run-a-acceptance <run-root>/run-a/bounded-acceptance.json --run-b-corpus <run-root>/run-b/corpus --run-b-report <run-root>/run-b/measurement/report.json --run-b-acceptance <run-root>/run-b/bounded-acceptance.json --runtime runtime.lock.json --output <run-root>/technical-mvp-go-v3.json
```

Run `tlsci doctor` immediately before the measurements. The production command sequence requires Docker; unit tests use a fake runner and do not count as Octez evidence. Public CI dispatch remains a separate hosted run, and an output from this local protocol is not a user pilot.
