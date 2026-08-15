import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


class LicenseError(ValueError):
    """Raised when the local licence cannot be verified."""


@dataclass(frozen=True)
class LicenseClaims:
    author: str
    issued_to: str


def verify_license(license_path: Path, public_key_path: Path) -> LicenseClaims:
    payload, separator, signature = license_path.read_bytes().partition(b"\n--signature--\n")
    if not separator or not signature:
        raise LicenseError("license signature is missing")

    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as error:
        raise LicenseError("license signature is invalid") from error

    claims = json.loads(payload.decode("utf-8"))
    return LicenseClaims(author=claims["author"], issued_to=claims["issued_to"])
