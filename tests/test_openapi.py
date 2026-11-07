"""
Keep src/nodenorm/webapp/openapi.json in step with the handlers.

The OpenAPI document is written by hand and served as a static file (Tornado has nothing like
FastAPI's schema generation), so nothing stops it drifting from the code except these tests.
If one fails after a change to a handler, edit the JSON; if it fails after a change to the
JSON, the document now promises something the code doesn't do.
"""

import ast
import dataclasses
import importlib.resources
import inspect
import json
import re
import textwrap
from types import SimpleNamespace
from unittest import mock

import jsonschema
import pytest
import tornado.web
from openapi_spec_validator import validate
from tornado.testing import AsyncHTTPTestCase

import nodenorm
from nodenorm.babel import BabelReleases
from nodenorm.handlers import build_handlers
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.handlers.conflations import ValidConflationsHandler
from nodenorm.handlers.normalized_nodes import NormalizedNodesHandler, parse_boolean
from nodenorm.handlers.semantic_types import SemanticTypeHandler
from nodenorm.handlers.set_identifiers import SetIdentifierHandler, SetIDResponse
from nodenorm.handlers.status import NodeNormHealthHandler

SPEC = json.loads((importlib.resources.files(nodenorm) / "webapp" / "openapi.json").read_text(encoding="utf-8"))
RELEASES = BabelReleases("2026jul22", {"2026jul22": "nodenorm", "2025sep1": "nodenorm-old"})

# Only the handlers that answer the API itself: not the static files, index or redirects.
API_HANDLERS = {
    pattern: handler
    for pattern, handler, *_ in build_handlers().values()
    if isinstance(handler, type) and issubclass(handler, NodeNormalizationBaseHandler)
}


def test_document_is_valid_openapi():
    validate(SPEC)


def test_every_route_is_documented_and_every_documented_path_is_routed():
    assert set(SPEC["paths"]) == set(API_HANDLERS)


# Every method Tornado routes except OPTIONS, which the base handler answers for CORS preflight.
HTTP_METHODS = {method.lower() for method in tornado.web.RequestHandler.SUPPORTED_METHODS} - {"options"}


def test_documented_methods_match_the_handlers():
    for path, handler in API_HANDLERS.items():
        documented = set(SPEC["paths"][path]) & HTTP_METHODS
        implemented = {method for method in HTTP_METHODS if method in vars(handler)}
        assert documented == implemented, path


def resolve_reference(value: dict) -> dict:
    """Resolve local JSON pointers, including shared query parameters and headers."""
    while "$ref" in value:
        reference = value["$ref"]
        assert reference.startswith("#/"), reference
        value = SPEC
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def method_calls(handler, method):
    """Inspect a method and its handler helpers without mixing GET and POST fields."""
    visited = set()

    def visit(name):
        if name in visited:
            return
        visited.add(name)
        function = getattr(handler, name, None)
        if function is None or function.__module__ != handler.__module__:
            return
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            yield node
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                yield from visit(node.func.attr)

    return visit(method)


def literal_argument(call):
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def query_parameters(path_item, method):
    parameters = path_item.get("parameters", []) + path_item[method].get("parameters", [])
    return {p["name"] for parameter in parameters if (p := resolve_reference(parameter))["in"] == "query"}


def test_documented_query_parameters_match_the_handlers():
    readers = {"get_argument", "get_arguments", "get_query_argument", "get_query_arguments"}
    for path, handler in API_HANDLERS.items():
        for method in HTTP_METHODS & vars(handler).keys():
            documented = query_parameters(SPEC["paths"][path], method)
            implemented = {
                name
                for call in method_calls(handler, method)
                if call.func.attr in readers and (name := literal_argument(call)) is not None
            }
            assert documented == implemented, (path, method)


def test_query_parameter_check_resolves_shared_parameters_for_post():
    # POST /get_setid keeps its release selector in the query, outside the array body.
    assert query_parameters(SPEC["paths"]["/get_setid"], "post") == {"babel_version"}
    assert query_parameters(SPEC["paths"]["/get_semantic_types"], "get") == {"babel_version"}


def test_query_parameter_check_follows_helpers_and_keeps_methods_separate():
    class Handler:
        def get(self):
            self.read_release()

        def read_release(self):
            return self.get_query_argument("babel_version")

        def post(self):
            return self.get_query_arguments("batch")

    assert [
        literal_argument(call) for call in method_calls(Handler, "get") if call.func.attr == "get_query_argument"
    ] == ["babel_version"]
    assert [literal_argument(call) for call in method_calls(Handler, "post")] == ["batch"]


def resolve(schema: dict) -> dict:
    """Follow a local $ref, and an array to its items, down to the object schema of one body item."""
    schema = resolve_reference(schema)
    if schema.get("type") == "array":
        return resolve(schema["items"])
    return schema


def test_documented_request_bodies_match_the_handlers():
    # Body fields are read with .get("field", default), in post() or a helper it calls.
    posting = {path: handler for path, handler in API_HANDLERS.items() if "post" in vars(handler)}
    assert posting, "no handler defines post()"
    for path, handler in posting.items():
        body_schema = SPEC["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        documented = set(resolve(body_schema)["properties"])
        implemented = {
            name
            for call in method_calls(handler, "post")
            if call.func.attr == "get" and (name := literal_argument(call)) is not None
        }
        assert documented == implemented, path


def test_setid_response_schema_matches_the_dataclass():
    documented = set(SPEC["components"]["schemas"]["SetIDResponse"]["properties"])
    assert documented == {field.name for field in dataclasses.fields(SetIDResponse)}


class TestOpenAPIRedirect(AsyncHTTPTestCase):
    """/openapi.json is where NodeNorm Redis, SmartAPI and Babel Validation look for the document."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application(list(build_handlers().values()))

    def test_openapi_json_redirects_to_the_document(self):
        response = self.fetch("/openapi.json", follow_redirects=False)

        assert response.code == 301
        assert response.headers["Location"] == "/webapp/openapi.json"

    def test_served_document_is_valid_openapi(self):
        # test_document_is_valid_openapi() reads the file; this checks what the static route serves.
        response = self.fetch("/openapi.json")

        assert response.code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        validate(json.loads(response.body))

    def test_only_the_exact_path_redirects(self):
        assert self.fetch("/openapiXjson", follow_redirects=False).code == 404

    def test_api_paths_are_matched_exactly(self):
        # Regression: patterns like r"/get_setid?" made the last letter optional, so /get_seti was routed.
        for path in API_HANDLERS:
            assert self.fetch(path[:-1]).code == 404, path


class TestAllowedConflations(AsyncHTTPTestCase):
    """Regression: this endpoint returned HTTP 500 because it finished a bare list."""

    def get_app(self) -> tornado.web.Application:
        return tornado.web.Application([(r"/get_allowed_conflations", ValidConflationsHandler)])

    def test_response_matches_the_documented_schema(self):
        response = self.fetch("/get_allowed_conflations")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["ConflationList"])
        assert {"GeneProtein", "DrugChemical"} <= set(body["conflations"])

    def test_head_is_answered(self):
        assert self.fetch("/get_allowed_conflations", method="HEAD").code == 200


class TestSetIDErrors(AsyncHTTPTestCase):
    """Answers that /get_setid gives without reaching Elasticsearch."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_setid", SetIdentifierHandler)])
        app.biothings = SimpleNamespace(elasticsearch=SimpleNamespace(babel=RELEASES))
        return app

    def test_get_reports_an_unknown_conflation(self):
        # Regression: generate_setid() returned the dataclass itself here, which Tornado can't write (HTTP 500).
        response = self.fetch("/get_setid?curie=MESH:D014867&conflation=Bogus")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["SetIDResponse"])
        assert "Bogus" in body["error"]
        assert body["setid"] is None

    def test_post_reports_an_unknown_conflation(self):
        body = json.dumps([{"curies": ["MESH:D014867"], "conflations": ["Bogus"]}])
        response = self.fetch("/get_setid", method="POST", body=body)

        assert response.code == 200
        result = json.loads(response.body)
        jsonschema.validate(result["0"], SPEC["components"]["schemas"]["SetIDResponse"])
        assert "Bogus" in result["0"]["error"]

    def test_post_answers_a_set_with_no_curies(self):
        # The other early return in generate_setid(); it too used to return the dataclass (HTTP 500).
        empty_lookup = mock.AsyncMock(return_value={})
        with mock.patch("nodenorm.handlers.set_identifiers.get_normalized_nodes", empty_lookup):
            response = self.fetch("/get_setid", method="POST", body=json.dumps([{"curies": []}]))

        assert response.code == 200
        result = json.loads(response.body)
        jsonschema.validate(result["0"], SPEC["components"]["schemas"]["SetIDResponse"])
        assert result["0"]["normalized_curies"] == [] and result["0"]["setid"] is None

    def test_post_rejects_a_body_that_is_not_an_array_of_objects(self):
        # A single object was the documented body before; it used to fail with a 500.
        for body in ('{"curies": ["MESH:D014867"]}', '["MESH:D014867"]', "[]", "not json", b'[{"curies": ["\xff"]}]'):
            assert self.fetch("/get_setid", method="POST", body=body).code == 400, body


class FakeElasticsearch:
    """Enough of AsyncElasticsearch to exercise the documented response shapes offline."""

    def __init__(self):
        self.calls = []
        self.indices = SimpleNamespace(get_mapping=self.get_mapping)

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        # Real indices lowercase the `type` keyword, so aggregation keys come back lowercased.
        return SimpleNamespace(body={"aggregations": {"unique_types": {"buckets": [{"key": "biolink:disease"}]}}})

    async def msearch(self, **kwargs):
        self.calls.append(kwargs)
        responses = []
        for query in kwargs["searches"][1::2]:
            curie = query["query"]["bool"]["filter"][0]["terms"]["identifiers.i"][0]
            source = {
                "identifiers": [{"i": curie, "l": "Example", "d": ["Example description"]}],
                "type": "biolink:Disease",
                "preferred_name": "Example",
                "taxa": ["NCBITaxon:9606"],
                "ic": 1.0,
            }
            responses.append({"hits": {"total": {"value": 1}, "hits": [{"_id": curie, "_source": source}]}})
        return SimpleNamespace(body={"responses": responses})

    async def get_mapping(self, **kwargs):
        return {
            index: {"mappings": {"_meta": {"src": {"nodenorm": {"version": RELEASES.default_version}}}}}
            for index in kwargs["index"]
        }


class TestMultiBabelResponses(AsyncHTTPTestCase):
    def get_app(self):
        self.client = FakeElasticsearch()
        app = tornado.web.Application(list(build_handlers().values()))
        app.biothings = SimpleNamespace(
            config=SimpleNamespace(deployment_environment="CI"),
            elasticsearch=SimpleNamespace(
                async_client=self.client,
                indices=["nodenorm"],
                babel=RELEASES,
                babel_checked_at="2026-11-02T12:00:00+00:00",
                babel_refresh_error=None,
            ),
        )
        return app

    def validate_response(self, response, path, method):
        assert response.code == 200, response.body
        documented = SPEC["paths"][path][method]["responses"]["200"]
        if path != "/status":
            assert "X-Babel-Version" in documented.get("headers", {})
        schema = documented["content"]["application/json"]["schema"]
        jsonschema.validate(json.loads(response.body), {**schema, "components": SPEC["components"]})
        for name, header in documented.get("headers", {}).items():
            jsonschema.validate(response.headers[name], resolve_reference(header)["schema"])

    def test_selected_release_responses_match_the_document(self):
        for version in RELEASES.available_versions:
            for path, method, body in [
                ("/get_normalized_nodes", "get", None),
                ("/get_normalized_nodes", "post", {"curies": ["MONDO:0004976"], "babel_version": version}),
                ("/get_setid", "get", None),
                ("/get_setid", "post", [{"curies": ["MONDO:0004976"]}]),
                ("/get_semantic_types", "get", None),
            ]:
                if path == "/get_normalized_nodes" and method == "post":
                    url = path
                    body.update(description=True, individual_types=True)
                else:
                    url = f"{path}?babel_version={version}"
                    if method == "get" and path != "/get_semantic_types":
                        url += "&curie=MONDO:0004976"
                options = {"method": method.upper()}
                if body is not None:
                    options["body"] = json.dumps(body)
                    options["headers"] = {"Content-Type": "application/json"}
                response = self.fetch(url, **options)
                self.validate_response(response, path, method)
                assert response.headers["X-Babel-Version"] == version
                assert self.client.calls[-1]["index"] == RELEASES.resolve(version).search_indices

    def test_status_matches_current_metadata_schema(self):
        response = self.fetch("/status")
        self.validate_response(response, "/status", "get")
        body = json.loads(response.body)
        assert body["deployment_environment"] == "CI"
        assert body["babel_version"] == body["babel"]["default_version"]
        assert body["babel_version_url"] == body["babel"]["available_versions"][body["babel_version"]]


class TestSemanticTypes(AsyncHTTPTestCase):
    """Regression: the handler lower-cased every class ("biolink:namedthing"), and then
    reported the lowercased aggregation key next to its capitalised class_uri."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_semantic_types", SemanticTypeHandler)])
        app.biothings = SimpleNamespace(
            elasticsearch=SimpleNamespace(indices="nodenorm", async_client=FakeElasticsearch(), babel=RELEASES)
        )
        return app

    def test_types_are_biolink_class_curies(self):
        response = self.fetch("/get_semantic_types")

        assert response.code == 200
        body = json.loads(response.body)
        jsonschema.validate(body, SPEC["components"]["schemas"]["SemanticTypes"])
        types = body["semantic_types"]["types"]
        assert "biolink:Disease" in types and "biolink:NamedThing" in types
        assert all(re.fullmatch(r"biolink:[A-Z][A-Za-z]*", t) for t in types), types
        assert len({t.lower() for t in types}) == len(types), types


class UnreachableElasticsearch:
    """An AsyncElasticsearch whose cluster is down."""

    class indices:
        @staticmethod
        async def get_mapping(**kwargs):
            raise ConnectionError("cluster unreachable")


class TestStatusWithoutElasticsearch(AsyncHTTPTestCase):
    """An unreachable cluster preserves main's documented HTTP 503 health response."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/status", NodeNormHealthHandler)])
        app.biothings = SimpleNamespace(
            elasticsearch=SimpleNamespace(
                indices="nodenorm",
                async_client=UnreachableElasticsearch(),
                babel=RELEASES,
                babel_checked_at="2026-11-02T12:00:00+00:00",
                babel_refresh_error=None,
            )
        )
        return app

    def test_status_is_unavailable(self):
        response = self.fetch("/status")

        assert response.code == 503
        assert "503" in SPEC["paths"]["/status"]["get"]["responses"]
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("value", [True, "true", "TRUE", "1", "yes", "on", "t", "Y"])
def test_true_flags(value):
    assert parse_boolean("conflate", value) is True


@pytest.mark.parametrize("value", [False, "false", "False", "0", "no", "off", "f", "N"])
def test_false_flags(value):
    assert parse_boolean("conflate", value) is False


@pytest.mark.parametrize("value", ["", "maybe", "2", None, 0])
def test_other_flags_are_rejected(value):
    # Regression: anything but "false" used to mean true, so conflate=0 turned conflation on.
    with pytest.raises(tornado.web.HTTPError) as raised:
        parse_boolean("conflate", value)
    assert raised.value.status_code == 400


class TestNormalizedNodesFlags(AsyncHTTPTestCase):
    """A flag that isn't a boolean is rejected before Elasticsearch is queried."""

    def get_app(self) -> tornado.web.Application:
        app = tornado.web.Application([(r"/get_normalized_nodes", NormalizedNodesHandler)])
        app.biothings = SimpleNamespace()
        return app

    def test_get_rejects_a_flag_that_is_not_a_boolean(self):
        assert self.fetch("/get_normalized_nodes?curie=MESH:D014867&conflate=maybe").code == 400

    def test_post_rejects_a_flag_that_is_not_a_boolean(self):
        body = json.dumps({"curies": ["MESH:D014867"], "conflate": "maybe"})
        assert self.fetch("/get_normalized_nodes", method="POST", body=body).code == 400

    def test_post_rejects_a_body_that_is_not_a_json_object(self):
        # Each of these used to fail with a 500.
        for body in ('["MESH:D014867"]', "not json", b'{"curies": ["\xff"]}'):
            assert self.fetch("/get_normalized_nodes", method="POST", body=body).code == 400, body
