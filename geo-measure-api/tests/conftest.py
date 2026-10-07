import importlib
import io
import zipfile

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    import app.config, app.db, app.models, app.main, app.api.files
    for mod in (app.config, app.db, app.models, app.api.files, app.main):
        importlib.reload(mod)
    with TestClient(app.main.app) as c:
        yield c


def zip_shapefile(gdf, tmp_path, name="data"):
    """Shapefiles hold a single geometry type, so write one .shp per type into the zip."""
    shp_dir = tmp_path / "shp_src"
    shp_dir.mkdir(exist_ok=True)
    for gtype, part in gdf.groupby(gdf.geom_type):
        part.to_file(shp_dir / f"{name}_{gtype.lower()}.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in shp_dir.iterdir():
            zf.write(f, f.name)
    return buf.getvalue()


def sample_gdf(crs="EPSG:4326"):
    # ~1.11 km x ~1.08 km square near Bengaluru, a ~2 km line and a point
    return gpd.GeoDataFrame(
        {"name": ["plot", "road", "tree"]},
        geometry=[
            Polygon([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)]),
            LineString([(77.59, 12.97), (77.60, 12.98)]),
            Point(77.595, 12.975),
        ],
        crs=crs,
    )


KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>plot</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.59,12.97,0 77.60,12.97,0 77.60,12.98,0 77.59,12.98,0 77.59,12.97,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>road</name><LineString><coordinates>77.59,12.97,0 77.60,12.98,0</coordinates></LineString></Placemark>
<Placemark><name>tree</name><Point><coordinates>77.595,12.975,0</coordinates></Point></Placemark>
</Document></kml>"""
