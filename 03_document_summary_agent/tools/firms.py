"""Look up a firm's profile in the database (`data/firms.jsonl` and `data/third_party_providers.jsonl`)."""
from pydantic import BaseModel, Field
from utils import db

from .third_party_providers import ThirdPartyProvider, get_third_party_provider


class FirmProvider(ThirdPartyProvider):
    """A third-party provider, with the firm's own contract details for it."""

    contract_end: str = Field(description="When the firm's contract with the provider ends, as written in the data")
    material: bool = Field(description="Whether the firm treats the provider as material")


class Firm(BaseModel):
    """A firm's profile, with its third-party providers joined in."""

    firm_id: str = Field(description="The firm's ID, for example 'FIRM-001'")
    name: str = Field(description="The firm's name")
    type: str = Field(description="The kind of firm, for example 'retail bank'")
    customers: int = Field(description="The number of customers")
    total_assets: str = Field(description="Total assets, as written in the data")
    operational_resilience_senior_manager: str | None = Field(
        description="The senior manager named for operational resilience, or null if none yet"
    )
    notes: str = Field(description="Free-text notes about the firm")
    third_party_providers: list[FirmProvider] = Field(description="The firm's third-party providers")

def get_firm_profile(firm_id: str) -> dict:
    """The profile of a firm, for example "FIRM-001", as a JSON object, used to say what a document means for it."""
    row = db.get("firms", firm_id=firm_id)
    if row is None:
        raise ValueError(f"There is no firm {firm_id}.")

    providers = [
        FirmProvider(
            **get_third_party_provider(link["provider_id"]).model_dump(),
            contract_end=link["contract_end"],
            material=link["material"],
        )
        for link in row["providers"]
    ]
    firm = Firm(**{key: value for key, value in row.items() if key != "providers"}, third_party_providers=providers)
    return firm.model_dump()
