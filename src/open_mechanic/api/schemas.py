from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class VehicleProfileResponse(BaseModel):
    configured: bool
    year: int | None = None
    make: str | None = None
    model: str | None = None
    mileage: int | None = None


class SensorReadingResponse(BaseModel):
    name: str
    value: str
    unit: str | None
    supported: bool
    timestamp: datetime


class DTCResponse(BaseModel):
    code: str
    description: str
    status: str
    severity: str
    category: str


class Mode6TestResponse(BaseModel):
    monitor: str
    monitor_description: str
    category: str
    test_id: int | None
    test_name: str
    description: str
    value: str
    minimum: str
    maximum: str
    unit: str | None
    passed: bool | None
    status: str


class MisfireFindingResponse(BaseModel):
    source: str
    severity: str
    detail: str
    cylinder: int | None = None
    value: str | None = None
    threshold: str | None = None


class MisfireSummaryResponse(BaseModel):
    supported: bool
    status: str
    summary: str
    findings: list[MisfireFindingResponse] = Field(default_factory=list)


class HealthSnapshotResponse(BaseModel):
    connected: bool
    port: str | None
    protocol: str | None
    sensors: list[SensorReadingResponse]
    dtcs: list[DTCResponse]
    mode6: list[Mode6TestResponse] = Field(default_factory=list)
    misfire_summary: MisfireSummaryResponse | None = None


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    year: int
    make: str
    model: str
    mileage: int
    vin: str | None = None
    bypass_cache: bool = False


class DiagnosisResponse(BaseModel):
    severity: str
    summary: str
    likely_causes: list[str]
    repair_steps: list[str]
    estimated_cost_usd: dict[str, int]
    diy_feasible: bool
    diy_difficulty: str
    urgency: str
    disclaimer: str
    dtc_codes: list[str]
    vehicle_str: str
    cached: bool
    timestamp: datetime


class VehicleProfileRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    year: int
    make: str
    model: str
    mileage: int
    vin: str | None = None


class RecordSnapshotResponse(BaseModel):
    connected: bool
    vehicle_id: int | None
    session_id: int | None
    sensors_recorded: int
    dtcs_recorded: int
    timestamp: datetime


class HistoryVehicleResponse(BaseModel):
    id: int
    year: int
    make: str
    model: str
    mileage: int
    vin: str | None
    created_at: datetime


class HistorySessionSummaryResponse(BaseModel):
    id: int
    vehicle_id: int
    started_at: datetime
    ended_at: datetime | None
    port_used: str
    protocol: str | None
    sensor_count: int
    dtc_count: int


class HistorySensorReadingResponse(BaseModel):
    id: int
    session_id: int
    name: str
    value: str
    unit: str | None
    timestamp: datetime


class HistoryDTCRecordResponse(BaseModel):
    id: int
    session_id: int
    code: str
    description: str | None
    status: str
    severity: str | None
    category: str | None
    timestamp: datetime


class HistorySessionDetailResponse(BaseModel):
    summary: HistorySessionSummaryResponse
    sensors: list[HistorySensorReadingResponse]
    dtcs: list[HistoryDTCRecordResponse]


class MaintenanceEventRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    vehicle_id: int
    event_type: str
    description: str
    mileage: int | None = None
    occurred_at: datetime | None = None
    cost_usd: int | None = None


class MaintenanceEventResponse(BaseModel):
    id: int
    vehicle_id: int
    occurred_at: datetime
    mileage: int | None
    event_type: str
    description: str
    cost_usd: int | None


class PredictionRunRequest(BaseModel):
    vehicle_id: int


class PredictionResponse(BaseModel):
    id: int
    vehicle_id: int
    generated_at: datetime
    issue: str
    risk_score: float
    confidence: float
    severity: str
    explanation: str
    source_signals: list[str]


class HistoryImportRequest(BaseModel):
    rows: list[dict[str, Any]]


class HistoryImportResponse(BaseModel):
    imported_sessions: int
    skipped_rows: int


class HistoryExportResponse(BaseModel):
    schema_version: int
    generated_at: str
    vehicles: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    maintenance_events: list[dict[str, Any]]
    predictions: list[dict[str, Any]]
