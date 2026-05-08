import asyncio

from biothings.web.handlers import BaseHandler

from nodenorm.version import get_version


class VersionHandler(BaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        loop = asyncio.get_running_loop()
        version = await loop.run_in_executor(None, get_version)
        self.write({"version": version})
