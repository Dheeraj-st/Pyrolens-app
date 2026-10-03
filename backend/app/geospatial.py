"""GeoPandas/Rasterio fire-spread estimate with deterministic no-raster fallback."""
from pathlib import Path

from .schemas import ThermalEvent


def _terrain_sample(path: str | None, longitude: float, latitude: float) -> float | None:
    if not path or not Path(path).exists():
        return None
    try:
        import rasterio
        with rasterio.open(path) as raster:
            value = next(raster.sample([(longitude, latitude)]))[0]
            if raster.nodata is not None and value == raster.nodata:
                return None
            return float(value)
    except Exception:
        return None


def _generate_ellipse_geojson(longitude: float, latitude: float, radius_m: float, wind_kmh: float, direction_degrees: float) -> tuple[dict, float]:
    """Generate a wind-oriented elliptical polygon in GeoJSON format without external C-GIS dependencies."""
    import math

    stretch_factor = 1.0 + min(wind_kmh, 60.0) / 30.0
    semi_major_m = radius_m * stretch_factor
    semi_minor_m = radius_m

    # Downwind angle in radians:
    spread_azimuth = (direction_degrees + 180) % 360
    theta_rad = math.radians(90.0 - spread_azimuth)

    lat_rad = math.radians(latitude)
    m_per_deg_lat = 111132.92
    m_per_deg_lon = 111412.84 * math.cos(lat_rad)
    if abs(m_per_deg_lon) < 1.0:
        m_per_deg_lon = 1.0

    coordinates = []
    num_points = 36
    for i in range(num_points):
        alpha = 2 * math.pi * i / num_points
        x0 = semi_major_m * math.cos(alpha)
        y0 = semi_minor_m * math.sin(alpha)
        xr = x0 * math.cos(theta_rad) - y0 * math.sin(theta_rad)
        yr = x0 * math.sin(theta_rad) + y0 * math.cos(theta_rad)
        center_shift = (semi_major_m - semi_minor_m) * 0.35
        xr += center_shift * math.cos(theta_rad)
        yr += center_shift * math.sin(theta_rad)
        pt_lon = round(longitude + (xr / m_per_deg_lon), 6)
        pt_lat = round(latitude + (yr / m_per_deg_lat), 6)
        coordinates.append([pt_lon, pt_lat])

    coordinates.append(coordinates[0])
    area_km2 = round(math.pi * semi_major_m * semi_minor_m / 1_000_000.0, 2)
    geojson = {
        "type": "Polygon",
        "coordinates": [coordinates],
    }
    return geojson, area_km2


def estimate_spread(event: ThermalEvent, weather: dict, terrain_path: str | None, landcover_path: str | None) -> dict:
    """Create a wind-oriented impact polygon in WGS84 and terrain metadata.

    The geometric estimate is deliberately conservative; it is not a wildfire
    physics simulator. A production model should consume calibrated fuel,
    terrain, and weather forecast rasters.
    """
    wind_kmh = float(weather.get("wind_speed_kmh") or 0)
    direction_degrees = float(weather.get("wind_degrees") or 45)
    base_radius_m = max(250, min(8000, event.frp_mw * 42 + wind_kmh * 70))
    terrain_m = _terrain_sample(terrain_path, event.longitude, event.latitude)
    landcover_code = _terrain_sample(landcover_path, event.longitude, event.latitude)
    terrain_factor = 1.15 if terrain_m is not None and terrain_m > 500 else 1.0
    radius_m = base_radius_m * terrain_factor

    model_name = "geopandas-wind-ellipse-v1"
    try:
        import geopandas as gpd
        from shapely import affinity
        from shapely.geometry import Point, mapping

        point = gpd.GeoSeries([Point(event.longitude, event.latitude)], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]
        zone = point.buffer(radius_m)
        zone = affinity.scale(zone, xfact=1 + min(wind_kmh, 60) / 30, yfact=1, origin=point)
        zone = affinity.rotate(zone, 90 - direction_degrees, origin=point)
        wgs84_zone = gpd.GeoSeries([zone], crs="EPSG:3857").to_crs("EPSG:4326").iloc[0]
        geojson = mapping(wgs84_zone)
        area_km2 = round(zone.area / 1_000_000, 2)
    except Exception:
        # Fallback to pure-Python trigonometry ellipse
        geojson, area_km2 = _generate_ellipse_geojson(
            event.longitude, event.latitude, radius_m, wind_kmh, direction_degrees
        )
        model_name = "pyrolens-wind-ellipse-v1"

    return {
        "impact_zone_km2": area_km2,
        "impact_zone_geojson": geojson,
        "terrain_elevation_m": terrain_m,
        "landcover_code": landcover_code,
        "model": model_name,
    }
