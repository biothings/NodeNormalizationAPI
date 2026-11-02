"""Offline coverage for timed Babel discovery and independent health checks."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tornado.ioloop import IOLoop

from nodenorm import namespace as namespace_module, status_health
from nodenorm.babel import BabelReleases
from nodenorm.namespace import NodeNormalizationAPINamespace
from nodenorm.server import NodeNormalizationWebServer
from nodenorm.status_health import StatusHealthCache


def mapping(version):
    return {"mappings": {"_meta": {"src": {"nodenorm": {"version": version}}}}}


@pytest.fixture
def namespace():
    namespace = NodeNormalizationAPINamespace.__new__(NodeNormalizationAPINamespace)
    namespace.config = SimpleNamespace(elasticsearch={"ES_INDEX": "", "ES_ALIAS": "alias"})
    namespace.elasticsearch = SimpleNamespace(
        async_client=SimpleNamespace(
            indices=SimpleNamespace(
                exists=AsyncMock(return_value=False),
                exists_alias=AsyncMock(return_value=True),
                get_mapping=AsyncMock(
                    side_effect=[
                        {"new": mapping("2026jul22")},
                        {"old": mapping("2025sep1"), "new": mapping("2026jul22")},
                    ]
                ),
            )
        ),
        babel=BabelReleases("2025sep1", {"2025sep1": "old"}),
        indices=["old"],
        babel_checked_at="2026-10-23T12:00:00+00:00",
        babel_refresh_error=None,
    )
    return namespace


def test_server_starts_and_stops_background_discovery(namespace):
    namespace.config.webserver = {"HOST": "localhost", "PORT": 9000, "ENABLE_CURL_CLIENT": False}
    namespace.handlers = {}
    loop = IOLoop(make_current=False)
    loop.add_callback(loop.stop)
    with (
        patch("nodenorm.server.tornado.httpserver.HTTPServer"),
        patch("nodenorm.server.tornado.ioloop.IOLoop.instance", return_value=loop),
    ):
        NodeNormalizationWebServer(object(), namespace).start()

    assert namespace._babel_refresh_task is None
    assert loop.asyncio_loop.is_closed()


async def test_refresh_follows_alias_and_publishes_matching_default_indices(namespace):
    previous = namespace.elasticsearch.babel

    assert await namespace.refresh_babel_releases()

    current = namespace.elasticsearch.babel
    assert current.default_version == "2026jul22"
    assert current.resolve().search_indices == namespace.elasticsearch.indices == ["new"]
    assert current.resolve("2025sep1").search_indices == ["old"]
    assert previous.default_version == "2025sep1"
    assert previous.resolve().search_indices == ["old"]
    assert datetime.fromisoformat(namespace.elasticsearch.babel_checked_at).tzinfo is not None
    assert namespace.elasticsearch.babel_refresh_error is None
    indices = namespace.elasticsearch.async_client.indices
    indices.exists.assert_not_awaited()
    indices.exists_alias.assert_awaited_once_with(name="alias")


async def test_refresh_honors_configured_index_before_fallback_alias(namespace):
    namespace.config.elasticsearch["ES_INDEX"] = "configured-index"
    indices = namespace.elasticsearch.async_client.indices
    indices.exists.return_value = True

    assert await namespace.refresh_babel_releases()

    indices.exists_alias.assert_not_awaited()
    assert indices.get_mapping.await_args_list[0].kwargs["index"] == ["configured-index"]


@pytest.mark.parametrize(
    "default_mappings",
    [{}, {"old": mapping("2025sep1"), "new": mapping("2026jul22")}, {"bad": {"mappings": {}}}],
)
async def test_invalid_discovery_retains_entire_previous_snapshot(namespace, default_mappings):
    previous = namespace.elasticsearch.babel
    indices = namespace.elasticsearch.async_client.indices
    indices.get_mapping.side_effect = [default_mappings, {"new": mapping("2026jul22")}]

    assert not await namespace.refresh_babel_releases()

    assert namespace.elasticsearch.babel is previous
    assert namespace.elasticsearch.indices == ["old"]
    assert namespace.elasticsearch.babel_checked_at == "2026-10-23T12:00:00+00:00"
    assert namespace.elasticsearch.babel_refresh_error == {"code": "discovery_failed"}


async def test_discovery_failure_recovers_without_leaking_upstream_error(namespace):
    indices = namespace.elasticsearch.async_client.indices
    indices.get_mapping.side_effect = RuntimeError("private upstream connection details")
    assert not await namespace.refresh_babel_releases()
    assert namespace.elasticsearch.babel_refresh_error == {"code": "discovery_failed"}
    indices.get_mapping.side_effect = [
        {"new": mapping("2026jul22")},
        {"new": mapping("2026jul22")},
    ]

    assert await namespace.refresh_babel_releases()

    assert namespace.elasticsearch.babel.default_version == "2026jul22"
    assert namespace.elasticsearch.babel_refresh_error is None


async def test_background_refresh_runs_without_requests_and_retries_failure(namespace, monkeypatch):
    monkeypatch.setattr(namespace_module, "BABEL_REFRESH_INTERVAL", 0.01)
    monkeypatch.setattr(namespace_module, "BABEL_REFRESH_RETRY_INTERVAL", 0.001)
    completed = asyncio.Event()
    indices = namespace.elasticsearch.async_client.indices

    async def get_mapping(*, index=None, **kwargs):
        if indices.get_mapping.await_count == 1:
            raise RuntimeError("temporary outage")
        return {"new": mapping("2026jul22")}

    indices.get_mapping.side_effect = get_mapping
    refresh = namespace.refresh_babel_releases

    async def refresh_and_notify():
        refreshed = await refresh()
        if refreshed:
            completed.set()
        return refreshed

    namespace.refresh_babel_releases = refresh_and_notify
    namespace.start_babel_refresh()
    task = namespace._babel_refresh_task
    namespace.start_babel_refresh()
    assert namespace._babel_refresh_task is task
    try:
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert namespace.elasticsearch.babel.default_version == "2026jul22"
        assert namespace.elasticsearch.babel_refresh_error is None
        assert indices.get_mapping.await_count == 3
    finally:
        await namespace.stop_babel_refresh()
    assert task.cancelled()
    assert namespace._babel_refresh_task is None


async def test_background_shutdown_cancels_an_inflight_refresh(namespace, monkeypatch):
    monkeypatch.setattr(namespace_module, "BABEL_REFRESH_INTERVAL", 0.001)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def get_mapping(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    indices = namespace.elasticsearch.async_client.indices
    indices.get_mapping.side_effect = get_mapping
    previous = namespace.elasticsearch.babel
    namespace.start_babel_refresh()
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        await namespace.stop_babel_refresh()

    assert cancelled.is_set()
    assert indices.get_mapping.await_count == 1
    assert namespace.elasticsearch.babel is previous
    assert namespace.elasticsearch.babel_refresh_error is None


async def test_discovery_deadline_retains_snapshot(namespace, monkeypatch):
    monkeypatch.setattr(namespace_module, "BABEL_REFRESH_TIMEOUT", 0.001)

    async def never_returns(**kwargs):
        await asyncio.Event().wait()

    namespace.elasticsearch.async_client.indices.get_mapping.side_effect = never_returns
    previous = namespace.elasticsearch.babel

    assert not await asyncio.wait_for(namespace.refresh_babel_releases(), timeout=1)
    assert namespace.elasticsearch.babel is previous


async def test_health_refresh_is_shared_and_survives_caller_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()

    async def get_mapping(**kwargs):
        started.set()
        await release.wait()
        return {"old": mapping("2025sep1")}

    client = SimpleNamespace(indices=SimpleNamespace(get_mapping=AsyncMock(side_effect=get_mapping)))
    cache = StatusHealthCache(client, BabelReleases("2025sep1", {"2025sep1": "old"}).resolve())
    first = asyncio.create_task(cache.check())
    second = asyncio.create_task(cache.check())
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()

    assert await second
    assert await cache.check()
    client.indices.get_mapping.assert_awaited_once()


@pytest.mark.parametrize(
    "mappings",
    [
        {},
        {"a": mapping("2025sep1")},
        {"a": mapping("2025sep1"), "b": mapping("2026jul22")},
        {"a": mapping("2025sep1"), "b": {"mappings": {}}},
    ],
)
async def test_health_validates_every_default_backing_index(mappings):
    client = SimpleNamespace(indices=SimpleNamespace(get_mapping=AsyncMock(return_value=mappings)))
    release = BabelReleases("2025sep1", {"2025sep1": ("a", "b")}).resolve()

    assert not await StatusHealthCache(client, release).check()


async def test_health_deadline_reports_failure(monkeypatch):
    monkeypatch.setattr(status_health, "HEALTH_TIMEOUT", 0.001)

    async def never_returns(**kwargs):
        await asyncio.Event().wait()

    client = SimpleNamespace(indices=SimpleNamespace(get_mapping=AsyncMock(side_effect=never_returns)))
    release = BabelReleases("2025sep1", {"2025sep1": "old"}).resolve()

    assert not await asyncio.wait_for(StatusHealthCache(client, release).check(), timeout=1)
