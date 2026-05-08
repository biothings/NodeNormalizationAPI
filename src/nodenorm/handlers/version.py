from biothings.web.handlers import BaseHandler

from nodenorm.version import get_version


class VersionHandler(BaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        self.write({"version": get_version()})
