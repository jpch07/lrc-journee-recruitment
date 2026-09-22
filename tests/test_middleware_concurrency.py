from __future__ import annotations

import asyncio
import json
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app import main
from app.assessment_config import blank_assessment_definition
from app.assessment_runtime import active_assessment_definition
from app.tenant import current_system_id


def request_for(slug: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": f"/{slug}/admin", "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
    })


def test_workspace_database_queries_do_not_block_event_loop_or_mix_tenants(monkeypatch):
    definitions = {}
    for name in ("alpha", "beta"):
        definition = blank_assessment_definition()
        definitions[name] = definition.model_copy(update={"name": name})
    observed = []
    event_loop_thread = threading.get_ident()
    initial_definition = active_assessment_definition()
    initial_system = current_system_id()

    def record(stage, expected_system):
        observed.append((stage, threading.get_ident(), current_system_id(), expected_system))
        time.sleep(0.04)  # Represents network I/O or waiting for a pool checkout.

    def resolve(request):
        record("resolve", initial_system)
        return request.url.path.split("/")[1]

    def runtime(system_id):
        record("runtime", system_id)
        return definitions[system_id]

    def slug(system_id):
        record("slug", system_id)
        assert active_assessment_definition().name == system_id
        return system_id

    monkeypatch.setattr(main, "database_startup_error", None)
    monkeypatch.setattr(main, "_request_system_id", resolve)
    monkeypatch.setattr(main, "_runtime_for_system", runtime)
    monkeypatch.setattr(main, "_slug_for_system", slug)

    async def run():
        ticks = []
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                ticks.append(True)
                await asyncio.sleep(0.005)

        async def endpoint(request):
            expected = request.url.path.split("/")[1]
            await asyncio.sleep(0.01)
            assert current_system_id() == expected
            return JSONResponse({"system": current_system_id(), "name": active_assessment_definition().name})

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            responses = await asyncio.gather(*(
                main.prevent_stale_frontend_assets(request_for(name), endpoint)
                for name in ("alpha", "beta")
            ))
        finally:
            stop.set()
            await heartbeat_task
        # With synchronous database work on the event loop only the explicit
        # endpoint sleep yields. Workers let the heartbeat continue throughout.
        assert len(ticks) >= 10
        assert [json.loads(response.body) for response in responses] == [
            {"system": "alpha", "name": "alpha"}, {"system": "beta", "name": "beta"},
        ]
        assert "alpha" in responses[0].headers["set-cookie"]
        assert "beta" in responses[1].headers["set-cookie"]
        assert current_system_id() == initial_system
        assert active_assessment_definition() is initial_definition

    asyncio.run(run())
    assert len(observed) == 6
    assert all(thread_id != event_loop_thread for _, thread_id, _, _ in observed)
    assert all(actual == expected for _, _, actual, expected in observed)
