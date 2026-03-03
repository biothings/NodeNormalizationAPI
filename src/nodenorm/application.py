"""
NodeNormalization specific application builder for overriding
the default builder provided by the biothings.api package

Responsible for generating the tornado.web.Application instance
"""

import logging
from typing import override

from biothings.web.applications import TornadoBiothingsAPI

from nodenorm.handlers import build_handlers
from nodenorm.namespace import NodeNormalizationAPINamespace

logger = logging.getLogger(__name__)


class NodeNormalizationAPI(TornadoBiothingsAPI):

    @override
    @classmethod
    def get_app(cls, namespace: NodeNormalizationAPINamespace):
        """Generator for the TornadoApplication instance."""
        handlers = build_handlers()
        namespace.populate_handlers(handlers)
        settings = namespace.config.webserver["SETTINGS"]
        app = cls(handlers.values(), settings)
        app.biothings = namespace
        return app
