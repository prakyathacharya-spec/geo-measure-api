from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error: str | None = None
    warnings: list[str] = []
    created_at: datetime


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    layer: str | None
    geometry_type: str
    crs: str | None
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class MeasurementOut(BaseModel):
    status: str
    type: str | None
    value: float | None
    unit: str | None
    projected_crs: str | None
    message: str | None


class FeatureMeasurementOut(BaseModel):
    index: int
    layer: str | None
    geometry_type: str
    crs: str | None
    measurement: MeasurementOut


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_length_m: float
    by_status: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    results: list[FeatureMeasurementOut]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]
