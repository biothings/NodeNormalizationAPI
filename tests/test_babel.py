from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from nodenorm.babel import BabelReleases
from nodenorm.namespace import NodeNormalizationAPINamespace


RELEASES = {"2025sep1": "annotator-nodenorm", "2026jul22": "nodenorm-2026jul22"}
OLD_INDEX = "nodenorm_20260601_cuyvh23o_202609301231"
OLDER_INDEX = "nodenorm_20251106_lv86fxt0"
NEW_INDEX = "nodenorm_20260809_qvdpis31"


def test_release_selection_keeps_an_explicit_default():
    releases = BabelReleases("2025sep1", RELEASES)

    assert releases.resolve().version == "2025sep1"
    assert releases.resolve().index == "annotator-nodenorm"
    assert releases.resolve("2026jul22").index == "nodenorm-2026jul22"
    assert releases.available_versions == ["2025sep1", "2026jul22"]
    assert releases.resolve().version == "2025sep1"


@pytest.mark.parametrize("indices", ["index-a", ("index-a", "index-b")])
def test_release_search_indices_returns_a_fresh_list(indices):
    releases = BabelReleases("2025sep1", {"2025sep1": indices})
    release = releases.resolve()
    expected = [indices] if isinstance(indices, str) else list(indices)

    search_indices = release.search_indices
    assert search_indices == expected
    search_indices.append("unexpected-index")

    assert release.search_indices == expected
    assert releases.resolve().index == indices


@pytest.mark.parametrize("version", ["retired", "", "*", [], {}, 42, False])
def test_unknown_or_invalid_selector_lists_supported_releases(version):
    releases = BabelReleases("2025sep1", RELEASES)

    with pytest.raises(ValueError, match="Supported releases: 2025sep1, 2026jul22"):
        releases.resolve(version)


@pytest.mark.parametrize(
    "default_version,releases",
    [
        ("missing", RELEASES),
        (None, RELEASES),
        ([], RELEASES),
        ("2025sep1", {}),
        ("2025sep1", []),
        ("2025sep1", {"2025sep1": ""}),
        ("2025sep1", {"2025sep1": ["index-a", "index-b"]}),
        ("2025sep1", {"2025sep1": ()}),
        ("2025sep1", {"2025sep1": ("index-a", "")}),
        ("2025sep1", {"2025sep1": ("index-a", "index-*")}),
        ("2025sep1", {"2025sep1": ("index-a", None)}),
        ("2025sep1", {"2025sep1": ("index-a", ["index-b"])}),
        ("2025sep1", {"2025sep1": "index-*"}),
        ("2025sep1", {"2025sep1": "index-a,index-b"}),
        ("2025sep1", {"2025sep1": "index-?"}),
        ("2025sep1", {"2025sep1": "_all"}),
        ("2025sep1", {"2025sep1": " index "}),
        ("", {"": "index"}),
        ("2025sep1\r\n", {"2025sep1\r\n": "index"}),
        ("2025sep1\x00", {"2025sep1\x00": "index"}),
        ("2025 sep1", {"2025 sep1": "index"}),
        ("2025sep1é", {"2025sep1é": "index"}),
    ],
)
def test_invalid_release_configuration_is_rejected(default_version, releases):
    with pytest.raises(ValueError):
        BabelReleases(default_version, releases)


def namespace_config(babel=None):
    namespace = NodeNormalizationAPINamespace.__new__(NodeNormalizationAPINamespace)
    namespace.config = SimpleNamespace(
        elasticsearch={"ES_HOST": "http://test:9200", "ES_ARGS": {}, "ES_INDEX": "legacy", "ES_ALIAS": "alias"}
    )
    if babel is not None:
        namespace.config.babel = babel
    return namespace


def mapping(version, build_date=None):
    return {"mappings": {"_meta": {"build_date": build_date, "src": {"nodenorm": {"version": version}}}}}


@pytest.mark.parametrize("index_exists,target", [(True, "legacy"), (False, "alias")])
@pytest.mark.parametrize("babel", [None, {}])
def test_legacy_configuration_resolves_configured_target_to_concrete_index(index_exists, target, babel):
    namespace = namespace_config(babel)
    client = Mock()
    client.indices.exists.return_value = index_exists
    client.indices.exists_alias.return_value = True
    client.indices.get_mapping.side_effect = [
        {"backing-index": mapping("2025sep1")},
        {"backing-index": mapping("2025sep1"), "another-build": mapping("2026jul22")},
    ]

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["backing-index"]
    assert elasticsearch.babel.resolve().index == "backing-index"
    assert elasticsearch.babel.resolve("2026jul22").index == "another-build"
    assert elasticsearch.babel.available_versions == ["2025sep1", "2026jul22"]
    connections.get_client.assert_called_once_with("http://test:9200")
    connections.get_async_client.assert_called_once_with("http://test:9200")
    assert client.indices.get_mapping.call_args_list[0].kwargs == {"index": [target]}
    assert client.indices.get_mapping.call_args_list[1].kwargs == {"filter_path": "*.mappings._meta"}
    assert namespace.config.elasticsearch["ES_INDEX"] == "legacy"
    assert namespace.config.elasticsearch["ES_ALIAS"] == "alias"


def test_alias_rollover_preserves_its_exact_default_and_selects_latest_builds_for_other_releases():
    namespace = namespace_config()
    namespace.config.elasticsearch["ES_INDEX"] = ""
    client = Mock()
    client.indices.exists_alias.return_value = True
    all_mappings = {
        OLD_INDEX: mapping("2025sep1", "2026-09-30T12:31:00Z"),
        OLDER_INDEX: mapping("2025sep1", "2025-11-06T00:00:00Z"),
        NEW_INDEX: mapping("2026jul22", "2026-08-09T00:00:00Z"),
    }
    for index in (OLD_INDEX, OLDER_INDEX):
        all_mappings[index]["mappings"]["_meta"]["src"]["nodenorm"] = {
            "version": None,
            "url": "https://example.org/releases/2025sep1/",
        }
    alias_index = OLDER_INDEX

    def get_mapping(*, index=None, filter_path=None):
        if index is not None:
            assert index == ["alias"]
            return {alias_index: all_mappings[alias_index]}
        assert filter_path == "*.mappings._meta"
        return all_mappings

    client.indices.get_mapping.side_effect = get_mapping
    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        running = namespace.configure_elasticsearch()

        assert running.indices == [OLDER_INDEX]
        assert running.babel.default_version == "2025sep1"
        assert running.babel.resolve().search_indices == [OLDER_INDEX]
        assert running.babel.resolve("2025sep1").search_indices == [OLDER_INDEX]

        alias_index = NEW_INDEX
        restarted = namespace.configure_elasticsearch()

    assert restarted.indices == [NEW_INDEX]
    assert restarted.babel.default_version == "2026jul22"
    assert restarted.babel.resolve().search_indices == [NEW_INDEX]
    assert restarted.babel.resolve("2025sep1").search_indices == [OLD_INDEX]
    assert restarted.babel.available_versions == ["2025sep1", "2026jul22"]
    assert running.indices == [OLDER_INDEX]
    assert running.babel.resolve().search_indices == [OLDER_INDEX]
    assert running.babel.resolve("2025sep1").search_indices == [OLDER_INDEX]
    assert running.babel.errors == restarted.babel.errors == {}
    assert namespace.config.elasticsearch["ES_INDEX"] == ""
    assert namespace.config.elasticsearch["ES_ALIAS"] == "alias"


@pytest.mark.parametrize("new_version", ["2026jul22", "2027jan1"])
def test_new_metadata_release_is_available_without_changing_configuration(new_version):
    namespace = namespace_config()
    client = Mock()
    client.indices.exists.return_value = True
    client.indices.get_mapping.side_effect = [
        {"default-release-build": mapping("2025sep1")},
        {"default-release-build": mapping("2025sep1"), "new-release-build": mapping(new_version)},
    ]

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["default-release-build"]
    assert elasticsearch.babel.resolve(new_version).index == "new-release-build"
    assert elasticsearch.babel.available_versions == ["2025sep1", new_version]
    assert elasticsearch.babel.errors == {}


def test_multi_index_alias_preserves_all_default_backing_indices_despite_newer_builds():
    namespace = namespace_config({})
    client = Mock()
    client.indices.exists.return_value = False
    client.indices.exists_alias.return_value = True
    default_mappings = {
        "old-a": mapping("2025sep1", "2025-11-06T00:00:00Z"),
        "old-b": mapping("2025sep1", "2025-11-06T00:00:00Z"),
    }
    all_mappings = {
        **default_mappings,
        "old-c": mapping("2025sep1", "2026-09-30T12:31:00Z"),
        "new-release-build": mapping("2026jul22"),
    }
    client.indices.get_mapping.side_effect = [default_mappings, all_mappings]

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["old-a", "old-b"]
    assert elasticsearch.babel.default_version == "2025sep1"
    assert elasticsearch.babel.resolve().index == ("old-a", "old-b")
    assert elasticsearch.babel.resolve().search_indices == ["old-a", "old-b"]
    assert elasticsearch.babel.resolve("2025sep1").search_indices == ["old-a", "old-b"]
    assert elasticsearch.babel.resolve("2026jul22").index == "new-release-build"
    assert elasticsearch.babel.errors == {}
    assert client.indices.get_mapping.call_args_list[0].kwargs == {"index": ["alias"]}
    assert client.indices.get_mapping.call_args_list[1].kwargs == {"filter_path": "*.mappings._meta"}


def test_existing_default_is_included_even_when_absent_from_cluster_mapping_scan():
    namespace = namespace_config()
    client = Mock()
    client.indices.exists.return_value = True
    client.indices.get_mapping.side_effect = [
        {"old-release-build": mapping("2025sep1")},
        {"new-release-build": mapping("2026jul22")},
    ]

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.babel.resolve().index == "old-release-build"
    assert elasticsearch.babel.available_versions == ["2025sep1", "2026jul22"]
    assert elasticsearch.babel.errors == {}


@pytest.mark.parametrize(
    "configuration",
    [
        [],
        {"releases": RELEASES, "default_version": "2025sep1"},
        {"unknown_option": True},
        {"indices": {"2025sep1": OLD_INDEX}},
        {"supported_versions": ["2025sep1"]},
        {"supported_versions": None},
        {"supported_versions": "2025sep1"},
        {"supported_versions": {}},
        {"supported_versions": []},
        {"default_version": "2025sep1"},
        {"default_version": "2026jul22"},
        {"default_version": None},
        {"default_version": []},
        {"default_version": 42},
        {"default_version": False},
        {"default_version": ""},
        {"default_version": "2025sep1\r\n"},
        {"default_version": "2025sep1é"},
        {"default_version": "2025 sep1"},
    ],
)
def test_invalid_configuration_fails_before_connecting_to_elasticsearch(configuration):
    namespace = namespace_config(configuration)

    with patch("nodenorm.namespace.connections.es") as connections:
        with pytest.raises(ValueError):
            namespace.configure_elasticsearch()

    connections.get_client.assert_not_called()
    connections.get_async_client.assert_not_called()


def test_legacy_target_cannot_mix_babel_releases():
    namespace = namespace_config()
    client = Mock()
    client.indices.exists.return_value = True
    client.indices.get_mapping.return_value = {"old": mapping("2025sep1"), "new": mapping("2026jul22")}

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        with pytest.raises(ValueError, match="exactly one Babel release"):
            namespace.configure_elasticsearch()
