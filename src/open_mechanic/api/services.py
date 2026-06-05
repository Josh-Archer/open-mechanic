from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime

from open_mechanic.ai.diagnose import DiagnosticEngine
from open_mechanic.connection import OBDConnection
from open_mechanic.db.models import VehicleProfile as DiagnosticVehicleProfile
from open_mechanic.dtc import DTCCode, DTCReader
from open_mechanic.history import (
    DTCObservation,
    HistoryStore,
    SensorObservation,
    VehicleIdentity,
)
from open_mechanic.local_store import VehicleProfile as LocalVehicleProfile
from open_mechanic.local_store import load_vehicle_profile
from open_mechanic.mode6 import (
    MisfireFinding,
    MisfireSummary,
    Mode6Reader,
    Mode6TestResult,
    diagnose_misfires,
)
from open_mechanic.prediction import PredictiveMaintenanceEngine
from open_mechanic.reader import SensorPoller, SensorValue

from .schemas import (
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
    MisfireFindingResponse,
    MisfireSummaryResponse,
    Mode6TestResponse,
    PredictionResponse,
    PredictionRunRequest,
    RecordSnapshotResponse,
    SensorReadingResponse,
    VehicleProfileRequest,
    VehicleProfileResponse,
)

ConnectionFactory = Callable[[], OBDConnection]
ProfileLoader = Callable[[], LocalVehicleProfile | None]
EngineFactory = Callable[[], DiagnosticEngine]


class DiagnosticAPIService:
    def __init__(
        self,
        connection_factory: ConnectionFactory | None = None,
        profile_loader: ProfileLoader = load_vehicle_profile,
        engine_factory: EngineFactory | None = None,
        history_store: HistoryStore | None = None,
        prediction_engine: PredictiveMaintenanceEngine | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _default_connection
        self._profile_loader = profile_loader
        self._engine_factory = engine_factory or DiagnosticEngine
        self._history_store = history_store
        self._prediction_engine = prediction_engine or PredictiveMaintenanceEngine()

    def get_vehicle_profile(self) -> VehicleProfileResponse:
        profile = self._profile_loader()
        if profile is None:
            return VehicleProfileResponse(configured=False)

        return VehicleProfileResponse(
            configured=True,
            year=profile.year,
            make=profile.make,
            model=profile.model,
            mileage=profile.mileage,
        )

    def get_live_sensors(self) -> HealthSnapshotResponse:
        connection = self._connection_factory()
        connected = connection.connect()
        if not connected:
            return HealthSnapshotResponse(
                connected=False,
                port=connection.get_port(),
                protocol=None,
                sensors=[],
                dtcs=[],
            )

        try:
            raw_connection = connection.get_connection()
            protocol = raw_connection.protocol_name() if raw_connection is not None else None
            sensors = _sensor_responses(SensorPoller(connection).get_snapshot())
            return HealthSnapshotResponse(
                connected=True,
                port=connection.get_port(),
                protocol=protocol,
                sensors=sensors,
                dtcs=[],
            )
        finally:
            connection.disconnect()

    def get_dtcs(self) -> list[DTCResponse]:
        connection = self._connection_factory()
        connected = connection.connect()
        if not connected:
            return []

        try:
            return _dtc_responses(DTCReader(connection).get_dtcs())
        finally:
            connection.disconnect()

    def get_mode6(self) -> list[Mode6TestResponse]:
        connection = self._connection_factory()
        connected = connection.connect()
        if not connected:
            return []

        try:
            return _mode6_responses(Mode6Reader(connection).get_results())
        finally:
            connection.disconnect()

    def get_snapshot(self) -> HealthSnapshotResponse:
        connection = self._connection_factory()
        connected = connection.connect()
        if not connected:
            return HealthSnapshotResponse(
                connected=False,
                port=connection.get_port(),
                protocol=None,
                sensors=[],
                dtcs=[],
            )

        try:
            raw_connection = connection.get_connection()
            protocol = raw_connection.protocol_name() if raw_connection is not None else None
            sensor_snapshot = SensorPoller(connection).get_snapshot()
            dtcs = DTCReader(connection).get_dtcs()
            mode6_results = Mode6Reader(connection).get_results()
            misfire_summary = diagnose_misfires(mode6_results, dtcs, sensor_snapshot)
            return HealthSnapshotResponse(
                connected=True,
                port=connection.get_port(),
                protocol=protocol,
                sensors=_sensor_responses(sensor_snapshot),
                dtcs=_dtc_responses(dtcs),
                mode6=_mode6_responses(mode6_results),
                misfire_summary=_misfire_summary_response(misfire_summary),
            )
        finally:
            connection.disconnect()

    def diagnose(self, request: DiagnoseRequest) -> DiagnosisResponse:
        snapshot = self.get_snapshot()
        vehicle = DiagnosticVehicleProfile(
            year=request.year,
            make=request.make,
            model=request.model,
            mileage=request.mileage,
            vin=request.vin,
        )
        dtcs = [
            DTCCode(
                code=dtc.code,
                description=dtc.description,
                status=dtc.status,
                severity=dtc.severity,
                category=dtc.category,
            )
            for dtc in snapshot.dtcs
        ]
        sensor_snapshot = {
            sensor.name: {
                "value": sensor.value,
                "unit": sensor.unit,
                "supported": sensor.supported,
            }
            for sensor in snapshot.sensors
        }
        mode6_results = [
            Mode6TestResult(
                monitor=item.monitor,
                monitor_description=item.monitor_description,
                category=item.category,
                test_id=item.test_id,
                test_name=item.test_name,
                description=item.description,
                value=item.value,
                minimum=item.minimum,
                maximum=item.maximum,
                unit=item.unit,
                passed=item.passed,
                status=item.status,
            )
            for item in snapshot.mode6
        ]
        misfire_summary = (
            _misfire_summary_from_response(snapshot.misfire_summary)
            if snapshot.misfire_summary is not None
            else None
        )
        result = self._engine_factory().diagnose(
            vehicle,
            dtcs,
            sensor_snapshot,
            mode6_results=mode6_results,
            misfire_summary=misfire_summary,
            bypass_cache=request.bypass_cache,
        )
        return DiagnosisResponse(
            severity=result.severity,
            summary=result.summary,
            likely_causes=result.likely_causes,
            repair_steps=result.repair_steps,
            estimated_cost_usd=result.estimated_cost_usd,
            diy_feasible=result.diy_feasible,
            diy_difficulty=result.diy_difficulty,
            urgency=result.urgency,
            disclaimer=result.disclaimer,
            dtc_codes=result.dtc_codes,
            vehicle_str=result.vehicle_str,
            cached=result.cached,
            timestamp=result.timestamp,
        )

    def record_snapshot(self, request: VehicleProfileRequest) -> RecordSnapshotResponse:
        snapshot = self.get_snapshot()
        if not snapshot.connected:
            return RecordSnapshotResponse(
                connected=False,
                vehicle_id=None,
                session_id=None,
                sensors_recorded=0,
                dtcs_recorded=0,
                timestamp=_latest_snapshot_timestamp(snapshot),
            )

        detail = self._store().record_snapshot(
            _vehicle_identity(request),
            _sensor_observations(snapshot.sensors),
            _dtc_observations(snapshot.dtcs),
            port_used=snapshot.port,
            protocol=snapshot.protocol,
        )
        return RecordSnapshotResponse(
            connected=True,
            vehicle_id=detail.summary.vehicle_id,
            session_id=detail.summary.id,
            sensors_recorded=len(detail.sensors),
            dtcs_recorded=len(detail.dtcs),
            timestamp=detail.summary.started_at,
        )

    def list_history_vehicles(self) -> list[HistoryVehicleResponse]:
        return [_history_vehicle_response(vehicle) for vehicle in self._store().list_vehicles()]

    def list_history_sessions(
        self, vehicle_id: int | None = None, limit: int = 20, offset: int = 0
    ) -> list[HistorySessionSummaryResponse]:
        resolved_limit = max(1, min(limit, 100))
        resolved_offset = max(0, offset)
        sessions = self._store().list_sessions(
            vehicle_id=vehicle_id,
            limit=resolved_limit,
            offset=resolved_offset,
        )
        return [_history_session_summary_response(session) for session in sessions]

    def get_history_session(self, session_id: int) -> HistorySessionDetailResponse | None:
        detail = self._store().get_session_detail(session_id)
        if detail is None:
            return None
        return _history_session_detail_response(detail)

    def add_maintenance_event(self, request: MaintenanceEventRequest) -> MaintenanceEventResponse:
        event = self._store().add_maintenance_event(
            request.vehicle_id,
            event_type=request.event_type,
            description=request.description,
            mileage=request.mileage,
            occurred_at=request.occurred_at,
            cost_usd=request.cost_usd,
        )
        return _maintenance_event_response(event)

    def run_predictions(self, request: PredictionRunRequest) -> list[PredictionResponse]:
        history = self._store().get_vehicle_history(request.vehicle_id)
        if history is None:
            return []
        predictions = self._prediction_engine.score(history)
        stored = self._store().save_predictions(request.vehicle_id, predictions)
        return [_prediction_response(prediction) for prediction in stored]

    def get_current_predictions(self, vehicle_id: int) -> list[PredictionResponse]:
        return [
            _prediction_response(prediction)
            for prediction in self._store().latest_predictions(vehicle_id)
        ]

    def export_history(self, vehicle_id: int | None = None) -> HistoryExportResponse:
        return HistoryExportResponse(**self._store().export_anonymized(vehicle_id=vehicle_id))

    def import_history(self, request: HistoryImportRequest) -> HistoryImportResponse:
        result = self._store().import_session_log_rows(request.rows)
        return HistoryImportResponse(
            imported_sessions=result.imported_sessions,
            skipped_rows=result.skipped_rows,
        )

    def _store(self) -> HistoryStore:
        if self._history_store is None:
            self._history_store = HistoryStore()
        return self._history_store


def _sensor_responses(snapshot: dict[str, SensorValue]) -> list[SensorReadingResponse]:
    return [
        SensorReadingResponse(
            name=sensor.name,
            value=sensor.value,
            unit=sensor.unit,
            supported=sensor.supported,
            timestamp=sensor.timestamp,
        )
        for sensor in snapshot.values()
    ]


def _sensor_observations(sensors: list[SensorReadingResponse]) -> list[SensorObservation]:
    return [
        SensorObservation(
            name=sensor.name,
            value=sensor.value,
            unit=sensor.unit,
            timestamp=sensor.timestamp,
        )
        for sensor in sensors
    ]


def _dtc_responses(dtcs: list[DTCCode]) -> list[DTCResponse]:
    return [
        DTCResponse(
            code=dtc.code,
            description=dtc.description,
            status=dtc.status,
            severity=dtc.severity,
            category=dtc.category,
        )
        for dtc in dtcs
    ]


def _mode6_responses(results: list[Mode6TestResult]) -> list[Mode6TestResponse]:
    return [
        Mode6TestResponse(
            monitor=result.monitor,
            monitor_description=result.monitor_description,
            category=result.category,
            test_id=result.test_id,
            test_name=result.test_name,
            description=result.description,
            value=result.value,
            minimum=result.minimum,
            maximum=result.maximum,
            unit=result.unit,
            passed=result.passed,
            status=result.status,
        )
        for result in results
    ]


def _misfire_summary_response(summary: MisfireSummary) -> MisfireSummaryResponse:
    return MisfireSummaryResponse(
        supported=summary.supported,
        status=summary.status,
        summary=summary.summary,
        findings=[
            MisfireFindingResponse(
                source=finding.source,
                severity=finding.severity,
                detail=finding.detail,
                cylinder=finding.cylinder,
                value=finding.value,
                threshold=finding.threshold,
            )
            for finding in summary.findings
        ],
    )


def _misfire_summary_from_response(summary: MisfireSummaryResponse) -> MisfireSummary:
    return MisfireSummary(
        supported=summary.supported,
        status=summary.status,
        summary=summary.summary,
        findings=[
            MisfireFinding(
                source=finding.source,
                severity=finding.severity,
                detail=finding.detail,
                cylinder=finding.cylinder,
                value=finding.value,
                threshold=finding.threshold,
            )
            for finding in summary.findings
        ],
    )


def _dtc_observations(dtcs: list[DTCResponse]) -> list[DTCObservation]:
    now = datetime.now()
    return [
        DTCObservation(
            code=dtc.code,
            description=dtc.description,
            status=dtc.status,
            severity=dtc.severity,
            category=dtc.category,
            timestamp=now,
        )
        for dtc in dtcs
    ]


def _vehicle_identity(request: VehicleProfileRequest | DiagnoseRequest) -> VehicleIdentity:
    return VehicleIdentity(
        year=request.year,
        make=request.make,
        model=request.model,
        mileage=request.mileage,
        vin=request.vin,
    )


def _latest_snapshot_timestamp(snapshot: HealthSnapshotResponse) -> datetime:
    if snapshot.sensors:
        return max(sensor.timestamp for sensor in snapshot.sensors)
    return datetime.now()


def _history_vehicle_response(vehicle: object) -> HistoryVehicleResponse:
    return HistoryVehicleResponse.model_validate(vehicle, from_attributes=True)


def _history_session_summary_response(summary: object) -> HistorySessionSummaryResponse:
    return HistorySessionSummaryResponse.model_validate(summary, from_attributes=True)


def _history_session_detail_response(detail: object) -> HistorySessionDetailResponse:
    return HistorySessionDetailResponse.model_validate(detail, from_attributes=True)


def _maintenance_event_response(event: object) -> MaintenanceEventResponse:
    return MaintenanceEventResponse.model_validate(event, from_attributes=True)


def _prediction_response(prediction: object) -> PredictionResponse:
    return PredictionResponse.model_validate(prediction, from_attributes=True)


def _default_connection() -> OBDConnection:
    timeout = float(os.getenv("OPEN_MECHANIC_API_OBD_TIMEOUT", "3.0"))
    max_retries = int(os.getenv("OPEN_MECHANIC_API_OBD_RETRIES", "1"))
    return OBDConnection(timeout=timeout, max_retries=max_retries)
