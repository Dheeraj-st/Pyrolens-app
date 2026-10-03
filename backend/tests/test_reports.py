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


def test_csv_incident_report_export(client):
    response = client.get("/api/v1/reports/incidents.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    content = response.text
    # Check headers and data
    assert "id" in content.lower()
    assert "latitude" in content.lower()
    assert "longitude" in content.lower()
    # Ensure rows from real db are present
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    assert len(lines) > 50  # 504 rows + header


def test_pdf_incidents_overview_report(client):
    response = client.get("/api/v1/reports/incidents.pdf")
    assert response.status_code == 200
    assert "application/pdf" in response.headers.get("content-type", "")
    data = response.content
    assert data.startswith(b"%PDF-")
    assert len(data) > 1000


def test_pdf_single_incident_dossier(client):
    events = repository.list()
    assert len(events) > 0
    event_id = events[0].id

    # Test route 1: /api/v1/reports/incidents/{id}.pdf
    response1 = client.get(f"/api/v1/reports/incidents/{event_id}.pdf")
    assert response1.status_code == 200
    assert "application/pdf" in response1.headers.get("content-type", "")
    assert response1.content.startswith(b"%PDF-")

    # Test route 2: /api/v1/events/{id}/report.pdf
    response2 = client.get(f"/api/v1/events/{event_id}/report.pdf")
    assert response2.status_code == 200
    assert "application/pdf" in response2.headers.get("content-type", "")
    assert response2.content.startswith(b"%PDF-")


def test_pdf_single_incident_not_found(client):
    response = client.get("/api/v1/reports/incidents/nonexistent-event-999.pdf")
    assert response.status_code == 404

