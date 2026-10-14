from nodenorm.handlers.base import NodeNormalizationBaseHandler

ALLOWED_CONFLATIONS = ["GeneProtein", "DrugChemical"]


class ValidConflationsHandler(NodeNormalizationBaseHandler):
    name = "allowed-conflations"

    async def get(self):
        # Wrapped in an object, as NodeNorm Redis does.
        self.finish({"conflations": ALLOWED_CONFLATIONS})

    async def head(self):
        await self.get()
