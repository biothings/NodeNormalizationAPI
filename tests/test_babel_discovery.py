import pytest

from nodenorm.babel import BabelReleaseUnavailable, BabelReleases, get_babel_version


def mapping(version=None, *, source=None):
    if source is None:
        source = {"version": version}
    return {"mappings": {"_meta": {"src": {"nodenorm": source}}}}


def test_explicit_metadata_version_is_authoritative():
    metadata = mapping(source={"version": "2026jul22", "url": "https://example.org/releases/2025sep1/"})

    assert get_babel_version(metadata) == "2026jul22"


@pytest.mark.parametrize("version", [None, "", 42, False, [], {}, "2025 sep1", "2025sep1\r\n", "2025sep1é"])
def test_invalid_explicit_version_does_not_fall_back_to_valid_url(version):
    metadata = mapping(source={"version": version, "url": "https://example.org/releases/2025sep1/"})

    with pytest.raises(ValueError):
        get_babel_version(metadata)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/releases/2025sep1",
        "https://example.org/releases/2025sep1/",
        "https://example.org/releases/2025sep1/?download=true#source",
    ],
)
def test_legacy_source_url_identifies_dated_release(url):
    assert get_babel_version(mapping(source={"url": url})) == "2025sep1"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/releases/latest",
        "https://example.org/releases/VERSION.txt",
        "https://example.org/releases/nodenorm-2025sep1",
        "https://example.org/releases/stable",
        "https://example.org/releases/",
        "",
        None,
        42,
    ],
)
def test_legacy_source_url_must_identify_a_dated_release(url):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        get_babel_version(mapping(source={"url": url}))


def test_discovery_uses_metadata_without_inferencing_from_index_names():
    releases = BabelReleases.from_mappings(
        "2025sep1",
        ["2025sep1", "2026jul22"],
        {
            "arbitrary-index-a": mapping("2025sep1"),
            "arbitrary-index-b": mapping(source={"url": "https://example.org/releases/2026jul22/"}),
            "nodenorm-2025sep1": {"mappings": {}},
            "other-data": mapping("2024oct1"),
            "unrelated-index": {"mappings": {"_meta": {"src": {"other": {"version": "2025sep1"}}}}},
            "invalid-metadata": mapping(source={"version": "", "url": "https://example.org/releases/2025sep1/"}),
            "null-metadata": {"mappings": {"_meta": None}},
            "latest-index": mapping(source={"url": "https://example.org/releases/latest"}),
        },
    )

    assert releases.resolve().index == "arbitrary-index-a"
    assert releases.resolve("2026jul22").index == "arbitrary-index-b"
    assert releases.available_versions == ["2025sep1", "2026jul22"]
    assert releases.errors == {}


def test_missing_supported_release_stays_known_but_unavailable():
    releases = BabelReleases.from_mappings(
        "2025sep1", ["2025sep1", "2026jul22"], {"default-index": mapping("2025sep1")}
    )

    assert releases.resolve().index == "default-index"
    assert releases.available_versions == ["2025sep1", "2026jul22"]
    assert set(releases.errors) == {"2026jul22"}
    with pytest.raises(BabelReleaseUnavailable):
        releases.resolve("2026jul22")
    with pytest.raises(ValueError, match="Supported releases: 2025sep1, 2026jul22"):
        releases.resolve("retired")


def test_duplicate_metadata_version_is_unavailable_instead_of_selecting_an_index():
    releases = BabelReleases.from_mappings(
        "2025sep1",
        ["2025sep1", "2026jul22"],
        {
            "default-index": mapping("2025sep1"),
            "new-build-a": mapping("2026jul22"),
            "new-build-b": mapping("2026jul22"),
        },
    )

    assert releases.resolve().index == "default-index"
    assert set(releases.errors) == {"2026jul22"}
    with pytest.raises(BabelReleaseUnavailable):
        releases.resolve("2026jul22")


def test_existing_default_target_can_select_multiple_indices_of_the_same_release():
    releases = BabelReleases.from_mappings(
        "2025sep1",
        ["2025sep1", "2026jul22"],
        {
            "old-build-a": mapping("2025sep1"),
            "old-build-b": mapping("2025sep1"),
            "new-build": mapping("2026jul22"),
        },
        default_index="existing-alias",
    )

    assert releases.resolve().index == "existing-alias"
    assert releases.resolve("2026jul22").index == "new-build"
    assert releases.errors == {}
