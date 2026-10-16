"""Offline endpoint coverage for request-scoped Babel release selection."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from nodenorm.babel import BabelReleases


DEFAULT_VERSION = "2026jul22"
OLD_VERSION = "2025sep1"
DEFAULT_INDEX = "nodenorm_new"
OLD_INDEX = "nodenorm_old"


class FakeBiolinkToolkit:
    def get_ancestors(self, biolink_type):
        return [biolink_type]

    def get_element(self, ancestor):
        return {"class_uri": ancestor}


def load_endpoint_modules():
    """Load real handlers without constructing the network-backed BMT toolkit."""
    fake_biolink = ModuleType("nodenorm.biolink")
    fake_biolink.BIOLINK_MODEL_VERSION = "test"
    fake_biolink.toolkit = FakeBiolinkToolkit()
    substitutions = {
        name: sys.modules.get(name)
        for name in ("nodenorm.biolink", "nodenorm.handlers.normalized_nodes")
    }
    modules = {}
    sys.modules["nodenorm.biolink"] = fake_biolink
    try:
        for name in ("normalized_nodes", "set_identifiers", "semantic_types", "health"):
            module_name = f"_babel_endpoint_{name}"
            module_path = Path(__file__).parents[1] / "src" / "nodenorm" / "handlers" / f"{name}.py"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            modules[name] = module
            if name == "normalized_nodes":
                sys.modules["nodenorm.handlers.normalized_nodes"] = module
    finally:
        for name, original in substitutions.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return SimpleNamespace(**modules)


handlers = load_endpoint_modules()


class FakeAsyncElasticsearch:
    def __init__(self):
        self.calls = []
        self.failed_index = None
        self.cat_error = False
        self.indices = SimpleNamespace(get_mapping=self.get_mapping)
        self.cat = SimpleNamespace(nodes=self.nodes)

    def record(self, operation, kwargs):
        self.calls.append((operation, kwargs))
        index = kwargs.get("index")
        if index is not None:
            assert index in ([DEFAULT_INDEX], [OLD_INDEX])
            if index == [self.failed_index]:
                raise RuntimeError("selected release is unavailable")

    async def msearch(self, **kwargs):
        self.record("msearch", kwargs)
        prefix = "OLD" if kwargs["index"] == [OLD_INDEX] else "NEW"
        responses = []
        for query in kwargs["searches"][1::2]:
            curie = query["query"]["bool"]["filter"][0]["terms"]["identifiers.i"][0]
            family, number = curie.split(":")
            if family == "INPUT":
                identifier = {"i": f"{prefix}:{number}", "c": {"gp": [f"PARTNER:{number}"]}}
                biolink_type = "biolink:Gene"
            else:
                assert family == "PARTNER"
                identifier = {"i": f"{prefix}:P{number}"}
                biolink_type = "biolink:Protein"
            source = {"identifiers": [identifier], "type": biolink_type}
            responses.append({"hits": {"total": {"value": 1}, "hits": [{"_id": curie, "_source": source}]}})
        return SimpleNamespace(body={"responses": responses})

    async def search(self, **kwargs):
        self.record("search", kwargs)
        biolink_type = "biolink:Disease" if kwargs["index"] == [OLD_INDEX] else "biolink:Gene"
        return SimpleNamespace(body={"aggregations": {"unique_types": {"buckets": [{"key": biolink_type}]}}})

    async def get_mapping(self, **kwargs):
        self.record("get_mapping", kwargs)
        version = OLD_VERSION if kwargs["index"] == [OLD_INDEX] else DEFAULT_VERSION
        return {kwargs["index"][0]: {"mappings": {"_meta": {"src": {"nodenorm": {"version": version}}}}}}

    async def nodes(self, **kwargs):
        self.record("nodes", kwargs)
        if self.cat_error:
            raise RuntimeError("cat is unavailable")
        return [{"name": "test-node", "version": "8.17.0"}]


class TestBabelEndpoints(AsyncHTTPTestCase):
    def get_app(self):
        self.client = FakeAsyncElasticsearch()
        self.namespace = SimpleNamespace(
            elasticsearch=SimpleNamespace(
                async_client=self.client,
                indices=[DEFAULT_INDEX],
                babel=BabelReleases(DEFAULT_VERSION, {OLD_VERSION: OLD_INDEX, DEFAULT_VERSION: DEFAULT_INDEX}),
            )
        )
        app = Application(
            [
                (r"/get_setid", handlers.set_identifiers.SetIdentifierHandler),
                (r"/get_semantic_types", handlers.semantic_types.SemanticTypeHandler),
                (r"/status", handlers.health.NodeNormHealthHandler),
            ]
        )
        app.biothings = self.namespace
        return app

    def assert_selected_release(self, response, version):
        assert response.code == 200
        assert response.headers["X-Babel-Version"] == version
        return json.loads(response.body)

    def assert_unsupported(self, response):
        assert response.code == 400
        assert OLD_VERSION in response.body.decode()
        assert DEFAULT_VERSION in response.body.decode()
        assert self.client.calls == []

    def test_setid_get_selects_release_without_changing_default(self):
        default = self.assert_selected_release(self.fetch("/get_setid?curie=INPUT:1"), DEFAULT_VERSION)
        old = self.assert_selected_release(
            self.fetch(f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}"), OLD_VERSION
        )
        again = self.assert_selected_release(self.fetch("/get_setid?curie=INPUT:1"), DEFAULT_VERSION)

        assert default["normalized_curies"] == ["NEW:1"]
        assert old["normalized_curies"] == ["OLD:1"]
        assert old["setid"] != default["setid"]
        assert again == default
        assert [call[1]["index"] for call in self.client.calls] == [[DEFAULT_INDEX], [OLD_INDEX], [DEFAULT_INDEX]]
        assert self.namespace.elasticsearch.indices == [DEFAULT_INDEX]

    def test_setid_post_selects_one_release_for_all_groups_and_conflation(self):
        response = self.fetch(
            f"/get_setid?babel_version={OLD_VERSION}",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                [
                    {"curies": ["INPUT:2", "INPUT:1", "INPUT:1"], "conflations": []},
                    {"curies": ["INPUT:2"], "conflations": ["GeneProtein"]},
                ]
            ),
        )
        body = self.assert_selected_release(response, OLD_VERSION)

        assert set(body) == {"0", "1"}
        assert body["0"]["normalized_curies"] == ["OLD:1", "OLD:2"]
        assert body["0"]["normalized_string"] == "OLD:1||OLD:2"
        assert body["1"]["normalized_curies"] == ["OLD:P2"]
        assert body["1"]["conflations"] == ["GeneProtein"]
        assert len(self.client.calls) == 3
        assert all(operation == "msearch" and call["index"] == [OLD_INDEX] for operation, call in self.client.calls)
        assert self.namespace.elasticsearch.indices == [DEFAULT_INDEX]

    def test_setid_post_defaults_to_configured_release(self):
        response = self.fetch(
            "/get_setid",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps([{"curies": ["INPUT:1"]}]),
        )

        body = self.assert_selected_release(response, DEFAULT_VERSION)
        assert body["0"]["normalized_curies"] == ["NEW:1"]

    def test_setid_rejects_unsupported_release_before_lookup(self):
        self.assert_unsupported(self.fetch("/get_setid?curie=INPUT:1&babel_version=unknown"))
        self.assert_unsupported(
            self.fetch(
                "/get_setid?babel_version=unknown",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps([{"curies": ["INPUT:1"]}]),
            )
        )

    def test_setid_backend_failure_does_not_retry_default_release(self):
        self.client.failed_index = OLD_INDEX

        response = self.fetch(f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}")

        assert response.code == 500
        assert len(self.client.calls) == 1
        assert self.client.calls[0][1]["index"] == [OLD_INDEX]

    def test_semantic_types_select_release_and_default(self):
        old = self.assert_selected_release(
            self.fetch(f"/get_semantic_types?babel_version={OLD_VERSION}"), OLD_VERSION
        )
        default = self.assert_selected_release(self.fetch("/get_semantic_types"), DEFAULT_VERSION)

        assert set(old["semantic_types"]["types"]) == {"biolink:Disease", "biolink:disease"}
        assert set(default["semantic_types"]["types"]) == {"biolink:Gene", "biolink:gene"}
        assert [call[1]["index"] for call in self.client.calls] == [[OLD_INDEX], [DEFAULT_INDEX]]

    def test_semantic_types_reject_unsupported_release_before_lookup(self):
        self.assert_unsupported(self.fetch("/get_semantic_types?babel_version=unknown"))

    def test_semantic_types_backend_failure_does_not_retry_default_release(self):
        self.client.failed_index = OLD_INDEX

        response = self.fetch(f"/get_semantic_types?babel_version={OLD_VERSION}")

        assert response.code == 500
        assert len(self.client.calls) == 1
        assert self.client.calls[0][1]["index"] == [OLD_INDEX]

    def test_unknown_or_ambiguous_release_is_rejected_before_lookup(self):
        def mapping(version):
            return {"mappings": {"_meta": {"src": {"nodenorm": {"version": version}}}}}

        for matching_indices in (0, 2):
            with self.subTest(matching_indices=matching_indices):
                mappings = {
                    DEFAULT_INDEX: mapping(DEFAULT_VERSION),
                    **{f"old-build-{number}": mapping(OLD_VERSION) for number in range(matching_indices)},
                }
                releases = BabelReleases.from_mappings(
                    DEFAULT_VERSION, mappings, default_index=DEFAULT_INDEX
                )
                self.namespace.elasticsearch.babel = releases
                self.client.calls.clear()

                requests = [
                    (f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}", {}),
                    (
                        f"/get_setid?babel_version={OLD_VERSION}",
                        {
                            "method": "POST",
                            "headers": {"Content-Type": "application/json"},
                            "body": json.dumps([{"curies": ["INPUT:1"]}]),
                        },
                    ),
                    (f"/get_semantic_types?babel_version={OLD_VERSION}", {}),
                ]
                for url, options in requests:
                    response = self.fetch(url, **options)
                    assert response.code == (503 if matching_indices else 400)
                    if matching_indices:
                        assert OLD_VERSION in response.body.decode()
                    else:
                        assert DEFAULT_VERSION in response.body.decode()
                        assert b"Supported releases" in response.body
                    assert self.client.calls == []

                status = self.fetch("/status")
                assert status.code == 200
                body = json.loads(status.body)
                assert body["available_babel_versions"] == [DEFAULT_VERSION]
                assert body["default_babel_version"] == DEFAULT_VERSION
                assert body.get("babel_version_errors", {}) == releases.errors
                assert set(releases.errors) == ({OLD_VERSION} if matching_indices else set())

                default = self.assert_selected_release(self.fetch("/get_setid?curie=INPUT:1"), DEFAULT_VERSION)
                assert default["normalized_curies"] == ["NEW:1"]
                assert self.client.calls[-1][1]["index"] == [DEFAULT_INDEX]

    def test_status_and_requests_share_metadata_discovered_releases(self):
        self.namespace.elasticsearch.babel = BabelReleases.from_mappings(
            DEFAULT_VERSION,
            {
                DEFAULT_INDEX: {"mappings": {"_meta": {"src": {"nodenorm": {"version": DEFAULT_VERSION}}}}},
                OLD_INDEX: {"mappings": {"_meta": {"src": {"nodenorm": {"version": OLD_VERSION}}}}},
            },
            default_index=DEFAULT_INDEX,
        )

        response = self.fetch(f"/status?babel_version={OLD_VERSION}")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["status"] == "running"
        assert body["default_babel_version"] == DEFAULT_VERSION
        assert body["available_babel_versions"] == sorted([OLD_VERSION, DEFAULT_VERSION])
        assert body["babel_version"] == DEFAULT_VERSION
        assert body["babel_version_url"].endswith(f"/{DEFAULT_VERSION}/README.md")
        assert body["backend"] == "elasticsearch"
        assert body["version"] == handlers.health.get_openapi_version()
        assert body["biolink_model_toolkit_version"] == "test"
        assert body["elasticsearch"]["nodes"]["test-node"]["version"] == "8.17.0"
        assert self.client.calls[0] == ("get_mapping", {"index": [DEFAULT_INDEX]})

        selected = self.assert_selected_release(
            self.fetch(f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}"), OLD_VERSION
        )
        assert selected["normalized_curies"] == ["OLD:1"]
        assert self.client.calls[-1][1]["index"] == [OLD_INDEX]

    def test_status_keeps_release_discovery_when_cat_fails(self):
        self.client.cat_error = True

        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["status"] == "error"
        assert body["default_babel_version"] == DEFAULT_VERSION
        assert body["available_babel_versions"] == sorted([OLD_VERSION, DEFAULT_VERSION])
        assert body["babel_version"] == DEFAULT_VERSION
        assert body["babel_version_url"].endswith(f"/{DEFAULT_VERSION}/README.md")
        assert body["backend"] == "elasticsearch"
        assert body["version"] == handlers.health.get_openapi_version()
        assert self.client.calls[0] == ("get_mapping", {"index": [DEFAULT_INDEX]})

    def test_cors_exposes_selected_release_header(self):
        response = self.fetch(
            f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}",
            headers={"Origin": "https://example.org"},
        )

        self.assert_selected_release(response, OLD_VERSION)
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert "X-Babel-Version" in response.headers["Access-Control-Expose-Headers"]
