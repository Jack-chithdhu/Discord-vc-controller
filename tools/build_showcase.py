"""
Generates the feature-showcase PNGs in showcase/ from showcase_content.py.

    python tools/build_showcase.py

Dev-only tool: needs `pip install playwright && playwright install chromium`.
The bot itself never imports this; it only ships the finished PNGs.
"""
import base64, html, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
import showcase_content as sc  # noqa: E402

OUT = os.path.join(ROOT, "showcase")
SHOTS = os.path.join(OUT, "screenshots")
WIDTH = 1600

BTN = {"grey": "#4e5058", "green": "#248046", "blue": "#5865f2", "red": "#da373c"}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1600px;background:#0d0b16;font-family:'Inter','Noto Color Emoji',sans-serif;color:#ece9f7}
#card{width:1600px;padding:64px 72px 48px;background:
  radial-gradient(1200px 500px at 90% -10%, rgba(124,92,255,.35), transparent 60%),
  radial-gradient(900px 500px at -10% 0%, rgba(23,211,162,.16), transparent 55%),
  linear-gradient(180deg,#14111f,#0d0b16);position:relative}
.top{display:flex;align-items:center;gap:36px;margin-bottom:44px}
.badge{width:150px;height:150px;border-radius:38px;background:linear-gradient(135deg,#7c5cff,#a78bfa);display:flex;align-items:center;justify-content:center;font-size:84px;box-shadow:0 18px 50px rgba(124,92,255,.45);flex:none}
h1{font-size:92px;line-height:1.02;font-weight:800;letter-spacing:-2px}
.tag{font-size:46px;color:#b9b3d6;margin-top:10px;font-weight:500}
.body{display:flex;gap:48px;align-items:flex-start}
.items{flex:1;display:flex;flex-direction:column;gap:22px}
.item{display:flex;align-items:center;gap:30px;background:#1d1a2e;border:2px solid #322d4a;border-radius:30px;padding:26px 32px}
.item .e{font-size:68px;width:84px;text-align:center;flex:none}
.item .l{font-size:54px;font-weight:700;line-height:1.1}
.item .t{font-size:42px;color:#aaa4c6;margin-top:6px;line-height:1.2}
.phones{display:flex;gap:36px;margin-top:44px;align-items:flex-start;justify-content:center}
.phones.side{margin-top:0;flex-direction:column}
.phone{border-radius:40px;border:6px solid #322d4a;overflow:hidden;background:#12101c;box-shadow:0 20px 60px rgba(0,0,0,.5);flex:none}
.phone img{display:block;width:100%}
.shot{display:flex;flex-direction:column;align-items:center;gap:16px}
.chip{font-size:30px;font-weight:800;letter-spacing:2px;padding:10px 22px;border-radius:999px}
.chip.real{background:#0f3d31;color:#17d3a2;border:2px solid #17d3a2}
.chip.mock{background:#3a2d12;color:#ffc857;border:2px solid #ffc857}
.cap{font-size:34px;color:#aaa4c6}
.mocks{margin-top:44px;display:flex;flex-direction:column;gap:30px}
.mock{background:#313338;border-radius:26px;padding:34px 40px;position:relative}
.mock .chip{position:absolute;top:-22px;right:34px}
.mock .mt{font-size:50px;font-weight:800;border-left:10px solid #5865f2;padding-left:24px}
.mock .ml{font-size:40px;color:#c9cdd3;margin:14px 0 22px 34px}
.sel{background:#1e1f22;border-radius:14px;padding:22px 28px;font-size:38px;color:#b5bac1;display:flex;justify-content:space-between;margin-bottom:16px}
.row{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px}
.b{font-size:38px;font-weight:700;padding:22px 34px;border-radius:14px;color:#fff}
.box{border:3px dashed #5b5775;border-radius:18px;padding:26px 30px;font-size:42px;color:#b5bac1;margin-bottom:16px}
.flow{display:flex;flex-direction:column;gap:12px;margin-left:34px}
.step{background:#1e1f22;border-radius:16px;padding:22px 30px;font-size:44px;font-weight:600}
.arrow{font-size:40px;color:#7c5cff;text-align:center;line-height:1}
.cmds{display:flex;flex-direction:column;gap:14px}
.cmd{display:flex;align-items:baseline;gap:26px;background:#1d1a2e;border:2px solid #322d4a;border-radius:24px;padding:20px 30px}
.cmd .n{font-family:'DejaVu Sans Mono',monospace;font-size:42px;font-weight:700;color:#a78bfa;flex:none;min-width:0}
.cmd .d{font-size:38px;color:#c9c4e0}
.cmd.stack{flex-direction:column;gap:6px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.tile{background:#1d1a2e;border:2px solid #322d4a;border-radius:30px;padding:30px 32px;position:relative}
.tile .e{font-size:64px}
.tile .l{font-size:50px;font-weight:800;margin-top:8px}
.tile .t{font-size:38px;color:#aaa4c6;margin-top:6px;line-height:1.2}
.tile .ck{position:absolute;top:24px;right:28px;font-size:44px;color:#17d3a2;font-weight:800}
.foot{margin-top:48px;display:flex;justify-content:space-between;align-items:center;font-size:32px;color:#8f8aa8;border-top:2px solid #26223a;padding-top:26px}
.foot b{color:#a78bfa}
"""


def esc(s):
    return html.escape(s, quote=True)


def data_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def screen_html(slot):
    fname, label = sc.SCREENS[slot]
    path = os.path.join(SHOTS, fname)
    if not os.path.isfile(path):
        raise SystemExit(f"missing screenshot {path} - run tools/capture_dashboard.py")
    return (f'<div class="shot"><div class="phone"><img src="{data_uri(path)}"></div>'
            f'<span class="chip real">{sc.LABEL_REAL}</span><span class="cap">{esc(label)}</span></div>')


def mock_html(key):
    real_path = os.path.join(SHOTS, f"{key}.png")
    if os.path.isfile(real_path):  # a real Discord screenshot replaces the drawing
        return (f'<div class="shot"><div class="phone" style="max-width:900px"><img src="{data_uri(real_path)}"></div>'
                f'<span class="chip real">{sc.LABEL_REAL}</span></div>'), True
    m = sc.MOCKS[key]
    parts = [f'<div class="mock"><span class="chip mock">{sc.LABEL_MOCK}</span><div class="mt">{esc(m["title"])}</div>']
    for line in m.get("lines", []):
        parts.append(f'<div class="ml">{esc(line)}</div>')
    for s in m.get("selects", []):
        parts.append(f'<div class="sel"><span>{esc(s)}</span><span>⌄</span></div>')
    for b in m.get("boxes", []):
        parts.append(f'<div class="box">{esc(b)}</div>')
    if m.get("flow"):
        steps = []
        for i, st in enumerate(m["flow"]):
            if i:
                steps.append('<div class="arrow">▼</div>')
            steps.append(f'<div class="step">{esc(st)}</div>')
        parts.append(f'<div class="flow">{"".join(steps)}</div>')
    for row in m.get("buttons", []):
        parts.append('<div class="row">' + "".join(
            f'<div class="b" style="background:{BTN[st]}">{esc(lbl)}</div>' for lbl, st in row) + "</div>")
    parts.append("</div>")
    return "".join(parts), False


def items_html(items):
    return '<div class="items">' + "".join(
        f'<div class="item"><div class="e">{esc(e)}</div><div><div class="l">{esc(l)}</div><div class="t">{esc(t)}</div></div></div>'
        for e, l, t in items) + "</div>"


def body_html(card):
    kind = card.get("kind")
    if kind == "commands":
        rows = sc.card_commands(card)
        out = ['<div class="cmds">']
        for name, desc in rows:
            out.append(f'<div class="cmd stack"><span class="n">/{esc(name)}</span><span class="d">{esc(desc)}</span></div>'
                       if len(name) > 22 else
                       f'<div class="cmd"><span class="n">/{esc(name)}</span><span class="d">{esc(desc)}</span></div>')
        out.append("</div>")
        return "".join(out), False, False
    if kind == "overview":
        tiles = "".join(
            f'<div class="tile"><span class="ck">✓</span><div class="e">{esc(e)}</div><div class="l">{esc(l)}</div><div class="t">{esc(t)}</div></div>'
            for e, l, t in card["items"])
        return f'<div class="grid">{tiles}</div>', False, False
    screens = [screen_html(s) for s in card["screens"]]
    mocks, any_real_mock, any_mock = [], False, False
    for key in card["mocks"]:
        h, real = mock_html(key)
        mocks.append(h)
        any_real_mock |= real
        any_mock |= not real
    items = items_html(card["items"])
    if len(screens) == 1:
        main = f'<div class="body">{items}<div class="phones side" style="width:560px">{screens[0]}</div></div>'
    else:
        main = f'<div class="body">{items}</div>'
        if screens:
            main += f'<div class="phones">{"".join(screens)}</div>'
    if mocks:
        main += f'<div class="mocks">{"".join(mocks)}</div>'
    return main, bool(screens), any_mock


def footer_text(card, has_screens, has_mock):
    notes = []
    if has_screens:
        notes.append("Website screenshots are the real interface, captured with no live data.")
    if has_mock:
        notes.append("Discord panels are illustrative mockups - layout only, no real messages or stats.")
    return " ".join(notes)


def card_html(card):
    body, has_screens, has_mock = body_html(card)
    note = footer_text(card, has_screens, has_mock)
    return f"""<!doctype html><meta charset="utf-8"><style>{CSS}</style>
<div id="card">
  <div class="top"><div class="badge">{esc(card['emoji'])}</div>
  <div><h1>{esc(card['title'])}</h1><div class="tag">{esc(card['tagline'])}</div></div></div>
  {body}
  <div class="foot"><span><b>{esc(sc.BRAND)}</b></span><span>{esc(note)}</span></div>
</div>"""


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": 1000})
        for card in sc.CARDS:
            page.set_content(card_html(card))
            page.wait_for_timeout(250)
            path = os.path.join(OUT, card["file"])
            page.locator("#card").screenshot(path=path)
            print("built", path)
        browser.close()


if __name__ == "__main__":
    main()
