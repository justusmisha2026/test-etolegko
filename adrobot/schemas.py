from typing import Annotated
from uuid import UUID

import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveId = Annotated[int, Field(strict=True, gt=0)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignInput(InputModel):
    operation_id: UUID
    name: str = Field(min_length=1, max_length=150)
    country: str
    offer_id: PositiveId
    domain_id: PositiveId
    group_id: PositiveId
    traffic_source_id: PositiveId

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        code = value.upper()
        if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
            raise ValueError("Нужен код страны ISO 3166-1 alpha-2, например AU")
        return code


class OffersInput(InputModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    offer_ids: list[PositiveId] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_distribution(self) -> "OffersInput":
        if len(set(self.offer_ids)) != len(self.offer_ids):
            raise ValueError("Оффер может встречаться в потоке только один раз")
        return self
