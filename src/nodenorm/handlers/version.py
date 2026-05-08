import asyncio

from biothings.web.handlers import BaseHandler

from nodenorm.version import get_version


class VersionHandler(BaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        version = await asyncio.to_thread(get_version)
        self.write({"version": version})
