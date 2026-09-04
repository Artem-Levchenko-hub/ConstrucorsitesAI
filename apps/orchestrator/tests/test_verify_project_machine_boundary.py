from pathlib import Path

from scripts.verify_project_machine_boundary import fixture_profile, template_text_files


def test_template_text_files_excludes_local_install_and_build_caches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "page.tsx").write_text("export default 1", encoding="utf-8")
    for relative in (
        Path("node_modules/pkg/native.node"),
        Path(".next/cache/webpack.pack"),
        Path("tsconfig.tsbuildinfo"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x80\x81\x82")

    assert template_text_files(tmp_path) == {"src/page.tsx": "export default 1"}


def test_fixture_profile_matches_v2_machine_and_project_database_limits() -> None:
    profile = fixture_profile("postgres@sha256:" + "a" * 64)

    assert profile.is_v2 is True
    assert profile.profile_version == "docker-owner-cell-resources-v2"
    assert profile.active_machine_cpu_cores == 2.0
    assert profile.active_machine_memory_bytes == 2 * 1024**3
    assert profile.project_postgres_cpu_cores == 0.15
    assert profile.project_postgres_memory_bytes == 256 * 1024**2
    assert profile.managed_core_cpu_cores == 0.35
    assert profile.managed_core_memory_bytes == 768 * 1024**2
