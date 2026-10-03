"""PyroLens API: ingestion, intelligence and decision-support endpoints."""
import asyncio
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
from pathlib import Path
from uuid import uuid4

try:
    import jwt
except ImportError:  # Allows the demo API to start before optional auth extras are installed.
    jwt = None
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from .config import get_settings
from .database import init_db
from .storage import object_storage
from .validation import validate_events
from .alerts import send_alert
from .schemas import (
    ClassificationResponse, HealthResponse, ImpactResponse, IngestionRequest,
    IngestionResponse, JobResponse, RiskResponse, ThermalEvent, AlertRequest, AlertResponse,
    AlertRecord, AlertActionRequest, AlertActionResponse, DashboardAnalyticsResponse,
    KPIs, HotspotRegion, UserResponse, UserCreate, UserUpdate, LoginRequest, TokenResponse, UserProfile,
)
from .security import has_role_permission, create_access_token, verify_password, ROLE_HIERARCHY
from .reports import generate_incidents_pdf, generate_incident_dossier_pdf
from .ml import get_model_metadata

# Initialize database tables BEFORE importing any modules that use the database.
# ``init_db`` only creates missing tables, so this is safe on every restart.
init_db()

# Now import modules that depend on database tables
from .processing import assess_impact, assess_risk, classify
from .providers import FirmsNotConfiguredError, firms_provider, osm_provider, weather_provider, startup_tasks
from .repository import repository
from .workers import ingest_firms

settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer(auto_error=False)
_jobs: dict[str, JobResponse] = {}


class TokenRequest(BaseModel):
    username: str
    role: str = "analyst"


def require_role(*allowed_roles: str):
    async def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
        if settings.public_read_api and credentials is None and "viewer" in allowed_roles:
            return {"sub": "public-dashboard", "role": "viewer"}
        if settings.environment == "development" and credentials is None:
            if any(has_role_permission("analyst", r) for r in allowed_roles):
                return {"sub": "demo-analyst", "role": "analyst"}
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required (admin required)")
        if credentials is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        if jwt is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="PyJWT is required for authenticated requests")
        try:
            if settings.oidc_jwks_url:
                signing_key = jwt.PyJWKClient(settings.oidc_jwks_url).get_signing_key_from_jwt(credentials.credentials)
                claims = jwt.decode(
                    credentials.credentials,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=settings.oidc_audience,
                    issuer=settings.oidc_issuer,
                    options={"verify_aud": bool(settings.oidc_audience), "verify_iss": bool(settings.oidc_issuer)},
                )
            else:
                claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
        roles = claims.get("roles") or [claims.get("role")]
        if not any(has_role_permission(role, *allowed_roles) for role in roles if role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return claims
    return dependency


async def _refresh_firms_after_startup() -> None:
    """Populate a lightweight deployment without exposing a public write endpoint."""
    try:
        # Fetch last 7 days of data on startup
        accepted = await startup_tasks.fetch_recent_firms(days=7)
        print(f"Startup FIRMS fetch completed: {accepted} records accepted")
    except Exception as exc:
        # A provider outage must not prevent the API from starting. The status
        # endpoint reports this failed run without exposing provider details.
        repository.record_ingestion("firms", 0, 0, status="failed", error=type(exc).__name__)
        print(f"Startup FIRMS fetch failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    # Fetch last 7 days of FIRMS data on startup for the fire intelligence map
    import logging
    logger = logging.getLogger(__name__)
    if settings.firms_api_key:
        logger.info("Starting background FIRMS fetch for last 7 days...")
        print("Starting background FIRMS fetch for last 7 days...", flush=True)
        # Run in background to avoid blocking server startup
        import asyncio
        asyncio.create_task(_refresh_firms_after_startup())
    else:
        print("FIRMS_API_KEY not configured, skipping startup fetch", flush=True)

    # Auto-migrate ingestion_runs table if needed (dialect-agnostic for SQLite and PostgreSQL)
    try:
        from .database import engine, init_db
        init_db()
        from sqlalchemy import inspect, text
        with engine.connect() as conn:
            inspector = inspect(conn)
            if "ingestion_runs" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("ingestion_runs")}
                is_pg = engine.dialect.name == "postgresql"
                col_type = "TIMESTAMP WITHOUT TIME ZONE" if is_pg else "TIMESTAMP"
                
                if "records_accepted" not in columns:
                    conn.execute(text("ALTER TABLE ingestion_runs ADD COLUMN records_accepted INTEGER DEFAULT 0"))
                if "records_rejected" not in columns:
                    conn.execute(text("ALTER TABLE ingestion_runs ADD COLUMN records_rejected INTEGER DEFAULT 0"))
                if "completed_at" not in columns:
                    conn.execute(text(f"ALTER TABLE ingestion_runs ADD COLUMN completed_at {col_type}"))
                if "finished_at" not in columns:
                    conn.execute(text(f"ALTER TABLE ingestion_runs ADD COLUMN finished_at {col_type}"))
                conn.commit()
                print("Migration complete", flush=True)
    except Exception as e:
        logger.warning(f"Migration warning: {e}")



@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.environment, model_version=settings.model_version)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve a simple SVG favicon."""
    from fastapi.responses import Response
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="45" fill="#dc2626"/>
  <path d="M50 15 L50 85 M35 50 L65 50" stroke="white" stroke-width="8" stroke-linecap="round"/>
</svg>'''
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/v1/models", tags=["system"])
@app.get("/api/v1/models", tags=["system"])
@app.get("/api/v1/ml/metadata", tags=["system"])
async def list_models() -> dict:
    """List available ML models and runtime inference capability."""
    meta = get_model_metadata()
    return {
        "models": [meta],
        "default": meta["version"],
        "runtime": meta["status"],
        **meta,
    }



@app.post("/api/v1/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(credentials: LoginRequest) -> TokenResponse:
    username = credentials.username.strip()
    user = repository.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")



    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is deactivated")

    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    repository.record_login(user.username)
    token, expires = create_access_token(user.username, role=user.role)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires,
        user=repository._user_to_schema(user),
    )


@app.post("/api/v1/auth/token", tags=["auth"])
async def create_token(request: TokenRequest) -> dict:
    if settings.environment != "development":
        raise HTTPException(status_code=501, detail="Production tokens must be issued by the configured identity provider")
    if jwt is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="PyJWT is not installed")
    if request.role not in {"viewer", "analyst", "admin"}:
        raise HTTPException(status_code=422, detail="role must be viewer, analyst, or admin")
    token, expires = create_access_token(request.username, role=request.role)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires}


@app.get("/api/v1/auth/me", response_model=UserProfile, tags=["auth"])
async def get_current_user_profile(user_claims: dict = Depends(require_role("viewer", "analyst", "admin"))) -> UserProfile:
    username = user_claims.get("sub", "unknown")
    role = user_claims.get("role", "viewer")
    user = repository.get_user_by_username(username)
    return UserProfile(
        sub=username,
        username=username,
        role=role,
        permissions=list(ROLE_HIERARCHY.get(role.lower(), {role.lower()})),
        email=user.email if user else f"{username}@pyrolens.org",
        full_name=user.full_name if user else username.title(),
    )



@app.get("/api/v1/events", response_model=list[ThermalEvent], tags=["events"])
async def list_events(
    min_risk: float = Query(0, ge=0, le=100),
    event_type: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(600, ge=1, le=1000),
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> list[ThermalEvent]:
    # The original FE-* records are development fixtures. They are never sent
    # to the Africa operational UI, where an empty feed is more truthful.
    events = [event for event in repository.list() if not event.id.startswith("FE-") and event.risk_score >= min_risk]
    filtered_events = [
        event for event in events
        if (not event_type or event.event_type.lower() == event_type.lower())
        and (not status_filter or event.status.lower() == status_filter.lower())
    ]
    # A continent-scale FIRMS query can produce thousands of full records.
    # Keep dashboard reads bounded and send the strongest observations first.
    sorted_events = sorted(
        filtered_events,
        key=lambda event: (event.risk_score, event.confidence, event.frp_mw),
        reverse=True,
    )[:limit]
    
    return sorted_events


@app.get("/api/v1/events/{event_id}", response_model=ThermalEvent, tags=["events"])
async def get_event(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> ThermalEvent:
    event = repository.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Thermal event not found")
    return event


@app.post("/api/v1/ingestions", response_model=IngestionResponse, status_code=202, tags=["ingestion"])
async def ingest(request: IngestionRequest, _: dict = Depends(require_role("analyst", "admin"))) -> IngestionResponse:
    job_id = str(uuid4())
    job = JobResponse(job_id=job_id, status="queued", task=f"ingest:{request.source}", created_at=datetime.now(timezone.utc))
    _jobs[job_id] = job
    if request.records is not None:
        events, rejected = validate_events(request.records)
        accepted = repository.save_many(events)
        repository.record_ingestion(request.source, len(request.records), accepted)
        _jobs[job_id] = job.model_copy(update={"status": "completed"})
        return IngestionResponse(
            job_id=job_id, source=request.source, accepted_records=accepted,
            rejected_records=rejected, status="completed",
            validation={"received": len(request.records), "valid": len(events), "stored": accepted},
        )

    if request.source == "firms":
        if not settings.firms_api_key:
            raise HTTPException(status_code=503, detail="FIRMS_API_KEY is not configured. Add a NASA FIRMS MAP key to backend/.env.")
        if settings.use_celery:
            ingest_firms.apply_async(task_id=job_id)
            return IngestionResponse(job_id=job_id, source=request.source, accepted_records=0, rejected_records=0, status="queued")
        try:
            events = await firms_provider.fetch(request.model_dump())
            accepted = repository.save_many(events)
            repository.record_ingestion(request.source, len(events), accepted)
            _jobs[job_id] = job.model_copy(update={"status": "completed"})
            return IngestionResponse(job_id=job_id, source=request.source, accepted_records=accepted, rejected_records=0, status="completed")
        except FirmsNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            repository.record_ingestion(request.source, 0, 0, status="failed", error=str(exc))
            _jobs[job_id] = job.model_copy(update={"status": "failed"})
            raise HTTPException(status_code=502, detail="NASA FIRMS ingestion failed") from exc

    events = repository.list()
    _jobs[job_id] = job.model_copy(update={"status": "completed"})
    return IngestionResponse(job_id=job_id, source=request.source, accepted_records=0, rejected_records=0, status="completed", validation={"available_demo_events": len(events)})


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, tags=["jobs"])
async def get_job(job_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> JobResponse:
    if job := _jobs.get(job_id):
        return job
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/api/v1/geo/events/nearby", response_model=list[ThermalEvent], tags=["geospatial"])
async def nearby_events(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius_km: float = Query(3, gt=0, le=100),
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> list[ThermalEvent]:
    return repository.nearby(lat, lng, radius_km)


@app.post("/api/v1/assets", status_code=201, tags=["storage"])
async def upload_asset(
    file: UploadFile = File(...),
    _: dict = Depends(require_role("analyst", "admin")),
) -> dict[str, str]:
    """Store source imagery, rasters, generated maps, or model artifacts in MinIO."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Maximum upload size is 100 MB")
    try:
        return object_storage.upload(file.filename or "asset.bin", content, file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc


@app.get("/api/v1/events/{event_id}/classification", response_model=ClassificationResponse, tags=["intelligence"])
async def event_classification(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> ClassificationResponse:
    return await classify(await get_event(event_id))


@app.get("/api/v1/events/{event_id}/impact", response_model=ImpactResponse, tags=["intelligence"])
async def event_impact(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> ImpactResponse:
    return await assess_impact(await get_event(event_id))


@app.get("/api/v1/events/{event_id}/risk", response_model=RiskResponse, tags=["intelligence"])
async def event_risk(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> RiskResponse:
    return await assess_risk(await get_event(event_id))


@app.get("/api/v1/events/{event_id}/weather", tags=["layers"])
async def event_weather(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    return await weather_provider.for_event(await get_event(event_id))


@app.get("/api/v1/events/{event_id}/exposure", tags=["layers"])
async def event_exposure(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    return await osm_provider.exposure(await get_event(event_id))


@app.post("/api/v1/events/{event_id}/alerts", response_model=AlertResponse, tags=["alerts"])
async def issue_alert(event_id: str, request: AlertRequest, user_claims: dict = Depends(require_role("analyst", "admin"))) -> AlertResponse:
    event = await get_event(event_id)
    deliveries = await send_alert(event, request.channels, request.recipients, request.message)
    status_value = "sent" if any(delivery["status"] == "sent" for delivery in deliveries) else "recorded"
    severity = request.severity or event.risk_level
    message = request.message or f"PyroLens {severity} alert: {event.name} at {event.location}. Risk score: {event.risk_score:.0f}/100."
    alert_record = repository.create_alert(
        event_id=event.id,
        severity=severity,
        message=message,
        channels=request.channels,
        recipients=request.recipients,
        deliveries=deliveries,
    )
    return AlertResponse(event_id=event.id, status=status_value, deliveries=deliveries, alert_id=alert_record.id)


@app.get("/api/v1/alerts", response_model=list[AlertRecord], tags=["alerts"])
async def list_alerts(
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    event_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> list[AlertRecord]:
    return repository.list_alerts(status=status_filter, severity=severity, event_id=event_id, limit=limit, offset=offset)


@app.get("/api/v1/alerts/{alert_id}", response_model=AlertRecord, tags=["alerts"])
async def get_alert(alert_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> AlertRecord:
    alert = repository.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@app.post("/api/v1/alerts/{alert_id}/acknowledge", response_model=AlertActionResponse, tags=["alerts"])
async def acknowledge_alert(
    alert_id: str,
    action_req: AlertActionRequest | None = None,
    user_claims: dict = Depends(require_role("analyst", "admin")),
) -> AlertActionResponse:
    username = user_claims.get("sub", "analyst")
    alert = repository.acknowledge_alert(alert_id, username=username)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertActionResponse(
        alert_id=alert.id,
        status=alert.status,
        action="acknowledged",
        updated_by=username,
        updated_at=alert.acknowledged_at or datetime.now(timezone.utc),
    )


@app.post("/api/v1/alerts/{alert_id}/resolve", response_model=AlertActionResponse, tags=["alerts"])
async def resolve_alert(
    alert_id: str,
    action_req: AlertActionRequest | None = None,
    user_claims: dict = Depends(require_role("analyst", "admin")),
) -> AlertActionResponse:
    username = user_claims.get("sub", "analyst")
    alert = repository.resolve_alert(alert_id, username=username)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertActionResponse(
        alert_id=alert.id,
        status=alert.status,
        action="resolved",
        updated_by=username,
        updated_at=alert.resolved_at or datetime.now(timezone.utc),
    )


@app.get("/api/v1/events/{event_id}/alerts", response_model=list[AlertRecord], tags=["alerts"])
async def get_event_alerts(event_id: str, _: dict = Depends(require_role("viewer", "analyst", "admin"))) -> list[AlertRecord]:
    return repository.get_alerts_for_event(event_id)


# =============================================================================
# DASHBOARD & ANALYTICS ENDPOINTS (Grounded in real database data)
# =============================================================================
@app.get("/api/v1/analytics/dashboard", response_model=DashboardAnalyticsResponse, tags=["analytics"])
@app.get("/api/v1/dashboard/stats", response_model=DashboardAnalyticsResponse, tags=["analytics"])
async def get_dashboard_analytics(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> DashboardAnalyticsResponse:
    return repository.get_dashboard_analytics()


@app.get("/api/v1/analytics/kpis", response_model=KPIs, tags=["analytics"])
async def get_analytics_kpis(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> KPIs:
    return repository.get_dashboard_analytics().kpis


@app.get("/api/v1/analytics/hotspots", response_model=list[HotspotRegion], tags=["analytics"])
async def get_analytics_hotspots(
    limit: int = Query(10, ge=1, le=50),
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> list[HotspotRegion]:
    regions = repository.get_dashboard_analytics().hotspot_regions
    return regions[:limit]


# =============================================================================
# GIS / MVT / GEOJSON ENDPOINTS
# =============================================================================
@app.get("/api/v1/geo/incidents.geojson", tags=["geospatial"])
@app.get("/api/v1/geo/events.geojson", tags=["geospatial"])
async def geojson_incidents(
    min_risk: float = Query(0, ge=0, le=100),
    event_type: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(600, ge=1, le=1000),
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> dict:
    events = [
        e for e in repository.list()
        if not e.id.startswith("FE-") and e.risk_score >= min_risk
        and (not event_type or e.event_type.lower() == event_type.lower())
        and (not status_filter or e.status.lower() == status_filter.lower())
    ][:limit]
    features = [
        {
            "type": "Feature",
            "id": ev.id,
            "geometry": {
                "type": "Point",
                "coordinates": [ev.longitude, ev.latitude],
            },
            "properties": {
                "id": ev.id,
                "name": ev.name,
                "location": ev.location,
                "event_type": ev.event_type,
                "risk_score": ev.risk_score,
                "risk_level": ev.risk_level,
                "confidence": ev.confidence,
                "frp_mw": ev.frp_mw,
                "brightness_kelvin": ev.brightness_kelvin,
                "status": ev.status,
                "observed_at": ev.observed_at.isoformat(),
            },
        }
        for ev in events
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
    }


@app.get("/api/v1/geo/incidents/{event_id}/impact.geojson", tags=["geospatial"])
async def geojson_incident_impact(
    event_id: str,
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> dict:
    event = await get_event(event_id)
    impact = await assess_impact(event)
    return {
        "type": "Feature",
        "id": f"{event_id}-impact",
        "geometry": impact.impact_zone_geojson,
        "properties": {
            "event_id": event.id,
            "name": event.name,
            "direction": impact.direction,
            "impact_zone_km2": impact.impact_zone_km2,
            "confidence": impact.confidence,
            "model": impact.model,
            "wind_speed_kmh": impact.wind_speed_kmh,
            "temperature_c": impact.temperature_c,
            "humidity_pct": impact.humidity_pct,
        },
    }


@app.get("/api/v1/geo/tiles/{z}/{x}/{y}.pbf", tags=["geospatial"])
async def vector_tile(
    z: int,
    x: int,
    y: int,
    _: dict = Depends(require_role("viewer", "analyst", "admin")),
) -> Response:
    """Mapbox Vector Tile endpoint. Returns MVT protobuf with PostGIS ST_AsMVT when available."""
    from .database import engine, SessionLocal
    if engine and engine.dialect.name == "postgresql":
        try:
            from sqlalchemy import text
            with SessionLocal() as db:
                stmt = text("""
                    WITH bounds AS (
                        SELECT ST_TileEnvelope(:z, :x, :y) AS geom
                    ),
                    mvtgeom AS (
                        SELECT id, name, type, risk_score, confidence,
                               ST_AsMVTGeom(ST_Transform(ST_SetSRID(ST_MakePoint(lng, lat), 4326), 3857), bounds.geom) AS geom
                        FROM incidents, bounds
                        WHERE ST_Intersects(ST_Transform(ST_SetSRID(ST_MakePoint(lng, lat), 4326), 3857), bounds.geom)
                    )
                    SELECT ST_AsMVT(mvtgeom.*, 'incidents') FROM mvtgeom;
                """)
                tile_bytes = db.execute(stmt, {"z": z, "x": x, "y": y}).scalar()
                if tile_bytes:
                    return Response(content=tile_bytes, media_type="application/x-protobuf")
        except Exception:
            pass
    return Response(status_code=204, media_type="application/x-protobuf", headers={"X-GIS-Engine": "sqlite-fallback"})


# =============================================================================
# ADMIN USER MANAGEMENT ENDPOINTS (Strictly requires admin role)
# =============================================================================
@app.get("/api/v1/admin/users", response_model=list[UserResponse], tags=["admin"])
async def admin_list_users(_: dict = Depends(require_role("admin"))) -> list[UserResponse]:
    return repository.list_users()


@app.post("/api/v1/admin/users", response_model=UserResponse, status_code=201, tags=["admin"])
async def admin_create_user(user_data: UserCreate, _: dict = Depends(require_role("admin"))) -> UserResponse:
    existing = repository.get_user_by_username(user_data.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{user_data.username}' is already registered")
    if len(user_data.password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters")
    return repository.create_user(user_data)


@app.get("/api/v1/admin/users/{user_id}", response_model=UserResponse, tags=["admin"])
async def admin_get_user(user_id: str, _: dict = Depends(require_role("admin"))) -> UserResponse:
    user = repository.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.patch("/api/v1/admin/users/{user_id}", response_model=UserResponse, tags=["admin"])
async def admin_update_user(user_id: str, update_data: UserUpdate, _: dict = Depends(require_role("admin"))) -> UserResponse:
    user = repository.update_user(user_id, update_data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.delete("/api/v1/admin/users/{user_id}", status_code=200, tags=["admin"])
async def admin_delete_user(user_id: str, current_claims: dict = Depends(require_role("admin"))) -> dict:
    target = repository.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.username == current_claims.get("sub"):
        raise HTTPException(status_code=400, detail="Cannot delete your own administrative account")
    repository.delete_user(user_id)
    return {"status": "deleted", "user_id": user_id}


@app.get("/api/v1/layers/infrastructure", tags=["layers"])
async def infrastructure_layers(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    return await osm_provider.infrastructure_summary()


@app.get("/api/v1/operational-area", tags=["system"])
async def operational_area(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    """Live status of the Africa pilot's operational sources, without secrets."""
    probe = ThermalEvent(
        id="africa-operational-probe", name=settings.monitoring_area_name,
        lat=settings.monitoring_latitude, lng=settings.monitoring_longitude,
        confidence=100, frp_mw=0, brightness_kelvin=0,
    )
    weather = await weather_provider.for_event(probe)
    infrastructure = await osm_provider.infrastructure_summary()
    latest = repository.latest_ingestion("firms")
    return {
        "area": {"name": settings.monitoring_area_name, "latitude": settings.monitoring_latitude, "longitude": settings.monitoring_longitude, "bbox": settings.firms_default_bbox},
        "sources": {
            "firms": {"configured": bool(settings.firms_api_key), "source": settings.firms_source, "latest_ingestion": latest},
            "weather": weather,
            "openstreetmap": infrastructure,
            "imagery": {"status": "public-wmts", "provider": "NASA GIBS", "layer": "VIIRS SNPP Corrected Reflectance True Color"},
            "terrain": {"status": "ready" if settings.terrain_raster_path and Path(settings.terrain_raster_path).is_file() else "raster-required", "path_configured": bool(settings.terrain_raster_path)},
            "landcover": {"status": "ready" if settings.landcover_raster_path and Path(settings.landcover_raster_path).is_file() else "raster-required", "path_configured": bool(settings.landcover_raster_path)},
            "classification": {"status": "trained-model" if not settings.model_version.endswith("synthetic-v1") else "rule-based", "model_version": settings.model_version},
            "alerts": {"sms": "configured" if all([settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number]) else "credentials-required", "email": "configured" if settings.smtp_host and settings.smtp_from_address else "credentials-required"},
            "identity": {"status": "configured" if settings.oidc_jwks_url else "development-only"},
        },
    }


@app.get("/api/v1/feeds/status", tags=["ingestion"])
async def feed_status(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    events = repository.list()
    firms_events = [event for event in events if event.id.startswith("FIRMS-")]
    latest_firms_run = repository.latest_ingestion("firms")
    latest_count = latest_firms_run["records_seen"] if latest_firms_run else None
    freshness = "live key not configured"
    if settings.firms_api_key:
        freshness = "NASA FIRMS has not been refreshed yet"
        if latest_firms_run and latest_firms_run["status"] == "completed":
            freshness = (
                "No NASA FIRMS detections in the selected area/time window"
                if latest_count == 0
                else "live NASA FIRMS feed"
            )
        elif latest_firms_run:
            freshness = "NASA FIRMS refresh failed; review the latest ingestion run"
    return {
        "lastSync": (
            f"scheduled every {settings.firms_refresh_seconds // 60} min"
            if settings.use_celery
            else "on-demand refresh"
        ),
        "detections24h": len(firms_events),
        "frpObservations": round(sum(event.frp_mw for event in firms_events)),
        "avgConfidence": round(sum(event.confidence for event in firms_events) / len(firms_events)) if firms_events else 0,
        "satelliteObservations": sum(event.observations for event in firms_events),
        "recordsProcessed": latest_count if latest_count is not None else 0,
        "dataFreshness": freshness,
        "sources": [
            {"name": "NASA FIRMS — VIIRS", "status": "Live — no detections" if settings.firms_api_key and latest_count == 0 else "Live" if settings.firms_api_key else "Key required"},
            {"name": "OpenStreetMap / Overpass", "status": "Live query available" if settings.osm_live_enabled else "Disabled"},
            {"name": "Open-Meteo weather", "status": "Live query available"},
        ],
    }


@app.get("/api/v1/firms/status", tags=["ingestion"])
async def firms_status(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    """Expose configuration state without returning the NASA MAP key."""
    return {
        "configured": bool(settings.firms_api_key),
        "source": settings.firms_source,
        "bbox": settings.firms_default_bbox,
        "days": settings.firms_days,
        "refresh_seconds": settings.firms_refresh_seconds,
        "mode": "celery" if settings.use_celery else "on-demand",
    }


@app.get("/api/v1/reports/incidents.csv", tags=["reports"])
async def export_incidents(_: dict = Depends(require_role("analyst", "admin"))) -> StreamingResponse:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "name", "latitude", "longitude", "classification", "risk_score", "risk_level", "status", "observed_at"])
    for event in repository.list():
        writer.writerow([event.id, event.name, event.latitude, event.longitude, event.event_type, event.risk_score, event.risk_level, event.status, event.observed_at.isoformat()])
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=pyrolens-incidents.csv"})


@app.get("/api/v1/reports/incidents.pdf", tags=["reports"])
async def export_incidents_pdf(
    min_risk: float = Query(0, ge=0, le=100),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    _: dict = Depends(require_role("analyst", "admin")),
) -> Response:
    events = [
        e for e in repository.list()
        if not e.id.startswith("FE-") and e.risk_score >= min_risk
        and (not status_filter or e.status.lower() == status_filter.lower())
    ][:limit]
    stats = repository.get_dashboard_analytics()
    pdf_bytes = generate_incidents_pdf(events, stats)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=pyrolens-incidents-report.pdf"},
    )


@app.get("/api/v1/reports/incidents/{event_id}.pdf", tags=["reports"])
@app.get("/api/v1/events/{event_id}/report.pdf", tags=["reports"])
async def export_incident_dossier_pdf(
    event_id: str,
    _: dict = Depends(require_role("analyst", "admin")),
) -> Response:
    event = await get_event(event_id)
    impact = await assess_impact(event)
    alerts = repository.get_alerts_for_event(event_id)
    pdf_bytes = generate_incident_dossier_pdf(event, impact=impact, alerts=alerts)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=pyrolens-incident-{event.id}.pdf"},
    )


@app.get("/api/v1/providers", tags=["system"])
async def providers(_: dict = Depends(require_role("viewer", "analyst", "admin"))) -> dict:
    return {
        "operational_area": settings.monitoring_area_name,
        "firms": {"status": "configured" if settings.firms_api_key else "credentials-required", "mode": "NASA FIRMS adapter"},
        "weather": {"status": "configured", "mode": "Open-Meteo live weather adapter"},
        "osm": {"status": "configured" if settings.osm_live_enabled else "disabled", "mode": "OpenStreetMap Overpass adapter"},
        "model": {"status": "rule-based" if settings.model_version.endswith("synthetic-v1") else "active", "version": settings.model_version},
    }
