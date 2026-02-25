from typing import Callable

from nodenorm.handlers.conflations import ValidConflationsHandler
from nodenorm.handlers.health import NodeNormHealthHandler
from nodenorm.handlers.normalized_nodes import NormalizedNodesHandler
from nodenorm.handlers.semantic_types import SemanticTypeHandler
from nodenorm.handlers.set_identifiers import SetIdentifierHandler
from nodenorm.handlers.version import VersionHandler


API_PREFIX = "nodenorm"
API_VERSION = ""

ES_HOST = "http://su10.scripps.edu:9200"
ES_INDEX = "nodenorm_20251106_lv86fxt0"
ES_DOC_TYPE = "node"


def build_handlers() -> dict[str, tuple[str, Callable]]:
    """Generate our handler mapping for the nodenorm API."""

    handler_collection = [
        (r"/get_allowed_conflations?", ValidConflationsHandler),
        (r"/get_normalized_nodes?", NormalizedNodesHandler),
        (r"/get_semantic_types?", SemanticTypeHandler),
        (r"/get_setid?", SetIdentifierHandler),
        (r"/status?", NodeNormHealthHandler),
        (r"/version", VersionHandler),
    ]
    handlers = {handler[0]: handler for handler in handler_collection}
    return handlers
