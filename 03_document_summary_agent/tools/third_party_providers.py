"""Look up a third-party provider in the database (`data/third_party_providers.jsonl`)."""
from pydantic import BaseModel, Field
from utils import db


class ThirdPartyProvider(BaseModel):
    """A technology or service provider that firms rely on."""

    provider_id: str = Field(description="The provider's ID, for example 'PROVIDER-001'")
    name: str = Field(description="The provider's name")
    service: str = Field(description="The service it provides, for example 'card processing'")

def get_third_party_provider(provider_id: str) -> ThirdPartyProvider:
    """The provider's ID, name and service, for example for "PROVIDER-001"."""
    row = db.get("third_party_providers", provider_id=provider_id)
    if row is None:
        raise ValueError(f"There is no provider {provider_id}.")
    return ThirdPartyProvider(**row)
