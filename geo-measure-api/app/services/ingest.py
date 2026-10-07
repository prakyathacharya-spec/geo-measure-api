"""File ingestion: safe unzip -> read with GDAL (via pyogrio) -> normalise features."""
import json
import math
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import numpy as np
import pyogrio
from pyproj import CRS
from shapely.geometry import mapping

from app.config import settings
from app.services.crs import WGS84, crs_label
from app.services.measure import measure

SHAPEFILE_EXTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


class IngestError(Exception):
    """Raised for user-correctable problems with the uploaded file."""


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry_type: str
    geometry: dict | None
    crs: str | None
    properties: dict
    measurement: dict


@dataclass
class ParsedFile:
    crs: str | None
    features: list[ParsedFeature] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def file_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        return "shapefile"
    if suffix == ".kml":
        return "kml"
    raise IngestError("Unsupported file type. Upload a .zip (Shapefile) or a .kml file.")


def safe_extract_shapefiles(zip_path: Path, dest: Path) -> list[Path]:
    """Extract only shapefile components, guarding against zip-slip and zip bombs."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise IngestError("The uploaded file is not a valid zip archive.") from exc

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > settings.max_zip_members:
            raise IngestError("Zip archive contains too many files.")
        if sum(m.file_size for m in members) > settings.max_unzipped_mb * 1024 * 1024:
            raise IngestError("Zip archive is too large when uncompressed.")

        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        for m in members:
            p = PurePosixPath(m.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts:
                raise IngestError("Zip archive contains unsafe paths.")
            if "__MACOSX" in p.parts or p.name.startswith("._"):
                continue
            if p.suffix.lower() not in SHAPEFILE_EXTS:
                continue
            # flatten: keep the path but guarantee it resolves inside dest
            target = (dest / Path(*p.parts)).resolve()
            if root not in target.parents:
                raise IngestError("Zip archive contains unsafe paths.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as out:
                out.write(src.read())

    shps = sorted(dest.rglob("*.shp"))
    if not shps:
        raise IngestError("No .shp file found inside the zip archive.")
    return shps


def _clean(value):
    """Make a pandas/numpy attribute value JSON-safe."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if str(value) in ("NaT", "nan", "<NA>"):
        return None
    return str(value)


def _read_layers(path: Path) -> list[tuple[str, "pyogrio.geopandas.GeoDataFrame"]]:
    try:
        layers = [row[0] for row in pyogrio.list_layers(path)]
    except Exception as exc:
        raise IngestError(f"Could not read geospatial data: {exc}") from exc
    out = []
    for name in layers:
        try:
            gdf = pyogrio.read_dataframe(path, layer=name)
        except Exception as exc:
            raise IngestError(f"Could not read layer '{name}': {exc}") from exc
        if len(gdf):
            out.append((name, gdf))
    return out


def parse_file(path: Path, file_type: str, workdir: Path) -> ParsedFile:
    sources = safe_extract_shapefiles(path, workdir) if file_type == "shapefile" else [path]

    layers: list[tuple[str, object]] = []
    for src in sources:
        layers.extend(_read_layers(src))
    if not layers:
        raise IngestError("The file contains no features.")

    parsed = ParsedFile(crs=None)
    seen_crs: list[str] = []
    index = 0

    for layer_name, gdf in layers:
        crs: CRS | None = gdf.crs
        if crs is None:
            crs = WGS84
            parsed.warnings.append(
                f"Layer '{layer_name}' has no CRS defined; assumed EPSG:4326."
            )
        label = crs_label(crs)
        if label not in seen_crs:
            seen_crs.append(label)

        props_df = gdf.drop(columns=gdf.geometry.name)
        records = props_df.to_dict("records")
        for geom, props in zip(gdf.geometry, records):
            parsed.features.append(
                ParsedFeature(
                    index=index,
                    layer=layer_name,
                    geometry_type=geom.geom_type if geom is not None else "None",
                    geometry=json.loads(json.dumps(mapping(geom))) if geom is not None and not geom.is_empty else None,
                    crs=label,
                    properties={str(k): _clean(v) for k, v in props.items()},
                    measurement=measure(geom, crs),
                )
            )
            index += 1

    parsed.crs = seen_crs[0] if len(seen_crs) == 1 else "MIXED: " + ", ".join(seen_crs)
    return parsed
