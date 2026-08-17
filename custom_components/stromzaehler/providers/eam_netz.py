"""EAM Netz GmbH meter reading provider."""

from __future__ import annotations

from datetime import date
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .base import MeterReadingProvider, MeterReadingSubmission

AUTH_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/auth"
METER_DATA_GET_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/meterdata/{date}"
METER_DATA_POST_ENDPOINT = "https://enm.mein-portal.de/swp/enm/api/v2/meterdata"
OBIS_IMPORT = "1-0:1.8.0"


class EAMNetzError(Exception):
    """Base error for EAM Netz communication."""


class EAMNetzAuthError(EAMNetzError):
    """Authentication failed."""


class EAMNetzMeterNotFoundError(EAMNetzError):
    """Configured meter could not be found in EAM metadata."""


class EAMNetzRegisterNotFoundError(EAMNetzError):
    """Expected import register could not be found for the meter."""


class EAMNetzProvider(MeterReadingProvider):
    """Submit electricity meter readings to EAM Netz GmbH."""

    def __init__(
        self,
        session: ClientSession,
        contract_account: str,
        meter_number: str,
    ) -> None:
        self._session = session
        self._contract_account = contract_account.strip()
        self._meter_number = meter_number.strip()

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
                payload = await response.json(content_type=None)
        except ClientResponseError as err:
            if 400 <= err.status < 500:
                raise EAMNetzAuthError(
                    "Vertragskontonummer oder Zählernummer wurden von EAM Netz abgelehnt"
                ) from err
            raise EAMNetzError(f"Authentifizierung fehlgeschlagen: {err}") from err
        except (ClientError, ValueError, TypeError) as err:
            raise EAMNetzError(f"Authentifizierung fehlgeschlagen: {err}") from err

        token = payload.get("token") if isinstance(payload, dict) else None
        if not token:
            raise EAMNetzAuthError("Authentifizierung lieferte keinen API-Token")
        return str(token)

    async def _async_meter_metadata(
        self, token: str, reading_date: date
    ) -> dict[str, Any]:
        url = METER_DATA_GET_ENDPOINT.format(date=reading_date.strftime("%Y%m%d"))
        try:
            async with self._session.get(
                url, headers={"X-Apitoken": token}
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (ClientError, ValueError, TypeError) as err:
            raise EAMNetzError(
                f"Zählerdaten konnten nicht abgerufen werden: {err}"
            ) from err

        if not isinstance(payload, dict):
            raise EAMNetzError("EAM Netz lieferte unerwartete Zählerdaten")

        matched_meter = False
        for account in payload.get("contractAccounts", []):
            if not isinstance(account, dict):
                continue
            for contract in account.get("contracts", []):
                if not isinstance(contract, dict):
                    continue
                for meter in contract.get("meters", []):
                    if not isinstance(meter, dict):
                        continue
                    if str(meter.get("number", "")).strip() != self._meter_number:
                        continue
                    matched_meter = True
                    if meter.get("obis") != OBIS_IMPORT:
                        continue
                    metadata = dict(meter)
                    metadata["contract"] = str(contract.get("number", ""))
                    metadata["contractAccount"] = str(account.get("number", ""))
                    return metadata

        if matched_meter:
            raise EAMNetzRegisterNotFoundError(
                "Der EAM-Zähler wurde gefunden, aber das Bezugsregister 1-0:1.8.0 fehlt"
            )
        raise EAMNetzMeterNotFoundError(
            "Die Anmeldung war erfolgreich, aber die Zählernummer wurde in den EAM-Zählerdaten nicht gefunden"
        )

    async def async_validate_credentials(self, reading_date: date) -> None:
        """Validate login data and ensure the expected import register exists."""
        token = await self._async_authenticate()
        await self._async_meter_metadata(token, reading_date)

    @staticmethod
    def _validate_metadata(
        metadata: dict[str, Any], reading_date: date, reading_kwh: float
    ) -> None:
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
                raise EAMNetzError(
                    f"Ablesedatum liegt vor dem zulässigen Datum {boundary.isoformat()}"
                )
            if not lower and reading_date > boundary:
                raise EAMNetzError(
                    f"Ablesedatum liegt nach dem zulässigen Datum {boundary.isoformat()}"
                )

        last_result = metadata.get("lastReadingResult")
        if last_result is not None and reading_kwh < float(last_result):
            raise EAMNetzError(
                f"Lokaler Zählerstand {reading_kwh:.3f} kWh liegt unter dem zuletzt gemeldeten Stand {float(last_result):.3f} kWh"
            )

    async def async_submit_meter_reading(
        self, reading: MeterReadingSubmission
    ) -> None:
        """Authenticate, load metadata and submit the import register."""
        reading_date = reading.timestamp.date()
        token = await self._async_authenticate()
        metadata = await self._async_meter_metadata(token, reading_date)
        self._validate_metadata(metadata, reading_date, reading.import_kwh)

        payload = {
            "meterReadingSaveData": [
                {
                    "contract": metadata.get("contract", ""),
                    "equipmentNumber": metadata.get("equipment"),
                    "meterNumber": self._meter_number,
                    "meterReadingDate": reading_date.strftime("%Y-%m-%d"),
                    "meterReadingNew": str(int(reading.import_kwh)),
                    "register": metadata.get("register"),
                    "division": metadata.get("division"),
                    "registerKind": metadata.get("registerKind"),
                    "contractAccount": metadata.get("contractAccount")
                    or self._contract_account,
                }
            ],
            "phone": "",
            "savePhone": False,
            "sandEmail": "X",
        }

        try:
            async with self._session.post(
                METER_DATA_POST_ENDPOINT,
                headers={"X-Apitoken": token},
                json=payload,
            ) as response:
                response_body = await response.text()
                if response.status >= 400:
                    details = response_body.strip() or response.reason or "keine Antwortdetails"
                    raise EAMNetzError(
                        f"Zählerstand konnte nicht übermittelt werden: HTTP {response.status} {details}"
                    )
        except EAMNetzError:
            raise
        except ClientError as err:
            raise EAMNetzError(
                f"Zählerstand konnte nicht übermittelt werden: {err}"
            ) from err
