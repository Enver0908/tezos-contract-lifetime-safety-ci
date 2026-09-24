# Distribution and fixture provenance

The project source in this repository is distributed under the MIT License in `LICENSE`.

The following local paths are deliberately excluded from Git distribution until their redistribution rights have been reviewed:

- `references/`: includes third-party source repositories; the Atomex reference repository contains a GPL-3.0 license.
- `fixtures/real/`: contains scripts, storage snapshots, entrypoint data, and captured context from deployed Tezos contracts.

Public chain visibility is not treated as proof that a snapshot, source file, or derivative script may be republished under this project's MIT license. The local evidence package records addresses, block references, hashes, and measurement results separately. No endorsement by Atomex, QuipuSwap, or another contract owner is implied.

CI uses only the synthetic fixtures tracked under `fixtures/synthetic/`. A real-contract fixture may be added only after its source, applicable license or permission, required notices, and exact files to distribute have been recorded here and reviewed.
