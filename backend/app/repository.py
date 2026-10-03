from __future__ import annotations
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

from sqlalchemy import select, text

import uuid
from .models import Incident, IngestionRun, Alert, User, JobRecord
from .schemas import (
    ThermalEvent, AlertRecord, DashboardAnalyticsResponse, KPIs, DistributionItem,
    TimeSeriesItem, HotspotRegion, AlertsSummary, UserResponse, UserCreate, UserUpdate,
    JobResponse
)
from .security import hash_password


def _get_db():
    """Return an initialized database binding for API, worker, and test imports."""
    from .database import Base, SessionLocal, engine, init_db

    init_db()
    # ``init_db`` updates the module globals, so bind them only after it runs.
    from .database import SessionLocal, engine
    return Base, SessionLocal, engine

# Replace this repository with SQLAlchemy/PostGIS persistence without changing routes.
_SEED_EVENTS = [
    ThermalEvent(id="FE-2291", name="Industrial Zone A - Tank Farm 3", location="Industrial Zone A, Sector 12", lat=19.076, lng=72.877, event_type="Industrial Fire", confidence=94, frp_mw=186, brightness_kelvin=412, observations=14, persistence_score=82, risk_score=87, risk_level="CRITICAL", status="Warning Issued"),
    ThermalEvent(id="FE-2288", name="Riverside Chemical Storage", location="Riverside Industrial Belt", lat=19.12, lng=72.85, event_type="Persistent Thermal Source", confidence=88, frp_mw=94, brightness_kelvin=378, observations=38, persistence_score=91, risk_score=71, risk_level="HIGH", status="Responding"),
    ThermalEvent(id="FE-2276", name="Northern Agri Belt Burn", location="Northern Agricultural Zone", lat=19.20, lng=72.83, event_type="Agricultural Burning", confidence=81, frp_mw=22, brightness_kelvin=331, observations=6, persistence_score=26, risk_score=44, risk_level="MEDIUM", status="Monitoring"),
    ThermalEvent(id="FE-2299", name="Eastside Warehousing Cluster", location="Eastside Logistics Park", lat=19.09, lng=72.93, event_type="Possible False Positive", confidence=52, frp_mw=6, brightness_kelvin=302, observations=1, persistence_score=12, risk_score=18, risk_level="LOW", status="Active"),
]

class EventRepository:
    def __init__(self):
        Base, SessionLocal, engine = _get_db()
        # _get_db() ensures the schema exists before seed data is read or written.
        self._seed()

    def _seed(self) -> None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            if db.scalar(select(Incident.id).limit(1)):
                return
            for event in _SEED_EVENTS:
                db.add(self._to_model(event))
            db.commit()

    @staticmethod
    def _to_model(event: ThermalEvent) -> Incident:
        return Incident(
            id=event.id, name=event.name, location=event.location, lat=event.latitude, lng=event.longitude,
            type=event.event_type, confidence=round(event.confidence), risk=event.risk_level,
            risk_score=round(event.risk_score), status=event.status, detection_time=event.observed_at.isoformat(),
            satellite=event.source, frp=f"{event.frp_mw:g} MW", brightness=f"{event.brightness_kelvin:g} K",
            persistence=f"{event.persistence_score:g}", persistence_score=round(event.persistence_score),
            impact_direction="North-East", impact_zone=f"{max(0.1, event.frp_mw / 60):.1f} km2",
            impact_confidence=round(min(95, event.confidence * 0.82)), exposure={}, environmental={},
            raw_payload=event.model_dump(mode="json"),
        )

    @staticmethod
    def _to_schema(model: Incident) -> ThermalEvent:
        def _parse_num(val):
            if not val:
                return 0.0
            try:
                return float(str(val).split()[0])
            except (ValueError, IndexError):
                return 0.0

        observed = model.created_at
        if observed and observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        elif observed is None:
            observed = datetime.now(timezone.utc)

        return ThermalEvent(
            id=model.id,
            name=model.name,
            location=model.location or "",
            lat=model.lat,
            lng=model.lng,
            source=model.satellite or "VIIRS",
            event_type=model.type or "Unknown",
            confidence=model.confidence or 0,
            frp_mw=_parse_num(model.frp),
            brightness_kelvin=_parse_num(model.brightness),
            observed_at=observed,
            observations=1,
            persistence_score=model.persistence_score or 0,
            risk_score=model.risk_score or 0,
            risk_level=model.risk or "LOW",
            status=model.status or "Active",
            detection_time=model.detection_time,
            satellite=model.satellite,
            frp=model.frp,
            brightness=model.brightness,
            persistence=model.persistence,
            impact_direction=model.impact_direction,
            impact_zone=model.impact_zone,
            impact_confidence=model.impact_confidence,
            exposure=model.exposure or {},
            environmental=model.environmental or {},
        )

    def list(self) -> list[ThermalEvent]:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            return [self._to_schema(model) for model in db.scalars(select(Incident).order_by(Incident.created_at.desc())).all()]

    def get(self, event_id: str) -> ThermalEvent | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            model = db.get(Incident, event_id)
            return self._to_schema(model) if model else None

    def save_many(self, events: list[ThermalEvent]) -> int:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            written = 0
            for event in events:
                existing = db.get(Incident, event.id)
                if existing:
                    refreshed = self._to_model(event)
                    for column in Incident.__table__.columns.keys():
                        if column not in {"id", "created_at"}:
                            setattr(existing, column, getattr(refreshed, column))
                    existing.updated_at = datetime.now(timezone.utc)
                    continue
                db.add(self._to_model(event))
                written += 1
            db.commit()
            return written

    def nearby(self, latitude: float, longitude: float, radius_km: float) -> list[ThermalEvent]:
        """Use PostGIS in production and a geographic fallback for SQLite demos."""
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            if engine.dialect.name == "postgresql":
                statement = text("""
                    SELECT * FROM incidents
                    WHERE ST_DWithin(
                      ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                      ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                      :radius_meters
                    )
                    ORDER BY created_at DESC
                """)
                models = db.query(Incident).from_statement(statement).params(
                    latitude=latitude, longitude=longitude, radius_meters=radius_km * 1000
                ).all()
                return [self._to_schema(model) for model in models]

            def distance_km(model: Incident) -> float:
                lat_delta = radians(model.lat - latitude)
                lng_delta = radians(model.lng - longitude)
                a = sin(lat_delta / 2) ** 2 + cos(radians(latitude)) * cos(radians(model.lat)) * sin(lng_delta / 2) ** 2
                return 6371 * 2 * asin(sqrt(a))

            return [self._to_schema(model) for model in db.scalars(select(Incident)).all() if distance_km(model) <= radius_km]

    def record_ingestion(self, source: str, seen: int, written: int, status: str = "completed", error: str | None = None) -> int:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            run = IngestionRun(source=source, status=status, records_seen=seen, records_written=written, finished_at=datetime.now(timezone.utc))
            db.add(run)
            db.commit()
            db.refresh(run)
            return run.id

    def latest_ingestion(self, source: str) -> dict | None:
        """Return metadata for the most recent provider refresh, without payload data."""
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            run = db.scalar(
                select(IngestionRun)
                .where(IngestionRun.source == source)
                .order_by(IngestionRun.finished_at.desc(), IngestionRun.id.desc())
                .limit(1)
            )
            if run is None:
                return None
            return {
                "status": run.status,
                "records_seen": run.records_seen,
                "records_written": run.records_written,
                "finished_at": run.finished_at,
                "error": run.error,
            }

    def update_location(self, event_id: str, location: str) -> bool:
        """Update the location of an event (used by background geocoding)."""
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            model = db.get(Incident, event_id)
            if model:
                model.location = location
                model.updated_at = datetime.now(timezone.utc)
                db.commit()
                return True
            return False

    # =========================================================================
    # ALERT REPOSITORY METHODS
    # =========================================================================
    @staticmethod
    def _alert_to_schema(alert: Alert) -> AlertRecord:
        created = alert.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elif created is None:
            created = datetime.now(timezone.utc)

        ack_at = alert.acknowledged_at
        if ack_at and ack_at.tzinfo is None:
            ack_at = ack_at.replace(tzinfo=timezone.utc)

        res_at = alert.resolved_at
        if res_at and res_at.tzinfo is None:
            res_at = res_at.replace(tzinfo=timezone.utc)

        return AlertRecord(
            id=alert.id,
            event_id=alert.event_id,
            severity=alert.severity or "MEDIUM",
            status=alert.status or "active",
            message=alert.message or "",
            channels=alert.channels or [],
            recipients=alert.recipients or [],
            deliveries=alert.deliveries or [],
            acknowledged_at=ack_at,
            acknowledged_by=alert.acknowledged_by,
            resolved_at=res_at,
            resolved_by=alert.resolved_by,
            created_at=created,
        )

    def create_alert(
        self,
        event_id: str,
        message: str,
        severity: str = "MEDIUM",
        channels: list[str] | None = None,
        recipients: list[str] | None = None,
        deliveries: list[dict] | None = None,
    ) -> AlertRecord:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            alert = Alert(
                id=str(uuid.uuid4()),
                event_id=event_id,
                severity=severity or "MEDIUM",
                status="active",
                message=message,
                channels=channels or ["dashboard"],
                recipients=recipients or [],
                deliveries=deliveries or [],
                created_at=datetime.now(timezone.utc),
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            return self._alert_to_schema(alert)

    def get_alert(self, alert_id: str) -> AlertRecord | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            alert = db.get(Alert, alert_id)
            return self._alert_to_schema(alert) if alert else None

    def list_alerts(
        self,
        status: str | None = None,
        severity: str | None = None,
        event_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AlertRecord]:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            query = select(Alert)
            if status:
                query = query.where(Alert.status == status.lower())
            if severity:
                query = query.where(Alert.severity == severity.upper())
            if event_id:
                query = query.where(Alert.event_id == event_id)
            query = query.order_by(Alert.created_at.desc()).offset(offset).limit(limit)
            return [self._alert_to_schema(a) for a in db.scalars(query).all()]

    def acknowledge_alert(self, alert_id: str, username: str = "analyst") -> AlertRecord | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            alert = db.get(Alert, alert_id)
            if not alert:
                return None
            alert.status = "acknowledged"
            alert.acknowledged_at = datetime.now(timezone.utc)
            alert.acknowledged_by = username
            db.commit()
            db.refresh(alert)
            return self._alert_to_schema(alert)

    def resolve_alert(self, alert_id: str, username: str = "analyst") -> AlertRecord | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            alert = db.get(Alert, alert_id)
            if not alert:
                return None
            alert.status = "resolved"
            alert.resolved_at = datetime.now(timezone.utc)
            alert.resolved_by = username
            db.commit()
            db.refresh(alert)
            return self._alert_to_schema(alert)

    def get_alerts_for_event(self, event_id: str) -> list[AlertRecord]:
        return self.list_alerts(event_id=event_id, limit=100)

    # =========================================================================
    # ANALYTICS & DASHBOARD METHODS (Computed from real database data)
    # =========================================================================
    def get_dashboard_analytics(self) -> DashboardAnalyticsResponse:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            incidents = db.scalars(select(Incident)).all()
            alerts = db.scalars(select(Alert)).all()

            total_incidents = len(incidents)
            active_events = 0
            potential_fires = 0
            persistent_thermal = 0
            high_risk_zones = 0
            critical_events = 0
            confidences = []
            risk_scores = []
            frps = []

            risk_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            class_counts: dict[str, int] = {}
            time_series_map: dict[str, dict[str, int]] = {}
            region_map: dict[str, dict[str, float]] = {}

            for inc in incidents:
                status_lower = (inc.status or "").lower()
                if status_lower in ("active", "warning issued", "responding", "monitoring"):
                    active_events += 1

                inc_type = inc.type or "Unknown"
                if "fire" in inc_type.lower() or "potential" in inc_type.lower():
                    potential_fires += 1
                if "persistent" in inc_type.lower() or (inc.persistence_score and inc.persistence_score >= 70):
                    persistent_thermal += 1

                risk_upper = (inc.risk or "LOW").upper()
                if risk_upper in risk_counts:
                    risk_counts[risk_upper] += 1
                else:
                    risk_counts["LOW"] += 1

                if risk_upper in ("HIGH", "CRITICAL") or (inc.risk_score and inc.risk_score >= 65):
                    high_risk_zones += 1
                if risk_upper == "CRITICAL" or (inc.risk_score and inc.risk_score >= 85):
                    critical_events += 1

                if inc.confidence is not None:
                    confidences.append(inc.confidence)
                if inc.risk_score is not None:
                    risk_scores.append(inc.risk_score)

                frp_val = 0.0
                if inc.frp:
                    try:
                        frp_val = float(str(inc.frp).split()[0])
                    except (ValueError, IndexError):
                        frp_val = 0.0
                frps.append(frp_val)

                class_counts[inc_type] = class_counts.get(inc_type, 0) + 1

                # Group by date
                dt_str = inc.detection_time or (inc.created_at.strftime("%Y-%m-%d") if inc.created_at else "Unknown")
                date_key = dt_str[:10] if len(dt_str) >= 10 else "Recent"
                if date_key not in time_series_map:
                    time_series_map[date_key] = {"events": 0, "high_risk": 0, "industrial": 0}
                time_series_map[date_key]["events"] += 1
                if risk_upper in ("HIGH", "CRITICAL"):
                    time_series_map[date_key]["high_risk"] += 1
                if "industrial" in inc_type.lower():
                    time_series_map[date_key]["industrial"] += 1

                # Group by region/location
                loc = inc.location or "Satellite coordinates"
                region_name = loc.split(",")[0].strip() if "," in loc else loc.strip()
                if not region_name or region_name.startswith("Near ") or region_name.startswith("NASA FIRMS"):
                    region_name = "Central Monitoring Belt"
                if region_name not in region_map:
                    region_map[region_name] = {"count": 0.0, "risk_sum": 0.0, "frp_sum": 0.0, "persistence_sum": 0.0}
                region_map[region_name]["count"] += 1.0
                region_map[region_name]["risk_sum"] += float(inc.risk_score or 0)
                region_map[region_name]["frp_sum"] += frp_val
                region_map[region_name]["persistence_sum"] += float(inc.persistence_score or 0)

            avg_conf = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
            avg_risk = round(sum(risk_scores) / len(risk_scores), 1) if risk_scores else 0.0
            tot_frp = round(sum(frps), 1)
            avg_frp = round(tot_frp / len(frps), 1) if frps else 0.0

            kpis = KPIs(
                total_incidents=total_incidents,
                active_thermal_events=active_events,
                potential_fire_events=potential_fires,
                persistent_thermal_sources=persistent_thermal,
                high_risk_zones=high_risk_zones,
                critical_events=critical_events,
                avg_confidence=avg_conf,
                avg_risk_score=avg_risk,
                total_frp_mw=tot_frp,
                avg_frp_mw=avg_frp,
                satellite_observations=total_incidents,
            )

            risk_colors = {"CRITICAL": "#B3261E", "HIGH": "#C2410C", "MEDIUM": "#B45309", "LOW": "#1E7A4C"}
            risk_dist = [
                DistributionItem(
                    name=r,
                    value=c,
                    percentage=round(c / total_incidents * 100, 1) if total_incidents else 0.0,
                    color=risk_colors.get(r, "#5E6573"),
                )
                for r, c in risk_counts.items()
            ]

            class_colors = {
                "Industrial Fire": "#B3261E",
                "Potential Fire Event": "#E11D48",
                "Agricultural Burning": "#B45309",
                "Persistent Thermal Source": "#D97706",
                "Possible False Positive": "#5E6573",
            }
            class_dist = [
                DistributionItem(
                    name=name,
                    value=c,
                    percentage=round(c / total_incidents * 100, 1) if total_incidents else 0.0,
                    color=class_colors.get(name, "#C2410C"),
                )
                for name, c in sorted(class_counts.items(), key=lambda x: x[1], reverse=True)
            ]

            time_series = [
                TimeSeriesItem(
                    date=d,
                    events=stats["events"],
                    high_risk=stats["high_risk"],
                    industrial=stats["industrial"],
                )
                for d, stats in sorted(time_series_map.items())
            ]

            hotspot_regions = []
            for rname, rstats in sorted(region_map.items(), key=lambda x: x[1]["count"], reverse=True)[:6]:
                cnt = int(rstats["count"])
                avg_r = round(rstats["risk_sum"] / cnt, 1)
                avg_p = rstats["persistence_sum"] / cnt
                pers_label = "Very High" if avg_p >= 75 else "High" if avg_p >= 50 else "Moderate" if avg_p >= 25 else "Low"
                hotspot_regions.append(
                    HotspotRegion(
                        name=rname,
                        count=cnt,
                        risk=avg_r,
                        persistence=pers_label,
                        total_frp_mw=round(rstats["frp_sum"], 1),
                    )
                )

            active_alerts = sum(1 for a in alerts if a.status == "active")
            ack_alerts = sum(1 for a in alerts if a.status == "acknowledged")
            res_alerts = sum(1 for a in alerts if a.status == "resolved")
            alerts_summary = AlertsSummary(
                total=len(alerts),
                active=active_alerts,
                acknowledged=ack_alerts,
                resolved=res_alerts,
            )

            return DashboardAnalyticsResponse(
                kpis=kpis,
                risk_distribution=risk_dist,
                class_distribution=class_dist,
                time_series=time_series,
                hotspot_regions=hotspot_regions,
                alerts_summary=alerts_summary,
            )

    # =========================================================================
    # USER MANAGEMENT METHODS
    # =========================================================================
    @staticmethod
    def _user_to_schema(user: User) -> UserResponse:
        created = user.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elif created is None:
            created = datetime.now(timezone.utc)
        last_log = user.last_login
        if last_log and last_log.tzinfo is None:
            last_log = last_log.replace(tzinfo=timezone.utc)
        return UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=bool(user.is_active),
            created_at=created,
            last_login=last_log,
        )

    def list_users(self) -> list[UserResponse]:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            users = db.scalars(select(User).order_by(User.created_at.asc())).all()
            return [self._user_to_schema(u) for u in users]

    def get_user_by_id(self, user_id: str) -> UserResponse | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            user = db.get(User, user_id)
            return self._user_to_schema(user) if user else None

    def get_user_by_username(self, username: str) -> User | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            return db.scalar(select(User).where(User.username == username))

    def create_user(self, user_data: UserCreate) -> UserResponse:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            user = User(
                id=str(uuid.uuid4()),
                username=user_data.username,
                email=user_data.email,
                hashed_password=hash_password(user_data.password),
                full_name=user_data.full_name,
                role=user_data.role,
                is_active=1,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return self._user_to_schema(user)

    def update_user(self, user_id: str, update_data: UserUpdate) -> UserResponse | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if not user:
                return None
            if update_data.email is not None:
                user.email = update_data.email
            if update_data.full_name is not None:
                user.full_name = update_data.full_name
            if update_data.role is not None:
                user.role = update_data.role
            if update_data.is_active is not None:
                user.is_active = 1 if update_data.is_active else 0
            if update_data.password:
                user.hashed_password = hash_password(update_data.password)
            db.commit()
            db.refresh(user)
            return self._user_to_schema(user)

    def delete_user(self, user_id: str) -> bool:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if not user:
                return False
            db.delete(user)
            db.commit()
            return True

    def record_login(self, username: str) -> None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == username))
            if user:
                user.last_login = datetime.now(timezone.utc)
                db.commit()

    # =========================================================================
    # JOB PERSISTENCE METHODS
    # =========================================================================
    def save_job(self, job_id: str, task: str, status: str = "queued", result: dict | None = None, error: str | None = None) -> None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            job = JobRecord(
                id=str(uuid.uuid4()),
                job_id=job_id,
                task=task,
                status=status,
                result=result,
                error=error,
                created_at=datetime.now(timezone.utc),
            )
            db.add(job)
            db.commit()

    def update_job(self, job_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            job = db.scalar(select(JobRecord).where(JobRecord.job_id == job_id))
            if job:
                job.status = status
                if result is not None:
                    job.result = result
                if error is not None:
                    job.error = error
                if status in ("completed", "failed"):
                    job.completed_at = datetime.now(timezone.utc)
                db.commit()

    def get_job(self, job_id: str) -> JobResponse | None:
        Base, SessionLocal, engine = _get_db()
        with SessionLocal() as db:
            job = db.scalar(select(JobRecord).where(JobRecord.job_id == job_id))
            if not job:
                return None
            created = job.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elif created is None:
                created = datetime.now(timezone.utc)
            return JobResponse(
                job_id=job.job_id,
                status=job.status,
                task=job.task,
                created_at=created,
                result=job.result,
                error=job.error,
            )


repository = EventRepository()
