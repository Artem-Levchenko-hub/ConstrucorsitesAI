
import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services import snapshot_restore as restore


def test_max_restore_is_rejected_before_activation():
    with pytest.raises(ApiError) as caught:
        restore.ensure_restore_supported("max_miniapp")
    assert caught.value.status_code == 409
    assert "Текущая версия сохранена" in caught.value.message

