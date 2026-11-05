"""Local policy for newly chosen passwords; never apply it during login.

The small, deterministic denylist catches high-confidence common/demo choices,
not every breached credential. Matching is normalized, but accepted passwords
are returned byte-for-byte unchanged. Keep the web policy in sync via the shared
password-policy test vectors; neither side transmits passwords to a service.
"""

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError

PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128

_COMMON_WORDS = (
    "password", "passw0rd", "qwerty", "qwertyuiop", "asdfgh", "zxcvbn",
    "admin", "administrator", "letmein", "welcome", "iloveyou", "changeme",
    "temporarypassword", "testpassword", "demopassword", "demo", "test",
    "secret", "omnia", "yleum", "пароль", "йцукен",
)
_COMMON_PHRASES = frozenset({
    "correcthorsebatterystaple", "thisisapassword", "thisismypassword",
    "notasecurepassword", "yourpasswordhere", "pleasechangeme",
    "changethispassword", "changemeplease",
})
_COMMON_VARIANT = re.compile(r"[0-9]*(?:" + "|".join(_COMMON_WORDS) + r")+[0-9]*")
_PADDED_REPEAT = re.compile(r"(.)\1{7,}[0-9]{0,8}", re.DOTALL)
# Unicode White_Space plus BOM, matching the browser's explicit character class.
_SPACING = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_SEPARATORS = re.compile("[" + re.escape(_SPACING) + r"._!@#$%^&*+=?\-]")
_SEQUENCES = (
    "0123456789", "1234567890", "9876543210", "0987654321",
    "12345", "123456", "654321", "abcdef", "abc123",
    "abcdefghijklmnopqrstuvwxyz", "zyxwvutsrqponmlkjihgfedcba",
    "qwertyuiopasdfghjklzxcvbnm", "mnbvcxzlkjhgfdsapoiuytrewq",
)


def new_password_error(password: str) -> str | None:
    """Count Unicode code points; normalize only the private denylist comparison."""
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return "Пароль должен содержать от 15 до 128 символов."
    normalized = unicodedata.normalize("NFKC", password).lower().strip(_SPACING)
    compact = _SEPARATORS.sub("", normalized)
    repeated = any(
        len(normalized) % width == 0
        and normalized == normalized[:width] * (len(normalized) // width)
        for width in range(1, 5)
    )
    if (
        not normalized
        or repeated
        or _PADDED_REPEAT.fullmatch(normalized)
        or compact in _COMMON_PHRASES
        or _COMMON_VARIANT.fullmatch(compact)
        or any(compact and compact in sequence * 14 for sequence in _SEQUENCES)
    ):
        return "Выберите менее распространённый пароль без простых повторов."
    return None


def validate_new_password(password: str) -> str:
    error = new_password_error(password)
    if error:
        raise PydanticCustomError("password_policy", error)
    return password


NewPassword = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH),
    AfterValidator(validate_new_password),
]
