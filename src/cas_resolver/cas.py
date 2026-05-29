from __future__ import annotations

import re
from collections.abc import Iterable

# CAS Registry Number format: 2-7 digits, hyphen, 2 digits, hyphen, 1 digit
CAS_PATTERN = re.compile(r"(?<!\d)(\d{2,7}-\d{2}-\d)(?!\d)")


def is_valid_cas_number(value: str) -> bool:
    """Return True when value has CAS RN format and a valid checksum."""
    if CAS_PATTERN.fullmatch(value) is None:
        return False

    digits = value.replace("-", "")
    check_digit = int(digits[-1])
    body_digits = digits[:-1]

    checksum = sum(
        int(digit) * multiplier
        for multiplier, digit in enumerate(reversed(body_digits), start=1)
    )

    return checksum % 10 == check_digit

def extract_valid_cas_numbers(values: Iterable[str]) -> list[str]:
    """Extract unique, checksum-valid CAS Registry Numbers from text values."""
    valid_numbers: list[str] = [] # To preserve the order of first occurrence
    seen_numbers: set[str] = set() # To track seen numbers for uniqueness faster than list lookup

    for value in values:
        for match in CAS_PATTERN.findall(value):
            if is_valid_cas_number(match) and match not in seen_numbers:
                valid_numbers.append(match)
                seen_numbers.add(match)

    return valid_numbers