"""Base API for future electricity provider submissions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class MeterReadingSubmission:
    """Meter reading payload independent of a concrete provider."""

    timestamp: datetime
    import_kwh: float
    export_kwh: float | None = None
    meter_number: str | None = None


class MeterReadingProvider(ABC):
    """Interface implemented by grid/operator or supplier adapters."""

    @abstractmethod
    async def async_submit_meter_reading(
        self, reading: MeterReadingSubmission
    ) -> None:
        """Submit a meter reading to the external provider."""
