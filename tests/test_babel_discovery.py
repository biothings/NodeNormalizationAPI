from itertools import permutations

import pytest

from nodenorm.babel import BabelReleases, get_babel_version


def mapping(version=None, *, source=None, build_date=None):
    if source is None:
        source = {"version": version}
    return {"mappings": {"_meta": {"build_date": build_date, "src": {"nodenorm": source}}}}


def test_explicit_metadata_version_is_authoritative():
    metadata = mapping(source={"version": "2026jul22", "url": "https://example.org/releases/2025sep1/"})

    assert get_babel_version(metadata) == "2026jul22"


@pytest.mark.parametrize("version", ["", 42, False, [], {}, "2025 sep1", "2025sep1\r\n", "2025sep1é"])
def test_invalid_explicit_version_does_not_fall_back_to_valid_url(version):
    metadata = mapping(source={"version": version, "url": "https://example.org/releases/2025sep1/"})

    with pytest.raises(ValueError):
        get_babel_version(metadata)


@pytest.mark.parametrize("version_metadata", [{}, {"version": None}], ids=["missing-version", "null-version"])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/releases/2025sep1",
        "https://example.org/releases/2025sep1/",
        "https://example.org/releases/2025sep1/?download=true#source",
    ],
)
def test_legacy_source_url_identifies_dated_release(url, version_metadata):
    assert get_babel_version(mapping(source={"url": url, **version_metadata})) == "2025sep1"


@pytest.mark.parametrize("version_metadata", [{}, {"version": None}], ids=["missing-version", "null-version"])
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
def test_legacy_source_url_must_identify_a_dated_release(url, version_metadata):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        get_babel_version(mapping(source={"url": url, **version_metadata}))


def test_discovery_uses_metadata_without_inferencing_from_index_names():
    releases = BabelReleases.from_mappings(
        "2025sep1",
        {
            "arbitrary-index-a": mapping("2025sep1"),
            "arbitrary-index-b": mapping(source={"version": None, "url": "https://example.org/releases/2026jul22/"}),
            "nodenorm-2025sep1": {"mappings": {}},
            "arbitrary-index-c": mapping("2024oct1"),
            "unrelated-index": {"mappings": {"_meta": {"src": {"other": {"version": "2025sep1"}}}}},
            "invalid-metadata": mapping(source={"version": "", "url": "https://example.org/releases/2025sep1/"}),
            "null-metadata": {"mappings": {"_meta": None}},
            "latest-index": mapping(source={"url": "https://example.org/releases/latest"}),
        },
    )

    assert releases.resolve().index == "arbitrary-index-a"
    assert releases.resolve("2026jul22").index == "arbitrary-index-b"
    assert releases.resolve("2024oct1").index == "arbitrary-index-c"
    assert releases.available_versions == ["2024oct1", "2025sep1", "2026jul22"]
    assert releases.errors == {}


def test_release_without_valid_metadata_is_unknown():
    releases = BabelReleases.from_mappings("2025sep1", {"default-index": mapping("2025sep1")})

    assert releases.resolve().index == "default-index"
    assert releases.available_versions == ["2025sep1"]
    assert releases.errors == {}
    with pytest.raises(ValueError, match="Supported releases: 2025sep1"):
        releases.resolve("2026jul22")
    with pytest.raises(ValueError, match="Supported releases: 2025sep1"):
        releases.resolve("retired")


@pytest.mark.parametrize("default_version", ["2025sep1", "2026jul22"])
def test_without_an_alias_target_build_dates_select_one_index_for_every_release(default_version):
    releases = BabelReleases.from_mappings(
        default_version,
        {
            "a-recent-old-release-build": mapping("2025sep1", build_date="2026-09-30T12:31:00Z"),
            "z-earlier-old-release-build": mapping("2025sep1", build_date="2025-11-06T00:00:00Z"),
            "a-recent-new-release-build": mapping("2026jul22", build_date="2026-08-09T00:00:00Z"),
            "z-earlier-new-release-build": mapping("2026jul22", build_date="2026-08-01T00:00:00Z"),
        },
    )

    assert releases.resolve("2025sep1").index == "a-recent-old-release-build"
    assert releases.resolve("2026jul22").index == "a-recent-new-release-build"
    assert releases.resolve().version == default_version
    assert releases.available_versions == ["2025sep1", "2026jul22"]
    assert releases.errors == {}


@pytest.mark.parametrize("default_index", ["older-default-a", ("older-default-a", "older-default-b")])
def test_default_alias_targets_override_build_dates_and_nondefault_releases_select_latest_build(default_index):
    releases = BabelReleases.from_mappings(
        "2025sep1",
        {
            "older-default-a": mapping("2025sep1", build_date="2025-11-06T00:00:00Z"),
            "older-default-b": mapping("2025sep1", build_date="2025-11-06T00:00:00Z"),
            "newer-default-copy": mapping("2025sep1", build_date="2026-09-30T12:31:00Z"),
            "a-latest-nondefault": mapping("2026jul22", build_date="2026-08-09T00:00:00Z"),
            "z-earlier-nondefault": mapping("2026jul22", build_date="2026-08-01T00:00:00Z"),
        },
        default_index=default_index,
    )
    expected_indices = [default_index] if isinstance(default_index, str) else list(default_index)

    assert releases.resolve().search_indices == expected_indices
    assert releases.resolve("2025sep1").search_indices == expected_indices
    assert releases.resolve("2026jul22").search_indices == ["a-latest-nondefault"]
    assert releases.errors == {}


def test_default_alias_target_remains_available_without_a_cluster_scan_entry():
    releases = BabelReleases.from_mappings(
        "2025sep1", {"new-build": mapping("2026jul22")}, default_index="alias-backed-build"
    )

    assert releases.resolve().search_indices == ["alias-backed-build"]
    assert releases.resolve("2026jul22").search_indices == ["new-build"]
    assert releases.errors == {}


@pytest.mark.parametrize("index_order", list(permutations(("a-latest", "m-middle", "z-earliest"))))
def test_build_selection_does_not_depend_on_mapping_order(index_order):
    dates = {
        "a-latest": "2026-09-30T12:31:00Z",
        "m-middle": "2026-08-01T00:00:00Z",
        "z-earliest": "2025-11-06T00:00:00Z",
    }
    releases = BabelReleases.from_mappings(
        "2025sep1", {index: mapping("2025sep1", build_date=dates[index]) for index in index_order}
    )

    assert releases.resolve().search_indices == ["a-latest"]
    assert releases.errors == {}


@pytest.mark.parametrize(
    "earlier,later",
    [
        ("2026-09-30T13:00:00+02:00", "2026-09-30T10:30:00-02:00"),
        ("2026-09-30T12:00:00Z", "2026-09-30T12:31:00"),
    ],
    ids=["compare-offsets-in-utc", "naive-date-is-utc"],
)
def test_build_dates_are_compared_as_utc_instants(earlier, later):
    releases = BabelReleases.from_mappings(
        "2025sep1",
        {
            "z-earlier": mapping("2025sep1", build_date=earlier),
            "a-later": mapping("2025sep1", build_date=later),
        },
    )

    assert releases.resolve().index == "a-later"


@pytest.mark.parametrize("build_date", [None, "", "not a date", "2026-13-01T00:00:00Z", 42, False, [], {}])
def test_valid_build_date_wins_over_missing_or_invalid_date(build_date):
    releases = BabelReleases.from_mappings(
        "2025sep1",
        {
            "a-valid-date": mapping("2025sep1", build_date="2025-11-06T00:00:00Z"),
            "z-invalid-date": mapping("2025sep1", build_date=build_date),
        },
    )

    assert releases.resolve().index == "a-valid-date"
    assert releases.errors == {}


@pytest.mark.parametrize("reverse_order", [False, True])
def test_equal_build_dates_use_the_lexicographically_greatest_index(reverse_order):
    indices = [
        ("a-build", mapping("2025sep1", build_date="2026-09-30T12:00:00Z")),
        ("z-build", mapping("2025sep1", build_date="2026-09-30T14:00:00+02:00")),
    ]
    releases = BabelReleases.from_mappings("2025sep1", dict(reversed(indices) if reverse_order else indices))

    assert releases.resolve().index == "z-build"


@pytest.mark.parametrize("reverse_order", [False, True])
def test_no_valid_build_dates_use_the_lexicographically_greatest_index(reverse_order):
    indices = [
        ("a-build", mapping("2025sep1", build_date="not a date")),
        ("z-build", mapping("2025sep1")),
    ]
    indices[1][1]["mappings"]["_meta"].pop("build_date")
    releases = BabelReleases.from_mappings("2025sep1", dict(reversed(indices) if reverse_order else indices))

    assert releases.resolve().search_indices == ["z-build"]
    assert releases.errors == {}


def test_newest_build_with_invalid_release_metadata_is_excluded_from_selection():
    releases = BabelReleases.from_mappings(
        "2025sep1",
        {
            "valid-old-build": mapping("2025sep1", build_date="2025-11-06T00:00:00Z"),
            "invalid-new-build": mapping(
                source={"version": "", "url": "https://example.org/releases/2025sep1/"},
                build_date="2026-09-30T12:31:00Z",
            ),
        },
    )

    assert releases.resolve().index == "valid-old-build"
    assert releases.errors == {}
