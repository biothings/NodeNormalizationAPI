"""
The deployment configuration (docker/configuration/config.json, passed with --conf) is laid
over the packaged default (src/nodenorm/config/config.default.json), so it holds only the
settings the deployment changes.
"""

import json
import pathlib
from types import SimpleNamespace

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from nodenorm.application import NodeNormalizationAPI
from nodenorm.namespace import NodeNormalizationAPINamespace, merge_configuration

DEPLOYMENT_CONFIGURATION = pathlib.Path(__file__).parent.parent / "docker" / "configuration" / "config.json"


def namespace(conf=None) -> NodeNormalizationAPINamespace:
    """A namespace configured by the real loader, without the Elasticsearch connection the constructor makes."""
    namespace = NodeNormalizationAPINamespace.__new__(NodeNormalizationAPINamespace)
    namespace.handlers = {}
    namespace.default_configuration = "config.default.json"
    namespace.config = namespace.load_configuration(SimpleNamespace(conf=conf, host=None, port=None))
    return namespace


def load(conf=None) -> SimpleNamespace:
    return namespace(conf).config


def keys(configuration: dict, prefix: str = "") -> set[str]:
    """Every key path in a configuration, such as "elasticsearch.ES_ARGS.sniff"."""
    paths = set()
    for key, value in configuration.items():
        paths.add(prefix + key)
        if isinstance(value, dict):
            paths |= keys(value, prefix + key + ".")
    return paths


def test_merge_keeps_the_keys_an_override_leaves_out():
    base = {"elasticsearch": {"ES_HOST": "http://localhost:9200", "ES_ARGS": {"sniff": False, "request_timeout": 60}}}
    override = {"elasticsearch": {"ES_HOST": "http://es:9200", "ES_ARGS": {"request_timeout": 5}}}

    assert merge_configuration(base, override) == {
        "elasticsearch": {"ES_HOST": "http://es:9200", "ES_ARGS": {"sniff": False, "request_timeout": 5}}
    }
    assert base["elasticsearch"]["ES_HOST"] == "http://localhost:9200", "the base must not be modified"


def test_merge_replaces_lists_whole():
    merged = merge_configuration({"URLS": ["^/$", "^/status$"]}, {"URLS": ["^/webapp"]})
    assert merged == {"URLS": ["^/webapp"]}


def test_deployment_configuration_only_names_settings_the_default_has():
    # A misspelt key in the override would otherwise be added silently and change nothing.
    default = vars(load())
    deployment = json.loads(DEPLOYMENT_CONFIGURATION.read_text(encoding="utf-8"))
    assert keys(deployment) <= keys(default)


def test_deployment_configuration_is_complete_and_not_in_debug_mode():
    default = vars(load())
    deployed = vars(load(str(DEPLOYMENT_CONFIGURATION)))

    assert keys(deployed) == keys(default)
    # Tornado's debug mode serves tracebacks in error pages and reloads on file changes.
    assert deployed["webserver"]["SETTINGS"]["debug"] is False
    assert deployed["webserver"]["SETTINGS"]["autoreload"] is False
    assert deployed["elasticsearch"]["ES_HOST"] != default["elasticsearch"]["ES_HOST"]
    assert deployed["telemetry"]["OPENTELEMETRY_ENABLED"] is True


class TestDeployedApplication(AsyncHTTPTestCase):
    """The application get_app() builds from the deployment configuration."""

    def get_app(self) -> tornado.web.Application:
        return NodeNormalizationAPI.get_app(namespace(str(DEPLOYMENT_CONFIGURATION)))

    def test_settings_reach_tornado(self):
        # Regression: get_app() passed the settings positionally, into default_host, so none applied.
        assert self._app.settings["debug"] is False
        assert self._app.settings["autoreload"] is False
        assert "static_path" not in self._app.settings, "a static_path adds a catch-all route ahead of the API"

    def test_routes_still_reach_their_handlers(self):
        assert self.fetch("/version").code == 200
        assert self.fetch("/webapp/openapi.json").code == 200
        assert self.fetch("/").code == 200
