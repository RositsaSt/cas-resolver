from __future__ import annotations

import httpx

from cas_resolver.pubchem import PubChemClient


def make_client(
    handler: httpx.MockTransport,
    *,
    max_retries: int = 0,
) -> PubChemClient:
    return PubChemClient(
        transport=handler,
        max_retries=max_retries,
        request_delay_seconds=0,
        sleeper=lambda _: None,
    )


def test_resolve_returns_single_valid_cas_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/compound/name/Acetone/cids/JSON"):
            return httpx.Response(
                200,
                json={"IdentifierList": {"CID": [180]}},
            )

        if request.url.path.endswith("/compound/cid/180/synonyms/JSON"):
            return httpx.Response(
                200,
                json={
                    "InformationList": {
                        "Information": [
                            {
                                "CID": 180,
                                "Synonym": [
                                    "Acetone",
                                    "Propanone",
                                    "67-64-1",
                                ],
                            }
                        ]
                    }
                },
            )

        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    with make_client(transport) as client:
        result = client.resolve("Acetone")

    assert result.status == "resolved"
    assert result.cid == 180
    assert result.cas_rn == "67-64-1"
    assert result.candidate_cas_rns == ("67-64-1",)


def test_resolve_returns_not_found_for_unknown_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"Fault": {"Message": "No CID found"}})

    transport = httpx.MockTransport(handler)

    with make_client(transport) as client:
        result = client.resolve("Unknown product")

    assert result.status == "not_found"
    assert result.cid is None
    assert result.cas_rn is None


def test_resolve_reports_multiple_cids_as_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"IdentifierList": {"CID": [100, 200]}},
        )

    transport = httpx.MockTransport(handler)

    with make_client(transport) as client:
        result = client.resolve("Ambiguous material")

    assert result.status == "ambiguous"
    assert result.cid is None
    assert result.cas_rn is None
    assert "multiple CIDs" in result.note


def test_resolve_reports_multiple_cas_numbers_as_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/compound/name/Example/cids/JSON"):
            return httpx.Response(
                200,
                json={"IdentifierList": {"CID": [123]}},
            )

        if request.url.path.endswith("/compound/cid/123/synonyms/JSON"):
            return httpx.Response(
                200,
                json={
                    "InformationList": {
                        "Information": [
                            {
                                "CID": 123,
                                "Synonym": [
                                    "67-64-1",
                                    "64-17-5",
                                ],
                            }
                        ]
                    }
                },
            )

        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)

    with make_client(transport) as client:
        result = client.resolve("Example")

    assert result.status == "ambiguous"
    assert result.cid == 123
    assert result.cas_rn is None
    assert result.candidate_cas_rns == ("67-64-1", "64-17-5")


def test_client_retries_temporary_api_failure() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        if request_count == 1:
            return httpx.Response(503)

        if request.url.path.endswith("/compound/name/Acetone/cids/JSON"):
            return httpx.Response(
                200,
                json={"IdentifierList": {"CID": [180]}},
            )

        return httpx.Response(
            200,
            json={
                "InformationList": {
                    "Information": [
                        {
                            "CID": 180,
                            "Synonym": ["67-64-1"],
                        }
                    ]
                }
            },
        )

    transport = httpx.MockTransport(handler)

    with make_client(transport, max_retries=1) as client:
        result = client.resolve("Acetone")

    assert result.status == "resolved"
    assert request_count == 3