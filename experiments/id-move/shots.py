"""ID の引っ越しの画面を実機から撮る（CDP ヘッドレス Chrome）。

かんたんウィザードの状態は sessionStorage に載るので、そこへ実データセットの
id を置いてから開けば、実 API・実データのまま各画面に着地できる。
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import sys

import httpx
import websockets

CDP = "http://127.0.0.1:9333"
UI = "http://localhost:5199"
OUT = pathlib.Path(__file__).parent / "shots"
TOKEN = "verify-token"


def snapshot(dataset_id: str, name: str, step: int, *, gate: bool) -> dict:
    snap = {
        "step": step,
        "kind": "csv",
        "q1": None,
        "q2": None,
        "dialectOverrides": {},
        "skeleton": None,
        "annotations": None,
        "inspectionMd": "",
        "proposal": "",
        "datasetId": dataset_id,
        "datasetName": name,
        "sourceAttached": True,
        "autoFixed": False,
        "confirmed": True,
        "columnSamples": {},
        "pubName": name,
        "published": False,
        "redesigning": True,
        "reingested": True,
    }
    if gate:
        snap["gateSkeleton"] = None
    return snap


class Tab:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def send(self, method: str, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def js(self, expr: str):
        r = await self.send(
            "Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True
        )
        return r.get("result", {}).get("value")


async def shoot(tab: Tab, out: pathlib.Path, selector: str | None = None) -> None:
    if selector:
        await tab.js(
            f"(() => {{ const e = document.querySelector({selector!r});"
            " if (e) e.scrollIntoView({block:'center'}); return !!e; })()"
        )
        await asyncio.sleep(0.4)
    r = await tab.send("Page.captureScreenshot", format="png")
    out.write_bytes(base64.b64decode(r["data"]))
    print("  →", out.name, f"({out.stat().st_size // 1024} KB)")


async def open_wizard(tab: Tab, snap: dict) -> None:
    await tab.send("Page.navigate", url=UI)
    await asyncio.sleep(2.0)
    await tab.js(
        f"(() => {{ sessionStorage.setItem('asterism.apiToken', {TOKEN!r});"
        f" sessionStorage.setItem('asterism.kantan', {json.dumps(json.dumps(snap))});"
        " return 1; })()"
    )
    await tab.send("Page.navigate", url=UI)
    await asyncio.sleep(2.2)
    await tab.js(
        "(() => { const b = [...document.querySelectorAll('button')]"
        ".find(e => (e.textContent||'').startsWith('データを追加'));"
        " if (b) b.click(); return !!b; })()"
    )
    await asyncio.sleep(3.0)


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    moved_ds, blocked_ds = sys.argv[1], sys.argv[2]
    async with httpx.AsyncClient() as c:
        info = (await c.get(f"{CDP}/json/list")).json()
    page = next(t for t in info if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        tab = Tab(ws)
        await tab.send("Page.enable")
        await tab.send("Runtime.enable")

        print("① 公開の直前 — 全部引き継げる場合")
        await open_wizard(tab, snapshot(moved_ds, "熱電測定", 8, gate=False))
        await shoot(tab, OUT / "s8-id-move-forwarded.png", ".kz-idmove")

        print("② 公開の直前 — 引き継げないものがある場合")
        await open_wizard(tab, snapshot(blocked_ds, "列が変わる例", 8, gate=False))
        await shoot(tab, OUT / "s8-id-move-blocked.png", ".kz-idmove")

        print("③ 見直し中の「ためす」— 数えかたへの戻り道（公開後も出る）")
        await open_wizard(tab, snapshot(moved_ds, "熱電測定", 7, gate=True))
        await tab.js(
            "(() => { const b = [...document.querySelectorAll('button')]"
            ".find(e => (e.textContent||'').trim() === 'データの数えかたに戻る');"
            " if (b) b.scrollIntoView({block:'center'}); return !!b; })()"
        )
        await asyncio.sleep(0.5)
        await shoot(tab, OUT / "s7-back-to-counting.png")

        print("④ 公開済みデータの「データの数えかた」画面")
        await tab.js("window.confirm = () => true; 1")
        await tab.js(
            "(() => { const b = [...document.querySelectorAll('button')]"
            ".find(e => (e.textContent||'').trim() === 'データの数えかたに戻る');"
            " if (b) b.click(); return !!b; })()"
        )
        await asyncio.sleep(5.0)
        await shoot(tab, OUT / "s4-counting-after-publish.png")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
