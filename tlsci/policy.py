from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Baseline, Measurement, PolicyDecision, RuntimeLock


@dataclass(frozen=True)
class Policy:
    max_delta_percent: int = 15
    min_delta_gas: int = 50
    max_headroom_percent: int = 80
    require_reproducible: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        return cls(
            max_delta_percent=int(data.get("max_delta_percent", 15)),
            min_delta_gas=int(data.get("min_delta_gas", 50)),
            max_headroom_percent=int(data.get("max_headroom_percent", 80)),
            require_reproducible=bool(data.get("require_reproducible", True)),
        )


def _percent(numerator: int, denominator: int) -> str:
    return f"{(numerator * 10000 // denominator) / 100:.2f}"


def evaluate_measurements(
    measurements: list[Measurement],
    baseline: Baseline | None,
    policy: Policy,
    runtime: RuntimeLock,
) -> list[PolicyDecision]:
    decisions: list[PolicyDecision] = []
    for measurement in measurements:
        rules: list[str] = []
        old_gas: int | None = None
        new_gas = measurement.minimum_successful_gas_budget
        delta_gas: int | None = None
        delta_percent: str | None = None
        if measurement.status != "success" or new_gas is None:
            decisions.append(
                PolicyDecision(
                    scenario_id=measurement.scenario_id,
                    size=measurement.size,
                    decision="invalid",
                    rule_ids=("MEASUREMENT_NOT_SUCCESSFUL",),
                    old_gas=None,
                    new_gas=new_gas,
                    delta_gas=None,
                    delta_percent=None,
                    message=f"measurement status is {measurement.status}",
                )
            )
            continue
        if new_gas * 100 >= runtime.hard_gas_limit_per_operation * policy.max_headroom_percent:
            rules.append("HEADROOM")
        if baseline is not None:
            key = f"{measurement.scenario_id}:{measurement.size}"
            baseline_entry = baseline.scenarios.get(key)
            if baseline_entry is None:
                rules.append("BASELINE_MISSING")
            else:
                baseline_fixture = baseline_entry.get("fixture_sha256")
                if baseline_fixture is not None and baseline_fixture != measurement.fixture_sha256:
                    rules.append("BASELINE_FIXTURE_MISMATCH")
                baseline_method = baseline_entry.get("measurement_method")
                if baseline_method is not None and baseline_method != measurement.measurement_method:
                    rules.append("BASELINE_METHOD_MISMATCH")
                baseline_cap = baseline_entry.get("gas_cap")
                if baseline_cap is not None and int(baseline_cap) != measurement.gas_cap:
                    rules.append("BASELINE_GAS_CAP_MISMATCH")
                old_gas = baseline_entry.get("minimum_successful_gas_budget")
                if old_gas is None or int(old_gas) <= 0:
                    rules.append("BASELINE_INVALID")
                else:
                    old_gas = int(old_gas)
                    delta_gas = new_gas - old_gas
                    delta_percent = _percent(delta_gas, old_gas)
                    if delta_gas > policy.min_delta_gas and delta_gas * 100 > old_gas * policy.max_delta_percent:
                        rules.append("GAS_REGRESSION")
        if rules:
            decision = "invalid" if any(
                rule in {
                    "BASELINE_MISSING",
                    "BASELINE_INVALID",
                    "BASELINE_FIXTURE_MISMATCH",
                    "BASELINE_METHOD_MISMATCH",
                    "BASELINE_GAS_CAP_MISMATCH",
                }
                for rule in rules
            ) else "violation"
            decisions.append(
                PolicyDecision(
                    scenario_id=measurement.scenario_id,
                    size=measurement.size,
                    decision=decision,
                    rule_ids=tuple(rules),
                    old_gas=old_gas,
                    new_gas=new_gas,
                    delta_gas=delta_gas,
                    delta_percent=delta_percent,
                    message="; ".join(rules),
                )
            )
        else:
            decisions.append(
                PolicyDecision(
                    scenario_id=measurement.scenario_id,
                    size=measurement.size,
                    decision="pass",
                    rule_ids=(),
                    old_gas=old_gas,
                    new_gas=new_gas,
                    delta_gas=delta_gas,
                    delta_percent=delta_percent,
                    message="policy passed",
                )
            )
    return decisions


def exit_code_for(decisions: list[PolicyDecision], errors: list[str]) -> int:
    if any(error.startswith("invalid:") for error in errors):
        return 2
    if errors:
        return 3
    if any(decision.decision == "invalid" for decision in decisions):
        return 2
    if any(decision.decision == "violation" for decision in decisions):
        return 1
    return 0
