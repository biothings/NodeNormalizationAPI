from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from nodenorm.babel import BabelReleases
from nodenorm.namespace import NodeNormalizationAPINamespace


RELEASES = {"2025sep1": "annotator-nodenorm", "2026jul22": "nodenorm-2026jul22"}


def test_release_selection_keeps_an_explicit_default():
    releases = BabelReleases("2025sep1", RELEASES)

    assert releases.resolve().version == "2025sep1"
    assert releases.resolve().index == "annotator-nodenorm"
    assert releases.resolve("2026jul22").index == "nodenorm-2026jul22"
    assert releases.available_versions == ["2025sep1", "2026jul22"]
    assert releases.resolve().version == "2025sep1"


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
def test_legacy_configuration_discovers_version_without_changing_target(index_exists, target):
    namespace = namespace_config()
    client = Mock()
    client.indices.exists.return_value = index_exists
    client.indices.exists_alias.return_value = True
    client.indices.get_mapping.return_value = {"backing-index": mapping("2025sep1")}

    with patch("nodenorm.namespace.connections.es") as connections:
        connections.get_client.return_value = client
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == [target]
    assert elasticsearch.babel.resolve().index == target
    assert elasticsearch.babel.available_versions == ["2025sep1"]
    client.indices.get_mapping.assert_called_once_with(index=[target])


def test_configured_releases_override_legacy_target_without_requiring_all_releases_online():
    namespace = namespace_config({"default_version": "2026jul22", "releases": RELEASES})

    with patch("nodenorm.namespace.connections.es") as connections:
        elasticsearch = namespace.configure_elasticsearch()

    assert elasticsearch.indices == ["nodenorm-2026jul22"]
    assert elasticsearch.babel.resolve("2025sep1").index == "annotator-nodenorm"
    connections.get_client.return_value.indices.exists.assert_not_called()
    connections.get_client.return_value.indices.exists_alias.assert_not_called()
    connections.get_client.return_value.indices.get_mapping.assert_not_called()


@pytest.mark.parametrize("configuration", [[], {"default_version": "retired", "releases": RELEASES}])
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
