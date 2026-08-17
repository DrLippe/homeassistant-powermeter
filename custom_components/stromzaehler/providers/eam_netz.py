"""EAM Netz GmbH meter reading provider."""

from __future__ import annotations

from datetime import date
from typing import Any

from aiohttp import ClientError, ClientSession

from .base import MeterReadingProvider, MeterReadingSubmission

AUTH_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/auth"
METER_DATA_GET_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/meterdata/{date}"
METER_DATA_POST_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/meterdata"
OBIS_IMPORT = "1-0:1.8.0"


class EAMNetzError(Exception):
    """Base error for EAM Netz communication."""


class EAMNetzProvider(MeterReadingProvider):
    """Submit electricity meter readings to EAM Netz GmbH."""

    def __init__(
        self,
        session: ClientSession,
        contract_account: str,
        meter_number: str,
        contract_number: str = "",
    ) -> None:
        self._session = session
        self._contract_account = contract_account
        self._meter_number = meter_number
        self._contract_number = contract_number

    async def _async_authenticate(self) -> str:
        try:
            async with self._session.post(
                AUTH_ENDPOINT,
                json={
                    "contractAccountNumber": self._contract_account,
                    "meterNumber": self._meter_number,
                },
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (ClientError, ValueError) as err:
            raise EAMNetzError(f"Authentifizierung fehlgeschlagen: {err}") from err

        token = payload.get("token") if isinstance(payload, dict) else None
        if not token:
            raise EAMNetzError("Authentifizierung lieferte keinen API-Token")
        return str(token)

    async def _async_meter_metadata(self, token: str, reading_date: date) -> dict[str, Any]:
        url = METER_DATA_GET_ENDPOINT.format(date=reading_date.strftime("%Y%m%d"))
        try:
            async with self._session.get(url, headers={"X-Apitoken": token}) as response:
                response.raise_for_status()
                payload = await response.json()
        except (ClientError, ValueError) as err:
            raise EAMNetzError(f"Zählerdaten konnten nicht abgerufen werden: {err}") from err

        for account in payload.get("contractAccounts", []):
            if str(account.get("number", "")) != self._contract_account:
                continue
            for contract in account.get("contracts", []):
                for meter in contract.get("meters", []):
                    if (
                        str(meter.get("number", "")) == self._meter_number
                        and meter.get("obis") == OBIS_IMPORT
                    ):
                        metadata = dict(meter)
                        metadata["contract"] = str(contract.get("number", ""))
                        return metadata
        raise EAMNetzError("Passendes EAM-Zählwerk 1-0:1.8.0 wurde nicht gefunden")

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any], reading_date: date, reading_kwh: float) -> None:
        if metadata.get("disabled"):
            reason = metadata.get("disabledReason") or "Zählwerk ist deaktiviert"
            raise EAMNetzError(str(reason))

        for key, lower in (("readingDateMin", True), ("readingDateMax", False)):
            raw = metadata.get(key)
            if not raw:
                continue
            try:
                boundary = date.fromisoformat(str(raw))
            except ValueError:
                continue
            if lower and reading_date < boundary:
                raise EAMNetzError(f"Ablesedatum liegt vor dem zulässigen Datum {boundary.isoformat()}")
            if not lower and reading_date > boundary:
                raise EAMNetzError(f"Ablesedatum liegt nach dem zulässigen Datum {boundary.isoformat()}")

        last_result = metadata.get("lastReadingResult")
        if last_result is not None and reading_kwh < float(last_result):
            raise EAMNetzError(
                f"Lokaler Zählerstand {reading_kwh:.3f} kWh liegt unter dem zuletzt gemeldeten Stand {float(last_result):.3f} kWh"
            )

    async def async_submit_meter_reading(self, reading: MeterReadingSubmission) -> None:
        """Authenticate, load metadata and submit the import register."""
        reading_date = reading.timestamp.date()
        token = await self._async_authenticate()
        metadata = await self._async_meter_metadata(token, reading_date)
        self._validate_metadata(metadata, reading_date, reading.import_kwh)

        payload = {
            "contract": self._contract_number or metadata.get("contract", ""),
            "equipmentNumber": metadata.get("equipment"),
            "meterNumber": self._meter_number,
            "meterReadingDate": reading_date.strftime("%Y-%m-%d"),
            "meterReadingNew": int(reading.import_kwh),
            "register": metadata.get("register"),
            "division": metadata.get("division"),
            "registerKind": metadata.get("registerKind"),
            "contractAccount": self._contract_account,
        }

        try:
            async with self._session.post(
                METER_DATA_POST_ENDPOINT,
                headers={"X-Apitoken": token},
                json=payload,
            ) as response:
                response.raise_for_status()
                await response.read()
        except ClientError as err:
            raise EAMNetzError(f"Zählerstand konnte nicht übermittelt werden: {err}") from err
