"""Harvest a Cloudflare Turnstile token for snapgen.ai via real Chrome + Playwright."""
import asyncio, sys, time
from playwright.async_api import async_playwright

SITEKEY = "0x4AAAAAACDBydnKT0zYzh2H"
PAGES = [
    "https://snapgen.ai/app/imagen",
    "https://snapgen.ai/auth/signup",
    "https://snapgen.ai/",
    "https://geminigen.ai/app/imagen",
    "https://geminigen.ai/",
]


async def harvest(timeout=90):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--window-position=32000,32000"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768}, locale="en-US")
        page = await ctx.new_page()
        try:
            for url in PAGES:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    print("goto fail", url, e, file=sys.stderr)
                    continue
                t0 = time.time()
                while time.time() - t0 < timeout:
                    tok = await page.evaluate("""() => {
                        const el = document.querySelector('[name="cf-turnstile-response"], [name="cf-chl-widget-r"], textarea[id^="cf-chl-widget"]');
                        if (el && el.value && el.value.length > 20) return el.value;
                        const ifr = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                        return null;
                    }""")
                    if tok:
                        await browser.close()
                        return tok
                    # also try explicit render if turnstile global exists
                    rendered = await page.evaluate("""() => {
                        if (window.turnstile && !document.getElementById('gw-ts')) {
                            const d = document.createElement('div'); d.id = 'gw-ts';
                            document.body.appendChild(d);
                            window.turnstile.render('#gw-ts', {sitekey: '""" + SITEKEY + """',
                                callback: t => { window.__gwTok = t; }});
                            return true;
                        }
                        return false;
                    }""")
                    if rendered:
                        await page.wait_for_function("window.__gwTok && window.__gwTok.length > 20", timeout=timeout*1000)
                        tok = await page.evaluate("window.__gwTok")
                        await browser.close()
                        return tok
                    await asyncio.sleep(1)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    return None


if __name__ == "__main__":
    t = asyncio.run(harvest())
    print("TOKEN:", (t or "FAIL"))
