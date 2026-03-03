import logging

from biothings.web.handlers import BaseHandler


logger = logging.getLogger(__name__)


class ValidConflationsHandler(BaseHandler):
    name = "allowed-conflations"

    async def get(self):
        conflations = ["GeneProtein", "DrugChemical"]
        self.finish(conflations)

    async def head(self):
        conflations = ["GeneProtein", "DrugChemical"]
        self.finish(conflations)
