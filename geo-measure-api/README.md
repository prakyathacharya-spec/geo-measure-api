# Geospatial File Measurement API

A FastAPI service that accepts a **zipped Shapefile** or a **KML**, extracts every feature
(ID, geometry type, geometry, CRS, properties) and returns **area** (polygons) and **length**
(lines), always computed in a metric projected CRS, never in degrees.

## Setup

Requires Python 3.10+ (wheels for GDAL/PROJ/GEOS are bundled via `pyogrio`, `pyproj`, `shapely`; no system GDAL needed).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000  (Swagger UI at /docs)
pytest                                  # run the tests
```

Docker alternative:

```bash
docker build -t geo-measure . && docker run -p 8000:8000 -v geo-data:/data geo-measure
```

Config (env vars): `GEO_DATA_DIR` (default `./data`; holds the SQLite DB and uploads),
`GEO_MAX_UPLOAD_MB` (50), `GEO_MAX_UNZIPPED_MB` (300), `GEO_MAX_ZIP_MEMBERS` (500).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/files/` | Upload `.zip` (Shapefile) or `.kml` as multipart field `file` |
| GET | `/api/files/{id}/` | File info and processing status |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements + file-level summary |
| GET | `/api/files/{id}/features/` | Extracted features: geometry (GeoJSON, source CRS), CRS, properties |
| GET | `/health` | Liveness |

The last two list endpoints accept `?limit=` (1-1000, default 100) and `?offset=`.

### Upload

```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```
```json
{
  "id": "b7cb0330996f4a858ed0c1358d257283",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": null,
  "warnings": [],
  "created_at": "2026-10-07T07:21:22.903077Z"
}
```

Status codes: `201` ok · `415` wrong extension · `413` over size limit ·
`422` unreadable/corrupt content (the record is kept with `status: "FAILED"` and an `error` message).

### Measurements

```bash
curl http://localhost:8000/api/files/{id}/measurements/
```
```json
{
  "file_id": "b7cb...", "total": 3, "limit": 100, "offset": 0,
  "summary": {
    "total_area_m2": 1201683.92,
    "total_length_m": 1550.43,
    "by_status": {"OK": 2, "NOT_REQUIRED": 1}
  },
  "results": [
    {"index": 0, "layer": "survey", "geometry_type": "Polygon", "crs": "EPSG:4326",
     "measurement": {"status": "OK", "type": "area", "value": 1201683.92, "unit": "m2",
                     "projected_crs": "EPSG:32643", "message": null}},
    {"index": 1, "layer": "survey", "geometry_type": "LineString", "crs": "EPSG:4326",
     "measurement": {"status": "OK", "type": "length", "value": 1550.43, "unit": "m",
                     "projected_crs": "EPSG:32643", "message": null}},
    {"index": 2, "layer": "survey", "geometry_type": "Point", "crs": "EPSG:4326",
     "measurement": {"status": "NOT_REQUIRED", "type": null, "value": null, "unit": null,
                     "projected_crs": null, "message": "No measurement defined for Point"}}
  ]
}
```

`measurement.status` is one of `OK`, `NOT_REQUIRED` (points), `UNSUPPORTED` (e.g. GeometryCollection),
`SKIPPED` (null/empty geometry) or `ERROR` (unexpected failure on that one feature). The file
as a whole never fails because of a single bad feature. Requesting measurements/features of a
file that is not `COMPLETED` returns `409`; an unknown id returns `404`.

## Architecture

```
app/
  main.py            FastAPI app, lifespan (creates tables)
  config.py          env-based settings
  db.py, models.py   SQLAlchemy 2.0 + SQLite: UploadedFile (1) -> Feature (N)
  schemas.py         Pydantic response models
  api/files.py       HTTP layer only: validation, persistence, status codes
  services/
    ingest.py        safe unzip, read via GDAL, normalise features
    crs.py           CRS labels, UTM zone selection, transformations
    measure.py       area/length calculation, graceful failure handling
tests/               API tests + accuracy check against geodesic ground truth
```

**File-processing flow.** `POST /api/files/` → extension check → stream to disk with a size cap →
create `UploadedFile(status=PROCESSING)` → (zip only) extract just the shapefile components with
zip-slip / zip-bomb guards → read every `.shp` / every KML layer with `pyogrio` →
for each feature build `{index, layer, geometry_type, GeoJSON geometry, crs, properties}` →
compute its measurement → bulk-insert features, set `COMPLETED`. Any `IngestError` sets `FAILED`
with a readable message. Attribute values are made JSON-safe (NaN/NaT → `null`, numpy → Python, dates → ISO).

**Measurement flow.** For each geometry: empty → `SKIPPED`; Point/MultiPoint → `NOT_REQUIRED`;
Polygon/MultiPolygon → area; LineString/MultiLineString → length; anything else → `UNSUPPORTED`.
Measured values are computed once at upload time and stored, so reads are cheap.

**CRS handling.** Regardless of the source CRS (geographic *or* projected), each feature is measured like this:
1. Take a representative point of the geometry and convert it to lon/lat (WGS84).
2. Pick the **UTM zone** for that point (`326xx` north / `327xx` south; UPS poles beyond 84°N / 80°S).
3. Transform the geometry to that UTM CRS (`pyproj`, `always_xy=True`) and use Shapely's planar `area` / `length`, which are in square metres / metres.

The chosen CRS is returned per feature as `projected_crs`. If a Shapefile has no `.prj`, EPSG:4326 is assumed and a warning is added to the file info. Mixed-CRS zips report `"MIXED: ..."` at file level while each feature keeps its own CRS.

## Design decisions

- **FastAPI over Django/DRF.** The service is a thin API with no admin/auth needs; FastAPI gives typed schemas, validation and OpenAPI docs for free.
- **Local UTM per feature, instead of one CRS per file.** Files can span several zones; per-feature selection keeps distortion low (typically well under 0.1%) without any user input. Alternatives considered: a single equal-area CRS (e.g. EPSG:6933) — simple, but larger distortion for lengths; geodesic calculation on the ellipsoid (`pyproj.Geod`) — the most accurate, but the brief asks for projected measurements, so I use it only as ground truth in tests (area within 0.5% of geodesic).
- **Always reproject, even for projected inputs.** Web Mercator (EPSG:3857) areas are badly inflated away from the equator, and some projected CRSs use feet. Re-projecting everything to UTM yields metres and one consistent method.
- **GDAL via `pyogrio` rather than pure-Python parsers.** One reader for both formats, handles encodings and all KML quirks. Chosen over `fiona` (slower, heavier install) and hand-written KML parsing.
- **SQLite + SQLAlchemy.** Zero-setup for local runs, but the ORM layer makes a move to PostgreSQL/PostGIS a config change. Features and measurements are stored as JSON.
- **Synchronous processing.** Simple and deterministic for files up to the size cap; the `status` field and the 409 behaviour already model an async lifecycle (see future scope).
- **Defensive handling.** Size limits, zip-slip and zip-bomb checks, only shapefile extensions are extracted, invalid polygons are repaired with `make_valid` (and flagged in `message`), per-feature error isolation.
- **Measurement semantics.** Lengths/areas are planar 2D in the projected CRS (KML altitude is ignored); polygon holes are subtracted; Multi* geometries are summed.

## Learnings

- A Shapefile holds exactly one geometry type, so mixed data arrives as several `.shp` files in one zip. This is why the reader treats each as a layer.
- KML is always WGS84 and GDAL exposes Folders/geometry kinds as separate layers, so feature indexes must be assigned across layers.
- Projected CRS ≠ safe for measurement. "Projected" only means flat; Web Mercator in particular is not area-preserving.
- Validating against `pyproj.Geod` made it easy to test CRS handling objectively.
- Dependencies matter: wheels that bundle GDAL avoid the usual system-library setup pain.

## Future scope

- Background processing (Celery/RQ or FastAPI tasks) with polling/webhooks for large files; chunked/resumable uploads.
- PostGIS storage with spatial indexes, bbox filters and GeoJSON `FeatureCollection` output.
- Geodesic measurement option (`?method=geodesic`) and handling of features crossing UTM zone borders or the antimeridian.
- More formats: KMZ, GeoJSON, GeoPackage; Shapefile `.zip` with nested folders; reprojected-to-WGS84 geometry output.
- Perimeter for polygons, 3D length using elevation, unit selection (ha, km², ft, acres).
- Auth, per-user quotas, file deletion/retention endpoints, structured logging and metrics, CI with coverage.
