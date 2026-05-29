from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal
from urllib.parse import quote

import httpx

from cas_resolver.cas import extract_valid_cas_numbers


BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

ResolutionStatus = Literal[
    "resolved",
    "not_found",
    "ambiguous",
    "no_cas_found",
    "api_error",
]


@dataclass(frozen=True)
class ResolutionResult:
    """Result of resolving one submitted chemical name through PubChem."""

    input_name: str
    cid: int | None
    cas_rn: str | None
    status: ResolutionStatus
    note: str = ""
    candidate_cas_rns: tuple[str, ...] = ()


class PubChemAPIError(RuntimeError):
    """Raised when PubChem cannot be queried or returns unexpected data."""


class PubChemClient:
    """Resolve chemical names to CAS Registry Numbers through PubChem PUG REST."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        request_delay_seconds: float = 0.25,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")

        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must not be negative")

        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "cas-resolver/0.1"},
            transport=transport,
        )
        self._max_retries = max_retries
        self._request_delay_seconds = request_delay_seconds
        self._sleeper = sleeper
        self._clock = clock
        self._last_request_time: float | None = None

    def __enter__(self) -> PubChemClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def resolve(self, name: str) -> ResolutionResult:
        """Resolve one submitted chemical name to a single CAS RN when possible."""
        submitted_name = name.strip()

        if not submitted_name:
            return ResolutionResult(
                input_name=name,
                cid=None,
                cas_rn=None,
                status="not_found",
                note="Chemical name is empty.",
            )

        try:
            cids = self._get_cids(submitted_name)

            if not cids:
                return ResolutionResult(
                    input_name=name,
                    cid=None,
                    cas_rn=None,
                    status="not_found",
                    note="PubChem returned no compound match for the submitted name.",
                )

            if len(cids) > 1:
                return ResolutionResult(
                    input_name=name,
                    cid=None,
                    cas_rn=None,
                    status="ambiguous",
                    note=f"PubChem returned multiple CIDs: {cids}.",
                )

            cid = cids[0]
            synonyms = self._get_synonyms(cid)
            cas_numbers = extract_valid_cas_numbers(synonyms)

            if not cas_numbers:
                return ResolutionResult(
                    input_name=name,
                    cid=cid,
                    cas_rn=None,
                    status="no_cas_found",
                    note="A PubChem CID was found, but no valid CAS RN was extracted.",
                )

            if len(cas_numbers) > 1:
                return ResolutionResult(
                    input_name=name,
                    cid=cid,
                    cas_rn=None,
                    status="ambiguous",
                    note="Multiple valid CAS RN candidates were found.",
                    candidate_cas_rns=tuple(cas_numbers),
                )

            return ResolutionResult(
                input_name=name,
                cid=cid,
                cas_rn=cas_numbers[0],
                status="resolved",
                candidate_cas_rns=tuple(cas_numbers),
            )

        except PubChemAPIError as exc:
            return ResolutionResult(
                input_name=name,
                cid=None,
                cas_rn=None,
                status="api_error",
                note=str(exc),
            )

    def _get_cids(self, name: str) -> list[int]:
        encoded_name = quote(name, safe="")
        data = self._request_json(f"/compound/name/{encoded_name}/cids/JSON")

        if data is None:
            return []

        try:
            raw_cids = data["IdentifierList"]["CID"]
            return [int(cid) for cid in raw_cids]
        except (KeyError, TypeError, ValueError) as exc:
            raise PubChemAPIError(
                "PubChem returned an unexpected CID response structure."
            ) from exc

    def _get_synonyms(self, cid: int) -> list[str]:
        data = self._request_json(f"/compound/cid/{cid}/synonyms/JSON")

        if data is None:
            return []

        try:
            information = data["InformationList"]["Information"][0]
            synonyms = information.get("Synonym", [])
        except (KeyError, IndexError, TypeError) as exc:
            raise PubChemAPIError(
                "PubChem returned an unexpected synonym response structure."
            ) from exc

        if not isinstance(synonyms, list) or not all(
            isinstance(value, str) for value in synonyms
        ):
            raise PubChemAPIError(
                "PubChem returned synonyms in an unexpected format."
            )

        return synonyms

    def _request_json(self, path: str) -> dict[str, Any] | None:
        last_error = "Unknown PubChem request error."

        for attempt in range(self._max_retries + 1):
            self._throttle_requests()

            try:
                response = self._client.get(path)
            except httpx.RequestError as exc:
                last_error = f"PubChem request failed: {exc}."
            else:
                if response.status_code == 404:
                    return None

                if response.status_code not in RETRYABLE_STATUS_CODES:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise PubChemAPIError(
                            f"PubChem returned HTTP {response.status_code}."
                        ) from exc

                    try:
                        return response.json()
                    except ValueError as exc:
                        raise PubChemAPIError(
                            "PubChem returned invalid JSON."
                        ) from exc

                last_error = (
                    f"PubChem returned retryable HTTP {response.status_code}."
                )

            if attempt < self._max_retries:
                retry_delay_seconds = 2**attempt
                self._sleeper(retry_delay_seconds)

        raise PubChemAPIError(last_error)

    def _throttle_requests(self) -> None:
        if self._request_delay_seconds == 0:
            return

        now = self._clock()

        if self._last_request_time is not None:
            elapsed_seconds = now - self._last_request_time
            remaining_delay = self._request_delay_seconds - elapsed_seconds

            if remaining_delay > 0:
                self._sleeper(remaining_delay)

        self._last_request_time = self._clock()