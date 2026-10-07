"""CRS helpers: labelling, and picking a projected CRS suitable for measuring."""
from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS | None) -> str | None:
    """'EPSG:4326' when the CRS has an authority code, otherwise its name."""
    if crs is None:
        return None
    auth = crs.to_authority(min_confidence=70)
    if auth:
        return f"{auth[0]}:{auth[1]}"
    return crs.name


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat); UPS beyond 84N / 80S."""
    if lat > 84:
        return 32661  # UPS North
    if lat < -80:
        return 32761  # UPS South
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst_epsg: int | str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_user_input(dst_epsg), always_xy=True)


def to_crs(geom: BaseGeometry, src: CRS, dst: int | str) -> BaseGeometry:
    return transform(_transformer(src.to_wkt(), dst).transform, geom)


def lonlat_of(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    """A representative (lon, lat) for the geometry, used to choose the projected CRS."""
    pt = geom.representative_point()
    if src.is_geographic and src.equals(WGS84):
        return pt.x, pt.y
    out = to_crs(pt, src, 4326)
    return out.x, out.y


def projected_crs_for(geom: BaseGeometry, src: CRS) -> int:
    lon, lat = lonlat_of(geom, src)
    return utm_epsg(lon, lat)
