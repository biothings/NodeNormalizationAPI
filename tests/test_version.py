import pytest

from nodenorm import version as nodenorm_version


@pytest.fixture(autouse=True)
def clear_version_caches():
    nodenorm_version.get_github_commit_hash.cache_clear()
    nodenorm_version.get_version.cache_clear()
    yield


def test_read_version_file_uses_configured_path(tmp_path, monkeypatch):
    configured_version_file = tmp_path / "configured-version.txt"
    configured_version_file.write_text("configured-sha\n", encoding="utf-8")
    fallback_version_file = tmp_path / "version.txt"
    fallback_version_file.write_text("fallback-sha\n", encoding="utf-8")

    monkeypatch.setenv(nodenorm_version.VERSION_FILE_ENV_VAR, str(configured_version_file))

    assert nodenorm_version.read_version_file([fallback_version_file]) == "configured-sha"


def test_read_version_file_ignores_missing_and_blank_files(tmp_path, monkeypatch):
    blank_version_file = tmp_path / "blank-version.txt"
    blank_version_file.write_text("\n", encoding="utf-8")
    version_file = tmp_path / "version.txt"
    version_file.write_text("build-sha\n", encoding="utf-8")

    monkeypatch.delenv(nodenorm_version.VERSION_FILE_ENV_VAR, raising=False)

    assert (
        nodenorm_version.read_version_file([tmp_path / "missing.txt", blank_version_file, version_file])
        == "build-sha"
    )


def test_read_version_file_allows_empty_fallback_paths(monkeypatch):
    monkeypatch.delenv(nodenorm_version.VERSION_FILE_ENV_VAR, raising=False)

    assert nodenorm_version.read_version_file([]) is None


def test_get_version_prefers_build_version_file(monkeypatch):
    monkeypatch.setattr(nodenorm_version, "read_version_file", lambda: "build-sha")
    monkeypatch.setattr(
        nodenorm_version,
        "get_github_commit_hash",
        lambda: pytest.fail("Git fallback should not run when a build version file exists"),
    )

    assert nodenorm_version.get_version() == "build-sha"


def test_get_version_falls_back_to_git(monkeypatch):
    monkeypatch.setattr(nodenorm_version, "read_version_file", lambda: None)
    monkeypatch.setattr(nodenorm_version, "get_github_commit_hash", lambda: "git-sha")

    assert nodenorm_version.get_version() == "git-sha"


def test_get_version_caches_resolved_version(monkeypatch):
    calls = 0

    def read_version_file():
        nonlocal calls
        calls += 1
        return "build-sha"

    monkeypatch.setattr(nodenorm_version, "read_version_file", read_version_file)

    assert nodenorm_version.get_version() == "build-sha"
    assert nodenorm_version.get_version() == "build-sha"
    assert calls == 1
