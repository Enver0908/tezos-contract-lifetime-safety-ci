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
    max_successful_gas_budget: int | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (self.max_delta_percent, self.min_delta_gas, self.max_headroom_percent)
        ):
            raise ValueError("policy thresholds must be integers")
        if self.max_delta_percent < 0:
            raise ValueError("max_delta_percent must be non-negative")
        if self.min_delta_gas < 0:
            raise ValueError("min_delta_gas must be non-negative")
        if not 1 <= self.max_headroom_percent <= 100:
            raise ValueError("max_headroom_percent must be between 1 and 100")
        if not isinstance(self.require_reproducible, bool):
            raise ValueError("require_reproducible must be a boolean")
        if self.max_successful_gas_budget is not None and (
            type(self.max_successful_gas_budget) is not int or self.max_successful_gas_budget <= 0
        ):
            raise ValueError("max_successful_gas_budget must be a positive integer when set")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        reproducible = data.get("require_reproducible", True)
        if not isinstance(reproducible, bool):
            raise ValueError("require_reproducible must be a boolean")
        integer_fields = {
            "max_delta_percent": data.get("max_delta_percent", 15),
            "min_delta_gas": data.get("min_delta_gas", 50),
            "max_headroom_percent": data.get("max_headroom_percent", 80),
        }
        if any(type(value) is not int for value in integer_fields.values()):
            raise ValueError("policy thresholds must be integers")
        successful_budget = data.get("max_successful_gas_budget")
        if successful_budget is not None and type(successful_budget) is not int:
            raise ValueError("max_successful_gas_budget must be an integer")
        return cls(
            max_delta_percent=integer_fields["max_delta_percent"],
            min_delta_gas=integer_fields["min_delta_gas"],
            max_headroom_percent=integer_fields["max_headroom_percent"],
            require_reproducible=reproducible,
            max_successful_gas_budget=successful_budget,
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
        if measurement.status == "budget_exceeded":
            rule = (
                "PROTOCOL_LIMIT_EXCEEDED"
                if measurement.cap_kind == "protocol"
                else "SEARCH_LIMIT_REACHED"
                if measurement.cap_kind == "search"
                else "TEST_BUDGET_EXCEEDED"
            )
            decisions.append(
                PolicyDecision(
                    scenario_id=measurement.scenario_id,
                    size=measurement.size,
                    decision="violation",
                    rule_ids=(rule,),
                    old_gas=None,
                    new_gas=None,
                    delta_gas=None,
                    delta_percent=None,
                    message=(
                        "script execution exhausted the pinned protocol gas limit"
                        if measurement.cap_kind == "protocol"
                        else "script execution exceeded the declared measurement search limit"
                        if measurement.cap_kind == "search"
                        else "legacy search cap was reached; protocol exhaustion was not established"
                    ),
                )
            )
            continue
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
        if (
            policy.max_successful_gas_budget is not None
            and new_gas > policy.max_successful_gas_budget
        ):
            rules.append("TEST_BUDGET_EXCEEDED")
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
                baseline_cap_kind = baseline_entry.get("cap_kind")
                if baseline_cap_kind is not None and baseline_cap_kind != measurement.cap_kind:
                    rules.append("BASELINE_CAP_KIND_MISMATCH")
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
                    "BASELINE_CAP_KIND_MISMATCH",
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
                    max_successful_gas_budget=policy.max_successful_gas_budget,
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
                    max_successful_gas_budget=policy.max_successful_gas_budget,
                )
            )
    return decisions


def exit_code_for(decisions: list[PolicyDecision], errors: list[str]) -> int:
    if any(not error.startswith("invalid:") for error in errors):
        return 3
    if errors:
        return 2
    if any(decision.decision == "invalid" for decision in decisions):
        return 2
    if any(decision.decision == "violation" for decision in decisions):
        return 1
    return 0
