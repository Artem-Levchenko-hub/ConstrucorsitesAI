from omnia_api.core.release import normalize_release_sha


def test_normalize_release_sha_accepts_lower_hex() -> None:
    assert normalize_release_sha("a7c4fc22") == "a7c4fc22"


def test_normalize_release_sha_rejects_unsafe_or_unknown_values() -> None:
    assert normalize_release_sha(None) == "unknown"
    assert normalize_release_sha("A7C4FC22") == "unknown"
    assert normalize_release_sha("a7c4fc22\nSECRET=x") == "unknown"
    assert normalize_release_sha("abc123") == "unknown"
