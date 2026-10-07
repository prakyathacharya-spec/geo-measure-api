import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Feature, FileStatus, UploadedFile
from app.schemas import (FeatureMeasurementOut, FeaturesResponse, FeatureOut, FileOut,
                         MeasurementsResponse, MeasurementSummary)
from app.services.ingest import IngestError, file_type_for, parse_file

router = APIRouter(prefix="/api/files", tags=["files"])

CHUNK = 1024 * 1024


def _get_file(session: Session, file_id: str) -> UploadedFile:
    obj = session.get(UploadedFile, file_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return obj


def _get_completed(session: Session, file_id: str) -> UploadedFile:
    obj = _get_file(session, file_id)
    if obj.status != FileStatus.COMPLETED.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File is {obj.status}" + (f": {obj.error}" if obj.error else ""),
        )
    return obj


def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream to disk, enforcing the size limit without loading it all in memory."""
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with open(dest, "wb") as out:
        while chunk := upload.file.read(CHUNK):
            written += len(chunk)
            if written > limit:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"File exceeds the {settings.max_upload_mb} MB limit",
                )
            out.write(chunk)


@router.post("/", response_model=FileOut, status_code=status.HTTP_201_CREATED)
def upload_file(file: UploadFile, session: Session = Depends(get_session)):
    """Upload a zipped Shapefile or a KML; features and measurements are computed immediately."""
    filename = Path(file.filename or "").name
    try:
        file_type = file_type_for(filename)
    except IngestError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    record = UploadedFile(filename=filename, file_type=file_type,
                          status=FileStatus.PROCESSING.value)
    session.add(record)
    session.commit()

    folder = settings.upload_dir / record.id
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / filename

    try:
        try:
            _save_upload(file, stored)
        except HTTPException:
            session.delete(record)
            session.commit()
            shutil.rmtree(folder, ignore_errors=True)
            raise

        parsed = parse_file(stored, file_type, folder / "extracted")
        record.crs = parsed.crs
        record.feature_count = len(parsed.features)
        record.warnings = parsed.warnings
        record.status = FileStatus.COMPLETED.value
        session.add_all(
            Feature(file_id=record.id, index=f.index, layer=f.layer,
                    geometry_type=f.geometry_type, geometry=f.geometry, crs=f.crs,
                    properties=f.properties, measurement=f.measurement)
            for f in parsed.features
        )
        session.commit()
        return record
    except IngestError as exc:
        record.status = FileStatus.FAILED.value
        record.error = str(exc)
        session.commit()
        return JSONResponse(FileOut.model_validate(record).model_dump(mode="json"),
                            status_code=422)
    except HTTPException:
        raise
    except Exception:
        record.status = FileStatus.FAILED.value
        record.error = "Unexpected error while processing the file"
        session.commit()
        raise


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, session: Session = Depends(get_session)):
    return _get_file(session, file_id)


@router.get("/{file_id}/features/", response_model=FeaturesResponse)
def list_features(file_id: str, limit: int = Query(100, ge=1, le=1000),
                  offset: int = Query(0, ge=0), session: Session = Depends(get_session)):
    """Extracted features: id/index, geometry type, geometry (source CRS), CRS, properties."""
    obj = _get_completed(session, file_id)
    rows = session.scalars(select(Feature).where(Feature.file_id == file_id)
                           .order_by(Feature.index).limit(limit).offset(offset)).all()
    return FeaturesResponse(file_id=file_id, total=obj.feature_count, limit=limit,
                            offset=offset,
                            results=[FeatureOut.model_validate(r) for r in rows])


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(file_id: str, limit: int = Query(100, ge=1, le=1000),
                     offset: int = Query(0, ge=0), session: Session = Depends(get_session)):
    obj = _get_completed(session, file_id)
    rows = session.scalars(select(Feature).where(Feature.file_id == file_id)
                           .order_by(Feature.index).limit(limit).offset(offset)).all()

    # Summary is computed over the whole file, not just the requested page.
    summary = MeasurementSummary(total_area_m2=0.0, total_length_m=0.0, by_status={})
    for (m,) in session.execute(select(Feature.measurement).where(Feature.file_id == file_id)):
        summary.by_status[m["status"]] = summary.by_status.get(m["status"], 0) + 1
        if m["status"] == "OK" and m["type"] == "area":
            summary.total_area_m2 += m["value"]
        elif m["status"] == "OK" and m["type"] == "length":
            summary.total_length_m += m["value"]

    return MeasurementsResponse(
        file_id=file_id, total=obj.feature_count, limit=limit, offset=offset,
        summary=summary,
        results=[FeatureMeasurementOut(index=r.index, layer=r.layer,
                                       geometry_type=r.geometry_type, crs=r.crs,
                                       measurement=r.measurement) for r in rows],
    )
