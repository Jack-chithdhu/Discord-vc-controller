"""
Captures phone-sized screenshots of the web dashboard tabs into showcase/screenshots/.

    python tools/capture_dashboard.py http://localhost:8080
    python tools/capture_dashboard.py https://your-site.example --pw YOURPASSWORD

Run it against your DEPLOYED site to get screenshots with your real data. Check them
before committing: they may show member names. (The ones in the repo were captured from
the app's own code against an empty test database, so they show the real interface but
no data.)  Needs: pip install playwright && playwright install chromium   (dev only).
"""
import argparse, os, sys

# tab -> (output file, crop height in screen pixels at 2x)
TABS = {
    "teams": ("dashboard_teams.png", 1640),
    "lock": ("dashboard_lock.png", 520),
    "dashboard": ("dashboard_dashboard.png", 700),
    "stats": ("dashboard_stats.png", 1300),
    "log": ("dashboard_log.png", 400),
    "settings": ("dashboard_settings.png", 1680),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--pw", default="")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "showcase", "screenshots"))
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright
    from PIL import Image
    os.makedirs(args.out, exist_ok=True)
    url = args.base_url.rstrip("/") + "/" + (f"?pw={args.pw}" if args.pw else "")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 430, "height": 900}, device_scale_factor=2)
        page.goto(url)
        page.wait_for_timeout(2500)
        for tab, (name, crop_h) in TABS.items():
            page.evaluate(f"switchTab('{tab}')")
            page.wait_for_timeout(1500)
            path = os.path.join(args.out, name)
            page.screenshot(path=path)
            im = Image.open(path)
            im.crop((0, 0, im.width, min(crop_h, im.height))).save(path, optimize=True)
            print("saved", path)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
