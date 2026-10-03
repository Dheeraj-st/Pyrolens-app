import pytest
from starlette.testclient import TestClient

from app.main import app
from app.repository import repository


@pytest.fixture
def client():
    return TestClient(app)


def test_alert_lifecycle_and_persistence(client):
    # Fetch an incident to attach the alert to
    events = repository.list()
    assert len(events) > 0
    event_id = events[0].id

    # 1. Issue an alert via API
    alert_payload = {
        "channels": ["dashboard"],
        "recipients": [],
        "message": f"Critical fire escalation for event {event_id}",
        "severity": "CRITICAL",
    }
    response = client.post(f"/api/v1/events/{event_id}/alerts", json=alert_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["event_id"] == event_id
    assert "alert_id" in data
    assert data["alert_id"] is not None
    alert_id = data["alert_id"]

    # 2. Verify alert persistence by fetching single alert
    get_res = client.get(f"/api/v1/alerts/{alert_id}")
    assert get_res.status_code == 200
    alert_obj = get_res.json()
    assert alert_obj["id"] == alert_id
    assert alert_obj["event_id"] == event_id
    assert alert_obj["severity"] == "CRITICAL"
    assert alert_obj["status"] == "active"
    assert alert_obj["message"] == alert_payload["message"]

    # 3. Verify alert appears in event alert history
    history_res = client.get(f"/api/v1/events/{event_id}/alerts")
    assert history_res.status_code == 200
    history = history_res.json()
    assert any(a["id"] == alert_id for a in history)

    # 4. Verify alert listing with filters
    list_res = client.get(f"/api/v1/alerts?status=active&event_id={event_id}")
    assert list_res.status_code == 200
    active_alerts = list_res.json()
    assert any(a["id"] == alert_id for a in active_alerts)

    # 5. Acknowledge alert
    ack_res = client.post(f"/api/v1/alerts/{alert_id}/acknowledge", json={"note": "Assigned to station 4"})
    assert ack_res.status_code == 200
    ack_data = ack_res.json()
    assert ack_data["alert_id"] == alert_id
    assert ack_data["status"] == "acknowledged"
    assert ack_data["action"] == "acknowledged"

    # Verify acknowledged status in DB
    refreshed = client.get(f"/api/v1/alerts/{alert_id}").json()
    assert refreshed["status"] == "acknowledged"
    assert refreshed["acknowledged_by"] is not None
    assert refreshed["acknowledged_at"] is not None

    # 6. Resolve alert
    res_res = client.post(f"/api/v1/alerts/{alert_id}/resolve", json={"note": "Controlled by local responders"})
    assert res_res.status_code == 200
    res_data = res_res.json()
    assert res_data["alert_id"] == alert_id
    assert res_data["status"] == "resolved"
    assert res_data["action"] == "resolved"

    # Verify resolved status in DB
    final_alert = client.get(f"/api/v1/alerts/{alert_id}").json()
    assert final_alert["status"] == "resolved"
    assert final_alert["resolved_by"] is not None
    assert final_alert["resolved_at"] is not None


def test_alert_not_found(client):
    res = client.get("/api/v1/alerts/nonexistent-id-0000")
    assert res.status_code == 404

