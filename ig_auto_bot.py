import asyncio, json, os, requests, sys, time, re
from pathlib import Path
from urllib.parse import unquote_plus
from datetime import datetime

# ── CONFIG — env variables se read karo (GitHub Secrets) ──────
ADMIN_URL    = os.environ.get("ADMIN_URL", "https://scrapemedia.shaktiornate.com/admin.php")
API_KEY      = os.environ.get("API_KEY",   "Password123!")
IS_CI        = os.environ.get("CI", "false").lower() == "true"   # GitHub Actions pe True hoga

SESSION_FILE = "ig_session.json"
captured     = {}

FALLBACK_REELS = [
    "https://www.instagram.com/reel/DUSb9rwiGlE/",
    "https://www.instagram.com/reel/C8NMEMpyYN4/",
    "https://www.instagram.com/reel/DHmes7boA9L/",
    "https://www.instagram.com/reel/C9oEMaBofqc/",
]

# ── REQUEST INTERCEPTOR ────────────────────────────────────────
async def intercept(request):
    try:
        if "graphql" in request.url and request.method == "POST":
            body = request.post_data or ""
            params = {}
            for pair in body.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            if params.get("lsd"):
                captured["IG_LSD"]     = params["lsd"]
                captured["IG_JAZOEST"] = params.get("jazoest", "")
                captured["IG_SPIN_R"]  = params.get("__spin_r", "")
                captured["IG_SPIN_T"]  = params.get("__spin_t", "")
                captured["IG_REV"]     = params.get("__rev", "")
                captured["IG_HSI"]     = params.get("__hsi", "")
                captured["IG_AV"]      = params.get("av", "")
                if params.get("__hs"):  captured["IG_HS"]  = params["__hs"]
                if params.get("__s"):   captured["IG_S"]   = params["__s"]
                if params.get("__dyn"): captured["IG_DYN"] = params["__dyn"]
                if params.get("__ccg"): captured["IG_CCG"] = params["__ccg"]
                if params.get("dpr"):   captured["IG_DPR"] = params["dpr"]
                if params.get("__crn"): captured["IG_CRN"] = params["__crn"]

                dtsg = params.get("fb_dtsg", "")
                if dtsg and len(dtsg) > 10:
                    captured["IG_FB_DTSG"] = dtsg

                doc_id       = params.get("doc_id", "")
                friendly_name = params.get("fb_api_req_friendly_name", "")

                if doc_id and len(doc_id) > 10:
                    if not captured.get("IG_INITIAL_DOC_ID"):
                        captured["IG_INITIAL_DOC_ID"] = doc_id
                        print(f"  📌 doc_id (any): {doc_id} [{friendly_name[:30]}]")
                    if "PolarisPost" in friendly_name:
                        captured["IG_INITIAL_DOC_ID"] = doc_id
                        print(f"  ✅ doc_id (PolarisPost): {doc_id}")
    except Exception:
        pass

# ── HELPERS ────────────────────────────────────────────────────
async def try_open_reel(page, url):
    print(f"  Opening: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    await page.wait_for_timeout(5000)
    if captured.get("IG_INITIAL_DOC_ID"):
        return True
    try:
        await page.evaluate("window.scrollTo(0, 300)")
        await page.wait_for_timeout(2000)
    except Exception:
        pass
    if captured.get("IG_INITIAL_DOC_ID"):
        return True
    selectors = [
        'svg[aria-label="Comment"]',
        'svg[aria-label="Kommentar"]',
        '[data-testid="comment-icon"]',
        'span[aria-label="Comment"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            await btn.click(timeout=2000)
            await page.wait_for_timeout(3000)
            if captured.get("IG_INITIAL_DOC_ID"):
                return True
        except Exception:
            pass
    try:
        await page.keyboard.press("c")
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    return bool(captured.get("IG_INITIAL_DOC_ID"))

async def fetch_doc_id_from_html(page):
    try:
        html = await page.content()
        patterns = [
            r'"PolarisPostActionLoadPostQueryQuery[^"]*"[^}]*"id"\s*:\s*"(\d+)"',
            r'doc_id["\s:]+(\d{15,})',
            r'"queryID"\s*:\s*"(\d{15,})"',
            r'PolarisPost[^}]{0,200}"(\d{17,})"',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                doc_id = m.group(1)
                print(f"  ✅ doc_id HTML se mila: {doc_id}")
                captured["IG_INITIAL_DOC_ID"] = doc_id
                return True
    except Exception as e:
        print(f"  HTML parse error: {e}")
    return False

# ── SESSION: BASE64 SE RESTORE (GitHub Actions) ───────────────
def restore_session_from_env():
    """
    GitHub Secret 'IG_SESSION_B64' se session restore karo.
    Local machine pe: base64 -i ig_session.json | tr -d '\\n'
    Phir GitHub → Settings → Secrets → IG_SESSION_B64 mein paste karo.
    """
    b64 = os.environ.get("IG_SESSION_B64", "")
    if not b64:
        print("  ⚠️  IG_SESSION_B64 secret nahi mila — session nahi milegi")
        return False
    import base64
    try:
        data = base64.b64decode(b64).decode("utf-8")
        with open(SESSION_FILE, "w") as f:
            f.write(data)
        print("  ✅ Session GitHub Secret se restore ho gayi")
        return True
    except Exception as e:
        print(f"  ❌ Session restore failed: {e}")
        return False

# ── MAIN BOT ──────────────────────────────────────────────────
async def run_bot(login_mode=False):
    global captured
    captured = {}
    print(f"\n{'='*50}")
    print(f"  Bot started — {datetime.now().strftime('%d/%m %H:%M:%S')}")
    print(f"  Mode: {'CI/GitHub Actions' if IS_CI else 'Local'}")
    print(f"{'='*50}")

    # CI mein session env se restore karo
    if IS_CI:
        restore_session_from_env()

    session_exists = Path(SESSION_FILE).exists()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("\n❌ Playwright nahi mila! pip install playwright")
        return False

    async with async_playwright() as p:
        launch_args = {
            # ✅ CI mein headless=True, local mein headless=False
            "headless": True if IS_CI else False,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",     # CI ke liye zaroori
                "--disable-gpu",               # CI ke liye zaroori
                "--disable-setuid-sandbox",
            ]
        }
        print(f"  Browser launching (headless={launch_args['headless']})...")
        browser = await p.chromium.launch(**launch_args)

        ctx_args = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
        }
        if session_exists and not login_mode:
            ctx_args["storage_state"] = SESSION_FILE
            print("  Pehle wali session load ho rahi hai...")

        context = await browser.new_context(**ctx_args)
        page    = await context.new_page()
        page.on("request", intercept)

        print("  Instagram open ho raha hai...")
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        is_login_page = "login" in page.url or "accounts" in page.url

        if is_login_page:
            if IS_CI:
                # ── CI MODE: Cookie inject karo (agar IG_COOKIE_JSON secret diya ho) ──
                cookie_json = os.environ.get("IG_COOKIE_JSON", "")
                if cookie_json:
                    try:
                        cookies = json.loads(cookie_json)
                        await context.add_cookies(cookies)
                        await page.reload(wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(3000)
                        is_login_page = "login" in page.url or "accounts" in page.url
                        if not is_login_page:
                            print("  ✅ Cookie inject se login ho gaya!")
                        else:
                            print("  ❌ Cookie inject kaam nahi ki — expire ho gayi hogi")
                    except Exception as e:
                        print(f"  ❌ Cookie inject error: {e}")
                else:
                    print("\n  ❌ CI mein login page aa gaya aur koi cookie nahi mili!")
                    print("     Fix: IG_SESSION_B64 ya IG_COOKIE_JSON secret update karo")
                    await browser.close()
                    return False
            else:
                # ── LOCAL MODE: Manual login ──
                if session_exists:
                    print("\n  Session expire ho gayi - dobara login karo")
                    Path(SESSION_FILE).unlink(missing_ok=True)
                print("\n" + "="*50)
                print("  Browser mein Instagram LOGIN karo")
                print("  Login hone ke baad yahan ENTER dabao")
                print("="*50)
                input("\n  >> ENTER dabao jab login ho jaye: ")
                await context.storage_state(path=SESSION_FILE)
                print(f"\n  ✅ Session save ho gayi: {SESSION_FILE}")
                print(f"\n  🔑 GitHub Secret ke liye base64 banao:")
                print(f"     base64 -i {SESSION_FILE} | tr -d '\\n'")
                print(f"     Output ko GitHub → Secrets → IG_SESSION_B64 mein daalo\n")

        print("\n  Values fetch kar raha hai...")

        # Step 1: Explore se reel dhundo
        try:
            await page.goto("https://www.instagram.com/explore/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
            reel_link = None
            try:
                reel_link = await page.locator('a[href*="/reel/"]').first.get_attribute("href", timeout=5000)
            except Exception:
                pass
            if reel_link:
                url = f"https://www.instagram.com{reel_link}" if reel_link.startswith("/") else reel_link
                await try_open_reel(page, url)
                if not captured.get("IG_INITIAL_DOC_ID"):
                    await fetch_doc_id_from_html(page)
        except Exception as e:
            print(f"  Explore error: {e}")

        # Step 2: Fallback reels
        if not captured.get("IG_INITIAL_DOC_ID"):
            print("  Fallback reels try kar raha hai...")
            for reel_url in FALLBACK_REELS:
                await try_open_reel(page, reel_url)
                if not captured.get("IG_INITIAL_DOC_ID"):
                    await fetch_doc_id_from_html(page)
                if captured.get("IG_INITIAL_DOC_ID"):
                    break

        # Step 3: Reels tab
        if not captured.get("IG_INITIAL_DOC_ID"):
            print("  Reels tab try kar raha hai...")
            try:
                await page.goto("https://www.instagram.com/reels/", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)
                reel_link = None
                try:
                    reel_link = await page.locator('a[href*="/reel/"]').first.get_attribute("href", timeout=5000)
                except Exception:
                    pass
                if reel_link:
                    url = f"https://www.instagram.com{reel_link}" if reel_link.startswith("/") else reel_link
                    await try_open_reel(page, url)
                    if not captured.get("IG_INITIAL_DOC_ID"):
                        await fetch_doc_id_from_html(page)
            except Exception as e:
                print(f"  Reels tab error: {e}")

        # Step 4: Cookies capture
        try:
            cookies = await context.cookies()
            ig_cookies = [c for c in cookies if "instagram.com" in c.get("domain", "")]
            if ig_cookies:
                captured["IG_FULL_COOKIE"] = "; ".join([f"{c['name']}={c['value']}" for c in ig_cookies])
                print(f"  ✅ Cookies capture hui ({len(ig_cookies)} cookies)")
        except Exception:
            pass

        await browser.close()

    # ── RESULTS ───────────────────────────────────────────────
    print(f"\n  Results:")
    print(f"  LSD     : {'OK — ' + captured['IG_LSD'][:15] + '...' if captured.get('IG_LSD') else 'MISSING ❌'}")
    print(f"  FB_DTSG : {'OK ✅' if captured.get('IG_FB_DTSG') else 'MISSING ❌'}")
    print(f"  DOC_ID  : {'OK — ' + captured['IG_INITIAL_DOC_ID'] if captured.get('IG_INITIAL_DOC_ID') else 'MISSING ❌'}")
    print(f"  Cookie  : {'OK — ' + str(len(captured.get('IG_FULL_COOKIE', ''))) + ' chars ✅' if captured.get('IG_FULL_COOKIE') else 'MISSING ❌'}")

    if not captured.get("IG_LSD") or not captured.get("IG_FULL_COOKIE"):
        print("\n  ❌ Zaroori values nahi mili!")
        if IS_CI:
            print("     Fix: IG_SESSION_B64 secret purana ho gaya — locally login karo aur dobara set karo")
        else:
            print("     Chalao: python3 ig_auto_bot.py --login")
        return False

    return True

# ── SERVER UPDATE ─────────────────────────────────────────────
def update_server():
    print(f"\n  Server update kar raha hai...")
    keys = ["IG_LSD", "IG_FB_DTSG", "IG_FULL_COOKIE", "IG_INITIAL_DOC_ID",
            "IG_AV", "IG_JAZOEST", "IG_SPIN_R", "IG_SPIN_T", "IG_REV", "IG_HSI",
            "IG_HS", "IG_S", "IG_DYN", "IG_CCG", "IG_DPR", "IG_CRN"]
    payload = {k: captured.get(k, "") for k in keys}
    payload = {k: v for k, v in payload.items() if v}   # empty skip

    try:
        resp = requests.post(f"{ADMIN_URL}?api_key={API_KEY}", json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("success"):
            print(f"  ✅ Server updated! ({resp.json().get('updated_at', '')})")
            return True
        print(f"  ❌ Error: {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ Connection error: {e}")
    return False

# ── ENTRY POINTS ──────────────────────────────────────────────
async def run_once(login_mode=False):
    if await run_bot(login_mode=login_mode):
        update_server()
        print(f"\n  ✅ Done! — {datetime.now().strftime('%H:%M:%S')}")
    else:
        print(f"\n  ❌ Bot failed!")
        sys.exit(1)   # GitHub Actions mein red mark dikhao

def run_auto():
    try:
        import schedule
    except ImportError:
        print("pip install schedule")
        sys.exit(1)
    print("\nAUTO MODE — Har 4 ghante update hoga (Ctrl+C se band karo)\n")
    asyncio.run(run_once())
    schedule.every(4).hours.do(lambda: asyncio.run(run_once()))
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    if "--login" in sys.argv:
        asyncio.run(run_once(login_mode=True))
    elif "--auto" in sys.argv:
        run_auto()
    elif "--test" in sys.argv:
        try:
            resp = requests.get(f"{ADMIN_URL}?api_key={API_KEY}&action=status", timeout=10)
            print(f"HTTP: {resp.status_code} | {resp.text[:200]}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        asyncio.run(run_once())
