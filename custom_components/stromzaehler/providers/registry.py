"""Provider registry for Stromzähler."""

from __future__ import annotations

from dataclasses import dataclass

from ..const import PROVIDER_EAM_NETZ, PROVIDER_NONE


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Describe a configurable electricity provider."""

    provider_id: str
    name: str
    supports_submission: bool = False


GRID_PROVIDERS: dict[str, ProviderDefinition] = {
    PROVIDER_NONE: ProviderDefinition(PROVIDER_NONE, "Keiner"),
    PROVIDER_EAM_NETZ: ProviderDefinition(
        PROVIDER_EAM_NETZ,
        "EAM Netz GmbH",
        supports_submission=True,
    ),
}

ENERGY_SUPPLIERS: dict[str, ProviderDefinition] = {
    PROVIDER_NONE: ProviderDefinition(PROVIDER_NONE, "Keiner"),
}
