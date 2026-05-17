import logging

from nodenorm.handlers.base import NodeNormalizationBaseHandler

logger = logging.getLogger(__name__)


class ValidConflationsHandler(NodeNormalizationBaseHandler):
    name = "allowed-conflations"

    async def get(self):
        conflations = ["GeneProtein", "DrugChemical"]
        self.finish(conflations)

    async def head(self):
        conflations = ["GeneProtein", "DrugChemical"]
        self.finish(conflations)
