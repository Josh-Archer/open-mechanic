# API Contract

Last reviewed: 2026-05-31

The FastAPI backend is a local-first Phase 3 foundation for dashboards, local automations, predictive maintenance, and future self-hosted deployments. It wraps the existing OBD, DTC, vehicle profile, history, prediction, and AI diagnostic modules without requiring web callers to know those internals.

## Run Locally

```bash
pip install -e ".[api]"
uvicorn open_mechanic.api:create_app --factory --reload
```

Hardware calls use `OPEN_MECHANIC_API_OBD_TIMEOUT=3.0` and `OPEN_MECHANIC_API_OBD_RETRIES=1` by default so API smoke tests fail fast when no adapter is connected. Override those values for slower adapters.

## Current Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Service liveness check. |
| `GET` | `/api/vehicle` | Local vehicle profile from `local_data/vehicle_profile.json`. |
| `GET` | `/api/live` | One-shot live sensor snapshot. |
| `GET` | `/api/dtc` | Current fault codes. |
| `GET` | `/api/mode6` | Supported Mode 6 onboard monitor test results. |
| `GET` | `/api/snapshot` | Combined connection, sensor, DTC, Mode 6, and misfire snapshot. |
| `POST` | `/api/snapshot/record` | Record one combined snapshot into local SQLite history. |
| `POST` | `/api/diagnose` | AI diagnosis using the current snapshot and submitted vehicle details. |
| `GET` | `/api/history/vehicles` | Vehicles present in local history. |
| `GET` | `/api/history/sessions` | Recorded diagnostic sessions, optionally filtered by vehicle. |
| `GET` | `/api/history/sessions/{session_id}` | Recorded session details, including sensors and DTCs. |
| `POST` | `/api/history/maintenance-events` | Add a user maintenance/repair event for prediction context. |
| `POST` | `/api/history/import` | Import local JSONL session log rows. |
| `GET` | `/api/history/export` | Export anonymized local history for backup or opt-in sharing. |
| `POST` | `/api/predictions/run` | Run deterministic local predictive risk scoring for a vehicle. |
| `GET` | `/api/predictions/current` | Latest stored predictions for a vehicle. |

## Response Shapes

`GET /api/health`

```json
{
  "status": "ok",
  "service": "open-mechanic"
}
```

`GET /api/vehicle`

```json
{
  "configured": true,
  "year": 2018,
  "make": "Ford",
  "model": "F-150",
  "mileage": 85000
}
```

`GET /api/live` and `GET /api/snapshot`

```json
{
  "connected": true,
  "port": "/dev/cu.usbserial-0",
  "protocol": "CAN",
  "sensors": [
    {
      "name": "RPM",
      "value": "750",
      "unit": "rpm",
      "supported": true,
      "timestamp": "2026-05-22T01:02:03"
    }
  ],
  "dtcs": [],
  "mode6": [
    {
      "monitor": "MONITOR_MISFIRE_CYLINDER_1",
      "monitor_description": "Misfire Cylinder 1 Data",
      "category": "misfire",
      "test_id": 1,
      "test_name": "Misfire counts",
      "description": "Misfire counts",
      "value": "12",
      "minimum": "0",
      "maximum": "5",
      "unit": "count",
      "passed": false,
      "status": "failed"
    }
  ],
  "misfire_summary": {
    "supported": true,
    "status": "possible_misfire",
    "summary": "Mode 6 misfire monitor failures were reported before a confirmed DTC.",
    "findings": [
      {
        "source": "mode6",
        "severity": "warning",
        "detail": "Misfire Cylinder 1 Data failed Misfire counts",
        "cylinder": 1,
        "value": "12",
        "threshold": "0..5"
      }
    ]
  }
}
```

`GET /api/mode6`

```json
[
  {
    "monitor": "MONITOR_CATALYST_B1",
    "monitor_description": "Catalyst Monitor Bank 1",
    "category": "catalyst",
    "test_id": 1,
    "test_name": "Rich to lean sensor threshold voltage",
    "description": "Rich to lean sensor threshold voltage",
    "value": "0.80",
    "minimum": "0.10",
    "maximum": "1.00",
    "unit": "V",
    "passed": true,
    "status": "passed"
  }
]
```

`GET /api/dtc`

```json
[
  {
    "code": "P0420",
    "description": "Catalyst system efficiency below threshold",
    "status": "confirmed",
    "severity": "warning",
    "category": "emissions"
  }
]
```

`POST /api/diagnose`

Request:

```json
{
  "year": 2018,
  "make": "Ford",
  "model": "F-150",
  "mileage": 85000,
  "vin": null,
  "bypass_cache": false
}
```

`POST /api/snapshot/record`

Request:

```json
{
  "year": 2018,
  "make": "Ford",
  "model": "F-150",
  "mileage": 85000,
  "vin": null
}
```

Response:

```json
{
  "connected": true,
  "vehicle_id": 1,
  "session_id": 10,
  "sensors_recorded": 13,
  "dtcs_recorded": 1,
  "timestamp": "2026-05-31T12:00:00"
}
```

`GET /api/history/vehicles`

```json
[
  {
    "id": 1,
    "year": 2018,
    "make": "Ford",
    "model": "F-150",
    "mileage": 85000,
    "vin": null,
    "created_at": "2026-05-31T12:00:00"
  }
]
```

`GET /api/history/sessions?vehicle_id=1&limit=20&offset=0`

```json
[
  {
    "id": 10,
    "vehicle_id": 1,
    "started_at": "2026-05-31T12:00:00",
    "ended_at": "2026-05-31T12:00:01",
    "port_used": "/dev/ttyUSB0",
    "protocol": "CAN",
    "sensor_count": 13,
    "dtc_count": 1
  }
]
```

`POST /api/history/maintenance-events`

```json
{
  "vehicle_id": 1,
  "event_type": "oil_change",
  "description": "Changed oil and filter",
  "mileage": 85000,
  "occurred_at": "2026-05-01T09:00:00",
  "cost_usd": 45
}
```

`POST /api/predictions/run`

Request:

```json
{
  "vehicle_id": 1
}
```

Response:

```json
[
  {
    "id": 4,
    "vehicle_id": 1,
    "generated_at": "2026-05-31T12:05:00",
    "issue": "Recurring DTC P0420",
    "risk_score": 0.7,
    "confidence": 0.6,
    "severity": "warning",
    "explanation": "P0420 appeared in 2 recorded session(s), including 2 confirmed observation(s).",
    "source_signals": ["P0420 session count=2", "P0420 confirmed count=2"]
  }
]
```

`GET /api/history/export`

Returns a schema-versioned JSON object containing anonymized `vehicle_key` values, recorded sessions, maintenance events, and prediction records. Raw VINs are not included. `OPEN_MECHANIC_EXPORT_SALT` controls the local hash salt.

Response:

```json
{
  "severity": "warning",
  "summary": "Catalyst efficiency below threshold",
  "likely_causes": ["Aged catalytic converter"],
  "repair_steps": ["Inspect O2 sensor waveforms"],
  "estimated_cost_usd": { "low": 100, "high": 500 },
  "diy_feasible": false,
  "diy_difficulty": "moderate",
  "urgency": "soon",
  "disclaimer": "This diagnosis is informational only and does not constitute professional mechanical advice. Consult a qualified mechanic before making safety-critical repairs.",
  "dtc_codes": ["P0420"],
  "vehicle_str": "2018 Ford F-150 (85,000 miles)",
  "cached": false,
  "timestamp": "2026-05-22T01:02:03"
}
```

## Dashboard Contract Notes

- Treat `/api/live` as a polling endpoint for now. A streaming endpoint can be added later without changing the snapshot shape.
- Show `connected=false` as a normal no-adapter state, not as a dashboard crash.
- Read `severity` and `category` as display hints. Do not use either value to clear codes or recommend safety-critical action without the disclaimer.
- Mode 6 is best-effort and vehicle-dependent. Missing Mode 6 data means unsupported/no data, not a failed monitor.
- `misfire_summary` is a triage aid built from Mode 6 misfire monitors, P030x/P031x DTCs, and supporting live sensor clues. It is not a substitute for mechanical confirmation.
- The current API is local-first. Auth, hosted multi-user access, and community upload are not implemented.
- Predictive risk scores are deterministic and explainable; they do not require an AI API key.
- Anonymized export is for backup or explicit opt-in sharing. There is no automatic upload.

## Planned Endpoints

- `GET /api/guides/{dtc}`: repair guide content once guide sources and licensing are finalized.
- `GET /api/live/stream`: optional SSE stream for dashboards that need live updates.
