import dataclasses
import json
import logging
import time
from typing import Union

from tornado.web import HTTPError

from nodenorm.biolink import toolkit
from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.namespace import NodeNormalizationAPINamespace

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@dataclasses.dataclass(frozen=True)
class NormalizedNode:
    curie: str
    canonical_identifier: str
    preferred_label: str
    information_content: float
    identifiers: list[str]
    types: list[str]
    taxa: list[str]


class NormalizedNodesHandler(NodeNormalizationBaseHandler):
    """
    Mirror implementation to the renci implementation found at
    https://nodenormalization-sri.renci.org/docs

    We intend to mirror the /get_normalized_nodes endpoint
    """

    name = "normalizednodes"

    async def get(self):
        normalized_curies = self.get_arguments("curie")
        if len(normalized_curies) == 0:
            raise HTTPError(
                detail="Missing curie argument, there must be at least one curie to normalize", status_code=400
            )

        def parse_boolean(argument: Union[str, bool]) -> bool:
            if isinstance(argument, bool):
                return argument
            if isinstance(argument, str):
                return not argument.lower() == "false"
            return False

        conflate = parse_boolean(self.get_argument("conflate", True))
        drug_chemical_conflate = parse_boolean(self.get_argument("drug_chemical_conflate", False))
        description = parse_boolean(self.get_argument("description", False))
        individual_types = parse_boolean(self.get_argument("individual_types", False))

        normalized_nodes = await get_normalized_nodes(
            self.biothings,
            normalized_curies,
            conflate,
            drug_chemical_conflate,
            include_descriptions=description,
            include_individual_types=individual_types,
        )

        # If curie contains at least one entry, then the only way normalized_nodes could be blank
        # would be if an error occurred during processing.
        if not normalized_nodes:
            raise HTTPError(detail="Error occurred during processing.", status_code=500)

        self.finish(normalized_nodes)

    async def post(self):
        """
        Returns the equivalent identifiers and semantic types for the curie(s)

        Example body
        {
          "curie": [
            "MESH:D014867",
            "NCIT:C34373"
          ]
        }

        Example output
        {
          "MESH:D014867": {
            "id": {
              "identifier": "CHEBI:15377",
              "label": "Water"
            },
            "equivalent_identifiers": [
              {
                "identifier": "CHEBI:15377",
                "label": "water"
              },
              ...
            ],
            "type": [
              "biolink:SmallMolecule",
              "biolink:MolecularEntity",
              "biolink:ChemicalEntity",
              "biolink:PhysicalEssence",
              "biolink:ChemicalOrDrugOrTreatment",
              "biolink:ChemicalEntityOrGeneOrGeneProduct",
              "biolink:ChemicalEntityOrProteinOrPolypeptide",
              "biolink:NamedThing",
              "biolink:PhysicalEssenceOrOccurrent"
            ],
            "information_content": 47.7
          },
          "NCIT:C34373": {
            "id": {
              "identifier": "MONDO:0004976",
              "label": "amyotrophic lateral sclerosis"
            },
            "equivalent_identifiers": [
              {
                "identifier": "MONDO:0004976",
                "label": "amyotrophic lateral sclerosis"
              },
              ...
            ],
            "type": [
              "biolink:Disease",
              "biolink:DiseaseOrPhenotypicFeature",
              "biolink:BiologicalEntity",
              "biolink:ThingWithTaxon",
              "biolink:NamedThing"
            ],
            "information_content": 74.9
          }
        }
        """
        post_body: dict = json.loads(self.request.body)
        normalization_curies = post_body.get("curies", [])
        if len(normalization_curies) == 0:
            raise HTTPError(
                detail="Missing curie argument, there must be at least one curie to normalize", status_code=400
            )

        conflate = post_body.get("conflate", True)
        drug_chemical_conflate = post_body.get("drug_chemical_conflate", False)
        description = post_body.get("description", False)
        individual_types = post_body.get("individual_types", False)

        normalized_nodes = await get_normalized_nodes(
            self.biothings,
            normalization_curies,
            conflate,
            drug_chemical_conflate,
            include_descriptions=description,
            include_individual_types=individual_types,
        )

        # If curie contains at least one entry, then the only way normalized_nodes could be blank
        # would be if an error occurred during processing.
        if not normalized_nodes:
            raise HTTPError(detail="Error occurred during processing.", status_code=500)

        self.finish(normalized_nodes)


async def get_normalized_nodes(
    biothings_metadata: NodeNormalizationAPINamespace,
    curies: list[str],
    conflate_gene_protein: bool = False,
    conflate_chemical_drug: bool = False,
    include_descriptions: bool = False,
    include_individual_types: bool = False,
) -> dict:
    start_time = time.perf_counter_ns()

    conflations = {
        "GeneProtein": conflate_gene_protein,
        "DrugChemical": conflate_chemical_drug,
    }

    nodes = await _lookup_curie_metadata(biothings_metadata, curies, conflations)

    normal_nodes = {}
    for aggregate_node in nodes:
        normal_node = await create_normalized_node(
            aggregate_node,
            include_descriptions=include_descriptions,
            include_individual_types=include_individual_types,
            conflations=conflations,
        )
        normal_nodes[aggregate_node.curie] = normal_node

    end_time = time.perf_counter_ns()
    logger.debug(
        (
            f"Normalized {len(curies)} nodes in {(end_time - start_time)/1_000_000:.2f} ms with arguments "
            f"(curies={curies}, conflate_gene_protein={conflate_gene_protein}, conflate_chemical_drug={conflate_chemical_drug}, "
            f"include_descriptions={include_descriptions}, include_individual_types={include_individual_types})"
        )
    )
    return normal_nodes


async def create_normalized_node(
    aggregate_node: NormalizedNode,
    include_descriptions: bool = False,
    include_individual_types: bool = False,
    conflations: dict = None,
) -> dict:
    """
    Construct the output format given the aggregated node data
    from elasticsearch
    """
    normal_node = {}

    # It's possible that we didn't find a canonical_id
    if aggregate_node.canonical_identifier is None:
        return None

    if conflations is None:
        conflations = {}

    # If we have 'None' in the equivalent IDs, skip it so we don't confuse things further down the line.
    if None in aggregate_node.identifiers:
        logging.warning(
            "Filtering none-type values for canonical identifier {%s} among equivalent identifiers [%s]",
            aggregate_node.canonical_identifier,
            aggregate_node.identifiers,
        )
        aggregate_node.identifiers = [eqid for eqid in aggregate_node.identifiers if eqid is not None]
        if not aggregate_node.identifiers:
            logging.warning(
                "Only discovered none-type values for canonical identifier {%s} among filtered equivalent identifiers [%s]",
                aggregate_node.canonical_identifier,
                aggregate_node.identifiers,
            )
            return None

    # If we have 'None' in the canonical types, something went horribly wrong (specifically: we couldn't
    # find the type information for all the eqids for this clique). Return None.
    if None in aggregate_node.types:
        logging.error(
            "No types found for canonical identifier {%s} among types [%s]",
            aggregate_node.canonical_identifier,
            aggregate_node.types,
        )
        return None

    if aggregate_node.preferred_label is not None and aggregate_node.preferred_label != "":
        normal_node = {
            "id": {"identifier": aggregate_node.identifiers[0]["i"], "label": aggregate_node.preferred_label}
        }
    else:
        if aggregate_node.identifiers is not None and len(aggregate_node.identifiers) > 0:
            normal_node = {"id": {"identifier": aggregate_node.identifiers[0]["i"]}}
        else:
            normal_node = {"id": {"identifier": aggregate_node.canonical_identifier}}

    # if descriptions are enabled, collect all available descriptions and use the first as the preferred one
    if include_descriptions:
        descriptions = unique_list(
            [
                description
                for identifier in aggregate_node.identifiers
                for description in identifier.get("d", [])
                if description
            ]
        )
        if len(descriptions) > 0:
            normal_node["id"]["description"] = descriptions[0]
            normal_node["descriptions"] = descriptions

    # now need to reformat the identifier keys.  It could be cleaner but we have to worry about if there is a label
    normal_node["equivalent_identifiers"] = []
    for identifier in aggregate_node.identifiers:
        eq_item = {"identifier": identifier["i"]}
        if "l" in identifier:
            eq_item["label"] = identifier["l"]

        # if descriptions is enabled and exist add them to each eq_id entry
        if include_descriptions and "d" in identifier and len(identifier["d"]) > 0:
            eq_item["description"] = identifier["d"][0]

        # if individual types have been requested, add them too.
        if include_individual_types and "t" in identifier:
            eq_item["type"] = identifier["t"][-1]

        normal_node["equivalent_identifiers"].append(eq_item)

    normal_node["type"] = aggregate_node.types

    # add the info content to the node if we got one
    if aggregate_node.information_content is not None:
        normal_node["information_content"] = aggregate_node.information_content

    normal_node["taxa"] = aggregate_node.taxa

    return normal_node


async def _lookup_curie_metadata(
    biothings_metadata: NodeNormalizationAPINamespace, curies: list[str], conflations: dict
) -> list[NormalizedNode]:
    """
    Handles the lookup process for the CURIE identifiers within our elasticsearch instance

    Ported from the redis instance, this performs one batch lookup call through Elasticsearch
    msearch, with one size-1 search per input CURIE. We expect a 1-1 mapping for CURIE
    identifier to document; upstream data processing is responsible for resolving duplicates.
    """
    identifier_result_lookup, malformed_curies = await _lookup_equivalent_identifiers(biothings_metadata, curies)

    nodes = []
    for input_curie in curies:
        if input_curie in malformed_curies:
            node = NormalizedNode(
                curie=input_curie,
                canonical_identifier=None,
                preferred_label=None,
                information_content=-1.0,
                identifiers=[],
                types=[],
                taxa=[],
            )
            nodes.append(node)
        else:
            result = identifier_result_lookup[input_curie]
            result_source = result.get("_source", {})
            identifiers = result_source.get("identifiers", [])
            biolink_type = result_source.get("type", None)
            preferred_label = result_source.get("preferred_name", None)
            taxa = result_source.get("taxa", [])

            # Every equivalent identifier here has the same type.
            for eqid in identifiers:
                eqid.update({"t": [biolink_type]})

            try:
                canonical_identifier = identifiers[0].get("i", None)
            except IndexError:
                canonical_identifier = None
            finally:
                if canonical_identifier is None:
                    continue
            try:
                information_content = round(float(result_source.get("ic", None)), 1)
                if information_content == 0.0:
                    information_content = None
            except TypeError:
                information_content = None

            node_types = await _populate_biolink_type_ancestors(biolink_type, canonical_identifier)

            conflation_identifiers = []
            conflation_information = identifiers[0].get("c", {})
            if conflations.get("GeneProtein", False):
                gene_protein_identifiers = conflation_information.get("gp", None)
                if gene_protein_identifiers is not None:
                    conflation_identifiers.extend(gene_protein_identifiers)

            if conflations.get("DrugChemical", False):
                drug_chemical_identifiers = conflation_information.get("dc", None)
                if drug_chemical_identifiers is not None:
                    conflation_identifiers.extend(drug_chemical_identifiers)

            if any(conflations.values()) and len(conflation_identifiers) > 0:
                conflation_result_lookup, malformed_conflation_curies = await _lookup_equivalent_identifiers(
                    biothings_metadata, conflation_identifiers
                )

                replacement_identifiers = []
                replacement_types = []
                skipped_conflation_curies = []
                conflation_label_discovered = False
                for conflation_curie in conflation_identifiers:
                    if conflation_curie in malformed_conflation_curies:
                        skipped_conflation_curies.append(conflation_curie)
                        continue

                    conflation_result = conflation_result_lookup.get(conflation_curie)
                    if not conflation_result:
                        skipped_conflation_curies.append(conflation_curie)
                        continue

                    conflation_biolink_type = conflation_result.get("_source", {}).get("type", [])
                    conflation_identifier_lookup = conflation_result.get("_source", {}).get("identifiers", [])
                    if not conflation_identifier_lookup:
                        skipped_conflation_curies.append(conflation_curie)
                        continue

                    for conflation_entry in conflation_identifier_lookup:
                        conflation_entry.update({"t": [conflation_biolink_type]})

                    conflation_types = await _populate_biolink_type_ancestors(
                        conflation_biolink_type, conflation_identifier_lookup[0].get("i", None)
                    )

                    replacement_identifiers += conflation_identifier_lookup
                    replacement_types += conflation_types

                    conflation_preferred_label = conflation_result.get("_source", {}).get("preferred_name", None)
                    if conflation_preferred_label is not None and not conflation_label_discovered:
                        preferred_label = conflation_preferred_label
                        conflation_label_discovered = True

                replacement_types = unique_list(replacement_types)

                if not replacement_identifiers:
                    logger.warning(
                        "Unable to resolve any conflation CURIEs for %s; falling back to base normalized node. "
                        "Skipped conflation CURIEs: %s",
                        input_curie,
                        skipped_conflation_curies or conflation_identifiers,
                    )
                    node = NormalizedNode(
                        curie=input_curie,
                        canonical_identifier=canonical_identifier,
                        preferred_label=preferred_label,
                        information_content=information_content,
                        identifiers=identifiers,
                        types=node_types,
                        taxa=taxa,
                    )
                else:
                    node = NormalizedNode(
                        curie=input_curie,
                        canonical_identifier=canonical_identifier,
                        preferred_label=preferred_label,
                        information_content=information_content,
                        identifiers=replacement_identifiers,
                        types=replacement_types,
                        taxa=taxa,
                    )
                    if skipped_conflation_curies:
                        logger.warning(
                            "Skipped %s conflation CURIEs while normalizing %s: %s",
                            len(skipped_conflation_curies),
                            input_curie,
                            skipped_conflation_curies[:10],
                        )
                nodes.append(node)
            else:
                node = NormalizedNode(
                    curie=input_curie,
                    canonical_identifier=canonical_identifier,
                    preferred_label=preferred_label,
                    information_content=information_content,
                    identifiers=identifiers,
                    types=node_types,
                    taxa=taxa,
                )
                nodes.append(node)
    return nodes


async def _populate_biolink_type_ancestors(biolink_type: Union[str, list[str]], canonical_identifier: str) -> list[str]:
    if not isinstance(biolink_type, list):
        biolink_type = [biolink_type]

    biolink_type_tree = []
    for bltype in biolink_type:
        if not bltype:
            fallback_type = "biolink:NamedThing"
            logging.error(
                "No type information found for '%s'. Default type set to -> '%s'",
                canonical_identifier,
                fallback_type,
            )
            biolink_type_tree.append(fallback_type)
        else:
            for anc in toolkit.get_ancestors(bltype):
                biolink_type_tree.append(toolkit.get_element(anc)["class_uri"])

    # We need to remove `biolink:Entity` from the types returned.
    # (See explanation at https://github.com/TranslatorSRI/NodeNormalization/issues/173)
    try:
        biolink_type_tree.remove("biolink:Entity")
    except ValueError:
        pass
    return biolink_type_tree


def unique_list(seq) -> list:
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]


async def _lookup_equivalent_identifiers(
    biothings_metadata: NodeNormalizationAPINamespace, curies: list[str]
) -> tuple[dict, set]:
    if len(curies) == 0:
        return {}, set()

    source_fields = ["identifiers", "type", "ic", "preferred_name", "taxa"]
    search_indices = biothings_metadata.elasticsearch.indices

    searches = []
    for curie in curies:
        searches.append({})
        searches.append(
            {
                "query": {"bool": {"filter": [{"terms": {"identifiers.i": [curie]}}]}},
                "size": 1,
                "track_total_hits": True,
                "_source": source_fields,
            }
        )

    msearch_result = await biothings_metadata.elasticsearch.async_client.msearch(
        index=search_indices,
        searches=searches,
    )

    # Post processing to ensure we can identify invalid curies provided by the query
    identifier_result_lookup = {}
    malformed_curies = set()
    responses = msearch_result.body["responses"]
    if len(responses) != len(curies):
        raise RuntimeError(
            f"Elasticsearch msearch returned {len(responses)} responses for {len(curies)} CURIEs; "
            "unable to safely align lookup results."
        )

    for curie, response in zip(curies, responses):
        if "error" in response:
            raise RuntimeError(f"Elasticsearch msearch failed for CURIE {curie}: {response['error']}")

        hits_metadata = response.get("hits", {})
        total_hits = hits_metadata.get("total", 0)
        if isinstance(total_hits, dict):
            total_hits = total_hits.get("value", 0)

        hits = hits_metadata.get("hits", [])
        if len(hits) == 0:
            malformed_curies.add(curie)
            continue

        if total_hits > 1:
            logger.warning(
                "Expected 1 Elasticsearch document for CURIE %s but found %s. Returning first hit %s.",
                curie,
                total_hits,
                hits[0].get("_id"),
            )

        identifier_result_lookup[curie] = hits[0]

    return identifier_result_lookup, malformed_curies
