from __future__ import annotations

from dataclasses import dataclass

from open_mechanic.history import PredictionInput, StoredSensorReading, StoredVehicleHistory


@dataclass(frozen=True)
class SensorRule:
    sensor_name: str
    low: float | None
    high: float | None
    issue: str
    explanation: str


SENSOR_RULES: tuple[SensorRule, ...] = (
    SensorRule(
        "COOLANT_TEMP",
        None,
        110.0,
        "Cooling system stress",
        "Coolant temperature is above the normal operating range.",
    ),
    SensorRule(
        "CONTROL_MODULE_VOLTAGE",
        12.0,
        15.0,
        "Charging system voltage anomaly",
        "Control module voltage is outside the expected charging range.",
    ),
    SensorRule(
        "ENGINE_LOAD",
        None,
        90.0,
        "High engine load",
        "Engine load is repeatedly near the top of the reported range.",
    ),
)

FUEL_TRIM_SENSORS = {"SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1"}


class PredictiveMaintenanceEngine:
    def score(self, history: StoredVehicleHistory) -> list[PredictionInput]:
        predictions: list[PredictionInput] = []
        predictions.extend(_dtc_predictions(history))
        predictions.extend(_sensor_predictions(history))

        maintenance_prediction = _maintenance_prediction(history)
        if maintenance_prediction is not None:
            predictions.append(maintenance_prediction)

        if predictions:
            return sorted(predictions, key=lambda item: item.risk_score, reverse=True)

        confidence = _history_confidence(len(history.sessions))
        return [
            PredictionInput(
                issue="No immediate risk signals",
                risk_score=0.05,
                confidence=confidence,
                severity="info",
                explanation="Recorded sessions do not currently show repeated DTCs or sensor anomalies.",
                source_signals=[f"{len(history.sessions)} recorded session(s) reviewed"],
            )
        ]


def _dtc_predictions(history: StoredVehicleHistory) -> list[PredictionInput]:
    code_counts: dict[str, int] = {}
    confirmed_counts: dict[str, int] = {}
    severities: dict[str, str | None] = {}

    for session in history.sessions:
        seen_in_session: set[str] = set()
        for dtc in session.dtcs:
            if dtc.code in seen_in_session:
                continue
            seen_in_session.add(dtc.code)
            code_counts[dtc.code] = code_counts.get(dtc.code, 0) + 1
            severities[dtc.code] = dtc.severity
            if dtc.status == "confirmed":
                confirmed_counts[dtc.code] = confirmed_counts.get(dtc.code, 0) + 1

    predictions: list[PredictionInput] = []
    for code, count in sorted(code_counts.items()):
        if count < 2 and confirmed_counts.get(code, 0) == 0:
            continue
        confirmed = confirmed_counts.get(code, 0)
        severity_weight = _severity_weight(severities.get(code))
        risk = _clamp(0.30 + min(count, 5) * 0.10 + confirmed * 0.08 + severity_weight)
        confidence = _clamp(0.25 + min(len(history.sessions), 8) * 0.05 + count * 0.08)
        predictions.append(
            PredictionInput(
                issue=f"Recurring DTC {code}",
                risk_score=round(risk, 2),
                confidence=round(confidence, 2),
                severity=_severity_from_risk(risk),
                explanation=(
                    f"{code} appeared in {count} recorded session(s), including "
                    f"{confirmed} confirmed observation(s)."
                ),
                source_signals=[
                    f"{code} session count={count}",
                    f"{code} confirmed count={confirmed}",
                    f"{code} reference severity={severities.get(code) or 'unknown'}",
                ],
            )
        )
    return predictions


def _sensor_predictions(history: StoredVehicleHistory) -> list[PredictionInput]:
    latest = _latest_sensor_values(history)
    predictions: list[PredictionInput] = []

    for rule in SENSOR_RULES:
        value = latest.get(rule.sensor_name)
        if value is None:
            continue
        if _violates_rule(value, rule):
            risk = _clamp(0.45 + _distance_weight(value, rule))
            predictions.append(
                PredictionInput(
                    issue=rule.issue,
                    risk_score=round(risk, 2),
                    confidence=round(_history_confidence(len(history.sessions)), 2),
                    severity=_severity_from_risk(risk),
                    explanation=rule.explanation,
                    source_signals=[f"{rule.sensor_name} latest={value:g}"],
                )
            )

    for name in sorted(FUEL_TRIM_SENSORS):
        value = latest.get(name)
        if value is None or abs(value) < 15.0:
            continue
        risk = _clamp(0.42 + min(abs(value) - 15.0, 30.0) / 60.0)
        predictions.append(
            PredictionInput(
                issue="Fuel trim drift",
                risk_score=round(risk, 2),
                confidence=round(_history_confidence(len(history.sessions)), 2),
                severity=_severity_from_risk(risk),
                explanation="Fuel trim is outside the normal correction range.",
                source_signals=[f"{name} latest={value:g}%"],
            )
        )

    trend_prediction = _trend_prediction(history)
    if trend_prediction is not None:
        predictions.append(trend_prediction)

    return predictions


def _maintenance_prediction(history: StoredVehicleHistory) -> PredictionInput | None:
    if history.maintenance_events or history.vehicle.mileage < 75000:
        return None
    return PredictionInput(
        issue="Maintenance history missing",
        risk_score=0.25,
        confidence=0.2,
        severity="info",
        explanation=(
            "This vehicle has higher mileage but no recorded maintenance events, "
            "so interval-based predictions have low confidence."
        ),
        source_signals=[f"mileage={history.vehicle.mileage}", "maintenance_events=0"],
    )


def _trend_prediction(history: StoredVehicleHistory) -> PredictionInput | None:
    coolant_values = _sensor_series(history, "COOLANT_TEMP")
    if len(coolant_values) < 3:
        return None
    delta = coolant_values[-1] - coolant_values[0]
    if delta < 15.0:
        return None
    risk = _clamp(0.35 + min(delta, 40.0) / 100.0)
    return PredictionInput(
        issue="Coolant temperature upward trend",
        risk_score=round(risk, 2),
        confidence=round(_history_confidence(len(history.sessions)), 2),
        severity=_severity_from_risk(risk),
        explanation="Coolant temperature has trended upward across recorded sessions.",
        source_signals=[f"COOLANT_TEMP delta={delta:g}"],
    )


def _latest_sensor_values(history: StoredVehicleHistory) -> dict[str, float]:
    values: dict[str, float] = {}
    for session in history.sessions:
        for sensor in session.sensors:
            parsed = _parse_sensor_float(sensor)
            if parsed is not None:
                values[sensor.name] = parsed
    return values


def _sensor_series(history: StoredVehicleHistory, sensor_name: str) -> list[float]:
    values: list[float] = []
    for session in history.sessions:
        for sensor in session.sensors:
            if sensor.name != sensor_name:
                continue
            parsed = _parse_sensor_float(sensor)
            if parsed is not None:
                values.append(parsed)
    return values


def _parse_sensor_float(sensor: StoredSensorReading) -> float | None:
    try:
        return float(sensor.value)
    except ValueError:
        return None


def _violates_rule(value: float, rule: SensorRule) -> bool:
    if rule.low is not None and value < rule.low:
        return True
    return bool(rule.high is not None and value > rule.high)


def _distance_weight(value: float, rule: SensorRule) -> float:
    if rule.low is not None and value < rule.low:
        return min(rule.low - value, 20.0) / 40.0
    if rule.high is not None and value > rule.high:
        return min(value - rule.high, 40.0) / 80.0
    return 0.0


def _severity_weight(severity: str | None) -> float:
    if severity == "critical":
        return 0.20
    if severity == "warning":
        return 0.12
    if severity == "info":
        return 0.02
    return 0.05


def _severity_from_risk(risk: float) -> str:
    if risk >= 0.75:
        return "critical"
    if risk >= 0.45:
        return "warning"
    return "info"


def _history_confidence(session_count: int) -> float:
    if session_count <= 0:
        return 0.1
    return _clamp(0.20 + min(session_count, 8) * 0.07)


def _clamp(value: float) -> float:
    return max(0.0, min(0.95, value))
