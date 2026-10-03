from .schemas import ClassificationResponse, ImpactResponse, RiskResponse, ThermalEvent
from .providers import osm_provider, weather_provider
from .ml import predict
from .geospatial import estimate_spread
from .config import get_settings

async def classify(event: ThermalEvent) -> ClassificationResponse:
    label, confidence, indicators, model_version = predict(event)
    return ClassificationResponse(event_id=event.id, classification=label, confidence=confidence, model_version=model_version, indicators=indicators)

async def assess_risk(event: ThermalEvent) -> RiskResponse:
    factors = [{"name": "Fire intensity", "value": event.frp_mw, "weight": 0.35}, {"name": "Persistence", "value": event.persistence_score, "weight": 0.25}, {"name": "Detection confidence", "value": event.confidence, "weight": 0.15}]
    score = round(event.risk_score or min(100, event.frp_mw / 3 + event.persistence_score * 0.35))
    level = "CRITICAL" if score >= 85 else "HIGH" if score >= 65 else "MEDIUM" if score >= 35 else "LOW"
    actions = ["Notify duty officer", "Validate with current weather and field teams"] if level in ("HIGH", "CRITICAL") else ["Continue satellite monitoring", "Request verification if persistence increases"]
    return RiskResponse(event_id=event.id, score=score, level=level, factors=factors, recommended_actions=actions)

async def assess_impact(event: ThermalEvent) -> ImpactResponse:
    weather = await weather_provider.for_event(event)
    exposed = await osm_provider.exposure(event)
    # Return the sampled weather alongside the exposure estimate so operational
    # clients can explain the direction and confidence they display.
    exposed["weather"] = weather
    settings = get_settings()
    spread = estimate_spread(event, weather, settings.terrain_raster_path, settings.landcover_raster_path)
    assumptions = [
        "Impact is an estimate, not a confirmed damage assessment",
        "Wind conditions are sampled at the event coordinates",
        "Terrain and land cover use configured rasters when provided; otherwise a conservative fallback is used",
    ]
    return ImpactResponse(
        event_id=event.id,
        direction=weather.get("wind_direction", "Unavailable"),
        confidence=round(min(95, event.confidence * 0.82), 1),
        exposed=exposed,
        assumptions=assumptions,
        wind_speed_kmh=float(weather.get("wind_speed_kmh")) if weather.get("wind_speed_kmh") is not None else None,
        temperature_c=float(weather.get("temperature_c")) if weather.get("temperature_c") is not None else None,
        humidity_pct=float(weather.get("humidity_pct")) if weather.get("humidity_pct") is not None else None,
        **spread,
    )
