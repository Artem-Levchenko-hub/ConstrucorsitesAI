"""Surface classifier — the login-waiver detector for the composition gates (16/5d).

Pure function, no browser: every case is a hand-built observation dict shaped
like the ``_AUDIT_JS`` the taste / hierarchy extractors emit. The detector must
be strict in both directions — waive a real sparse login, but never launder a
broken / blank page or a rich landing-with-signup into a waiver.
"""

from omnia_api.services.surface_class import (
    _LOGIN_MAX_AFOLD_TEXTS,
    above_fold_text_count,
    is_login_surface,
)


def _txt(size=16, top=10):
    return {"size": size, "top": top}


def _login_obs(n_texts=8, has_password=True):
    """A sparse centred auth card — heading + a couple of labels + a button.

    Mirrors the live crm-ab7e1d login (8 above-fold text nodes, a password
    field) measured at 1440×900."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "hasPassword": has_password,
        "texts": [_txt(size=24 if i == 0 else 14, top=100 + i * 20) for i in range(n_texts)],
    }


def _rich_landing_obs(n_texts=50, has_password=True):
    """A content-rich landing that *also* embeds a signup form — many above-fold
    nodes. Must NOT be mistaken for a bare login surface."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "hasPassword": has_password,
        "texts": [_txt(size=16, top=100 + i * 12) for i in range(n_texts)],
    }


# ── above_fold_text_count ──────────────────────────────────────────────────────


def test_above_fold_counts_only_first_viewport():
    obs = {
        "viewportHeight": 900,
        "texts": [_txt(top=100), _txt(top=400), _txt(top=1200)],  # last is below fold
    }
    assert above_fold_text_count(obs) == 2


def test_above_fold_ignores_zero_size_nodes():
    obs = {"viewportHeight": 900, "texts": [_txt(size=0, top=100), _txt(top=100)]}
    assert above_fold_text_count(obs) == 1


# ── is_login_surface ───────────────────────────────────────────────────────────


def test_sparse_password_page_is_login():
    assert is_login_surface(_login_obs()) is True


def test_no_password_field_is_never_login():
    # A sparse page with no password input is a broken / blank render, not a
    # login — it must NOT be waived (teeth against empty pages).
    assert is_login_surface(_login_obs(has_password=False)) is False


def test_rich_page_with_password_is_not_login():
    # A landing that embeds a signup form stays rich above the fold — gated.
    assert is_login_surface(_rich_landing_obs()) is False


def test_login_threshold_boundary():
    # At the threshold it is still login; one node over and it is not.
    at = _login_obs(n_texts=_LOGIN_MAX_AFOLD_TEXTS)
    over = _login_obs(n_texts=_LOGIN_MAX_AFOLD_TEXTS + 1)
    assert is_login_surface(at) is True
    assert is_login_surface(over) is False


def test_missing_password_key_defaults_false():
    assert is_login_surface({"texts": [_txt()]}) is False
