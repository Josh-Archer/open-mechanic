from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from open_mechanic.db.models import PredictionResult, get_session, init_db
from open_mechanic.history import (
    DTCObservation,
    HistoryStore,
    PredictionInput,
    SensorObservation,
    VehicleIdentity,
    _dtcs_from_payload,
    _parse_datetime,
    _sensors_from_health_payload,
    _vehicle_from_log_rows,
)


def _store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(engine=init_db(str(tmp_path / "history.db")))


def _vehicle() -> VehicleIdentity:
    return VehicleIdentity(year=2018, make="Ford", model="F-150", mileage=85000, vin="VIN123")


def test_history_store_records_and_retrieves_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    timestamp = datetime(2026, 5, 31, 12, 0, 0)

    stored_vehicle = store.upsert_vehicle(_vehicle())
    updated_vehicle = store.upsert_vehicle(
        VehicleIdentity(year=2018, make="Ford", model="F-150", mileage=86000, vin="VIN123")
    )
    detail = store.record_snapshot(
        _vehicle(),
        [SensorObservation("RPM", "750", "rpm", timestamp)],
        [DTCObservation("P0420", "Catalyst", "confirmed", "warning", "emissions", timestamp)],
        port_used="/dev/test",
        protocol="CAN",
    )

    assert stored_vehicle.id == updated_vehicle.id
    assert updated_vehicle.mileage == 86000
    assert detail.summary.sensor_count == 1
    assert detail.summary.dtc_count == 1
    assert detail.sensors[0].name == "RPM"
    assert detail.dtcs[0].code == "P0420"
    assert store.list_vehicles()[0].vin == "VIN123"
    assert (
        store.list_sessions(vehicle_id=stored_vehicle.id, limit=1, offset=0)[0].id
        == detail.summary.id
    )
    assert store.get_session_detail(detail.summary.id) == detail
    assert store.get_session_detail(9999) is None


def test_history_store_maintenance_predictions_and_export(tmp_path: Path) -> None:
    store = _store(tmp_path)
    vehicle = store.upsert_vehicle(_vehicle())
    event = store.add_maintenance_event(
        vehicle.id,
        event_type="oil_change",
        description="Changed oil and filter",
        mileage=85000,
        occurred_at=datetime(2026, 5, 1, 9, 0, 0),
        cost_usd=45,
    )
    predictions = store.save_predictions(
        vehicle.id,
        [
            PredictionInput("Recurring DTC P0420", 0.7, 0.6, "warning", "Repeated", ["P0420"]),
            PredictionInput("Recurring DTC P0420", 0.8, 0.7, "critical", "Worse", ["P0420"]),
        ],
    )

    history = store.get_vehicle_history(vehicle.id)
    export = store.export_anonymized(vehicle.id)

    assert event.description == "Changed oil and filter"
    assert history is not None
    assert history.maintenance_events[0].event_type == "oil_change"
    assert predictions[0].source_signals == ["P0420"]
    assert store.latest_predictions(vehicle.id)[0].risk_score == 0.8
    assert export["vehicles"][0]["vehicle_key"]
    assert "VIN123" not in json.dumps(export)
    assert export["maintenance_events"][0]["event_type"] == "oil_change"
    assert export["predictions"][0]["issue"] == "Recurring DTC P0420"
    assert store.get_vehicle_history(9999) is None


def test_history_imports_local_session_log_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rows: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-31T12:00:00",
            "event": "session_started",
            "payload": {"vehicle": {"year": 2018, "make": "Ford", "model": "F-150"}},
        },
        {
            "timestamp": "2026-05-31T12:00:01",
            "event": "sensor_snapshot",
            "payload": {
                "RPM": {"value": "750", "unit": "rpm"},
                "BAD": "not a sensor",
            },
        },
        {
            "timestamp": "2026-05-31T12:00:02",
            "event": "health_snapshot",
            "payload": {
                "sensors": [
                    {"name": "COOLANT_TEMP", "value": "95", "unit": "degC"},
                    {"label": "Engine Load", "value": "20", "unit": "%"},
                    "bad",
                ],
                "dtcs": [
                    {
                        "code": "P0420",
                        "description": "Catalyst",
                        "status": "confirmed",
                        "severity": "warning",
                        "category": "emissions",
                    },
                    {"description": "missing code"},
                ],
            },
        },
        {
            "timestamp": "bad timestamp",
            "event": "dtcs",
            "payload": {"codes": [{"code": "P0300"}]},
        },
        {"timestamp": "2026-05-31T12:00:04", "event": "unknown", "payload": {}},
        {"timestamp": "2026-05-31T12:00:05", "event": "sensor_snapshot", "payload": []},
    ]

    result = store.import_session_log_rows(rows)
    sessions = store.list_sessions()

    assert result.imported_sessions == 3
    assert result.skipped_rows == 3
    assert len(sessions) == 3
    assert sessions[0].vehicle_id == sessions[1].vehicle_id
    assert store.import_session_log_rows([{"event": "sensor_snapshot"}]).skipped_rows == 1


def test_history_private_parsers_handle_empty_and_invalid_values(tmp_path: Path) -> None:
    assert _parse_datetime(None) is None
    assert _parse_datetime("not a date") is None
    assert _dtcs_from_payload(None, datetime(2026, 5, 31)) == []
    assert _dtcs_from_payload(["bad"], datetime(2026, 5, 31)) == []
    assert _sensors_from_health_payload({}, datetime(2026, 5, 31)) == []
    assert _vehicle_from_log_rows([{"event": "session_started", "payload": []}]) is None
    assert (
        _vehicle_from_log_rows([{"event": "session_started", "payload": {"vehicle": []}}]) is None
    )

    engine = init_db(str(tmp_path / "predictions.db"))
    with get_session(engine) as session:
        model = PredictionResult(
            vehicle_id=1,
            issue="bad source",
            risk_score=0.1,
            confidence=0.1,
            severity="info",
            explanation="bad json shape",
            source_signals_json=json.dumps({"not": "a list"}),
        )
        session.add(model)

    stored = HistoryStore(engine=engine).latest_predictions(1)
    assert stored[0].source_signals == []
