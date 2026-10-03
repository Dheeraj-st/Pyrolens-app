from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import AliasChoices, BaseModel, Field

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

class HealthResponse(BaseModel):
    status: str
    environment: str
    model_version: str

class ThermalEvent(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    name: str
    latitude: float = Field(validation_alias=AliasChoices("lat", "latitude"))
    longitude: float = Field(validation_alias=AliasChoices("lng", "longitude"))

    source: str = "VIIRS"
    event_type: str = "Unknown"
    confidence: float = Field(ge=0, le=100)
    frp_mw: float = Field(ge=0)
    brightness_kelvin: float = Field(ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    observations: int = Field(default=1, ge=1)
    persistence_score: float = Field(default=0, ge=0, le=100)
    risk_score: float = Field(default=0, ge=0, le=100)
    risk_level: RiskLevel = "LOW"
    status: str = "Active"
    location: str = ""
    detection_time: str | None = None
    satellite: str | None = None
    frp: str | None = None
    brightness: str | None = None
    persistence: str | None = None
    impact_direction: str | None = None
    impact_zone: str | None = None
    impact_confidence: int | None = None
    exposure: dict[str, Any] | None = None
    environmental: dict[str, Any] | None = None

class IngestionRequest(BaseModel):
    source: Literal["firms", "upload", "synthetic", "demo"] = "synthetic"
    start_date: str | None = None
    end_date: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    days: int | None = Field(default=None, ge=1, le=5)
    records: list[dict[str, Any]] | None = None

class IngestionResponse(BaseModel):
    job_id: str
    source: str
    accepted_records: int
    rejected_records: int
    status: str
    validation: dict[str, int] = Field(default_factory=dict)

class ClassificationResponse(BaseModel):
    event_id: str
    classification: str
    confidence: float
    indicators: list[str]
    model_version: str

class ImpactResponse(BaseModel):
    event_id: str
    direction: str
    impact_zone_km2: float
    confidence: float
    exposed: dict[str, Any]
    assumptions: list[str]
    impact_zone_geojson: dict[str, Any] | None = None
    terrain_elevation_m: float | None = None
    landcover_code: float | None = None
    model: str = "heuristic"
    wind_speed_kmh: float | None = None
    temperature_c: float | None = None
    humidity_pct: float | None = None

class RiskResponse(BaseModel):
    event_id: str
    score: float
    level: RiskLevel
    factors: list[dict[str, Any]]
    recommended_actions: list[str]

class JobResponse(BaseModel):
    job_id: str
    status: str
    task: str
    created_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None

class AlertRequest(BaseModel):
    channels: list[Literal["dashboard", "sms", "email"]] = ["dashboard"]
    recipients: list[str] = []
    message: str | None = None
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None

class AlertResponse(BaseModel):
    event_id: str
    status: str
    deliveries: list[dict[str, str]]
    alert_id: str | None = None

class AlertRecord(BaseModel):
    id: str
    event_id: str
    severity: str = "MEDIUM"
    status: str = "active"  # active, acknowledged, resolved
    message: str
    channels: list[str] = []
    recipients: list[str] = []
    deliveries: list[dict[str, str]] = []
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    created_at: datetime

class AlertActionRequest(BaseModel):
    note: str | None = None

class AlertActionResponse(BaseModel):
    alert_id: str
    status: str
    action: str
    updated_by: str | None = None
    updated_at: datetime

# Analytics Schemas
class KPIs(BaseModel):
    total_incidents: int
    active_thermal_events: int
    potential_fire_events: int
    persistent_thermal_sources: int
    high_risk_zones: int
    critical_events: int
    avg_confidence: float
    avg_risk_score: float
    total_frp_mw: float
    avg_frp_mw: float
    satellite_observations: int

class DistributionItem(BaseModel):
    name: str
    value: int
    percentage: float = 0.0
    color: str | None = None

class TimeSeriesItem(BaseModel):
    date: str
    day: str | None = None
    events: int
    high_risk: int = 0
    industrial: int = 0

class HotspotRegion(BaseModel):
    name: str
    count: int
    risk: float
    persistence: str
    total_frp_mw: float = 0.0

class AlertsSummary(BaseModel):
    total: int = 0
    active: int = 0
    acknowledged: int = 0
    resolved: int = 0

class DashboardAnalyticsResponse(BaseModel):
    kpis: KPIs
    risk_distribution: list[DistributionItem]
    class_distribution: list[DistributionItem]
    time_series: list[TimeSeriesItem]
    hotspot_regions: list[HotspotRegion]
    alerts_summary: AlertsSummary

# User Management Schemas
class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None

class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: str | None = None
    role: Literal["admin", "analyst", "viewer"] = "analyst"

class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    role: Literal["admin", "analyst", "viewer"] | None = None
    is_active: bool | None = None
    password: str | None = None

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse | None = None

class UserProfile(BaseModel):
    sub: str
    username: str | None = None
    role: str
    permissions: list[str] = []
    email: str | None = None
    full_name: str | None = None

