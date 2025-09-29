from __future__ import annotations

from datetime import date as Date, datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class LinkItem(BaseModel):
    name: str
    url: str
    group: Optional[str] = None
    icon: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app: str
    time_utc: datetime


class AstroQuery(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    date: Optional[Date] = None
    tz_override: Optional[str] = Field(
        default=None, description="IANA TZ to force (e.g., Europe/London)."
    )
    elevation_m: float = Field(
        default=0.0, ge=-430.0, le=9000.0
    )  # Dead Sea to Everest-ish


class SunTimes(BaseModel):
    timezone: str
    date: Date
    dawn: Optional[datetime] = None
    sunrise: Optional[datetime] = None
    solar_noon: Optional[datetime] = None
    sunset: Optional[datetime] = None
    dusk: Optional[datetime] = None
    day_length_seconds: Optional[int] = None
    is_daylight_now: Optional[bool] = None
    civil_dawn: Optional[datetime] = None
    civil_dusk: Optional[datetime] = None
    nautical_dawn: Optional[datetime] = None
    nautical_dusk: Optional[datetime] = None
    astronomical_dawn: Optional[datetime] = None
    astronomical_dusk: Optional[datetime] = None


class MoonInfo(BaseModel):
    phase_day_0_29: int
    phase_name: str
    illumination_fraction_est: float = Field(..., ge=0.0, le=1.0)  # heuristic


class AstroResponse(BaseModel):
    query: AstroQuery
    timezone: str
    now_local: datetime
    sun: SunTimes
    moon: MoonInfo


class LinksResponse(BaseModel):
    links: List[LinkItem]
