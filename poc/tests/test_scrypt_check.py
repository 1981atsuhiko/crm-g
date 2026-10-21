from __future__ import annotations

import pytest

from crm_poc import settings
from crm_poc.scrypt_check import hash_passcode, measure, peak_memory_bytes, verify_passcode

OTHER_PASSCODE = "other-passcode"


def test_hash_passcode_returns_fixed_length_digest() -> None:
    digest = hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)

    assert len(digest) == settings.SCRYPT_DKLEN


def test_hash_passcode_is_deterministic_for_same_input() -> None:
    first = hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)
    second = hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)

    assert first == second


@pytest.mark.parametrize(
    ("passcode", "expected_result"),
    [
        (settings.DEMO_PASSCODE, True),
        (OTHER_PASSCODE, False),
        ("", False),
    ],
)
def test_verify_passcode(passcode: str, expected_result: bool) -> None:
    expected = hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)

    assert verify_passcode(passcode, settings.DEMO_SALT, expected) is expected_result


def test_peak_memory_bytes_is_positive() -> None:
    assert peak_memory_bytes() > 0


def test_measure_reports_digest_length_and_positive_values() -> None:
    result = measure()

    assert result.digest_length == settings.SCRYPT_DKLEN
    assert result.elapsed_seconds > 0
    assert result.peak_memory_after_bytes >= result.peak_memory_before_bytes
