from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from open_mechanic.api import create_app
from open_mechanic.api.schemas import (
    DiagnoseRequest,
    DiagnosisResponse,
    DTCResponse,
    HealthSnapshotResponse,
    HistoryExportResponse,
    HistoryImportRequest,
    HistoryImportResponse,
    HistorySessionDetailResponse,
    HistorySessionSummaryResponse,
    HistoryVehicleResponse,
    MaintenanceEventRequest,
    MaintenanceEventResponse,
    Mode6TestResponse,
    PredictionResponse,
    PredictionRunRequest,
    RecordSnapshotResponse,
    SensorReadingResponse,
    VehicleProfileRequest,
    VehicleProfileResponse,
)


class FakeService:
    def __init__(self) -> None:
        self.diagnose_requests: list[DiagnoseRequest] = []
        self.record_requests: list[VehicleProfileRequest] = []
        self.prediction_requests: list[PredictionRunRequest] = []

    def get_vehicle_profile(self) -> VehicleProfileResponse:
        return VehicleProfileResponse(
            configured=True,
            year=2018,
            make="Ford",
            model="F-150",
            mileage=85000,
        )

    def get_live_sensors(self) -> HealthSnapshotResponse:
        return HealthSnapshotResponse(
            connected=True,
            port="/dev/test",
            protocol="CAN",
            sensors=[
                SensorReadingResponse(
                    name="RPM",
                    value="750",
                    unit="rpm",
                    supported=True,
                    timestamp=datetime(2026, 5, 22, 1, 2, 3),
                )
            ],
            dtcs=[],
        )

    def get_dtcs(self) -> list[DTCResponse]:
        return [
            DTCResponse(
                code="P0420",
                description="Catalyst system efficiency below threshold",
                status="confirmed",
                severity="warning",
                category="emissions",
            )
        ]

    def get_mode6(self) -> list[Mode6TestResponse]:
        return [
            Mode6TestResponse(
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

    def get_snapshot(self) -> HealthSnapshotResponse:
        return HealthSnapshotResponse(
            connected=True,
            port="/dev/test",
            protocol="CAN",
            sensors=[],
            dtcs=self.get_dtcs(),
            mode6=self.get_mode6(),
        )

    def diagnose(self, request: DiagnoseRequest) -> DiagnosisResponse:
        self.diagnose_requests.append(request)
        return DiagnosisResponse(
            severity="warning",
            summary="Catalyst efficiency below threshold",
            likely_causes=["Cause A", "Cause B"],
            repair_steps=["Step A", "Step B"],
            estimated_cost_usd={"low": 100, "high": 500},
            diy_feasible=False,
            diy_difficulty="moderate",
            urgency="soon",
            disclaimer="diagnostic disclaimer",
            dtc_codes=["P0420"],
            vehicle_str="2018 Ford F-150 (85,000 miles)",
            cached=False,
            timestamp=datetime(2026, 5, 22, 1, 2, 3),
        )

    def record_snapshot(self, request: VehicleProfileRequest) -> RecordSnapshotResponse:
        self.record_requests.append(request)
        return RecordSnapshotResponse(
            connected=True,
            vehicle_id=1,
            session_id=2,
            sensors_recorded=1,
            dtcs_recorded=1,
            timestamp=datetime(2026, 5, 22, 1, 2, 3),
        )

    def list_history_vehicles(self) -> list[HistoryVehicleResponse]:
        return [
            HistoryVehicleResponse(
                id=1,
                year=2018,
                make="Ford",
                model="F-150",
                mileage=85000,
                vin=None,
                created_at=datetime(2026, 5, 22, 1, 2, 3),
            )
        ]

    def list_history_sessions(
        self, vehicle_id: int | None = None, limit: int = 20, offset: int = 0
    ) -> list[HistorySessionSummaryResponse]:
        return [
            HistorySessionSummaryResponse(
                id=2,
                vehicle_id=vehicle_id or 1,
                started_at=datetime(2026, 5, 22, 1, 2, 3),
                ended_at=datetime(2026, 5, 22, 1, 2, 4),
                port_used="/dev/test",
                protocol="CAN",
                sensor_count=1,
                dtc_count=1,
            )
        ]

    def get_history_session(self, session_id: int) -> HistorySessionDetailResponse | None:
        if session_id == 404:
            return None
        summary = self.list_history_sessions()[0]
        return HistorySessionDetailResponse(summary=summary, sensors=[], dtcs=[])

    def add_maintenance_event(self, request: MaintenanceEventRequest) -> MaintenanceEventResponse:
        return MaintenanceEventResponse(
            id=3,
            vehicle_id=request.vehicle_id,
            occurred_at=request.occurred_at or datetime(2026, 5, 22, 1, 2, 3),
            mileage=request.mileage,
            event_type=request.event_type,
            description=request.description,
            cost_usd=request.cost_usd,
        )

    def import_history(self, request: HistoryImportRequest) -> HistoryImportResponse:
        return HistoryImportResponse(imported_sessions=len(request.rows), skipped_rows=0)

    def export_history(self, vehicle_id: int | None = None) -> HistoryExportResponse:
        return HistoryExportResponse(
            schema_version=1,
            generated_at="2026-05-22T01:02:03",
            vehicles=[{"vehicle_key": "anon"}],
            sessions=[],
            maintenance_events=[],
            predictions=[],
        )

    def run_predictions(self, request: PredictionRunRequest) -> list[PredictionResponse]:
        self.prediction_requests.append(request)
        return [self._prediction()]

    def get_current_predictions(self, vehicle_id: int) -> list[PredictionResponse]:
        return [self._prediction(vehicle_id=vehicle_id)]

    def _prediction(self, vehicle_id: int = 1) -> PredictionResponse:
        return PredictionResponse(
            id=4,
            vehicle_id=vehicle_id,
            generated_at=datetime(2026, 5, 22, 1, 2, 3),
            issue="Recurring DTC P0420",
            risk_score=0.7,
            confidence=0.6,
            severity="warning",
            explanation="Repeated fault code",
            source_signals=["P0420"],
        )


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app(service=FakeService()))

    assert client.get("/api/health").json() == {"status": "ok", "service": "open-mechanic"}


def test_vehicle_endpoint_returns_profile() -> None:
    client = TestClient(create_app(service=FakeService()))

    response = client.get("/api/vehicle")

    assert response.status_code == 200
    assert response.json()["model"] == "F-150"


def test_live_endpoint_returns_sensor_snapshot() -> None:
    client = TestClient(create_app(service=FakeService()))

    response = client.get("/api/live")

    assert response.status_code == 200
    assert response.json()["sensors"][0]["name"] == "RPM"


def test_dtc_endpoint_returns_fault_codes() -> None:
    client = TestClient(create_app(service=FakeService()))

    response = client.get("/api/dtc")

    assert response.status_code == 200
    assert response.json()[0]["code"] == "P0420"


def test_mode6_endpoint_returns_monitor_tests() -> None:
    client = TestClient(create_app(service=FakeService()))

    response = client.get("/api/mode6")

    assert response.status_code == 200
    assert response.json()[0]["monitor"] == "MONITOR_MISFIRE_CYLINDER_1"


def test_snapshot_endpoint_returns_combined_snapshot() -> None:
    client = TestClient(create_app(service=FakeService()))

    response = client.get("/api/snapshot")

    assert response.status_code == 200
    assert response.json()["dtcs"][0]["severity"] == "warning"
    assert response.json()["mode6"][0]["status"] == "failed"


def test_diagnose_endpoint_passes_request_to_service() -> None:
    service = FakeService()
    client = TestClient(create_app(service=service))

    response = client.post(
        "/api/diagnose",
        json={
            "year": 2018,
            "make": "Ford",
            "model": "F-150",
            "mileage": 85000,
            "bypass_cache": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "Catalyst efficiency below threshold"
    assert service.diagnose_requests[0].bypass_cache is True


def test_history_and_prediction_endpoints() -> None:
    service = FakeService()
    client = TestClient(create_app(service=service))

    record_response = client.post(
        "/api/snapshot/record",
        json={"year": 2018, "make": "Ford", "model": "F-150", "mileage": 85000},
    )
    assert record_response.status_code == 200
    assert record_response.json()["session_id"] == 2
    assert service.record_requests[0].model == "F-150"

    assert client.get("/api/history/vehicles").json()[0]["make"] == "Ford"
    assert (
        client.get("/api/history/sessions?vehicle_id=1&limit=5&offset=0").json()[0]["vehicle_id"]
        == 1
    )
    assert client.get("/api/history/sessions/2").json()["summary"]["id"] == 2
    assert client.get("/api/history/sessions/404").status_code == 404

    maintenance_response = client.post(
        "/api/history/maintenance-events",
        json={"vehicle_id": 1, "event_type": "oil_change", "description": "Changed oil"},
    )
    assert maintenance_response.json()["event_type"] == "oil_change"

    assert (
        client.post("/api/history/import", json={"rows": [{"event": "x"}]}).json()[
            "imported_sessions"
        ]
        == 1
    )
    assert (
        client.get("/api/history/export?vehicle_id=1").json()["vehicles"][0]["vehicle_key"]
        == "anon"
    )

    run_response = client.post("/api/predictions/run", json={"vehicle_id": 1})
    assert run_response.json()[0]["issue"] == "Recurring DTC P0420"
    assert service.prediction_requests[0].vehicle_id == 1
    assert client.get("/api/predictions/current?vehicle_id=1").json()[0]["vehicle_id"] == 1
