import pytest
from starlette.testclient import TestClient

from app.main import app
from app.repository import repository
from app.schemas import ImpactResponse


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def speedup_external_providers(monkeypatch):
    import app.main as main_mod

    async def fake_assess(ev):
        return ImpactResponse(
            event_id=ev.id,
            direction="NW",
            impact_zone_km2=0.45,
            confidence=85.0,
            exposed={"buildings": 12, "roads": 6, "industrial_sites": 2, "critical_assets": 1, "population": 288, "source": "test"},
            assumptions=["Test model assumption"],
            wind_speed_kmh=16.5,
            temperature_c=29.0,
            humidity_pct=42.0,
            impact_zone_geojson={"type": "Polygon", "coordinates": [[[28.0, -15.0], [28.1, -15.0], [28.1, -15.1], [28.0, -15.1], [28.0, -15.0]]]},
        )

    monkeypatch.setattr(main_mod, "assess_impact", fake_assess)


def test_geojson_incidents_collection(client):
    response = client.get("/api/v1/geo/incidents.geojson?limit=50")
    assert response.status_code == 200
    data = response.json()

    assert data["type"] == "FeatureCollection"
    assert "features" in data
    features = data["features"]
    assert len(features) > 0

    first = features[0]
    assert first["type"] == "Feature"
    assert "geometry" in first
    assert first["geometry"]["type"] == "Point"
    coords = first["geometry"]["coordinates"]
    assert len(coords) == 2
    # [lon, lat] format
    lon, lat = coords
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90

    # Properties
    props = first["properties"]
    assert "id" in props
    assert "event_type" in props
    assert "confidence" in props
    assert "frp_mw" in props


def test_geojson_incidents_filtering(client):
    # Test min_risk filter
    res_high = client.get("/api/v1/geo/incidents.geojson?min_risk=70")
    assert res_high.status_code == 200
    features = res_high.json()["features"]
    for feat in features:
        risk = feat["properties"].get("risk_score")
        if risk is not None:
            assert risk >= 70


def test_geojson_single_incident_impact(client):
    events = repository.list()
    assert len(events) > 0
    event_id = events[0].id

    res = client.get(f"/api/v1/geo/incidents/{event_id}/impact.geojson")
    assert res.status_code == 200
    feat = res.json()
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] == "Polygon"

    # Find the polygon ring and verify closure
    rings = feat["geometry"]["coordinates"]
    assert len(rings) > 0
    exterior = rings[0]
    assert len(exterior) >= 4  # A valid closed ring in GeoJSON has at least 4 coordinates
    # First point equals last point
    assert exterior[0] == exterior[-1]


def test_vector_tile_endpoint(client):
    # Request z=8, x=145, y=130 (in the region of central Africa)
    res = client.get("/api/v1/geo/tiles/8/145/130.pbf")
    assert res.status_code in (200, 204)
    assert res.headers.get("x-gis-engine") in ("sqlite-fallback", "postgis")

