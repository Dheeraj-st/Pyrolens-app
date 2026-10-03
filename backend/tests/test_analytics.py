import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_dashboard_analytics_real_db(client):
    response = client.get("/api/v1/analytics/dashboard")
    assert response.status_code == 200
    data = response.json()

    # Verify top-level structure
    assert "kpis" in data
    assert "risk_distribution" in data
    assert "class_distribution" in data
    assert "time_series" in data
    assert "hotspot_regions" in data
    assert "alerts_summary" in data

    # Verify real database KPIs
    kpis = data["kpis"]
    assert kpis["total_incidents"] >= 500
    assert kpis["active_thermal_events"] >= 0
    assert kpis["avg_confidence"] > 0.0
    assert kpis["avg_risk_score"] >= 0.0
    assert kpis["total_frp_mw"] > 0.0
    assert kpis["avg_frp_mw"] > 0.0

    # Verify distributions have valid items
    assert len(data["risk_distribution"]) > 0
    risk_labels = [item["name"] for item in data["risk_distribution"]]
    assert any(label in ["CRITICAL", "HIGH", "MEDIUM", "LOW"] for label in risk_labels)

    assert len(data["class_distribution"]) > 0

    # Verify time series points
    assert len(data["time_series"]) > 0
    first_ts = data["time_series"][0]
    assert "date" in first_ts
    assert "events" in first_ts

    # Verify hotspot regions
    assert len(data["hotspot_regions"]) > 0
    first_hs = data["hotspot_regions"][0]
    assert "name" in first_hs
    assert "count" in first_hs
    assert "risk" in first_hs

    # Verify alerts summary
    alerts_sum = data["alerts_summary"]
    assert "total" in alerts_sum
    assert "active" in alerts_sum
    assert "acknowledged" in alerts_sum
    assert "resolved" in alerts_sum


def test_dashboard_stats_endpoint(client):
    response = client.get("/api/v1/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_incidents" in data or "kpis" in data


def test_analytics_kpis_endpoint(client):
    response = client.get("/api/v1/analytics/kpis")
    assert response.status_code == 200
    kpis = response.json()
    assert kpis["total_incidents"] >= 500
    assert kpis["total_frp_mw"] > 0.0
    assert kpis["avg_frp_mw"] > 0.0


def test_analytics_hotspots_endpoint(client):
    response = client.get("/api/v1/analytics/hotspots?limit=5")
    assert response.status_code == 200
    hotspots = response.json()
    assert isinstance(hotspots, list)
    assert len(hotspots) <= 5
    if len(hotspots) > 0:
        assert "name" in hotspots[0]
        assert "count" in hotspots[0]

