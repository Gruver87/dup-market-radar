import asyncio

from radar.api.ws_hub import WsHub


def test_ws_hub_fanout():
    hub = WsHub(max_queue=8)

    async def _run():
        q = hub.subscribe()
        hub.publish({"type": "snapshot", "ok": True})
        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        assert msg["type"] == "snapshot"
        assert msg["ok"] is True
        hub.unsubscribe(q)

    asyncio.run(_run())
    assert hub.status()["published"] >= 1


def test_ws_hub_close_all_wakes_waiter():
    hub = WsHub(max_queue=8)

    async def _run():
        q = hub.subscribe()
        hub.close_all()
        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        assert msg.get("type") == "_shutdown"
        assert hub.status()["subscribers"] == 0

    asyncio.run(_run())
