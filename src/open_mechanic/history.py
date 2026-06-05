from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from open_mechanic.db.models import (
    DiagnosticSession,
    DTCRecord,
    MaintenanceEvent,
    PredictionResult,
    SensorReading,
    VehicleProfile,
    get_session,
    init_db,
)


@dataclass(frozen=True)
class VehicleIdentity:
    year: int
    make: str
    model: str
    mileage: int
    vin: str | None = None


@dataclass(frozen=True)
class SensorObservation:
    name: str
    value: str
    unit: str | None
    timestamp: datetime


@dataclass(frozen=True)
class DTCObservation:
    code: str
    description: str | None
    status: str
    severity: str | None
    category: str | None
    timestamp: datetime


@dataclass(frozen=True)
class StoredVehicle:
    id: int
    year: int
    make: str
    model: str
    mileage: int
    vin: str | None
    created_at: datetime


@dataclass(frozen=True)
class StoredSensorReading:
    id: int
    session_id: int
    name: str
    value: str
    unit: str | None
    timestamp: datetime


@dataclass(frozen=True)
class StoredDTCRecord:
    id: int
    session_id: int
    code: str
    description: str | None
    status: str
    severity: str | None
    category: str | None
    timestamp: datetime


@dataclass(frozen=True)
class StoredSessionSummary:
    id: int
    vehicle_id: int
    started_at: datetime
    ended_at: datetime | None
    port_used: str
    protocol: str | None
    sensor_count: int
    dtc_count: int


@dataclass(frozen=True)
class StoredSessionDetail:
    summary: StoredSessionSummary
    sensors: list[StoredSensorReading]
    dtcs: list[StoredDTCRecord]


@dataclass(frozen=True)
class StoredMaintenanceEvent:
    id: int
    vehicle_id: int
    occurred_at: datetime
    mileage: int | None
    event_type: str
    description: str
    cost_usd: int | None


@dataclass(frozen=True)
class PredictionInput:
    issue: str
    risk_score: float
    confidence: float
    severity: str
    explanation: str
    source_signals: list[str]


@dataclass(frozen=True)
class StoredPrediction:
    id: int
    vehicle_id: int
    generated_at: datetime
    issue: str
    risk_score: float
    confidence: float
    severity: str
    explanation: str
    source_signals: list[str]


@dataclass(frozen=True)
class StoredVehicleHistory:
    vehicle: StoredVehicle
    sessions: list[StoredSessionDetail]
    maintenance_events: list[StoredMaintenanceEvent]


@dataclass(frozen=True)
class HistoryImportResult:
    imported_sessions: int
    skipped_rows: int


class HistoryStore:
    def __init__(self, db_path: str | None = None, engine: Engine | None = None) -> None:
        self._engine = engine or init_db(db_path)

    def upsert_vehicle(self, vehicle: VehicleIdentity) -> StoredVehicle:
        with get_session(self._engine) as session:
            model = _upsert_vehicle(session, vehicle)
            session.flush()
            return _stored_vehicle(model)

    def list_vehicles(self) -> list[StoredVehicle]:
        with get_session(self._engine) as session:
            vehicles = session.scalars(select(VehicleProfile).order_by(VehicleProfile.id)).all()
            return [_stored_vehicle(vehicle) for vehicle in vehicles]

    def record_snapshot(
        self,
        vehicle: VehicleIdentity,
        sensors: list[SensorObservation],
        dtcs: list[DTCObservation],
        *,
        port_used: str | None,
        protocol: str | None,
    ) -> StoredSessionDetail:
        with get_session(self._engine) as session:
            vehicle_model = _upsert_vehicle(session, vehicle)
            session.flush()

            session_model = DiagnosticSession(
                vehicle_id=vehicle_model.id,
                started_at=datetime.utcnow(),
                ended_at=datetime.utcnow(),
                port_used=port_used or "unknown",
                protocol=protocol,
            )
            session.add(session_model)
            session.flush()

            for sensor in sensors:
                session.add(
                    SensorReading(
                        session_id=session_model.id,
                        timestamp=sensor.timestamp,
                        sensor_name=sensor.name,
                        value=sensor.value,
                        unit=sensor.unit,
                    )
                )

            for dtc in dtcs:
                session.add(
                    DTCRecord(
                        session_id=session_model.id,
                        timestamp=dtc.timestamp,
                        code=dtc.code,
                        description=dtc.description,
                        status=dtc.status,
                        severity=dtc.severity,
                        category=dtc.category,
                    )
                )

            session.flush()
            return _session_detail(session, session_model)

    def list_sessions(
        self,
        *,
        vehicle_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[StoredSessionSummary]:
        with get_session(self._engine) as session:
            stmt = select(DiagnosticSession)
            if vehicle_id is not None:
                stmt = stmt.where(DiagnosticSession.vehicle_id == vehicle_id)
            stmt = stmt.order_by(DiagnosticSession.started_at.desc()).offset(offset).limit(limit)
            sessions = session.scalars(stmt).all()
            return [_session_summary(session, item) for item in sessions]

    def get_session_detail(self, session_id: int) -> StoredSessionDetail | None:
        with get_session(self._engine) as session:
            session_model = session.get(DiagnosticSession, session_id)
            if session_model is None:
                return None
            return _session_detail(session, session_model)

    def add_maintenance_event(
        self,
        vehicle_id: int,
        *,
        event_type: str,
        description: str,
        mileage: int | None,
        occurred_at: datetime | None,
        cost_usd: int | None,
    ) -> StoredMaintenanceEvent:
        with get_session(self._engine) as session:
            event = MaintenanceEvent(
                vehicle_id=vehicle_id,
                occurred_at=occurred_at or datetime.utcnow(),
                mileage=mileage,
                event_type=event_type,
                description=description,
                cost_usd=cost_usd,
            )
            session.add(event)
            session.flush()
            return _stored_maintenance_event(event)

    def get_vehicle_history(self, vehicle_id: int) -> StoredVehicleHistory | None:
        with get_session(self._engine) as session:
            vehicle = session.get(VehicleProfile, vehicle_id)
            if vehicle is None:
                return None
            sessions = session.scalars(
                select(DiagnosticSession)
                .where(DiagnosticSession.vehicle_id == vehicle_id)
                .order_by(DiagnosticSession.started_at)
            ).all()
            maintenance_events = session.scalars(
                select(MaintenanceEvent)
                .where(MaintenanceEvent.vehicle_id == vehicle_id)
                .order_by(MaintenanceEvent.occurred_at)
            ).all()
            return StoredVehicleHistory(
                vehicle=_stored_vehicle(vehicle),
                sessions=[_session_detail(session, item) for item in sessions],
                maintenance_events=[
                    _stored_maintenance_event(event) for event in maintenance_events
                ],
            )

    def save_predictions(
        self, vehicle_id: int, predictions: list[PredictionInput]
    ) -> list[StoredPrediction]:
        with get_session(self._engine) as session:
            stored: list[StoredPrediction] = []
            generated_at = datetime.utcnow()
            for prediction in predictions:
                model = PredictionResult(
                    vehicle_id=vehicle_id,
                    generated_at=generated_at,
                    issue=prediction.issue,
                    risk_score=prediction.risk_score,
                    confidence=prediction.confidence,
                    severity=prediction.severity,
                    explanation=prediction.explanation,
                    source_signals_json=json.dumps(prediction.source_signals),
                )
                session.add(model)
                session.flush()
                stored.append(_stored_prediction(model))
            return stored

    def latest_predictions(self, vehicle_id: int) -> list[StoredPrediction]:
        with get_session(self._engine) as session:
            rows = session.scalars(
                select(PredictionResult)
                .where(PredictionResult.vehicle_id == vehicle_id)
                .order_by(PredictionResult.generated_at.desc(), PredictionResult.id.desc())
            ).all()

            latest_by_issue: dict[str, StoredPrediction] = {}
            for row in rows:
                if row.issue not in latest_by_issue:
                    latest_by_issue[row.issue] = _stored_prediction(row)
            return list(latest_by_issue.values())

    def export_anonymized(self, vehicle_id: int | None = None) -> dict[str, Any]:
        salt = os.getenv("OPEN_MECHANIC_EXPORT_SALT", "open-mechanic-local-export")
        with get_session(self._engine) as session:
            vehicle_stmt = select(VehicleProfile).order_by(VehicleProfile.id)
            if vehicle_id is not None:
                vehicle_stmt = vehicle_stmt.where(VehicleProfile.id == vehicle_id)
            vehicles = session.scalars(vehicle_stmt).all()
            vehicle_ids = {vehicle.id for vehicle in vehicles}

            session_stmt = select(DiagnosticSession).order_by(DiagnosticSession.started_at)
            if vehicle_id is not None:
                session_stmt = session_stmt.where(DiagnosticSession.vehicle_id == vehicle_id)
            sessions = session.scalars(session_stmt).all()

            events = session.scalars(
                select(MaintenanceEvent).order_by(MaintenanceEvent.occurred_at)
            ).all()
            predictions = session.scalars(
                select(PredictionResult).order_by(PredictionResult.generated_at)
            ).all()

            return {
                "schema_version": 1,
                "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
                "vehicles": [_export_vehicle(vehicle, salt) for vehicle in vehicles],
                "sessions": [
                    _export_session(session, item, salt)
                    for item in sessions
                    if item.vehicle_id in vehicle_ids
                ],
                "maintenance_events": [
                    _export_maintenance_event(event, salt)
                    for event in events
                    if event.vehicle_id in vehicle_ids
                ],
                "predictions": [
                    _export_prediction(prediction, salt)
                    for prediction in predictions
                    if prediction.vehicle_id in vehicle_ids
                ],
            }

    def import_session_log_rows(self, rows: list[dict[str, Any]]) -> HistoryImportResult:
        vehicle = _vehicle_from_log_rows(rows)
        if vehicle is None:
            return HistoryImportResult(imported_sessions=0, skipped_rows=len(rows))

        imported = 0
        skipped = 0
        for row in rows:
            event = row.get("event")
            timestamp = _parse_datetime(row.get("timestamp")) or datetime.utcnow()
            payload = row.get("payload")
            if not isinstance(payload, dict):
                skipped += 1
                continue

            if event == "sensor_snapshot":
                sensors = _sensors_from_snapshot_payload(payload, timestamp)
                self.record_snapshot(
                    vehicle, sensors, [], port_used="imported-jsonl", protocol=None
                )
                imported += 1
            elif event == "health_snapshot":
                sensors = _sensors_from_health_payload(payload, timestamp)
                dtcs = _dtcs_from_payload(payload.get("dtcs"), timestamp)
                self.record_snapshot(
                    vehicle, sensors, dtcs, port_used="imported-jsonl", protocol=None
                )
                imported += 1
            elif event == "dtcs":
                dtcs = _dtcs_from_payload(payload.get("codes"), timestamp)
                self.record_snapshot(vehicle, [], dtcs, port_used="imported-jsonl", protocol=None)
                imported += 1
            else:
                skipped += 1

        return HistoryImportResult(imported_sessions=imported, skipped_rows=skipped)


def _upsert_vehicle(session: Session, vehicle: VehicleIdentity) -> VehicleProfile:
    stmt = select(VehicleProfile).where(
        VehicleProfile.year == vehicle.year,
        VehicleProfile.make == vehicle.make,
        VehicleProfile.model == vehicle.model,
    )
    if vehicle.vin is None:
        stmt = stmt.where(VehicleProfile.vin.is_(None))
    else:
        stmt = stmt.where(VehicleProfile.vin == vehicle.vin)
    existing = session.scalar(stmt)
    if existing is not None:
        existing.mileage = max(existing.mileage, vehicle.mileage)
        return existing

    model = VehicleProfile(
        year=vehicle.year,
        make=vehicle.make,
        model=vehicle.model,
        mileage=vehicle.mileage,
        vin=vehicle.vin,
    )
    session.add(model)
    return model


def _stored_vehicle(vehicle: VehicleProfile) -> StoredVehicle:
    return StoredVehicle(
        id=vehicle.id,
        year=vehicle.year,
        make=vehicle.make,
        model=vehicle.model,
        mileage=vehicle.mileage,
        vin=vehicle.vin,
        created_at=vehicle.created_at,
    )


def _stored_sensor(sensor: SensorReading) -> StoredSensorReading:
    return StoredSensorReading(
        id=sensor.id,
        session_id=sensor.session_id,
        name=sensor.sensor_name,
        value=sensor.value,
        unit=sensor.unit,
        timestamp=sensor.timestamp,
    )


def _stored_dtc(dtc: DTCRecord) -> StoredDTCRecord:
    return StoredDTCRecord(
        id=dtc.id,
        session_id=dtc.session_id,
        code=dtc.code,
        description=dtc.description,
        status=dtc.status,
        severity=dtc.severity,
        category=dtc.category,
        timestamp=dtc.timestamp,
    )


def _session_summary(session: Session, session_model: DiagnosticSession) -> StoredSessionSummary:
    sensor_count = len(
        session.scalars(
            select(SensorReading).where(SensorReading.session_id == session_model.id)
        ).all()
    )
    dtc_count = len(
        session.scalars(select(DTCRecord).where(DTCRecord.session_id == session_model.id)).all()
    )
    return StoredSessionSummary(
        id=session_model.id,
        vehicle_id=session_model.vehicle_id,
        started_at=session_model.started_at,
        ended_at=session_model.ended_at,
        port_used=session_model.port_used,
        protocol=session_model.protocol,
        sensor_count=sensor_count,
        dtc_count=dtc_count,
    )


def _session_detail(session: Session, session_model: DiagnosticSession) -> StoredSessionDetail:
    sensors = session.scalars(
        select(SensorReading)
        .where(SensorReading.session_id == session_model.id)
        .order_by(SensorReading.timestamp, SensorReading.id)
    ).all()
    dtcs = session.scalars(
        select(DTCRecord)
        .where(DTCRecord.session_id == session_model.id)
        .order_by(DTCRecord.timestamp, DTCRecord.id)
    ).all()
    return StoredSessionDetail(
        summary=_session_summary(session, session_model),
        sensors=[_stored_sensor(sensor) for sensor in sensors],
        dtcs=[_stored_dtc(dtc) for dtc in dtcs],
    )


def _stored_maintenance_event(event: MaintenanceEvent) -> StoredMaintenanceEvent:
    return StoredMaintenanceEvent(
        id=event.id,
        vehicle_id=event.vehicle_id,
        occurred_at=event.occurred_at,
        mileage=event.mileage,
        event_type=event.event_type,
        description=event.description,
        cost_usd=event.cost_usd,
    )


def _stored_prediction(prediction: PredictionResult) -> StoredPrediction:
    source_signals = json.loads(prediction.source_signals_json)
    if not isinstance(source_signals, list):
        source_signals = []
    return StoredPrediction(
        id=prediction.id,
        vehicle_id=prediction.vehicle_id,
        generated_at=prediction.generated_at,
        issue=prediction.issue,
        risk_score=prediction.risk_score,
        confidence=prediction.confidence,
        severity=prediction.severity,
        explanation=prediction.explanation,
        source_signals=[str(signal) for signal in source_signals],
    )


def _vehicle_key(vehicle_id: int, salt: str) -> str:
    raw = f"{salt}|vehicle-id:{vehicle_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _export_vehicle(vehicle: VehicleProfile, salt: str) -> dict[str, Any]:
    return {
        "vehicle_key": _vehicle_key(vehicle.id, salt),
        "year": vehicle.year,
        "make": vehicle.make,
        "model": vehicle.model,
        "mileage": vehicle.mileage,
        "created_at": vehicle.created_at.isoformat(timespec="seconds"),
    }


def _export_session(
    session: Session, session_model: DiagnosticSession, salt: str
) -> dict[str, Any]:
    detail = _session_detail(session, session_model)
    return {
        "vehicle_key": _vehicle_key(session_model.vehicle_id, salt),
        "started_at": detail.summary.started_at.isoformat(timespec="seconds"),
        "ended_at": detail.summary.ended_at.isoformat(timespec="seconds")
        if detail.summary.ended_at
        else None,
        "port_used": detail.summary.port_used,
        "protocol": detail.summary.protocol,
        "sensors": [
            {
                "name": sensor.name,
                "value": sensor.value,
                "unit": sensor.unit,
                "timestamp": sensor.timestamp.isoformat(timespec="seconds"),
            }
            for sensor in detail.sensors
        ],
        "dtcs": [
            {
                "code": dtc.code,
                "description": dtc.description,
                "status": dtc.status,
                "severity": dtc.severity,
                "category": dtc.category,
                "timestamp": dtc.timestamp.isoformat(timespec="seconds"),
            }
            for dtc in detail.dtcs
        ],
    }


def _export_maintenance_event(event: MaintenanceEvent, salt: str) -> dict[str, Any]:
    return {
        "vehicle_key": _vehicle_key(event.vehicle_id, salt),
        "occurred_at": event.occurred_at.isoformat(timespec="seconds"),
        "mileage": event.mileage,
        "event_type": event.event_type,
        "description": event.description,
        "cost_usd": event.cost_usd,
    }


def _export_prediction(prediction: PredictionResult, salt: str) -> dict[str, Any]:
    return {
        "vehicle_key": _vehicle_key(prediction.vehicle_id, salt),
        "generated_at": prediction.generated_at.isoformat(timespec="seconds"),
        "issue": prediction.issue,
        "risk_score": prediction.risk_score,
        "confidence": prediction.confidence,
        "severity": prediction.severity,
        "explanation": prediction.explanation,
        "source_signals": json.loads(prediction.source_signals_json),
    }


def _vehicle_from_log_rows(rows: list[dict[str, Any]]) -> VehicleIdentity | None:
    for row in rows:
        if row.get("event") != "session_started":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        vehicle = payload.get("vehicle")
        if not isinstance(vehicle, dict):
            continue
        year = vehicle.get("year")
        make = vehicle.get("make")
        model = vehicle.get("model")
        mileage = vehicle.get("mileage")
        if isinstance(year, int) and isinstance(make, str) and isinstance(model, str):
            return VehicleIdentity(
                year=year,
                make=make,
                model=model,
                mileage=mileage if isinstance(mileage, int) else 0,
            )
    return None


def _sensors_from_snapshot_payload(
    payload: dict[str, Any], timestamp: datetime
) -> list[SensorObservation]:
    sensors: list[SensorObservation] = []
    for name, raw_sensor in payload.items():
        if not isinstance(name, str) or not isinstance(raw_sensor, dict):
            continue
        value = raw_sensor.get("value")
        unit = raw_sensor.get("unit")
        sensors.append(
            SensorObservation(
                name=name,
                value=str(value),
                unit=unit if isinstance(unit, str) else None,
                timestamp=timestamp,
            )
        )
    return sensors


def _sensors_from_health_payload(
    payload: dict[str, Any], timestamp: datetime
) -> list[SensorObservation]:
    raw_sensors = payload.get("sensors")
    if not isinstance(raw_sensors, list):
        return []
    sensors: list[SensorObservation] = []
    for raw_sensor in raw_sensors:
        if not isinstance(raw_sensor, dict):
            continue
        name = raw_sensor.get("name") or raw_sensor.get("label")
        value = raw_sensor.get("value")
        unit = raw_sensor.get("unit")
        if isinstance(name, str):
            sensors.append(
                SensorObservation(
                    name=name,
                    value=str(value),
                    unit=unit if isinstance(unit, str) else None,
                    timestamp=timestamp,
                )
            )
    return sensors


def _dtcs_from_payload(raw_dtcs: object, timestamp: datetime) -> list[DTCObservation]:
    if not isinstance(raw_dtcs, list):
        return []
    dtcs: list[DTCObservation] = []
    for raw_dtc in raw_dtcs:
        if not isinstance(raw_dtc, dict):
            continue
        code = raw_dtc.get("code")
        if not isinstance(code, str):
            continue
        dtcs.append(
            DTCObservation(
                code=code,
                description=_optional_str(raw_dtc.get("description")),
                status=_optional_str(raw_dtc.get("status")) or "unknown",
                severity=_optional_str(raw_dtc.get("severity")),
                category=_optional_str(raw_dtc.get("category")),
                timestamp=timestamp,
            )
        )
    return dtcs


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
