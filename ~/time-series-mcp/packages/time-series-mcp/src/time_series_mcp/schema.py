"""Pydantic models for the time-series MCP.

Models are deliberately permissive (extra="ignore") so that older stored
JSON can be read back even if the model has gained fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal["yahoo", "comex", "generic_http", "manual"]
Cadence = Literal["tick", "1m", "5m", "15m", "1h", "4h", "1d", "1w", "1M"]
LinkRefType = Literal["hypothesis", "evidence", "question", "project"]


class SeriesMeta(BaseModel):
    """Metadata for a registered time series."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(description="Stable numeric id (autoincrement).")
    name: str = Field(description="Unique slug-like name, e.g. 'AAPL-close-1d'.")
    metric: str = Field(description="Human metric, e.g. 'close', 'registered_oz'.")
    unit: str = Field(description="Unit string, e.g. 'USD', 'oz', 'pct'.")
    cadence: Cadence = Field(description="Sampling cadence.")
    source_type: SourceType = Field(description="Adapter used to fetch data.")
    source_args: dict = Field(
        default_factory=dict,
        description="Adapter-specific args, e.g. {'symbol': 'AAPL'} or {'path': 'registered'}.",
    )
    description: str = Field(default="", description="Free-form description.")
    created_at: str = Field(description="ISO 8601 UTC timestamp.")
    last_synced_at: str | None = Field(default=None)
    last_value_ts: str | None = Field(default=None)
    last_value: float | None = Field(default=None)


class MetricPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    series_id: int
    ts: str = Field(description="ISO 8601 UTC timestamp of the point.")
    value: float
    meta: dict = Field(default_factory=dict)


class OHLCVBar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    series_id: int
    ts: str = Field(description="ISO 8601 UTC timestamp (bar close time).")
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class SeriesLink(BaseModel):
    model_config = ConfigDict(extra="ignore")

    series_id: int
    project_slug: str
    ref_type: LinkRefType
    ref_id: str
    linked_at: str
