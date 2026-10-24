"""Short health caching, independent of the ten-minute Babel discovery snapshot."""

import asyncio
import logging
from time import monotonic

from nodenorm.babel import get_babel_version


logger = logging.getLogger(__name__)
HEALTH_CACHE_TTL = 5
HEALTH_FAILURE_TTL = 1
HEALTH_TIMEOUT = 3


class StatusHealthCache:
    def __init__(self, client, release):
        self.client = client
        self.configuration = (release.version, tuple(release.search_indices))
        self._healthy = None
        self._expires_at = 0
        self._refresh = None

    async def check(self):
        if self._healthy is not None and monotonic() < self._expires_at:
            return self._healthy
        if self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self._load())
        # A disconnected probe must not cancel the check shared by other callers.
        return await asyncio.shield(self._refresh)

    async def _probe(self):
        version, indices = self.configuration
        mappings = await self.client.indices.get_mapping(
            index=list(indices), filter_path="*.mappings._meta.src.nodenorm"
        )
        return set(mappings) == set(indices) and all(
            get_babel_version(mapping) == version for mapping in mappings.values()
        )

    async def _load(self):
        try:
            self._healthy = await asyncio.wait_for(self._probe(), timeout=HEALTH_TIMEOUT)
        except Exception:
            logger.warning("NodeNorm Elasticsearch health check failed", exc_info=True)
            self._healthy = False
        self._expires_at = monotonic() + (HEALTH_CACHE_TTL if self._healthy else HEALTH_FAILURE_TTL)
        return self._healthy
