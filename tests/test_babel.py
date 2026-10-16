from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from nodenorm.babel import BabelReleaseUnavailable, BabelReleases
from nodenorm.namespace import NodeNormalizationAPINamespace


RELEASES = {"2025sep1": "annotator-nodenorm", "2026jul22": "nodenorm-2026jul22"}


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


def mapping(version):
    return {"mappings": {"_meta": {"src": {"nodenorm": {"version": version}}}}}


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
    assert client.indices.get_mapping.call_args_list[1].kwargs == {"filter_path": "*.mappings._meta.src.nodenorm"}
    assert namespace.config.elasticsearch["ES_INDEX"] == "legacy"
    assert namespace.config.elasticsearch["ES_ALIAS"] == "alias"


def test_alias_rollover_keeps_running_catalog_pinned_and_refreshes_on_startup():
    namespace = namespace_config()
    namespace.config.elasticsearch["ES_INDEX"] = ""
    client = Mock()
    client.indices.exists_alias.return_value = True
    all_mappings = {"old-build": mapping("2025sep1"), "new-build": mapping("2026jul22")}
    alias_mappings = {"old-build": mapping("2025sep1")}

    def get_mapping(*, index=None, filter_path=None):
        if index is not None:
            assert index == ["alias"]
            return alias_mappings
        assert filter_path == "*.mappings._meta.src.nodenorm"
        return all_mappings

    client.indices.get_mapping.side_effect = get_mapping
    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        running = namespace.configure_elasticsearch()

        alias_mappings = {"new-build": mapping("2026jul22")}
        assert running.indices == ["old-build"]
        assert running.babel.default_version == "2025sep1"
        assert running.babel.resolve().search_indices == ["old-build"]
        assert running.babel.resolve("2025sep1").search_indices == ["old-build"]

        restarted = namespace.configure_elasticsearch()

    assert restarted.indices == ["new-build"]
    assert restarted.babel.default_version == "2026jul22"
    assert restarted.babel.resolve().search_indices == ["new-build"]
    assert restarted.babel.resolve("2025sep1").search_indices == ["old-build"]
    assert namespace.config.elasticsearch["ES_INDEX"] == ""
    assert namespace.config.elasticsearch["ES_ALIAS"] == "alias"


def test_explicit_default_discovers_targets_from_the_configured_cluster():
    namespace = namespace_config({"default_version": "2026jul22"})
    client = Mock()
    client.indices.get_mapping.return_value = {
        "old-release-build": mapping("2025sep1"),
        "new-release-build": mapping("2026jul22"),
    }

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["new-release-build"]
    assert elasticsearch.babel.resolve("2025sep1").index == "old-release-build"
    connections.get_client.assert_called_once_with("http://test:9200")
    connections.get_async_client.assert_called_once_with("http://test:9200")
    client.indices.exists.assert_not_called()
    client.indices.exists_alias.assert_not_called()
    client.indices.get_mapping.assert_called_once_with(filter_path="*.mappings._meta.src.nodenorm")


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


@pytest.mark.parametrize("matches", [0, 2])
def test_unavailable_explicit_default_blocks_startup(matches):
    namespace = namespace_config({"default_version": "2026jul22"})
    client = Mock()
    client.indices.get_mapping.return_value = {
        "old-release-build": mapping("2025sep1"),
        **{f"new-release-build-{number}": mapping("2026jul22") for number in range(matches)},
    }

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        with pytest.raises(BabelReleaseUnavailable):
            namespace.configure_elasticsearch()


def test_omitted_default_uses_existing_target_metadata_and_discovers_other_releases():
    namespace = namespace_config({})
    client = Mock()
    client.indices.exists.return_value = False
    client.indices.exists_alias.return_value = True
    default_mappings = {"old-a": mapping("2025sep1"), "old-b": mapping("2025sep1")}
    all_mappings = {**default_mappings, "new-release-build": mapping("2026jul22")}
    client.indices.get_mapping.side_effect = [default_mappings, all_mappings]

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["old-a", "old-b"]
    assert elasticsearch.babel.default_version == "2025sep1"
    assert elasticsearch.babel.resolve().index == ("old-a", "old-b")
    assert elasticsearch.babel.resolve().search_indices == ["old-a", "old-b"]
    assert elasticsearch.babel.resolve("2026jul22").index == "new-release-build"
    assert elasticsearch.babel.errors == {}
    assert client.indices.get_mapping.call_args_list[0].kwargs == {"index": ["alias"]}
    assert client.indices.get_mapping.call_args_list[1].kwargs == {"filter_path": "*.mappings._meta.src.nodenorm"}


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
        {"supported_versions": ["2025sep1"]},
        {"supported_versions": None},
        {"supported_versions": "2025sep1"},
        {"supported_versions": {}},
        {"supported_versions": []},
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
