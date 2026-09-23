"""«Данных нет» и «проверка сломалась» — разные беды, и путать их нельзя.

23.09.2026 выяснилось, что ночная копия на новом сервере рапортовала об успехе,
не содержа ни базы платформы, ни объектов MinIO. Проверку состава завели, но её
первая версия дала ложный отказ: `grep -q` закрывал поток, распаковка получала
SIGPIPE, и под строгим режимом весь конвейер падал — отчёт винил данные, хотя
сломалась сама проверка.

Это не мелочь формулировки. Оператору эти два случая говорят противоположное:
при нехватке данных копия снята зря и задание надо чинить; при нечитаемом архиве
копия испорчена и восстанавливаться из неё нельзя. Ошибка в любую сторону стоит
либо восстановления из мусора, либо потерянного времени на исправное задание.

Ниже закреплено само различение, а не текст сообщений.
"""

from __future__ import annotations

import gzip
import subprocess
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "backup" / "verify-bundle.sh"

OK, MISSING_DATA, UNREADABLE = 0, 3, 4

_TABLES = ("users", "projects", "snapshots")


def _dump(path: Path, tables: tuple[str, ...] = _TABLES) -> None:
    body = "".join(f"CREATE TABLE public.{name} (id uuid);\n" for name in tables)
    path.write_bytes(gzip.compress(body.encode()))


def _minio(path: Path, repo_count: int) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for index in range(repo_count):
            uuid = f"{index:08d}-0000-4000-8000-000000000000"
            member = tarfile.TarInfo(f"./projects/repos/{uuid}.tar.gz/xl.meta")
            member.size = 0
            tar.addfile(member)


def _run(bundle: Path, live_projects: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), str(bundle), live_projects],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    _dump(tmp_path / "platform-omnia.sql.gz")
    _minio(tmp_path / "minio-data.tgz", repo_count=4)
    return tmp_path


def test_a_complete_bundle_passes(bundle: Path) -> None:
    result = _run(bundle, "4")

    assert result.returncode == OK, result.stderr
    assert "content check OK" in result.stdout


def test_more_projects_than_archived_repos_is_missing_data(bundle: Path) -> None:
    result = _run(bundle, "5")

    assert result.returncode == MISSING_DATA
    assert result.stderr.startswith("missing:")


@pytest.mark.parametrize("absent", _TABLES)
def test_a_dump_without_a_platform_table_is_missing_data(tmp_path: Path, absent: str) -> None:
    _dump(tmp_path / "platform-omnia.sql.gz", tuple(t for t in _TABLES if t != absent))
    _minio(tmp_path / "minio-data.tgz", repo_count=4)

    result = _run(tmp_path, "4")

    assert result.returncode == MISSING_DATA
    assert absent in result.stderr


def test_a_corrupt_dump_is_unreadable_not_missing_data(tmp_path: Path) -> None:
    """Главное различение: нечитаемый архив не объявляется пустой базой."""
    (tmp_path / "platform-omnia.sql.gz").write_bytes(b"this is not gzip at all")
    _minio(tmp_path / "minio-data.tgz", repo_count=4)

    result = _run(tmp_path, "4")

    assert result.returncode == UNREADABLE
    assert result.stderr.startswith("unreadable:")
    assert "no users table" not in result.stderr, "испорченный архив нельзя выдавать за пустую базу"


def test_a_truncated_dump_is_unreadable_not_missing_data(tmp_path: Path) -> None:
    # Обрыв на середине — самый частый вид порчи: начало читается, конца нет.
    body = b"".join(f"CREATE TABLE public.{t} (id uuid);\n".encode() for t in _TABLES)
    whole = gzip.compress(body)
    (tmp_path / "platform-omnia.sql.gz").write_bytes(whole[: len(whole) // 2])
    _minio(tmp_path / "minio-data.tgz", repo_count=4)

    result = _run(tmp_path, "4")

    assert result.returncode == UNREADABLE


def test_a_corrupt_minio_archive_is_unreadable_not_zero_repos(tmp_path: Path) -> None:
    _dump(tmp_path / "platform-omnia.sql.gz")
    (tmp_path / "minio-data.tgz").write_bytes(b"not a tarball")

    result = _run(tmp_path, "4")

    assert result.returncode == UNREADABLE
    assert "holds 0 project repos" not in result.stderr


def test_a_missing_file_is_unreadable_not_silently_ok(tmp_path: Path) -> None:
    _minio(tmp_path / "minio-data.tgz", repo_count=1)

    result = _run(tmp_path, "1")

    assert result.returncode == UNREADABLE


def test_an_unknown_live_count_warns_instead_of_claiming_success(bundle: Path) -> None:
    """Неизвестное число проектов — не доказательство целости.

    Прежний код молча пропускал сверку; теперь это видно в выводе, иначе копию
    без сравнения легко прочитать как проверенную.
    """
    result = _run(bundle, "unknown")

    assert result.returncode == OK
    assert "warning" in result.stdout
    assert "content check OK" not in result.stdout


def test_zero_live_projects_needs_no_repos(tmp_path: Path) -> None:
    _dump(tmp_path / "platform-omnia.sql.gz")
    _minio(tmp_path / "minio-data.tgz", repo_count=0)

    assert _run(tmp_path, "0").returncode == OK


def test_the_check_never_prints_archive_contents(bundle: Path) -> None:
    # В отчёт уходят числа и имена файлов, но не содержимое копии владельцев.
    result = _run(bundle, "5")

    assert "CREATE TABLE" not in result.stdout + result.stderr
    assert "xl.meta" not in result.stdout
