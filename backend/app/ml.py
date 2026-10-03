"""Versioned classifier inference and feature-attribution boundary."""
from pathlib import Path

from .config import get_settings
from .schemas import ThermalEvent

FEATURES = ["frp_mw", "brightness_kelvin", "persistence_score", "confidence", "observations"]
LABELS = ["Agricultural Burning", "Industrial Fire", "Persistent Thermal Source", "Possible False Positive"]


def _feature_vector(event: ThermalEvent):
    import numpy as np
    return np.array([[event.frp_mw, event.brightness_kelvin, event.persistence_score, event.confidence, event.observations]], dtype=float)


def load_artifact() -> dict | None:
    """Load the optional trained model without preventing API startup."""
    path = Path(get_settings().model_path)
    if not path.exists():
        return None
    try:
        import joblib
        return joblib.load(path)
    except (ImportError, ModuleNotFoundError, Exception):
        return None


def get_model_metadata() -> dict:
    """Return model runtime status and capability inspection interface."""
    artifact = load_artifact()
    has_model = artifact is not None
    
    # Check if numpy and shap are available
    try:
        import numpy  # noqa: F401
        has_numpy = True
    except ImportError:
        has_numpy = False

    try:
        import shap  # noqa: F401
        has_shap = True
    except ImportError:
        has_shap = False

    return {
        "status": "active" if has_model and has_numpy else "rule-based",
        "version": artifact.get("version", get_settings().model_version) if artifact else "rules-v1.0",
        "model_type": type(artifact.get("model")).__name__ if artifact and "model" in artifact else "HeuristicRuleEngine",
        "features": artifact.get("features", FEATURES) if artifact else FEATURES,
        "labels": artifact.get("labels", LABELS) if artifact else LABELS,
        "explainer": "shap-tree" if has_shap else "feature-importance" if has_model else "domain-heuristics",
        "shap_supported": has_shap,
        "is_calibrated": has_model,
        "ready": True,
    }


def _fallback(event: ThermalEvent) -> tuple[str, float, list[str], str]:
    if event.frp_mw >= 100 and event.persistence_score >= 60:
        label = "Industrial Fire"
    elif event.persistence_score >= 70:
        label = "Persistent Thermal Source"
    elif event.frp_mw < 10:
        label = "Possible False Positive"
    else:
        label = "Potential Fire Event"
    indicators = [
        f"FRP {event.frp_mw:.0f} MW",
        f"Persistence score {event.persistence_score:.0f}/100",
        f"Satellite confidence {event.confidence:.0f}%",
    ]
    return label, event.confidence, indicators, "rules-fallback"


def predict(event: ThermalEvent | dict) -> tuple[str, float, list[str], str]:
    if isinstance(event, dict):
        event = ThermalEvent(
            id=event.get("id", "FE-ML-PREDICT"),
            name=event.get("name", "Predict Thermal Event"),
            latitude=float(event.get("latitude", event.get("lat", -15.0))),
            longitude=float(event.get("longitude", event.get("lng", 28.0))),
            frp_mw=float(event.get("frp_mw", event.get("frp", 15.0))),
            brightness_kelvin=float(event.get("brightness_kelvin", event.get("brightness", 320.0))),
            persistence_score=float(event.get("persistence_score", event.get("persistence", 20.0))),
            confidence=float(event.get("confidence", 80.0)),
            observations=int(event.get("observations", 1)),
        )

    artifact = load_artifact()
    if not artifact:
        return _fallback(event)

    try:
        import numpy as np
        vector = _feature_vector(event)
        model = artifact["model"]
        probabilities = model.predict_proba(vector)[0]
        class_index = int(np.argmax(probabilities))
        labels = artifact.get("labels", LABELS)
        label = labels[class_index] if class_index < len(labels) else "Unknown Fire"
        indicators = explain(model, artifact.get("features", FEATURES), vector, class_index)
        return label, round(float(probabilities[class_index]) * 100, 1), indicators, artifact.get("version", "trained-v1")
    except Exception:
        # Graceful fallback if model artifact fails or dependencies missing
        return _fallback(event)


def explain(model: object, features: list[str], vector: object, class_index: int) -> list[str]:
    """Use SHAP when available; model importances are a transparent fallback."""
    try:
        import numpy as np
        import shap
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(vector)
        # Handle list-of-arrays returned for multiclass tree models
        if isinstance(shap_vals, list) and len(shap_vals) > class_index:
            contributions = np.asarray(shap_vals[class_index]).flatten()
        else:
            values = np.asarray(shap_vals)
            if values.ndim == 3:
                # Shape could be (n_samples, n_features, n_classes) or (n_classes, n_samples, n_features)
                if values.shape[0] == len(features):
                    contributions = values[:, 0, class_index].flatten()
                elif values.shape[2] == len(features):
                    contributions = values[0, class_index, :].flatten()
                else:
                    contributions = values[class_index, 0, :].flatten()
            elif values.ndim == 2:
                contributions = values[0].flatten()
            else:
                contributions = values.flatten()
        ranked = np.argsort(np.abs(contributions))[::-1][:3]
        return [f"SHAP: {features[index]} contributed {contributions[index]:+.3f}" for index in ranked if index < len(features)]
    except Exception:
        try:
            import numpy as np
            importance = getattr(model, "feature_importances_", np.zeros(len(features)))
            ranked = np.argsort(importance)[::-1][:3]
            return [f"Model importance: {features[index]} ({importance[index]:.2f})" for index in ranked if index < len(features)]
        except Exception:
            return [f"Feature: {f}" for f in features[:3]]
