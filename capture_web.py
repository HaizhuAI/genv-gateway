"""Drive the real snapgen.ai site in visible Chrome; capture signed API requests
(x-guard-id / turnstile_token / form fields) the site itself produces."""
import asyncio, json, sys
from playwright.async_api import async_playwright

OUT = "captured_requests.jsonl"
EMAIL = sys.argv[1] if len(sys.argv) > 1 else None
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled", "--window-position=1800,50",
                  "--window-size=1400,950"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 880}, locale="en-US",
            storage_state="auth_state.json" if __import__('os').path.exists("auth_state.json") else None)
        page = await ctx.new_page()
        f = open(OUT, "a", encoding="utf-8")

        async def on_req(req):
            if "api.snapgen.ai" in req.url or "api.geminigen.ai" in req.url:
                body = None
                try:
                    body = req.post_data
                except Exception:
                    pass
                rec = {"url": req.url, "method": req.method,
                       "headers": {k: v for k, v in req.headers.items()
                                   if k.lower() in ("authorization", "x-guard-id", "content-type",
                                                    "user-agent", "origin", "referer")},
                       "body": body[:3000] if body else None}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                print("[capture]", req.method, req.url)
        page.on("request", on_req)

        await page.goto("https://snapgen.ai/", wait_until="domcontentloaded", timeout=60000)
        if EMAIL:
            # login flow
            await page.goto("https://snapgen.ai/auth/login", wait_until="networkidle", timeout=60000)
            try:
                await page.fill('input[name="username"]', EMAIL, timeout=15000)
                await page.fill('input[name="password"]', PASSWORD)
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(8000)
                await ctx.storage_state(path="auth_state.json")
                print("saved auth_state.json")
            except Exception as e:
                print("login helper err:", e)
        await page.goto("https://snapgen.ai/app/video-gen", wait_until="networkidle", timeout=60000)
        # select Grok Lite model + submit a prompt to capture the real signed request
        try:
            await page.click('button:has-text("Grok Lite")', timeout=10000)
            await page.wait_for_timeout(1500)
            await page.fill("textarea", "a red apple slowly rotating on a wooden table")
            await page.wait_for_timeout(500)
            await page.click('button:has-text("Generate")')
            print("submitted grok-lite job")
        except Exception as e:
            print("drive err:", e)
        print("READY - capturing. Ctrl-C to stop.")
        while True:
            await asyncio.sleep(3600)

asyncio.run(main())
