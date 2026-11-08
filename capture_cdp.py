"""CDP-level network capture: logs EVERY request incl. EventSource/WebSocket."""
import asyncio, json, sys, os
from playwright.async_api import async_playwright

OUT = "cdp_requests.jsonl"
EMAIL = sys.argv[1]
PASSWORD = sys.argv[2]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled", "--window-position=1800,50"])
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 880}, locale="en-US",
            storage_state="auth_state.json" if os.path.exists("auth_state.json") else None)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Network.enable")
        f = open(OUT, "a", encoding="utf-8")

        def on_will(ev):
            r = ev["request"]
            if "api.snapgen.ai" in r["url"] or "geminigen" in r["url"]:
                rec = {"type": ev["type"], "url": r["url"], "method": r["method"],
                       "headers": r.get("headers", {}), "post": (r.get("postData") or "")[:4000],
                       "id": ev["requestId"]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                print("[req]", r["method"], r["url"][:100])
        cdp.on("Network.requestWillBeSent", on_will)
        cdp.on("Network.webSocketCreated", lambda ev:
               (f.write(json.dumps({"type": "WS", "url": ev["url"]}) + "\n"), f.flush(),
                print("[ws]", ev["url"])))

        await page.goto("https://snapgen.ai/auth/login", wait_until="networkidle", timeout=60000)
        if not os.path.exists("auth_state.json"):
            await page.fill('input[name="username"]', EMAIL)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(8000)
            await ctx.storage_state(path="auth_state.json")

        await page.goto("https://snapgen.ai/app/video-gen", wait_until="networkidle", timeout=60000)
        # click Grok Lite then generate
        try:
            await page.click('button:has-text("Grok Lite")', timeout=10000)
            await page.wait_for_timeout(1200)
            await page.fill("textarea", "a red apple slowly rotating on a wooden table")
            await page.click('button:has-text("Generate")')
            print("clicked generate")
        except Exception as e:
            print("drive err:", e)
        while True:
            await asyncio.sleep(3600)

asyncio.run(main())
