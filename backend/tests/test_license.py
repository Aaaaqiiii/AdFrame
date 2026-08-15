from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.license import LicenseError, verify_license
from app.core.logging import SecretRedactingFilter


def _write_signed_license(tmp_path: Path, payload: bytes) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    license_path = tmp_path / "license.json"
    public_key_path = tmp_path / "license-public.pem"
    license_path.write_bytes(payload + b"\n--signature--\n" + private_key.sign(payload))
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return license_path, public_key_path


def test_verify_license_returns_hidden_authorship_claim(tmp_path: Path) -> None:
    license_path, public_key_path = _write_signed_license(
        tmp_path,
        '{"author":"王嘉祺","issued_to":"local-windows"}'.encode(),
    )

    claims = verify_license(license_path, public_key_path)

    assert claims.author == "王嘉祺"
    assert claims.issued_to == "local-windows"


def test_verify_license_rejects_changed_payload(tmp_path: Path) -> None:
    license_path, public_key_path = _write_signed_license(
        tmp_path,
        '{"author":"王嘉祺","issued_to":"local-windows"}'.encode(),
    )
    license_path.write_bytes(b'{"author":"someone else"}\n--signature--\n' + license_path.read_bytes().split(b"\n--signature--\n")[1])

    with pytest.raises(LicenseError, match="signature"):
        verify_license(license_path, public_key_path)


def test_log_filter_redacts_provider_secrets() -> None:
    import logging
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "Authorization: Bearer abc123?token=xyz", (), None)

    SecretRedactingFilter().filter(record)

    assert "abc123" not in record.msg
