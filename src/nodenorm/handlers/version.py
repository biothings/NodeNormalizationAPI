from nodenorm.handlers.base import NodeNormalizationBaseHandler
from nodenorm.version import get_version


class VersionHandler(NodeNormalizationBaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        self.write({"version": get_version()})
