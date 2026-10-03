import pytest
from starlette.testclient import TestClient

from app.geospatial import _generate_ellipse_geojson
from app.main import app
from app.ml import explain, get_model_metadata, predict
from app.repository import repository
from app.schemas import ThermalEvent


@pytest.fixture
def client():
    return TestClient(app)


def test_model_metadata():
    metadata = get_model_metadata()
    assert "status" in metadata
    assert "model_type" in metadata
    assert "shap_supported" in metadata
    assert "features" in metadata
    assert isinstance(metadata["features"], list)
    assert len(metadata["features"]) > 0


def test_ml_predict_no_np_nameerror():
    features = {
        "brightness": 340.5,
        "confidence": 85.0,
        "frp": 45.2,
        "persistence": 30.0,
    }
    label, confidence, indicators, version = predict(features)
    assert isinstance(label, str)
    assert isinstance(confidence, float)
    assert isinstance(indicators, list)
    assert len(indicators) > 0
    assert 0.0 <= confidence <= 100.0


def test_ml_explain_shap_multidimensional_handling():
    # Create a mock tree model with feature_importances_ and a mock SHAP return
    class MockTreeModel:
        def __init__(self):
            import numpy as np
            self.feature_importances_ = np.array([0.4, 0.2, 0.15, 0.15, 0.1])

    features = ["frp_mw", "brightness_kelvin", "persistence_score", "confidence", "observations"]
    import numpy as np
    vector = np.array([[50.0, 320.0, 30.0, 85.0, 2.0]])
    model = MockTreeModel()

    # Call explain - verifies no exception when shap is absent or present
    indicators = explain(model, features, vector, class_index=0)
    assert isinstance(indicators, list)
    assert len(indicators) > 0
    assert any("Model importance" in ind or "SHAP" in ind or "Feature" in ind for ind in indicators)


def test_ml_endpoints(client):
    # Test GET /api/v1/ml/metadata
    res_meta = client.get("/api/v1/ml/metadata")
    assert res_meta.status_code == 200
    meta = res_meta.json()
    assert "model_type" in meta
    assert "features" in meta

    # Test event classification endpoint
    events = repository.list()
    assert len(events) > 0
    event_id = events[0].id

    res_cls = client.get(f"/api/v1/events/{event_id}/classification")
    assert res_cls.status_code == 200
    cls_data = res_cls.json()
    assert cls_data["event_id"] == event_id
    assert "classification" in cls_data
    assert "confidence" in cls_data
    assert "indicators" in cls_data
    assert isinstance(cls_data["indicators"], list)


def test_generate_ellipse_geojson_validity():
    # Verify pure python ellipse generation creates a valid GeoJSON polygon
    geojson, area_km2 = _generate_ellipse_geojson(
        longitude=28.3228,
        latitude=-15.3875,
        radius_m=750.0,
        wind_kmh=20.0,
        direction_degrees=135.0,
    )
    assert geojson["type"] == "Polygon"
    assert "coordinates" in geojson
    coords = geojson["coordinates"]
    assert len(coords) == 1  # 1 exterior ring
    ring = coords[0]
    assert len(ring) >= 16
    # Closed loop: first coord equals last coord
    assert ring[0] == ring[-1]
    assert area_km2 > 0.0

    # Check that coordinates are [lon, lat] around Zambia
    for pt in ring:
        assert len(pt) == 2
        lon, lat = pt
        assert 26.0 <= lon <= 30.0
        assert -18.0 <= lat <= -13.0

