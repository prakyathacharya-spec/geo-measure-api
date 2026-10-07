import pytest
from pyproj import Geod
from shapely.geometry import LineString, Polygon

from app.services.crs import utm_epsg
from app.services.measure import measure
from pyproj import CRS
from tests.conftest import KML, sample_gdf, zip_shapefile

GEOD = Geod(ellps="WGS84")
POLY = Polygon([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)])
LINE = LineString([(77.59, 12.97), (77.60, 12.98)])


def upload(client, name, data):
    return client.post("/api/files/", files={"file": (name, data)})


def by_geom(results):
    return {r["geometry_type"]: r["measurement"] for r in results}


def test_kml_upload_and_measurements(client):
    r = upload(client, "survey.kml", KML)
    assert r.status_code == 201
    info = r.json()
    assert info["status"] == "COMPLETED" and info["feature_count"] == 3
    assert info["crs"] == "EPSG:4326"

    got = client.get(f"/api/files/{info['id']}/").json()
    assert got["filename"] == "survey.kml"

    res = client.get(f"/api/files/{info['id']}/measurements/").json()
    m = by_geom(res["results"])
    true_area = abs(GEOD.geometry_area_perimeter(POLY)[0])
    true_len = GEOD.geometry_length(LINE)
    assert m["Polygon"]["value"] == pytest.approx(true_area, rel=0.005)
    assert m["Polygon"]["unit"] == "m2"
    assert m["LineString"]["value"] == pytest.approx(true_len, rel=0.005)
    assert m["Point"]["status"] == "NOT_REQUIRED"
    assert m["Polygon"]["projected_crs"] == "EPSG:32643"


def test_shapefile_upload(client, tmp_path):
    r = upload(client, "plots.zip", zip_shapefile(sample_gdf(), tmp_path))
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    feats = client.get(f"/api/files/{fid}/features/").json()
    assert feats["total"] == 3
    assert {f["properties"]["name"] for f in feats["results"]} == {"plot", "road", "tree"}
    assert feats["results"][0]["crs"] == "EPSG:4326"
    s = client.get(f"/api/files/{fid}/measurements/").json()["summary"]
    assert s["total_area_m2"] > 0 and s["total_length_m"] > 0


def test_projected_source_crs_gives_same_area(client, tmp_path):
    a = upload(client, "a.zip", zip_shapefile(sample_gdf(), tmp_path)).json()["id"]
    b = upload(client, "b.zip", zip_shapefile(sample_gdf().to_crs(3857), tmp_path)).json()["id"]
    ma = by_geom(client.get(f"/api/files/{a}/measurements/").json()["results"])
    mb = by_geom(client.get(f"/api/files/{b}/measurements/").json()["results"])
    assert mb["Polygon"]["value"] == pytest.approx(ma["Polygon"]["value"], rel=1e-4)


def test_unsupported_extension(client):
    assert upload(client, "x.txt", b"hi").status_code == 415


def test_bad_zip_is_failed_not_crash(client):
    r = upload(client, "bad.zip", b"not a zip")
    assert r.status_code == 422 and r.json()["status"] == "FAILED"
    fid = r.json()["id"]
    assert client.get(f"/api/files/{fid}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{fid}/measurements/").status_code == 409


def test_unknown_id(client):
    assert client.get("/api/files/nope/").status_code == 404


def test_pagination(client):
    fid = upload(client, "s.kml", KML).json()["id"]
    r = client.get(f"/api/files/{fid}/measurements/?limit=1&offset=1").json()
    assert len(r["results"]) == 1 and r["results"][0]["index"] == 1 and r["total"] == 3


def test_unsupported_geometry_is_graceful():
    from shapely.geometry import GeometryCollection, Point
    gc = GeometryCollection([Point(0, 0), LINE])
    assert measure(gc, CRS.from_epsg(4326))["status"] == "UNSUPPORTED"
    assert measure(None, CRS.from_epsg(4326))["status"] == "SKIPPED"


def test_invalid_polygon_repaired():
    bowtie = Polygon([(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98)])
    m = measure(bowtie, CRS.from_epsg(4326))
    assert m["status"] == "OK" and m["value"] > 0 and "repaired" in m["message"]


def test_utm_zone_selection():
    assert utm_epsg(77.59, 12.97) == 32643
    assert utm_epsg(-73.9, 40.7) == 32618
    assert utm_epsg(151.2, -33.9) == 32756


def test_zip_slip_rejected(client):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.shp", b"x")
    r = upload(client, "evil.zip", buf.getvalue())
    assert r.status_code == 422 and "unsafe" in r.json()["error"]
