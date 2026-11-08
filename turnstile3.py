"""Turnstile harvest v3: render widget on snapgen.ai, click checkbox, return token."""
import asyncio, sys
from playwright.async_api import async_playwright

SITEKEY = "0x4AAAAAACDBydnKT0zYzh2H"

async def harvest(timeout=180):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled", "--window-position=2000,100"])
        ctx = await browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US")
        page = await ctx.new_page()
        try:
            await page.goto("https://snapgen.ai/app/imagen", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_function("typeof window.turnstile !== 'undefined'", timeout=30000)
            await page.evaluate("""(sk) => {
                const d = document.createElement('div');
                d.id = 'gwts';
                d.style.cssText = 'position:fixed;left:40px;top:40px;z-index:99999';
                document.body.appendChild(d);
                window.turnstile.render('#gwts', {sitekey: sk,
                    callback: t => { window.__t = t; },
                    'error-callback': e => { window.__err = String(e); }});
            }""", SITEKEY)
            t0 = asyncio.get_event_loop().time()
            clicked = 0
            while asyncio.get_event_loop().time() - t0 < timeout:
                tok = await page.evaluate("window.__t||null")
                if tok:
                    await browser.close()
                    return tok
                err = await page.evaluate("window.__err||null")
                if err:
                    print("ts error:", err, file=sys.stderr)
                frames = [f for f in page.frames if "challenges.cloudflare.com" in (f.url or "")]
                if frames:
                    try:
                        fe = await frames[0].frame_element()
                        bb = await fe.bounding_box()
                        if bb and clicked < 8:
                            # checkbox sits left side of widget
                            await page.mouse.click(bb["x"] + 28, bb["y"] + bb["height"] / 2)
                            clicked += 1
                            print("clicked checkbox", clicked, file=sys.stderr)
                    except Exception as e:
                        print("click err:", e, file=sys.stderr)
                await asyncio.sleep(4)
        finally:
            await browser.close()
    return None

if __name__ == "__main__":
    t = asyncio.run(harvest())
    print("TOKEN:", (t or "FAIL"))
