from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .schemas import (
    DiagnoseRequest,
    DiagnosisResponse,
    DTCResponse,
    HealthResponse,
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
    VehicleProfileRequest,
    VehicleProfileResponse,
)
from .services import DiagnosticAPIService


def create_app(service: DiagnosticAPIService | None = None) -> FastAPI:
    app = FastAPI(title="open-mechanic API", version="0.1.0")
    api_service = service or DiagnosticAPIService()

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="open-mechanic")

    @app.get("/api/vehicle", response_model=VehicleProfileResponse)
    def vehicle() -> VehicleProfileResponse:
        return api_service.get_vehicle_profile()

    @app.get("/api/live", response_model=HealthSnapshotResponse)
    def live() -> HealthSnapshotResponse:
        return api_service.get_live_sensors()

    @app.get("/api/dtc", response_model=list[DTCResponse])
    def dtc() -> list[DTCResponse]:
        return api_service.get_dtcs()

    @app.get("/api/mode6", response_model=list[Mode6TestResponse])
    def mode6() -> list[Mode6TestResponse]:
        return api_service.get_mode6()

    @app.get("/api/snapshot", response_model=HealthSnapshotResponse)
    def snapshot() -> HealthSnapshotResponse:
        return api_service.get_snapshot()

    @app.post("/api/diagnose", response_model=DiagnosisResponse)
    def diagnose(request: DiagnoseRequest) -> DiagnosisResponse:
        return api_service.diagnose(request)

    @app.post("/api/snapshot/record", response_model=RecordSnapshotResponse)
    def record_snapshot(request: VehicleProfileRequest) -> RecordSnapshotResponse:
        return api_service.record_snapshot(request)

    @app.get("/api/history/vehicles", response_model=list[HistoryVehicleResponse])
    def history_vehicles() -> list[HistoryVehicleResponse]:
        return api_service.list_history_vehicles()

    @app.get("/api/history/sessions", response_model=list[HistorySessionSummaryResponse])
    def history_sessions(
        vehicle_id: int | None = None, limit: int = 20, offset: int = 0
    ) -> list[HistorySessionSummaryResponse]:
        return api_service.list_history_sessions(
            vehicle_id=vehicle_id,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/history/sessions/{session_id}", response_model=HistorySessionDetailResponse)
    def history_session(session_id: int) -> HistorySessionDetailResponse:
        detail = api_service.get_history_session(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="history session not found")
        return detail

    @app.post("/api/history/maintenance-events", response_model=MaintenanceEventResponse)
    def maintenance_event(request: MaintenanceEventRequest) -> MaintenanceEventResponse:
        return api_service.add_maintenance_event(request)

    @app.post("/api/history/import", response_model=HistoryImportResponse)
    def history_import(request: HistoryImportRequest) -> HistoryImportResponse:
        return api_service.import_history(request)

    @app.get("/api/history/export", response_model=HistoryExportResponse)
    def history_export(vehicle_id: int | None = None) -> HistoryExportResponse:
        return api_service.export_history(vehicle_id=vehicle_id)

    @app.post("/api/predictions/run", response_model=list[PredictionResponse])
    def prediction_run(request: PredictionRunRequest) -> list[PredictionResponse]:
        return api_service.run_predictions(request)

    @app.get("/api/predictions/current", response_model=list[PredictionResponse])
    def prediction_current(vehicle_id: int) -> list[PredictionResponse]:
        return api_service.get_current_predictions(vehicle_id)

    return app
