"""Offline endpoint coverage for request-scoped Babel release selection."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.web import Application

from nodenorm.babel import BabelReleases
from nodenorm.namespace import NodeNormalizationAPINamespace


DEFAULT_VERSION = "2026jul22"
OLD_VERSION = "2025sep1"
DEFAULT_INDEX = "nodenorm_20260809_qvdpis31"
OLD_INDEX = "nodenorm_20260601_cuyvh23o_202609301231"
OLDER_INDEX = "nodenorm_20251106_lv86fxt0"


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
        for name in ("normalized_nodes", "set_identifiers", "semantic_types", "status"):
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
            assert index in ([DEFAULT_INDEX], [OLD_INDEX], [OLDER_INDEX])
            if index == [self.failed_index]:
                raise RuntimeError("selected release is unavailable")

    async def msearch(self, **kwargs):
        self.record("msearch", kwargs)
        prefix = "NEW" if kwargs["index"] == [DEFAULT_INDEX] else "OLD"
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
        biolink_type = "biolink:Gene" if kwargs["index"] == [DEFAULT_INDEX] else "biolink:Disease"
        return SimpleNamespace(body={"aggregations": {"unique_types": {"buckets": [{"key": biolink_type}]}}})

    async def get_mapping(self, **kwargs):
        self.record("get_mapping", kwargs)
        version = DEFAULT_VERSION if kwargs["index"] == [DEFAULT_INDEX] else OLD_VERSION
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
            config=SimpleNamespace(deployment_environment="unknown"),
            elasticsearch=SimpleNamespace(
                async_client=self.client,
                indices=[DEFAULT_INDEX],
                babel=BabelReleases(DEFAULT_VERSION, {OLD_VERSION: OLD_INDEX, DEFAULT_VERSION: DEFAULT_INDEX}),
                babel_checked_at="2026-10-23T12:00:00+00:00",
                babel_refresh_error=None,
            )
        )
        app = Application(
            [
                (r"/get_setid", handlers.set_identifiers.SetIdentifierHandler),
                (r"/get_semantic_types", handlers.semantic_types.SemanticTypeHandler),
                (r"/status", handlers.status.NodeNormHealthHandler),
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

    def test_unknown_release_is_rejected_before_lookup(self):
        mappings = {
            DEFAULT_INDEX: {"mappings": {"_meta": {"src": {"nodenorm": {"version": DEFAULT_VERSION}}}}}
        }
        releases = BabelReleases.from_mappings(DEFAULT_VERSION, mappings)
        self.namespace.elasticsearch.babel = releases
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
            assert response.code == 400
            assert DEFAULT_VERSION in response.body.decode()
            assert b"Supported releases" in response.body
            assert self.client.calls == []

        status = self.fetch("/status")
        assert status.code == 200
        body = json.loads(status.body)
        assert body["babel"]["available_versions"] == {
            DEFAULT_VERSION: (
                f"https://github.com/NCATSTranslator/Babel/blob/main/releases/{DEFAULT_VERSION}/README.md"
            )
        }
        assert body["babel"]["default_version"] == DEFAULT_VERSION
        assert body["babel"]["indices"] == {DEFAULT_VERSION: [DEFAULT_INDEX]}
        assert "errors" not in body["babel"]

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
        )

        response = self.fetch(f"/status?babel_version={OLD_VERSION}")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["status"] == "running"
        assert body["babel"] == {
            "default_version": DEFAULT_VERSION,
            "available_versions": {
                OLD_VERSION: f"https://github.com/NCATSTranslator/Babel/blob/main/releases/{OLD_VERSION}/README.md",
                DEFAULT_VERSION: f"https://github.com/NCATSTranslator/Babel/blob/main/releases/{DEFAULT_VERSION}/README.md",
            },
            "indices": {OLD_VERSION: [OLD_INDEX], DEFAULT_VERSION: [DEFAULT_INDEX]},
            "checked_at": "2026-10-23T12:00:00+00:00",
        }
        assert set(body) == {"status", "deployment_environment", "version", "biolink_model_toolkit_version", "babel"}
        assert body["deployment_environment"] == "unknown"
        assert "backend" not in body
        assert body["version"] == handlers.status.get_openapi_version()
        assert body["biolink_model_toolkit_version"] == "test"
        assert "elasticsearch" not in body
        assert self.client.calls == [
            ("get_mapping", {"index": [DEFAULT_INDEX], "filter_path": "*.mappings._meta.src.nodenorm"})
        ]

        selected = self.assert_selected_release(
            self.fetch(f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}"), OLD_VERSION
        )
        assert selected["normalized_curies"] == ["OLD:1"]
        assert self.client.calls[-1][1]["index"] == [OLD_INDEX]

    def test_status_and_requests_use_the_same_selected_build_among_duplicate_indices(self):
        def mapping(version, build_date):
            return {"mappings": {"_meta": {"build_date": build_date, "src": {"nodenorm": {"version": version}}}}}

        self.namespace.elasticsearch.babel = BabelReleases.from_mappings(
            DEFAULT_VERSION,
            {
                DEFAULT_INDEX: mapping(DEFAULT_VERSION, "2026-08-09T00:00:00Z"),
                "z-older-default-build": mapping(DEFAULT_VERSION, "2026-08-01T00:00:00Z"),
                OLD_INDEX: mapping(OLD_VERSION, "2026-09-30T12:31:00Z"),
                OLDER_INDEX: mapping(OLD_VERSION, "2025-11-06T00:00:00Z"),
            },
        )

        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["babel"]["default_version"] == DEFAULT_VERSION
        assert set(body["babel"]["available_versions"]) == {OLD_VERSION, DEFAULT_VERSION}
        assert body["babel"]["indices"] == {OLD_VERSION: [OLD_INDEX], DEFAULT_VERSION: [DEFAULT_INDEX]}
        assert "errors" not in body["babel"]
        assert self.client.calls == [
            ("get_mapping", {"index": [DEFAULT_INDEX], "filter_path": "*.mappings._meta.src.nodenorm"})
        ]

        self.client.calls.clear()
        selected = self.assert_selected_release(
            self.fetch(f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}"), OLD_VERSION
        )
        semantic_types = self.assert_selected_release(
            self.fetch(f"/get_semantic_types?babel_version={OLD_VERSION}"), OLD_VERSION
        )
        default = self.assert_selected_release(self.fetch("/get_setid?curie=INPUT:1"), DEFAULT_VERSION)

        assert selected["normalized_curies"] == ["OLD:1"]
        assert set(semantic_types["semantic_types"]["types"]) == {"biolink:Disease", "biolink:disease"}
        assert default["normalized_curies"] == ["NEW:1"]
        assert [(operation, kwargs["index"]) for operation, kwargs in self.client.calls] == [
            ("msearch", [OLD_INDEX]),
            ("search", [OLD_INDEX]),
            ("msearch", [DEFAULT_INDEX]),
        ]
        assert self.namespace.elasticsearch.indices == [DEFAULT_INDEX]

    def test_default_and_explicit_selector_use_alias_build_even_when_a_newer_copy_exists(self):
        def mapping(version, build_date):
            return {"mappings": {"_meta": {"build_date": build_date, "src": {"nodenorm": {"version": version}}}}}

        self.namespace.elasticsearch.babel = BabelReleases.from_mappings(
            OLD_VERSION,
            {
                OLD_INDEX: mapping(OLD_VERSION, "2026-09-30T12:31:00Z"),
                OLDER_INDEX: mapping(OLD_VERSION, "2025-11-06T00:00:00Z"),
                DEFAULT_INDEX: mapping(DEFAULT_VERSION, "2026-08-09T00:00:00Z"),
            },
            default_index=OLDER_INDEX,
        )
        self.namespace.elasticsearch.indices = [OLDER_INDEX]

        for url in ("/get_setid?curie=INPUT:1", f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}"):
            body = self.assert_selected_release(self.fetch(url), OLD_VERSION)
            assert body["normalized_curies"] == ["OLD:1"]
        assert [kwargs["index"] for _, kwargs in self.client.calls] == [[OLDER_INDEX], [OLDER_INDEX]]

        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["babel"]["default_version"] == OLD_VERSION
        assert body["babel"]["indices"] == {OLD_VERSION: [OLDER_INDEX], DEFAULT_VERSION: [DEFAULT_INDEX]}

    def test_status_reports_only_the_alias_backing_indices_for_a_multi_index_default(self):
        def mapping(version, build_date):
            return {"mappings": {"_meta": {"build_date": build_date, "src": {"nodenorm": {"version": version}}}}}

        default_indices = ["default-build-a", "default-build-b"]
        default_mappings = {index: mapping(DEFAULT_VERSION, "2026-08-01T00:00:00Z") for index in default_indices}
        self.namespace.elasticsearch.babel = BabelReleases.from_mappings(
            DEFAULT_VERSION,
            {
                **default_mappings,
                "unselected-default-build": mapping(DEFAULT_VERSION, "2026-08-09T00:00:00Z"),
                OLD_INDEX: mapping(OLD_VERSION, "2026-09-30T12:31:00Z"),
            },
            default_index=tuple(default_indices),
        )
        self.namespace.elasticsearch.indices = default_indices
        get_mapping = AsyncMock(return_value=default_mappings)
        self.client.indices.get_mapping = get_mapping

        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["babel"]["indices"] == {OLD_VERSION: [OLD_INDEX], DEFAULT_VERSION: default_indices}
        assert set(body["babel"]["indices"]) == set(body["babel"]["available_versions"])
        get_mapping.assert_awaited_once_with(index=default_indices, filter_path="*.mappings._meta.src.nodenorm")

    def test_status_does_not_depend_on_cluster_node_access(self):
        self.client.cat_error = True

        response = self.fetch("/status")

        assert response.code == 200
        body = json.loads(response.body)
        assert body["status"] == "running"
        assert body["babel"]["default_version"] == DEFAULT_VERSION
        assert set(body["babel"]["available_versions"]) == {OLD_VERSION, DEFAULT_VERSION}
        assert "errors" not in body["babel"]
        assert "backend" not in body
        assert "elasticsearch" not in body
        assert body["version"] == handlers.status.get_openapi_version()
        assert body["biolink_model_toolkit_version"] == "test"
        assert self.client.calls == [
            ("get_mapping", {"index": [DEFAULT_INDEX], "filter_path": "*.mappings._meta.src.nodenorm"})
        ]

    def test_status_reuses_health_check_and_keeps_request_headers_separate(self):
        first = self.fetch("/status")
        second = self.fetch("/status", headers={"Origin": "https://example.org"})

        assert first.code == second.code == 200
        assert len(self.client.calls) == 1
        assert first.headers["Cache-Control"] == second.headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in first.headers
        assert second.headers["Access-Control-Allow-Origin"] == "*"

    def test_status_exposes_retained_snapshot_after_discovery_failure(self):
        self.namespace.elasticsearch.babel_checked_at = "2026-10-23T12:00:00+00:00"
        self.namespace.elasticsearch.babel_refresh_error = {"code": "discovery_failed"}

        response = self.fetch("/status")

        assert response.code == 200
        babel = json.loads(response.body)["babel"]
        assert babel["checked_at"] == "2026-10-23T12:00:00+00:00"
        assert babel["refresh_error"] == {"code": "discovery_failed"}
        assert babel["default_version"] == DEFAULT_VERSION

    def test_status_does_not_cache_success_for_the_ten_minute_discovery_interval(self):
        with patch("nodenorm.status_health.monotonic", return_value=0):
            assert self.fetch("/status").code == 200
        self.client.failed_index = DEFAULT_INDEX
        with patch("nodenorm.status_health.monotonic", return_value=5):
            failed = self.fetch("/status")
            assert failed.code == 503
            assert failed.headers["Cache-Control"] == "no-store"
            assert self.fetch("/status").code == 503
        assert len(self.client.calls) == 2
        self.client.failed_index = None
        with patch("nodenorm.status_health.monotonic", return_value=6):
            assert self.fetch("/status").code == 200

    @gen_test
    async def test_background_discovery_switches_status_and_routing_without_changing_inflight_request(self):
        await self.http_client.fetch(self.get_url("/status"))
        started = asyncio.Event()
        release_request = asyncio.Event()
        original_msearch = self.client.msearch

        async def delayed_msearch(**kwargs):
            started.set()
            await release_request.wait()
            return await original_msearch(**kwargs)

        self.client.msearch = delayed_msearch
        inflight = self.http_client.fetch(self.get_url("/get_setid?curie=INPUT:1"))
        await started.wait()
        old_snapshot = self.namespace.elasticsearch.babel
        self.namespace.config.elasticsearch = {"ES_INDEX": "", "ES_ALIAS": "alias"}
        self.client.indices.exists_alias = AsyncMock(return_value=True)
        mappings = {
            DEFAULT_INDEX: {"mappings": {"_meta": {"src": {"nodenorm": {"version": DEFAULT_VERSION}}}}},
            OLD_INDEX: {"mappings": {"_meta": {"src": {"nodenorm": {"version": OLD_VERSION}}}}},
        }

        async def get_mapping(*, index=None, **kwargs):
            if index == ["alias"]:
                return {OLD_INDEX: mappings[OLD_INDEX]}
            return mappings if index is None else {name: mappings[name] for name in index}

        self.client.indices.get_mapping = AsyncMock(side_effect=get_mapping)
        namespace = NodeNormalizationAPINamespace.__new__(NodeNormalizationAPINamespace)
        namespace.config = self.namespace.config
        namespace.elasticsearch = self.namespace.elasticsearch
        assert await namespace.refresh_babel_releases()
        assert old_snapshot.default_version == DEFAULT_VERSION
        release_request.set()
        previous = await inflight
        assert self.assert_selected_release(previous, DEFAULT_VERSION)["normalized_curies"] == ["NEW:1"]
        self.client.msearch = original_msearch

        current = await self.http_client.fetch(self.get_url("/get_setid?curie=INPUT:1"))
        assert self.assert_selected_release(current, OLD_VERSION)["normalized_curies"] == ["OLD:1"]
        status = await self.http_client.fetch(self.get_url("/status"))
        babel = json.loads(status.body)["babel"]
        assert babel["default_version"] == OLD_VERSION
        assert babel["indices"][OLD_VERSION] == self.namespace.elasticsearch.indices == [OLD_INDEX]
        assert babel["checked_at"] == self.namespace.elasticsearch.babel_checked_at
        assert self.client.indices.get_mapping.await_args.kwargs["index"] == [OLD_INDEX]

    def test_status_reports_configured_environment_independently_of_request_host(self):
        for environment in ("CI", "Prod", "Test", "unknown"):
            self.namespace.config.deployment_environment = environment
            for headers in (
                {},
                {"Host": "nodenorm.prod.example"},
                {"Host": "nodenorm.test.example", "X-Forwarded-Host": "nodenorm.ci.example"},
            ):
                with self.subTest(environment=environment, headers=headers):
                    response = self.fetch("/status", headers=headers)

                    assert response.code == 200
                    assert json.loads(response.body)["deployment_environment"] == environment

    def test_cors_exposes_selected_release_header(self):
        response = self.fetch(
            f"/get_setid?curie=INPUT:1&babel_version={OLD_VERSION}",
            headers={"Origin": "https://example.org"},
        )

        self.assert_selected_release(response, OLD_VERSION)
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert "X-Babel-Version" in response.headers["Access-Control-Expose-Headers"]
