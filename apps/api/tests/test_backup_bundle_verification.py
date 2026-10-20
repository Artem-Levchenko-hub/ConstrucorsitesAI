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
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "backup" / "verify-bundle.sh"
_BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"

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


def _globals(directory: Path) -> None:
    for name in ("platform-globals.sql.gz", "projects-globals.sql.gz"):
        (directory / name).write_bytes(gzip.compress(b"CREATE ROLE backup_restore_probe;\n"))


def _run(bundle: Path, live_projects: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        # Windows' MSYS argv bridge truncates literal newlines. An environment
        # handoff lets the real Bash script receive the complete malformed value.
        [_BASH, "-c", 'exec bash "$VERIFY_BUNDLE_SCRIPT" . "$VERIFY_LIVE_COUNT"'],
        cwd=bundle,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "MSYS_NO_PATHCONV": "1",
            "MSYS2_ARG_CONV_EXCL": "*",
            "VERIFY_BUNDLE_SCRIPT": _SCRIPT.as_posix(),
            "VERIFY_LIVE_COUNT": live_projects,
        },
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    _dump(tmp_path / "platform-omnia.sql.gz")
    _minio(tmp_path / "minio-data.tgz", repo_count=4)
    _globals(tmp_path)
    return tmp_path


def test_a_complete_bundle_passes(bundle: Path) -> None:
    result = _run(bundle, "4")

    assert result.returncode == OK, result.stderr
    assert "content check OK" in result.stdout


@pytest.mark.parametrize("name", ["platform-globals.sql.gz", "projects-globals.sql.gz"])
def test_cluster_roles_are_required_for_offline_restore(bundle: Path, name: str) -> None:
    (bundle / name).unlink()
    result = _run(bundle, "4")
    assert result.returncode == MISSING_DATA
    assert name in result.stderr


def test_corrupted_cluster_roles_are_unreadable(bundle: Path) -> None:
    (bundle / "platform-globals.sql.gz").write_bytes(b"truncated roles dump")
    result = _run(bundle, "4")
    assert result.returncode == UNREADABLE


def test_empty_cluster_roles_are_incomplete(bundle: Path) -> None:
    (bundle / "projects-globals.sql.gz").write_bytes(gzip.compress(b""))
    result = _run(bundle, "4")
    assert result.returncode == MISSING_DATA


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


def test_an_unknown_live_count_cannot_claim_verified_coverage(bundle: Path) -> None:
    """Неизвестное число проектов — не доказательство целости.

    Прежний код молча пропускал сверку; теперь это видно в выводе, иначе копию
    без сравнения легко прочитать как проверенную.
    """
    result = _run(bundle, "unknown")

    assert result.returncode == UNREADABLE
    assert "unconfirmed" in result.stderr
    assert "content check OK" not in result.stdout


@pytest.mark.parametrize(
    "live_count",
    ["", "oops", "5\nunknown", "4\n", " 4", "+4", "-1", "4.0", "04",
     "9223372036854775808", "9" * 100],
)
def test_malformed_live_count_cannot_claim_verified_coverage(bundle: Path, live_count: str) -> None:
    result = _run(bundle, live_count)

    assert result.returncode == UNREADABLE
    assert "unconfirmed" in result.stderr
    assert "content check OK" not in result.stdout


def test_zero_live_projects_needs_no_repos(tmp_path: Path) -> None:
    _dump(tmp_path / "platform-omnia.sql.gz")
    _minio(tmp_path / "minio-data.tgz", repo_count=0)
    _globals(tmp_path)

    assert _run(tmp_path, "0").returncode == OK


def test_largest_postgres_count_is_compared_without_overflow(bundle: Path) -> None:
    assert _run(bundle, "9223372036854775807").returncode == MISSING_DATA


def test_the_check_never_prints_archive_contents(bundle: Path) -> None:
    # В отчёт уходят числа и имена файлов, но не содержимое копии владельцев.
    result = _run(bundle, "5")

    assert "CREATE TABLE" not in result.stdout + result.stderr
    assert "xl.meta" not in result.stdout
