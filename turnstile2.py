"""Turnstile harvest v2: use the site's own widget (checkbox click) + token polling."""
import asyncio, sys
from playwright.async_api import async_playwright

async def harvest(timeout=120):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--window-position=2000,100"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900}, locale="en-US")
        page = await ctx.new_page()
        page.on("console", lambda m: print("[pg]", m.text[:160], file=sys.stderr))
        try:
            await page.goto("https://snapgen.ai/auth/signup", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(6)
            # find the turnstile iframe & click checkbox
            for attempt in range(40):
                tok = await page.evaluate("""() => {
                    const el = document.querySelector('textarea[name="cf-turnstile-response"], input[name="cf-turnstile-response"]');
                    return el && el.value && el.value.length > 20 ? el.value : null;
                }""")
                if tok:
                    await browser.close()
                    return tok
                frames = [f for f in page.frames if "challenges.cloudflare.com" in (f.url or "")]
                print("attempt", attempt, "frames:", len(frames), file=sys.stderr)
                if frames:
                    try:
                        box = await frames[0].frame_element()
                        bb = await box.bounding_box()
                        if bb:
                            await page.mouse.click(bb["x"] + 30, bb["y"] + bb["height"] / 2)
                    except Exception as e:
                        print("click err", e, file=sys.stderr)
                await asyncio.sleep(3)
        finally:
            await browser.close()
    return None

if __name__ == "__main__":
    t = asyncio.run(harvest())
    print("TOKEN:", (t or "FAIL"))
