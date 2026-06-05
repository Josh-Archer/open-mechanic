from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from open_mechanic.ai.diagnose import DiagnosisResult
from open_mechanic.api.schemas import (
    DiagnoseRequest,
    HealthSnapshotResponse,
    HistoryImportRequest,
    MaintenanceEventRequest,
    PredictionRunRequest,
    SensorReadingResponse,
    VehicleProfileRequest,
)
from open_mechanic.api.services import (
    DiagnosticAPIService,
    _default_connection,
    _latest_snapshot_timestamp,
)
from open_mechanic.dtc import DTCCode
from open_mechanic.history import HistoryStore, PredictionInput
from open_mechanic.local_store import VehicleProfile
from open_mechanic.mode6 import Mode6TestResult
from open_mechanic.reader import SensorValue


class FakeRawConnection:
    def protocol_name(self) -> str:
        return "CAN"


class FakeConnection:
    def __init__(self, *, connects: bool = True) -> None:
        self.connects = connects
        self.disconnected = False

    def connect(self) -> bool:
        return self.connects

    def disconnect(self) -> None:
        self.disconnected = True

    def is_connected(self) -> bool:
        return self.connects

    def get_port(self) -> str:
        return "/dev/test"

    def get_connection(self) -> FakeRawConnection | None:
        if not self.connects:
            return None
        return FakeRawConnection()


class FakeSensorPoller:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def get_snapshot(self) -> dict[str, SensorValue]:
        return {
            "RPM": SensorValue(
                name="RPM",
                value="750",
                unit="rpm",
                supported=True,
                timestamp=datetime(2026, 5, 22, 1, 2, 3),
            )
        }


class FakeDTCReader:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def get_dtcs(self) -> list[DTCCode]:
        return [
            DTCCode(
                code="P0420",
                description="Catalyst system efficiency below threshold",
                status="confirmed",
                severity="warning",
                category="emissions",
            )
        ]


class FakeMode6Reader:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def get_results(self) -> list[Mode6TestResult]:
        return [
            Mode6TestResult(
                monitor="MONITOR_MISFIRE_CYLINDER_1",
                monitor_description="Misfire Cylinder 1 Data",
                category="misfire",
                test_id=1,
                test_name="Misfire counts",
                description="Misfire counts",
                value="12",
                minimum="0",
                maximum="5",
                unit="count",
                passed=False,
                status="failed",
            )
        ]


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[
            tuple[Any, list[DTCCode], dict[str, Any], list[Mode6TestResult], Any, bool]
        ] = []

    def diagnose(
        self,
        vehicle: Any,
        dtcs: list[DTCCode],
        sensor_snapshot: dict[str, Any],
        *,
        mode6_results: list[Mode6TestResult] | None = None,
        misfire_summary: Any = None,
        bypass_cache: bool = False,
    ) -> DiagnosisResult:
        self.calls.append(
            (vehicle, dtcs, sensor_snapshot, mode6_results or [], misfire_summary, bypass_cache)
        )
        return DiagnosisResult(
            severity="warning",
            summary="Catalyst efficiency below threshold",
            likely_causes=["Cause A"],
            repair_steps=["Step A"],
            estimated_cost_usd={"low": 100, "high": 500},
            diy_feasible=False,
            diy_difficulty="moderate",
            urgency="soon",
            disclaimer="diagnostic disclaimer",
            dtc_codes=[dtc.code for dtc in dtcs],
            vehicle_str=f"{vehicle.year} {vehicle.make} {vehicle.model} ({vehicle.mileage:,} miles)",
            cached=False,
            timestamp=datetime(2026, 5, 22, 1, 2, 3),
        )


class FakePredictionEngine:
    def score(self, history: object) -> list[PredictionInput]:
        return [
            PredictionInput(
                issue="Recurring DTC P0420",
                risk_score=0.7,
                confidence=0.6,
                severity="warning",
                explanation="Repeated fault code",
                source_signals=["P0420"],
            )
        ]


def test_vehicle_profile_reports_unconfigured_when_profile_missing() -> None:
    service = DiagnosticAPIService(profile_loader=lambda: None)

    profile = service.get_vehicle_profile()

    assert profile.configured is False
    assert profile.make is None


def test_vehicle_profile_maps_local_profile() -> None:
    service = DiagnosticAPIService(
        profile_loader=lambda: VehicleProfile(
            year=2018,
            make="Ford",
            model="F-150",
            mileage=85000,
        )
    )

    profile = service.get_vehicle_profile()

    assert profile.configured is True
    assert profile.year == 2018
    assert profile.model == "F-150"


def test_live_sensors_returns_empty_snapshot_when_adapter_unavailable() -> None:
    service = DiagnosticAPIService(connection_factory=lambda: FakeConnection(connects=False))

    snapshot = service.get_live_sensors()

    assert snapshot.connected is False
    assert snapshot.port == "/dev/test"
    assert snapshot.sensors == []
    assert snapshot.dtcs == []


def test_get_dtcs_returns_empty_list_when_adapter_unavailable() -> None:
    service = DiagnosticAPIService(connection_factory=lambda: FakeConnection(connects=False))

    assert service.get_dtcs() == []


def test_get_dtcs_maps_codes_and_disconnects(monkeypatch: Any) -> None:
    connection = FakeConnection()
    monkeypatch.setattr("open_mechanic.api.services.DTCReader", FakeDTCReader)
    service = DiagnosticAPIService(connection_factory=lambda: connection)

    dtcs = service.get_dtcs()

    assert dtcs[0].code == "P0420"
    assert connection.disconnected is True


def test_get_mode6_maps_monitor_results_and_disconnects(monkeypatch: Any) -> None:
    connection = FakeConnection()
    monkeypatch.setattr("open_mechanic.api.services.Mode6Reader", FakeMode6Reader)
    service = DiagnosticAPIService(connection_factory=lambda: connection)

    results = service.get_mode6()

    assert results[0].monitor == "MONITOR_MISFIRE_CYLINDER_1"
    assert connection.disconnected is True


def test_get_mode6_returns_empty_list_when_adapter_unavailable() -> None:
    service = DiagnosticAPIService(connection_factory=lambda: FakeConnection(connects=False))

    assert service.get_mode6() == []


def test_snapshot_returns_empty_snapshot_when_adapter_unavailable() -> None:
    service = DiagnosticAPIService(connection_factory=lambda: FakeConnection(connects=False))

    snapshot = service.get_snapshot()

    assert snapshot.connected is False
    assert snapshot.port == "/dev/test"
    assert snapshot.sensors == []
    assert snapshot.dtcs == []


def test_live_sensors_maps_sensor_values_and_disconnects(monkeypatch: Any) -> None:
    connection = FakeConnection()
    monkeypatch.setattr("open_mechanic.api.services.SensorPoller", FakeSensorPoller)
    service = DiagnosticAPIService(connection_factory=lambda: connection)

    snapshot = service.get_live_sensors()

    assert snapshot.connected is True
    assert snapshot.protocol == "CAN"
    assert snapshot.sensors[0].name == "RPM"
    assert connection.disconnected is True


def test_snapshot_combines_sensors_and_dtcs(monkeypatch: Any) -> None:
    connection = FakeConnection()
    monkeypatch.setattr("open_mechanic.api.services.SensorPoller", FakeSensorPoller)
    monkeypatch.setattr("open_mechanic.api.services.DTCReader", FakeDTCReader)
    monkeypatch.setattr("open_mechanic.api.services.Mode6Reader", FakeMode6Reader)
    service = DiagnosticAPIService(connection_factory=lambda: connection)

    snapshot = service.get_snapshot()

    assert snapshot.connected is True
    assert snapshot.sensors[0].value == "750"
    assert snapshot.dtcs[0].code == "P0420"
    assert snapshot.mode6[0].status == "failed"
    assert snapshot.misfire_summary is not None
    assert snapshot.misfire_summary.status == "possible_misfire"
    assert connection.disconnected is True


def test_diagnose_passes_vehicle_snapshot_and_cache_flag(monkeypatch: Any) -> None:
    engine = FakeEngine()
    monkeypatch.setattr("open_mechanic.api.services.SensorPoller", FakeSensorPoller)
    monkeypatch.setattr("open_mechanic.api.services.DTCReader", FakeDTCReader)
    monkeypatch.setattr("open_mechanic.api.services.Mode6Reader", FakeMode6Reader)
    service = DiagnosticAPIService(
        connection_factory=lambda: FakeConnection(),
        engine_factory=lambda: engine,
    )

    result = service.diagnose(
        DiagnoseRequest(
            year=2018,
            make="Ford",
            model="F-150",
            mileage=85000,
            bypass_cache=True,
        )
    )

    vehicle, dtcs, sensor_snapshot, mode6_results, misfire_summary, bypass_cache = engine.calls[0]
    assert result.summary == "Catalyst efficiency below threshold"
    assert vehicle.model == "F-150"
    assert dtcs[0].code == "P0420"
    assert sensor_snapshot["RPM"]["supported"] is True
    assert mode6_results[0].passed is False
    assert misfire_summary.status == "possible_misfire"
    assert bypass_cache is True


def test_default_connection_uses_api_tuned_timeout(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class CapturingConnection:
        def __init__(self, *, timeout: float, max_retries: int) -> None:
            captured["timeout"] = timeout
            captured["max_retries"] = max_retries

    monkeypatch.setenv("OPEN_MECHANIC_API_OBD_TIMEOUT", "1.5")
    monkeypatch.setenv("OPEN_MECHANIC_API_OBD_RETRIES", "2")
    monkeypatch.setattr("open_mechanic.api.services.OBDConnection", CapturingConnection)

    _ = _default_connection()

    assert captured == {"timeout": 1.5, "max_retries": 2}


def test_latest_snapshot_timestamp_uses_sensor_timestamp() -> None:
    timestamp = datetime(2026, 5, 31, 12, 0, 0)
    snapshot = HealthSnapshotResponse(
        connected=True,
        port="/dev/test",
        protocol="CAN",
        sensors=[
            SensorReadingResponse(
                name="RPM",
                value="750",
                unit="rpm",
                supported=True,
                timestamp=timestamp,
            )
        ],
        dtcs=[],
    )

    assert _latest_snapshot_timestamp(snapshot) == timestamp


def test_record_snapshot_persists_history_when_connected(tmp_path: Path, monkeypatch: Any) -> None:
    connection = FakeConnection()
    monkeypatch.setattr("open_mechanic.api.services.SensorPoller", FakeSensorPoller)
    monkeypatch.setattr("open_mechanic.api.services.DTCReader", FakeDTCReader)
    store = HistoryStore(db_path=str(tmp_path / "history.db"))
    service = DiagnosticAPIService(
        connection_factory=lambda: connection,
        history_store=store,
    )

    response = service.record_snapshot(
        VehicleProfileRequest(year=2018, make="Ford", model="F-150", mileage=85000)
    )

    assert response.connected is True
    assert response.sensors_recorded == 1
    assert response.dtcs_recorded == 1
    assert service.list_history_sessions()[0].sensor_count == 1
    assert service.list_history_vehicles()[0].model == "F-150"
    assert service.get_history_session(response.session_id or 0) is not None


def test_record_snapshot_returns_empty_when_adapter_unavailable(tmp_path: Path) -> None:
    service = DiagnosticAPIService(
        connection_factory=lambda: FakeConnection(connects=False),
        history_store=HistoryStore(db_path=str(tmp_path / "history.db")),
    )

    response = service.record_snapshot(
        VehicleProfileRequest(year=2018, make="Ford", model="F-150", mileage=85000)
    )

    assert response.connected is False
    assert response.session_id is None


def test_history_maintenance_prediction_export_and_import(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("open_mechanic.api.services.SensorPoller", FakeSensorPoller)
    monkeypatch.setattr("open_mechanic.api.services.DTCReader", FakeDTCReader)
    store = HistoryStore(db_path=str(tmp_path / "history.db"))
    service = DiagnosticAPIService(
        connection_factory=lambda: FakeConnection(),
        history_store=store,
        prediction_engine=FakePredictionEngine(),
    )
    recorded = service.record_snapshot(
        VehicleProfileRequest(year=2018, make="Ford", model="F-150", mileage=85000)
    )
    vehicle_id = recorded.vehicle_id or 0

    event = service.add_maintenance_event(
        MaintenanceEventRequest(
            vehicle_id=vehicle_id,
            event_type="oil_change",
            description="Changed oil",
            mileage=85000,
        )
    )
    predictions = service.run_predictions(PredictionRunRequest(vehicle_id=vehicle_id))
    current = service.get_current_predictions(vehicle_id)
    export = service.export_history(vehicle_id)
    imported = service.import_history(
        HistoryImportRequest(
            rows=[
                {
                    "timestamp": "2026-05-31T12:00:00",
                    "event": "session_started",
                    "payload": {
                        "vehicle": {"year": 2018, "make": "Ford", "model": "F-150", "mileage": 1}
                    },
                },
                {
                    "timestamp": "2026-05-31T12:00:01",
                    "event": "sensor_snapshot",
                    "payload": {"RPM": {"value": "750", "unit": "rpm"}},
                },
            ]
        )
    )

    assert event.event_type == "oil_change"
    assert predictions[0].issue == "Recurring DTC P0420"
    assert current[0].risk_score == 0.7
    assert export.schema_version == 1
    assert imported.imported_sessions == 1
    assert service.get_history_session(9999) is None
    assert service.run_predictions(PredictionRunRequest(vehicle_id=9999)) == []


def test_history_store_is_created_lazily(monkeypatch: Any) -> None:
    created: list[bool] = []

    class FakeHistoryStore:
        def __init__(self) -> None:
            created.append(True)

        def list_vehicles(self) -> list[object]:
            return []

    monkeypatch.setattr("open_mechanic.api.services.HistoryStore", FakeHistoryStore)
    service = DiagnosticAPIService()

    assert service.list_history_vehicles() == []
    assert created == [True]
