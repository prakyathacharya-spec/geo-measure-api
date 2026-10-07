"""Measurement calculation. Always done in a local metric projected CRS (never in degrees)."""
from pyproj import CRS
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from app.services.crs import projected_crs_for, to_crs

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
NO_MEASURE_TYPES = {"Point", "MultiPoint"}


def _result(status: str, **kw) -> dict:
    base = {"status": status, "type": None, "value": None, "unit": None,
            "projected_crs": None, "message": None}
    base.update(kw)
    return base


def measure(geom: BaseGeometry | None, src_crs: CRS) -> dict:
    """Return a measurement dict for one geometry. Never raises.

    status: OK | NOT_REQUIRED | UNSUPPORTED | SKIPPED | ERROR
    """
    try:
        if geom is None or geom.is_empty:
            return _result("SKIPPED", message="Missing or empty geometry")

        gtype = geom.geom_type
        if gtype in NO_MEASURE_TYPES:
            return _result("NOT_REQUIRED", message=f"No measurement defined for {gtype}")
        if gtype not in AREA_TYPES | LENGTH_TYPES:
            return _result("UNSUPPORTED", message=f"Measurement not supported for {gtype}")

        epsg = projected_crs_for(geom, src_crs)
        projected = to_crs(geom, src_crs, epsg)

        message = None
        if gtype in AREA_TYPES:
            if not projected.is_valid:
                projected = make_valid(projected)
                message = "Invalid polygon geometry was repaired before measuring"
            return _result("OK", type="area", value=projected.area, unit="m2",
                           projected_crs=f"EPSG:{epsg}", message=message)

        return _result("OK", type="length", value=projected.length, unit="m",
                       projected_crs=f"EPSG:{epsg}")
    except Exception as exc:  # one bad feature must never fail the whole file
        return _result("ERROR", message=f"{type(exc).__name__}: {exc}")
