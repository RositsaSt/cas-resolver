from cas_resolver.cas import extract_valid_cas_numbers, is_valid_cas_number


def test_accepts_valid_cas_number() -> None:
    assert is_valid_cas_number("67-64-1") is True


def test_rejects_invalid_checksum() -> None:
    assert is_valid_cas_number("67-64-2") is False


def test_rejects_non_cas_text() -> None:
    assert is_valid_cas_number("Acetone") is False


def test_extracts_valid_cas_numbers_from_text_values() -> None:
    values = [
        "Acetone",
        "CAS-67-64-1",
        "Ethanol 64-17-5",
        "Invalid 67-64-2",
    ]

    assert extract_valid_cas_numbers(values) == ["67-64-1", "64-17-5"]


def test_does_not_return_duplicate_cas_numbers() -> None:
    values = ["67-64-1", "CAS 67-64-1", "Acetone: 67-64-1"]

    assert extract_valid_cas_numbers(values) == ["67-64-1"]