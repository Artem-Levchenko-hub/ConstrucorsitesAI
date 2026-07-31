import pytest
from scripts.rotate_encryption_keys import jwt_fernet, strong_fernet


def test_jwt_rotation_changes_ciphertext_and_preserves_plaintext() -> None:
    old = jwt_fernet("old-session-secret")
    new = jwt_fernet("new-session-secret")
    token = old.encrypt(b"github-token")

    rotated = new.encrypt(old.decrypt(token))

    assert rotated != token
    assert new.decrypt(rotated) == b"github-token"


@pytest.mark.parametrize(
    "old_secret,new_secret",
    [
        ("old-strong-secret", "new-strong-secret"),
        (
            "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE=",
        ),
    ],
)
def test_strong_rotation_supports_derived_and_fernet_keys(
    old_secret: str, new_secret: str
) -> None:
    old = strong_fernet(old_secret)
    new = strong_fernet(new_secret)
    token = old.encrypt(b"stored-business-credential")

    rotated = new.encrypt(old.decrypt(token))

    assert new.decrypt(rotated) == b"stored-business-credential"
