import importlib.util
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application


class FakeBiolinkToolkit:
    def get_ancestors(self, biolink_type):
        return [biolink_type]

    def get_element(self, ancestor):
        return {"class_uri": ancestor}


def load_normalized_nodes_module():
    module_name = "_normalized_nodes_under_test"
    module_path = Path(__file__).parents[1] / "src" / "nodenorm" / "handlers" / "normalized_nodes.py"
    fake_biolink = ModuleType("nodenorm.biolink")
    fake_biolink.BIOLINK_MODEL_VERSION = "test"
    fake_biolink.toolkit = FakeBiolinkToolkit()

    original_biolink = sys.modules.get("nodenorm.biolink")
    sys.modules["nodenorm.biolink"] = fake_biolink
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        if original_biolink is None:
            sys.modules.pop("nodenorm.biolink", None)
        else:
            sys.modules["nodenorm.biolink"] = original_biolink

    return module


normalized_nodes = load_normalized_nodes_module()
_lookup_curie_metadata = normalized_nodes._lookup_curie_metadata
_lookup_equivalent_identifiers = normalized_nodes._lookup_equivalent_identifiers
create_normalized_node = normalized_nodes.create_normalized_node
get_normalized_nodes = normalized_nodes.get_normalized_nodes
NormalizedNode = normalized_nodes.NormalizedNode


class FakeAsyncElasticsearch:
    def __init__(self, response_batches):
        self.response_batches = list(response_batches)
        self.calls = []

    async def msearch(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(body={"responses": self.response_batches.pop(0)})


def fake_namespace(response_batches, indices=None):
    return SimpleNamespace(
        elasticsearch=SimpleNamespace(
            async_client=FakeAsyncElasticsearch(response_batches),
            indices=indices or ["nodenorm"],
        )
    )


def hit_response(curie, source=None, total=1):
    if source is None:
        source = {
            "identifiers": [{"i": curie, "l": curie}],
            "type": "biolink:ChemicalEntity",
            "ic": 1.0,
            "preferred_name": curie,
            "taxa": [],
        }
    return {"hits": {"total": {"value": total}, "hits": [{"_id": curie, "_source": source}]}}


def no_hit_response():
    return {"hits": {"total": {"value": 0}, "hits": []}}


@pytest.mark.asyncio
async def test_create_normalized_node_aggregates_descriptions_when_requested():
    node = NormalizedNode(
        curie="NCIT:C34373",
        canonical_identifier="MONDO:0004976",
        preferred_label="amyotrophic lateral sclerosis",
        information_content=74.9,
        identifiers=[
            {"i": "MONDO:0004976", "l": "amyotrophic lateral sclerosis", "d": ["first description"]},
            {"i": "NCIT:C34373", "l": "Amyotrophic Lateral Sclerosis", "d": ["second description"]},
            {"i": "UMLS:C0002736", "l": "Amyotrophic Lateral Sclerosis", "d": ["first description", ""]},
            {"i": "MESH:D000690", "l": "Amyotrophic Lateral Sclerosis"},
        ],
        types=["biolink:Disease"],
        taxa=[],
    )

    response = await create_normalized_node(node, include_descriptions=True)

    assert response["id"]["description"] == "first description"
    assert response["descriptions"] == ["first description", "second description"]
    assert response["equivalent_identifiers"][0]["description"] == "first description"
    assert response["equivalent_identifiers"][1]["description"] == "second description"


@pytest.mark.asyncio
async def test_create_normalized_node_hides_descriptions_by_default():
    node = NormalizedNode(
        curie="NCIT:C34373",
        canonical_identifier="MONDO:0004976",
        preferred_label="amyotrophic lateral sclerosis",
        information_content=74.9,
        identifiers=[{"i": "MONDO:0004976", "l": "amyotrophic lateral sclerosis", "d": ["first description"]}],
        types=["biolink:Disease"],
        taxa=[],
    )

    response = await create_normalized_node(node)

    assert "description" not in response["id"]
    assert "descriptions" not in response
    assert "description" not in response["equivalent_identifiers"][0]


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_uses_shared_msearch_index():
    namespace = fake_namespace([[hit_response("CHEBI:17310"), no_hit_response()]])

    lookup, malformed = await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310", "MISSING:1"])

    assert set(lookup) == {"CHEBI:17310"}
    assert malformed == {"MISSING:1"}

    msearch_call = namespace.elasticsearch.async_client.calls[0]
    assert msearch_call["index"] == ["nodenorm"]
    assert msearch_call["searches"][0] == {}
    assert msearch_call["searches"][1]["query"]["bool"]["filter"][0]["terms"] == {"identifiers.i": ["CHEBI:17310"]}
    assert msearch_call["searches"][2] == {}
    assert msearch_call["searches"][3]["query"]["bool"]["filter"][0]["terms"] == {"identifiers.i": ["MISSING:1"]}


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_rejects_msearch_response_count_mismatch():
    namespace = fake_namespace([[hit_response("CHEBI:17310")]])

    with pytest.raises(RuntimeError, match="returned 1 responses for 2 CURIEs"):
        await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310", "CHEBI:12"])


@pytest.mark.asyncio
async def test_lookup_equivalent_identifiers_raises_on_per_search_error():
    namespace = fake_namespace([[{"error": {"type": "query_shard_exception", "reason": "boom"}}]])

    with pytest.raises(RuntimeError, match="Elasticsearch msearch failed for CURIE CHEBI:17310"):
        await _lookup_equivalent_identifiers(namespace, ["CHEBI:17310"])


@pytest.mark.asyncio
async def test_lookup_curie_metadata_falls_back_when_all_conflation_curies_are_missing(caplog):
    base_source = {
        "identifiers": [{"i": "BASE:1", "l": "base", "c": {"dc": ["MISSING:1"]}}],
        "type": "biolink:ChemicalEntity",
        "ic": 1.0,
        "preferred_name": "base",
        "taxa": [],
    }
    namespace = fake_namespace([[hit_response("BASE:1", base_source)], [no_hit_response()]])

    with caplog.at_level(logging.WARNING):
        nodes = await _lookup_curie_metadata(namespace, ["BASE:1"], {"DrugChemical": True})

    assert len(nodes) == 1
    assert nodes[0].curie == "BASE:1"
    assert [identifier["i"] for identifier in nodes[0].identifiers] == ["BASE:1"]
    assert "falling back to base normalized node" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_lookup_curie_metadata_logs_skipped_conflation_curies_once(caplog):
    base_source = {
        "identifiers": [{"i": "BASE:1", "l": "base", "c": {"dc": ["MISSING:1", "CONF:1"]}}],
        "type": "biolink:ChemicalEntity",
        "ic": 1.0,
        "preferred_name": "base",
        "taxa": [],
    }
    conflation_source = {
        "identifiers": [{"i": "CONF:1", "l": "conflated"}],
        "type": "biolink:Drug",
        "ic": 2.0,
        "preferred_name": "conflated",
        "taxa": [],
    }
    namespace = fake_namespace(
        [
            [hit_response("BASE:1", base_source)],
            [no_hit_response(), hit_response("CONF:1", conflation_source)],
        ]
    )

    with caplog.at_level(logging.WARNING):
        nodes = await _lookup_curie_metadata(namespace, ["BASE:1"], {"DrugChemical": True})

    assert len(nodes) == 1
    assert [identifier["i"] for identifier in nodes[0].identifiers] == ["CONF:1"]
    skip_logs = [record for record in caplog.records if "Skipped 1 conflation CURIEs" in record.message]
    assert len(skip_logs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", [[], ["biolink:Drug"], ["biolink:Drug", "biolink:Gene"], {}, 0, False])
async def test_invalid_source_type_returns_null_without_mutating_identifiers(source_type, caplog):
    source = {
        "type": source_type,
        "identifiers": [{"i": "CANONICAL:1", "t": ["existing metadata"]}, {"i": "INPUT:1"}],
    }
    original_source = deepcopy(source)
    namespace = fake_namespace([[hit_response("DOCUMENT:1", source)]])

    with caplog.at_level(logging.ERROR):
        response = await get_normalized_nodes(namespace, ["INPUT:1"], include_individual_types=True)

    assert response == {"INPUT:1": None}
    assert source == original_source
    errors = [record for record in caplog.records if getattr(record, "error_code", None) == "invalid_source_type"]
    assert len(errors) == 1
    assert errors[0].levelno >= logging.ERROR
    assert errors[0].input_curie == "INPUT:1"
    assert errors[0].document_id == "DOCUMENT:1"
    assert errors[0].source_type == source_type
    assert errors[0].stage == "base"


@pytest.mark.asyncio
async def test_mixed_batch_keeps_valid_results_and_nulls_corrupt_sources():
    corrupt_source = {
        "type": ["biolink:OrganismTaxon", "biolink:ChemicalEntity"],
        "identifiers": [{"i": "CORRUPT:1"}],
    }
    namespace = fake_namespace(
        [[hit_response("VALID:1"), hit_response("CORRUPT:1", corrupt_source), no_hit_response()]]
    )

    response = await get_normalized_nodes(
        namespace, ["VALID:1", "CORRUPT:1", "MISSING:1"], include_individual_types=True
    )

    assert set(response) == {"VALID:1", "CORRUPT:1", "MISSING:1"}
    assert response["CORRUPT:1"] is None
    assert response["MISSING:1"] is None
    assert response["VALID:1"]["id"]["identifier"] == "VALID:1"
    assert response["VALID:1"]["type"] == ["biolink:ChemicalEntity"]
    assert response["VALID:1"]["equivalent_identifiers"][0]["type"] == "biolink:ChemicalEntity"


@pytest.mark.asyncio
@pytest.mark.parametrize("conflation_key,option", [("gp", "conflate_gene_protein"), ("dc", "conflate_chemical_drug")])
@pytest.mark.parametrize("source_type", [[], ["biolink:Drug"], ["biolink:Drug", "biolink:Gene"]])
@pytest.mark.parametrize("has_valid_partner", [False, True])
async def test_corrupt_conflation_partner_nulls_original_input(
    conflation_key, option, source_type, has_valid_partner, caplog
):
    conflation_curies = ["PARTNER:VALID", "PARTNER:CORRUPT"] if has_valid_partner else ["PARTNER:CORRUPT"]
    base_source = {
        "type": "biolink:ChemicalEntity",
        "identifiers": [{"i": "BASE:CANONICAL", "c": {conflation_key: conflation_curies}}, {"i": "INPUT:1"}],
    }
    corrupt_source = {
        "type": source_type,
        "identifiers": [{"i": "PARTNER:CORRUPT", "t": ["existing metadata"]}],
    }
    original_corrupt_source = deepcopy(corrupt_source)
    conflation_responses = [hit_response("CORRUPT:DOCUMENT", corrupt_source)]
    if has_valid_partner:
        conflation_responses.insert(0, hit_response("PARTNER:VALID"))
    namespace = fake_namespace(
        [[hit_response("BASE:DOCUMENT", base_source), hit_response("UNRELATED:1")], conflation_responses]
    )

    with caplog.at_level(logging.ERROR):
        response = await get_normalized_nodes(
            namespace, ["INPUT:1", "UNRELATED:1"], include_individual_types=True, **{option: True}
        )

    assert set(response) == {"INPUT:1", "UNRELATED:1"}
    assert response["INPUT:1"] is None
    assert response["UNRELATED:1"]["id"]["identifier"] == "UNRELATED:1"
    assert corrupt_source == original_corrupt_source
    errors = [record for record in caplog.records if getattr(record, "error_code", None) == "invalid_source_type"]
    assert len(errors) == 1
    assert errors[0].input_curie == "INPUT:1"
    assert errors[0].document_id == "CORRUPT:DOCUMENT"
    assert errors[0].source_type == source_type
    assert errors[0].stage == "conflation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conflation_key,option,source_types",
    [
        ("gp", "conflate_gene_protein", ["biolink:Gene", "biolink:Protein"]),
        ("dc", "conflate_chemical_drug", ["biolink:ChemicalEntity", "biolink:Drug"]),
    ],
)
async def test_scalar_sources_conflate_with_ordered_ancestry_and_scalar_individual_types(
    conflation_key, option, source_types, monkeypatch
):
    monkeypatch.setattr(
        normalized_nodes.toolkit,
        "get_ancestors",
        lambda source_type: [
            source_type,
            "biolink:NamedThing",
            "biolink:Entity",
            "biolink:NamedThing",
            "biolink:Entity",
        ],
    )
    base_source = {
        "type": source_types[0],
        "identifiers": [{"i": "INPUT:1", "c": {conflation_key: ["PARTNER:1", "PARTNER:2"]}}],
    }
    partner_sources = [
        {"type": source_type, "identifiers": [{"i": f"PARTNER:{index}"}]}
        for index, source_type in enumerate(source_types, start=1)
    ]
    namespace = fake_namespace(
        [
            [hit_response("INPUT:1", base_source)],
            [hit_response(f"PARTNER:{index}", source) for index, source in enumerate(partner_sources, start=1)],
        ]
    )

    response = await get_normalized_nodes(namespace, ["INPUT:1"], include_individual_types=True, **{option: True})

    node = response["INPUT:1"]
    assert node["type"] == [source_types[0], "biolink:NamedThing", source_types[1]]
    assert node["equivalent_identifiers"] == [
        {"identifier": f"PARTNER:{index}", "type": source_type}
        for index, source_type in enumerate(source_types, start=1)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", [[], ["biolink:Gene"], ["biolink:Gene", "biolink:Protein"], {}, 0, False])
async def test_biolink_ancestor_helper_rejects_non_scalar_types(source_type):
    with pytest.raises(TypeError):
        await normalized_nodes._populate_biolink_type_ancestors(source_type, "INPUT:1")


@pytest.mark.asyncio
async def test_biolink_ancestor_helper_deduplicates_in_order_and_filters_every_entity(monkeypatch):
    monkeypatch.setattr(
        normalized_nodes.toolkit,
        "get_ancestors",
        lambda source_type: [
            source_type,
            "biolink:NamedThing",
            "biolink:Entity",
            source_type,
            "biolink:PhysicalEssence",
            "biolink:Entity",
            "biolink:NamedThing",
        ],
    )

    result = await normalized_nodes._populate_biolink_type_ancestors("biolink:Drug", "INPUT:1")

    assert result == ["biolink:Drug", "biolink:NamedThing", "biolink:PhysicalEssence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["missing", None, ""])
@pytest.mark.parametrize("conflate", [False, True])
async def test_missing_source_type_keeps_named_thing_fallback(source_type, conflate):
    source = {"identifiers": [{"i": "INPUT:1"}]}
    if source_type != "missing":
        source["type"] = source_type
    response_batches = [[hit_response("INPUT:1", source)]]
    if conflate:
        base_source = {
            "type": "biolink:Gene",
            "identifiers": [{"i": "INPUT:1", "c": {"gp": ["PARTNER:1"]}}],
        }
        response_batches = [[hit_response("INPUT:1", base_source)], [hit_response("PARTNER:1", source)]]
    namespace = fake_namespace(response_batches)

    response = await get_normalized_nodes(namespace, ["INPUT:1"], conflate_gene_protein=conflate)

    assert response["INPUT:1"]["type"] == ["biolink:NamedThing"]


class TestInvalidSourceTypeHTTP(AsyncHTTPTestCase):
    def get_app(self):
        app = Application([(r"/get_normalized_nodes", normalized_nodes.NormalizedNodesHandler)])
        app.biothings = fake_namespace(
            [
                [
                    hit_response("CORRUPT:1", {"type": [], "identifiers": [{"i": "CORRUPT:1"}]}),
                    hit_response(
                        "CORRUPT:2",
                        {"type": ["biolink:Gene", "biolink:Protein"], "identifiers": [{"i": "CORRUPT:2"}]},
                    ),
                ]
            ]
        )
        return app

    def test_get_all_corrupt_batch_returns_200_with_nulls(self):
        response = self.fetch("/get_normalized_nodes?curie=CORRUPT%3A1&curie=CORRUPT%3A2")

        assert response.code == 200
        assert json.loads(response.body) == {"CORRUPT:1": None, "CORRUPT:2": None}

    def test_post_all_corrupt_batch_returns_200_with_nulls(self):
        response = self.fetch(
            "/get_normalized_nodes",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"curies": ["CORRUPT:1", "CORRUPT:2"]}),
        )

        assert response.code == 200
        assert json.loads(response.body) == {"CORRUPT:1": None, "CORRUPT:2": None}
