"""Simulated callers. Opens wss:// "calls" through the load balancer and records, for each one,
which task served it, how long it lasted, and how it ended.

  python calls.py hold  <host> <count> <out.jsonl>    # long calls, ended by the server's call cap
  python calls.py probe <host> <seconds> <out.jsonl>  # a short new call every 2 s, to see where new calls land

<host> is the name on the listener's certificate. Set CA_FILE to trust a private or self-signed
certificate; certificate and hostname verification always stay on.
"""
import asyncio
import json
import os
import ssl
import sys
import time

import websockets

TLS = ssl.create_default_context(cafile=os.environ.get("CA_FILE"))


async def one_call(url, out, hold=True):
    rec = {"start": time.time()}
    try:
        async with websockets.connect(url, open_timeout=10, ssl=TLS) as ws:
            rec["task"] = json.loads(await ws.recv())["task"]
            if not hold:
                await ws.close()
            else:
                async for _ in ws:
                    pass
    except websockets.ConnectionClosed as e:
        rec["close_code"], rec["close_reason"] = e.rcvd.code if e.rcvd else None, e.rcvd.reason if e.rcvd else None
    except Exception as e:
        rec["error"] = repr(e)
    else:
        if ws.close_code is not None:
            rec["close_code"], rec["close_reason"] = ws.close_code, ws.close_reason
    rec["end"] = time.time()
    rec["seconds"] = round(rec["end"] - rec["start"], 1)
    out.write(json.dumps(rec) + "\n")
    out.flush()


async def main():
    mode, dns, n, path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    url = f"wss://{dns}/ws"
    with open(path, "a") as out:
        if mode == "hold":
            await asyncio.gather(*(one_call(url, out) for _ in range(n)))
        else:
            deadline = time.time() + n
            while time.time() < deadline:
                asyncio.ensure_future(one_call(url, out, hold=False))
                await asyncio.sleep(2)
            await asyncio.sleep(5)


asyncio.run(main())
