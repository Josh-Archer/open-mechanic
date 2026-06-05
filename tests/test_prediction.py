from __future__ import annotations

from datetime import datetime

from open_mechanic.history import (
    StoredDTCRecord,
    StoredMaintenanceEvent,
    StoredSensorReading,
    StoredSessionDetail,
    StoredSessionSummary,
    StoredVehicle,
    StoredVehicleHistory,
)
from open_mechanic.prediction import (
    PredictiveMaintenanceEngine,
    SensorRule,
    _clamp,
    _distance_weight,
    _history_confidence,
    _severity_from_risk,
    _severity_weight,
    _violates_rule,
)


def _vehicle(mileage: int = 85000) -> StoredVehicle:
    return StoredVehicle(1, 2018, "Ford", "F-150", mileage, None, datetime(2026, 5, 31))


def _summary(session_id: int) -> StoredSessionSummary:
    return StoredSessionSummary(
        id=session_id,
        vehicle_id=1,
        started_at=datetime(2026, 5, 31, 12, session_id, 0),
        ended_at=datetime(2026, 5, 31, 12, session_id, 1),
        port_used="/dev/test",
        protocol="CAN",
        sensor_count=1,
        dtc_count=1,
    )


def _sensor(session_id: int, name: str, value: str) -> StoredSensorReading:
    return StoredSensorReading(
        id=session_id,
        session_id=session_id,
        name=name,
        value=value,
        unit=None,
        timestamp=datetime(2026, 5, 31, 12, session_id, 0),
    )


def _dtc(session_id: int, code: str, status: str = "pending") -> StoredDTCRecord:
    return StoredDTCRecord(
        id=session_id,
        session_id=session_id,
        code=code,
        description="Test code",
        status=status,
        severity="warning",
        category="engine",
        timestamp=datetime(2026, 5, 31, 12, session_id, 0),
    )


def test_prediction_engine_reports_no_risk_for_clean_history() -> None:
    history = StoredVehicleHistory(
        vehicle=_vehicle(mileage=20000),
        sessions=[
            StoredSessionDetail(
                summary=_summary(1),
                sensors=[_sensor(1, "RPM", "750")],
                dtcs=[],
            )
        ],
        maintenance_events=[],
    )

    predictions = PredictiveMaintenanceEngine().score(history)

    assert predictions[0].issue == "No immediate risk signals"
    assert predictions[0].severity == "info"


def test_prediction_engine_scores_repeated_dtcs_and_sensor_anomalies() -> None:
    history = StoredVehicleHistory(
        vehicle=_vehicle(),
        sessions=[
            StoredSessionDetail(
                summary=_summary(1),
                sensors=[
                    _sensor(1, "COOLANT_TEMP", "95"),
                    _sensor(1, "CONTROL_MODULE_VOLTAGE", "11.5"),
                    _sensor(1, "SHORT_FUEL_TRIM_1", "18"),
                ],
                dtcs=[_dtc(1, "P0420", "confirmed")],
            ),
            StoredSessionDetail(
                summary=_summary(2),
                sensors=[
                    _sensor(2, "COOLANT_TEMP", "120"),
                    _sensor(2, "ENGINE_LOAD", "95"),
                    _sensor(2, "LONG_FUEL_TRIM_1", "-19"),
                    _sensor(2, "BAD", "not numeric"),
                ],
                dtcs=[_dtc(2, "P0420", "confirmed")],
            ),
            StoredSessionDetail(
                summary=_summary(3),
                sensors=[_sensor(3, "COOLANT_TEMP", "130")],
                dtcs=[_dtc(3, "P0300", "pending"), _dtc(3, "P0300", "pending")],
            ),
        ],
        maintenance_events=[],
    )

    issues = [prediction.issue for prediction in PredictiveMaintenanceEngine().score(history)]

    assert "Recurring DTC P0420" in issues
    assert "Cooling system stress" in issues
    assert "Charging system voltage anomaly" in issues
    assert "High engine load" in issues
    assert "Fuel trim drift" in issues
    assert "Coolant temperature upward trend" in issues
    assert "Maintenance history missing" in issues


def test_prediction_engine_uses_maintenance_events_to_skip_interval_warning() -> None:
    history = StoredVehicleHistory(
        vehicle=_vehicle(),
        sessions=[],
        maintenance_events=[
            StoredMaintenanceEvent(
                id=1,
                vehicle_id=1,
                occurred_at=datetime(2026, 5, 1),
                mileage=84000,
                event_type="oil_change",
                description="Changed oil",
                cost_usd=45,
            )
        ],
    )

    predictions = PredictiveMaintenanceEngine().score(history)

    assert predictions[0].issue == "No immediate risk signals"
    assert predictions[0].confidence == 0.1


def test_prediction_engine_ignores_small_coolant_trends() -> None:
    history = StoredVehicleHistory(
        vehicle=_vehicle(mileage=20000),
        sessions=[
            StoredSessionDetail(
                summary=_summary(1), sensors=[_sensor(1, "COOLANT_TEMP", "90")], dtcs=[]
            ),
            StoredSessionDetail(
                summary=_summary(2), sensors=[_sensor(2, "COOLANT_TEMP", "96")], dtcs=[]
            ),
            StoredSessionDetail(
                summary=_summary(3), sensors=[_sensor(3, "COOLANT_TEMP", "100")], dtcs=[]
            ),
        ],
        maintenance_events=[],
    )

    predictions = PredictiveMaintenanceEngine().score(history)

    assert predictions[0].issue == "No immediate risk signals"


def test_prediction_helpers_cover_thresholds() -> None:
    low_rule = SensorRule("VOLTAGE", 12.0, None, "low", "low")
    high_rule = SensorRule("TEMP", None, 100.0, "high", "high")

    assert _violates_rule(11.0, low_rule) is True
    assert _violates_rule(101.0, high_rule) is True
    assert _violates_rule(12.5, low_rule) is False
    assert _distance_weight(11.0, low_rule) > 0
    assert _distance_weight(101.0, high_rule) > 0
    assert _distance_weight(12.5, low_rule) == 0
    assert _severity_weight("critical") == 0.20
    assert _severity_weight("warning") == 0.12
    assert _severity_weight("info") == 0.02
    assert _severity_weight(None) == 0.05
    assert _severity_from_risk(0.8) == "critical"
    assert _severity_from_risk(0.5) == "warning"
    assert _severity_from_risk(0.1) == "info"
    assert _history_confidence(0) == 0.1
    assert _clamp(-1.0) == 0.0
    assert _clamp(2.0) == 0.95
