import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://localhost:8400/onebot") as ws:
        for ev in [
            {"post_type": "meta_event", "meta_event_type": "lifecycle", "self_id": 123},
            {"post_type": "message", "message_type": "private", "user_id": 999,
             "message_id": 1, "self_id": 123, "message": "/help"},
            {"post_type": "message", "message_type": "private", "user_id": 999,
             "message_id": 2, "self_id": 123, "message": "/model"},
            {"post_type": "message", "message_type": "private", "user_id": 999,
             "message_id": 3, "self_id": 123, "message": "/pool"},
            # group msg without @ -> should be ignored
            {"post_type": "message", "message_type": "group", "group_id": 555, "user_id": 999,
             "message_id": 4, "self_id": 123, "message": "/model"},
            # group msg with @ self -> should answer
            {"post_type": "message", "message_type": "group", "group_id": 555, "user_id": 999,
             "message_id": 5, "self_id": 123,
             "message": [{"type": "at", "data": {"qq": "123"}},
                         {"type": "text", "data": {"text": " /model"}}]},
        ]:
            await ws.send(json.dumps(ev))
            await asyncio.sleep(0.4)
        try:
            for _ in range(10):
                raw = await asyncio.wait_for(ws.recv(), 8)
                d = json.loads(raw)
                if d.get("action") == "send_msg":
                    print("BOT>>", json.dumps(d["params"], ensure_ascii=False)[:260])
        except asyncio.TimeoutError:
            pass

asyncio.run(main())
