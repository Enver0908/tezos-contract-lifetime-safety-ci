# MVP limitations

- A passing result is not a security audit or a proof of future callability.
- The current runner measures one script execution. It does not execute internal operations returned by the script.
- A public mainnet contract code snapshot proves deployed code identity at a block; generated storage growth cases are not historical user activity.
- Storage JSON byte length is a diagnostic measurement, not the protocol's exact storage accounting.
- Gas policy thresholds are project policy. They are not Tezos protocol guarantees.
- `STEPS_TO_QUOTA` is rejected during manifest validation because the automatic minimum-budget search does not model its gas-sensitive semantics. Other unsupported gas-sensitive instructions must be added to the same validation set before they are accepted.
- The MVP does not prove a user incident, willingness to pay, grant acceptance, or post-grant revenue.
