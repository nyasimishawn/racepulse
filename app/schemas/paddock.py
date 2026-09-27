from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

NewsCategory = Literal[
    "WEEKEND", "TYRES", "DRIVER_MARKET", "TECHNICAL", "GENERAL"
]
Compound = Literal["C1", "C2", "C3", "C4", "C5", "C6"]


class TyreNomination(BaseModel):
    hard: Compound
    medium: Compound
    soft: Compound

    @model_validator(mode="after")
    def ordered(self):
        if not int(self.hard[1:]) < int(self.medium[1:]) < int(self.soft[1:]):
            raise ValueError("Compounds must increase from hard to soft.")
        return self


class DriverMarketUpdate(BaseModel):
    driver_name: str = Field(min_length=1, max_length=150)
    team_name: str = Field(min_length=1, max_length=150)
    season: int = Field(ge=2018, le=2100)
    status: Literal["CONFIRMED", "REPORTED", "RUMOUR"]


class PaddockContext(BaseModel):
    category: NewsCategory = "GENERAL"
    evidence: Literal["OFFICIAL", "REPORTED", "ANALYSIS", "UNVERIFIED"] = (
        "UNVERIFIED"
    )
    summary: str = Field(default="", max_length=600)
    why_it_matters: str = Field(default="", max_length=1500)
    correction_note: str | None = Field(default=None, max_length=1500)
    calendar_weekend_id: UUID | None = None
    tyres: TyreNomination | None = None
    driver_market: DriverMarketUpdate | None = None

    @model_validator(mode="after")
    def associations(self):
        if self.tyres and (
            self.category != "TYRES" or not self.calendar_weekend_id
        ):
            raise ValueError(
                "Tyre nominations need a TYRES category and calendar weekend."
            )
        if self.driver_market:
            if self.category != "DRIVER_MARKET":
                raise ValueError(
                    "Driver moves need the DRIVER_MARKET category."
                )
            if (
                self.driver_market.status == "CONFIRMED"
                and self.evidence != "OFFICIAL"
            ):
                raise ValueError(
                    "Confirmed seats require an official announcement."
                )
        return self
