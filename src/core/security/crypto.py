import hashlib
import string
from typing import Any

from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.core.config import AppConfig
from src.core.constants import ENCRYPTED_PREFIX
from src.core.utils import json_utils

_cipher_suite = None


def _get_cipher_suite():
    """Lazy initialization of cipher suite to avoid circular imports."""
    global _cipher_suite
    if _cipher_suite is None:
        config = AppConfig.get()
        _cipher_suite = Fernet(config.crypt_key.get_secret_value().encode())
    return _cipher_suite


def encrypt(data: str) -> str:
    return ENCRYPTED_PREFIX + _get_cipher_suite().encrypt(data.encode()).decode()


def decrypt(data: str) -> str:
    return _get_cipher_suite().decrypt(data.removeprefix(ENCRYPTED_PREFIX).encode()).decode()


def get_webhook_hash(webhook_data: dict) -> str:
    return hashlib.sha256(json_utils.bytes_encode(webhook_data)).hexdigest()


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def deep_decrypt(value: Any) -> Any:
    if isinstance(value, str):
        if is_encrypted(value):
            try:
                decrypted = decrypt(value)
                return SecretStr(decrypted)
            except Exception:
                return value
        return value
    if isinstance(value, list):
        return [deep_decrypt(v) for v in value]
    if isinstance(value, dict):
        return {k: deep_decrypt(v) for k, v in value.items()}
    return value


def base62_encode(number: int) -> str:
    alphabet = string.ascii_letters + string.digits

    if number == 0:
        return alphabet[0]

    arr = []
    base = len(alphabet)

    while number:
        number, rem = divmod(number, base)
        arr.append(alphabet[rem])

    arr.reverse()
    return "".join(arr)
