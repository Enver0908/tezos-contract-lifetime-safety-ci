# Measurement contract

The MVP reports the minimum integer gas budget at which one Michelson script execution succeeds under a pinned Octez mockup protocol.

The result is not exact consumed gas, an operation-wide cost, storage burn, or a guarantee that internal operations will succeed. `run_code` returns the final storage and generated operations; the MVP does not execute those generated operations.

The search distinguishes `gas_exhausted` from `script_rejected`, type errors, malformed output, timeout, and Docker/runtime failure. Only a gas exhaustion result is allowed to move the lower bound of the integer search.

The input storage is generated independently for every size. Big-map entries are supplied through the protocol's lazy-storage context mechanism where required; they are not converted into an ordinary list to manufacture a warning.

The result is valid only when code hash, fixture hash, context, protocol, Octez digest, and measurement method are present in the report.
