"""Tests for the feature guide (/features) and /help.

Part 1 is pure (no database). Part 2 drives the real slash-command callbacks and is
skipped unless TEST_DATABASE_URL is set (same convention as the integration tests).
"""
import asyncio, importlib.util, os, re, sys

import pytest

import feature_guide as fg
import showcase_content as sc

HERE = os.path.dirname(os.path.abspath(__file__))
APP_SRC = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()


# ---------------------------------------------------------------- content
def test_every_page_fits_discord_limits():
    for i in range(fg.page_count()):
        p = fg.render_page(i)
        assert len(p["title"]) <= 256
        assert len(p["description"]) <= 4096
        assert len(p["fields"]) <= 25
        for name, value in p["fields"]:
            assert 0 < len(name) <= 256 and 0 < len(value) <= 1024
        total = len(p["title"]) + len(p["description"]) + len(p["footer"]) + sum(len(n) + len(v) for n, v in p["fields"])
        assert total <= fg.MAX_PAGE_CHARS + 500, (i, total)
        assert total < 6000


def test_page_keys_unique_and_footer_numbers():
    keys = [p["key"] for p in fg.build_pages()]
    assert len(keys) == len(set(keys))
    for i in range(fg.page_count()):
        assert f"Page {i + 1}/{fg.page_count()}" in fg.render_page(i)["footer"]


def test_planned_features_only_on_planned_page():
    planned_page = fg.page_index("planned")
    assert "not available" in fg.render_page(planned_page)["title"].lower() or "planned" in fg.render_page(planned_page)["title"].lower()
    for i in range(fg.page_count()):
        if i == planned_page:
            continue
        text = " ".join(v for _, v in fg.render_page(i)["fields"]) + fg.render_page(i)["description"]
        for word in ("monthly report", "health panel", "vc-monthly-report"):
            assert word not in text.lower(), (i, word)


def test_no_secrets_or_private_data_in_generated_content():
    blob = []
    for i in range(fg.page_count()):
        p = fg.render_page(i)
        blob.append(p["title"] + p["description"] + " ".join(n + v for n, v in p["fields"]))
    for admin in (True, False):
        for part in fg.split_for_discord(fg.help_overview(admin)):
            blob.append(part["description"] + " ".join(n + v for n, v in part["fields"]))
    for name in fg.command_names():
        d = fg.help_detail(name)
        blob.append(d["description"] + " ".join(n + v for n, v in d["fields"]))
    text = "\n".join(blob)
    for bad in ("postgres://", "postgresql://", "DATABASE_URL", "DISCORD_TOKEN", "WEB_PASSWORD", "api_key", "token"):
        assert bad.lower() not in text.lower(), bad
    assert not re.search(r"\b\d{17,20}\b", text), "looks like a Discord ID"
    assert not re.search(r"https?://", text), "guide must not embed links"


# ---------------------------------------------------------------- navigation
def test_prev_next_clamped():
    last = fg.page_count() - 1
    assert fg.navigate(0, "prev") == 0
    assert fg.navigate(0, "next") == 1
    assert fg.navigate(last, "next") == last
    assert fg.navigate(last, "prev") == last - 1


def test_home_commands_and_jump():
    assert fg.navigate(5, "home") == 0
    assert fg.navigate(0, "commands") == fg.first_commands_index()
    assert fg.render_page(fg.first_commands_index())["key"].startswith("commands")
    for _, key in fg.jump_options():
        idx = fg.navigate(0, f"jump:{key}")
        assert 0 <= idx < fg.page_count()
    assert len(fg.jump_options()) <= 25


def test_footer_round_trip_and_garbage():
    for i in range(fg.page_count()):
        assert fg.parse_footer_index(fg.render_page(i)["footer"]) == i
    assert fg.parse_footer_index(None) == 0
    assert fg.parse_footer_index("hello") == 0
    assert fg.parse_footer_index("Page 999/999") == fg.page_count() - 1


def test_unknown_action_keeps_page():
    assert fg.navigate(3, "bogus") == 3


# ---------------------------------------------------------------- help
def test_help_hides_admin_commands_from_non_admins():
    def listed(admin):
        return "\n".join(v for p in fg.split_for_discord(fg.help_overview(admin)) for _, v in p["fields"])
    user_text, admin_text = listed(False), listed(True)
    for entry in fg.COMMANDS:
        token = f"/{entry[0]}"
        if entry[4]:
            assert token not in user_text
            assert token in admin_text
        else:
            assert token in user_text


def test_help_split_parts_within_limits():
    for admin in (True, False):
        for part in fg.split_for_discord(fg.help_overview(admin)):
            assert len(part["title"]) <= 256 and len(part["description"]) <= 4096
            assert sum(len(n) + len(v) for n, v in part["fields"]) + len(part["description"]) < 6000
            for n, v in part["fields"]:
                assert len(v) <= 1024


def test_help_detail_known_and_unknown():
    d = fg.help_detail("/Extend-Reservation")
    assert d and "extend-reservation" in d["title"]
    assert fg.help_detail("nope") is None


def test_every_command_has_description_and_usage():
    for name, category, desc, usage, admin in fg.COMMANDS:
        assert desc.strip() and usage.startswith(f"/{name}"), name


# ---------------------------------------------------------------- catalog vs code
def test_catalog_matches_registered_commands_and_admin_flags():
    found = {}
    for m in re.finditer(r'@client\.tree\.command\(name="([^"]+)"[^\n]*\n((?:[ ]+@[^\n]*\n(?:[ ]+[^\n@]*\n)*?)*)[ ]+async def', APP_SRC):
        found[m.group(1)] = "default_permissions(administrator=True)" in m.group(2)
    assert set(found) == set(fg.command_names()), set(found) ^ set(fg.command_names())
    for name, category, desc, usage, admin in fg.COMMANDS:
        assert found[name] == admin, f"{name}: catalog admin={admin}, code admin={found[name]}"


def test_catalog_has_no_duplicate_commands():
    names = fg.command_names()
    assert len(names) == len(set(names))


def test_asset_names_only_catalog_files_and_safe():
    for n in fg.asset_names():
        assert re.fullmatch(r"[a-z0-9_]+\.(png|jpg|jpeg|gif|webp)", n)


# ---------------------------------------------------------------- app level (DB)
URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_guide_test", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    mod.bot = mod.make_bot()
    with mod._cursor() as cur:
        cur.execute("DELETE FROM bot_settings WHERE key = 'feature_guide_channel_id'")
    return mod


import discord


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Resp:
    def __init__(self):
        self.sent, self.edits, self.deferred = [], [], False

    async def defer(self, **kw):
        self.deferred = True

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))

    async def edit_message(self, **kw):
        self.edits.append(kw)


class Msg_:
    def __init__(self, chan, mid, kw):
        self.chan, self.id, self.kw, self.deleted = chan, mid, kw, False
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)

    async def delete(self):
        self.deleted = True
        self.chan.messages.pop(self.id, None)


class _R:
    status, reason = 404, "Not Found"


class Chan:
    id = 4242
    mention = "<#4242>"
    next_id = 1000

    def __init__(self, forbid=False):
        self.posts, self.messages, self.forbid = [], {}, forbid

    async def send(self, **kw):
        if self.forbid:
            raise discord.Forbidden(type("R", (), {"status": 403, "reason": "no"})(), "no")
        Chan.next_id += 1
        m = Msg_(self, Chan.next_id, kw)
        self.messages[m.id] = m
        self.posts.append(kw)
        return m

    async def fetch_message(self, mid):
        if mid not in self.messages:
            raise discord.NotFound(_R(), "gone")
        return self.messages[mid]


class Guild:
    def __init__(self, chan):
        self.chan = chan

    def get_channel(self, cid):
        return self.chan if cid == self.chan.id else None


def member(admin):
    class M(discord.Member):
        id = 77
        guild_permissions = discord.Permissions(administrator=admin)

        def __init__(self):
            pass
    return M()


class Inter:
    def __init__(self, user, guild=None, message=None):
        self.user, self.guild, self.message, self.response = user, guild, message, Resp()
        outer = self

        class _F:
            async def send(self, content=None, **kw):
                outer.response.sent.append((content, kw))
        self.followup = _F()


def cmd(app, name):
    return app.bot.tree.get_command(name).callback


@needs_db
def test_non_admin_cannot_set_or_post_guide_channel(app):
    chan = Chan()
    i = Inter(member(False), Guild(chan))
    run(cmd(app, "set-feature-guide-channel")(i, chan))
    assert "administrators" in i.response.sent[0][0]
    assert app.get_feature_guide_channel_id() != chan.id
    i = Inter(member(False), Guild(chan))
    run(cmd(app, "post-feature-guide")(i))
    assert "administrators" in i.response.sent[0][0] and not chan.posts


@needs_db
def test_commands_are_hidden_from_non_admins_by_default_permissions(app):
    for name in ("set-feature-guide-channel", "post-feature-guide"):
        assert app.bot.tree.get_command(name).default_permissions.administrator
    for name in ("features", "help"):
        assert app.bot.tree.get_command(name).default_permissions is None


def _reset_showcase(app):
    with app._cursor() as cur:
        cur.execute("DELETE FROM bot_settings WHERE key IN ('feature_showcase_state', 'feature_guide_channel_id')")


def _post(app, chan, admin=True, mode=None):
    i = Inter(member(admin), Guild(chan))
    mode_obj = type("C", (), {"value": mode})() if mode else None
    run(cmd(app, "post-feature-guide")(i, mode_obj))
    return i


@needs_db
def test_admin_sets_channel_and_posts_image_set_in_order(app):
    _reset_showcase(app)
    chan = Chan()
    run(cmd(app, "set-feature-guide-channel")(Inter(member(True), Guild(chan)), chan))
    assert app.get_feature_guide_channel_id() == 4242
    i = _post(app, chan)
    assert i.response.deferred
    expected = [sc.INTRO_TEXT] + [c["caption"] for c in sc.CARDS]
    assert [p["content"] for p in chan.posts] == expected
    assert chan.posts[0].get("file") is None
    attached = [p["file"].filename for p in chan.posts[1:]]
    assert attached == sc.all_files()
    assert all(isinstance(p["file"], discord.File) for p in chan.posts[1:])
    assert "Posted" in i.response.sent[-1][0]


@needs_db
def test_running_post_again_does_not_duplicate(app):
    _reset_showcase(app)
    chan = Chan()
    app.set_feature_guide_channel_id(chan.id)
    _post(app, chan)
    n = len(chan.posts)
    i = _post(app, chan)
    assert len(chan.posts) == n and len(chan.messages) == n
    assert "already posted" in i.response.sent[-1][0] and "Refresh" in i.response.sent[-1][0]


@needs_db
def test_refresh_edits_in_place_without_new_messages(app):
    _reset_showcase(app)
    chan = Chan()
    app.set_feature_guide_channel_id(chan.id)
    _post(app, chan)
    ids_before = list(chan.messages)
    i = _post(app, chan, mode="refresh")
    assert list(chan.messages) == ids_before and len(chan.posts) == len(ids_before)
    assert all(m.edits for m in chan.messages.values())
    assert all("attachments" in m.edits[0] for mid, m in chan.messages.items() if mid != ids_before[0])
    assert "updated in place" in i.response.sent[-1][0]


@needs_db
def test_refresh_after_a_message_was_deleted_keeps_order(app):
    _reset_showcase(app)
    chan = Chan()
    app.set_feature_guide_channel_id(chan.id)
    _post(app, chan)
    ids = list(chan.messages)
    chan.messages.pop(ids[5])                       # someone deleted image #5
    _post(app, chan, mode="refresh")
    state = app.get_showcase_state()
    assert len(state["message_ids"]) == 1 + len(sc.CARDS)
    assert state["message_ids"][:5] == ids[:5]      # earlier ones kept in place
    assert len(chan.messages) == 1 + len(sc.CARDS)  # no leftovers, no duplicates
    assert state["message_ids"] == sorted(state["message_ids"])  # still in posting order
    tail = [chan.messages[m].kw.get("file").filename for m in state["message_ids"][5:]]
    assert tail == sc.all_files()[4:]


@needs_db
def test_non_admin_cannot_post_even_if_they_reach_the_command(app):
    _reset_showcase(app)
    chan = Chan()
    app.set_feature_guide_channel_id(chan.id)
    i = _post(app, chan, admin=False)
    assert "administrators" in i.response.sent[0][0] and not chan.posts


@needs_db
def test_post_without_channel_or_without_permission_is_friendly(app):
    _reset_showcase(app)
    chan = Chan()
    i = _post(app, chan)
    assert "Set a channel first" in i.response.sent[0][0]
    app.set_feature_guide_channel_id(chan.id)
    chan_forbidden = Chan(forbid=True)
    i = _post(app, chan_forbidden)
    assert "permission" in i.response.sent[-1][0]


@needs_db
def test_missing_image_files_stop_the_post_cleanly(app, monkeypatch, tmp_path):
    _reset_showcase(app)
    chan = Chan()
    app.set_feature_guide_channel_id(chan.id)
    monkeypatch.setattr(app, "SHOWCASE_DIR", str(tmp_path))
    i = _post(app, chan)
    assert not chan.posts and "missing" in i.response.sent[-1][0]


@needs_db
def test_showcase_files_are_inside_the_deployed_folder(app):
    assert app.showcase_missing_files() == []
    for _, path in app.showcase_messages():
        if path:
            assert os.path.abspath(path).startswith(HERE), "images must ship with the code, not live in temp dirs"


@needs_db
def test_features_command_is_ephemeral_with_view(app):
    i = Inter(member(False))
    run(cmd(app, "features")(i))
    _, kw = i.response.sent[0]
    assert kw["ephemeral"] is True and isinstance(kw["view"], app.FeatureGuideView)


@needs_db
def test_help_command_admin_visibility_and_unknown(app):
    def text(embeds):
        return " ".join(f.value for e in embeds for f in e.fields)
    i = Inter(member(False))
    run(cmd(app, "help")(i, None))
    embeds = i.response.sent[0][1]["embeds"]
    assert "/reset-vc-stats" not in text(embeds) and "/daily" in text(embeds)
    i = Inter(member(True))
    run(cmd(app, "help")(i, None))
    assert "/reset-vc-stats" in text(i.response.sent[0][1]["embeds"])
    # detail on an admin command is refused for a normal user
    i = Inter(member(False))
    run(cmd(app, "help")(i, "reset-vc-stats"))
    assert "don't know" in i.response.sent[0][0]
    i = Inter(member(False))
    run(cmd(app, "help")(i, "daily"))
    assert i.response.sent[0][1]["embeds"][0].title == "/daily"


class Flags:
    def __init__(self, e):
        self.ephemeral = e


class Msg:
    def __init__(self, index, ephemeral):
        self.embeds = [discord.Embed(title="t").set_footer(text=fg.render_page(index)["footer"])]
        self.flags = Flags(ephemeral)


@needs_db
def test_buttons_navigate_in_place_when_ephemeral_and_copy_when_public(app):
    view = app.FeatureGuideView()
    ids = {getattr(c, "custom_id", None) for c in view.children}
    assert {"fg_prev", "fg_next", "fg_cmds", "fg_home", "fg_jump"} <= ids
    nxt = next(c for c in view.children if c.custom_id == "fg_next")
    i = Inter(member(False), message=Msg(2, True))
    run(nxt.callback(i))
    assert i.response.edits
    assert fg.parse_footer_index(i.response.edits[0]["embed"].footer.text) == 3
    i = Inter(member(False), message=Msg(2, False))
    run(nxt.callback(i))
    assert not i.response.edits and i.response.sent[0][1]["ephemeral"] is True
    cmds = next(c for c in view.children if c.custom_id == "fg_cmds")
    i = Inter(member(False), message=Msg(0, True))
    run(cmds.callback(i))
    assert fg.parse_footer_index(i.response.edits[0]["embed"].footer.text) == fg.first_commands_index()


@needs_db
def test_guide_images_only_when_file_and_site_url_present(app, tmp_path, monkeypatch):
    monkeypatch.delenv("SITE_BASE_URL", raising=False)
    assert app.guide_image_url("main_panel.png") is None
    monkeypatch.setenv("SITE_BASE_URL", "https://example.test/")
    assert app.guide_image_url("main_panel.png") is None  # file missing
    monkeypatch.setattr(app, "GUIDE_ASSETS_DIR", str(tmp_path))
    (tmp_path / "main_panel.png").write_bytes(b"x")
    assert app.guide_image_url("main_panel.png") == "https://example.test/guide-assets/main_panel.png"
    assert app.guide_image_url("../app.py") is None
    assert app.guide_image_url("secret.png") is None


@needs_db
def test_guide_asset_route_serves_only_catalog_files(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "GUIDE_ASSETS_DIR", str(tmp_path))
    (tmp_path / "main_panel.png").write_bytes(b"png")
    (tmp_path / "other.png").write_bytes(b"png")
    c = app.app.test_client()
    assert c.get("/guide-assets/main_panel.png").status_code == 200
    assert c.get("/guide-assets/other.png").status_code == 404
    assert c.get("/guide-assets/../app.py").status_code == 404


@needs_db
def test_all_guide_embeds_build(app):
    for i in range(fg.page_count()):
        e = app.build_guide_embed(i)
        assert len(e) < 6000


# ---------------------------------------------------------------- showcase content & images (no DB)
import struct

SHOWCASE_DIR = os.path.join(HERE, "showcase")


def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_every_card_image_exists_and_is_a_valid_png():
    for card in sc.CARDS:
        path = os.path.join(SHOWCASE_DIR, card["file"])
        assert os.path.isfile(path), card["file"]
        w, h = _png_size(path)
        assert w == 1600 and 800 <= h <= 3000, (card["file"], w, h)
        assert os.path.getsize(path) < 2_000_000, card["file"]  # far below Discord's upload limit


def test_card_files_are_unique_and_ordered():
    files = sc.all_files()
    assert len(files) == len(set(files)) == len(sc.CARDS)
    assert files == sorted(files)
    assert [int(f.split("_")[0]) for f in files] == list(range(1, len(files) + 1))
    keys = [c["key"] for c in sc.CARDS]
    assert len(keys) == len(set(keys))


def test_no_stray_files_in_showcase_folder():
    pngs = {f for f in os.listdir(SHOWCASE_DIR) if f.endswith(".png")}
    assert pngs == set(sc.all_files())


def test_every_command_and_feature_page_is_covered():
    assert set(fg.command_names()) <= sc.mentioned_commands()
    covered = {k for c in sc.CARDS for k in c["covers"]}
    assert {p["key"] for p in fg.FEATURE_PAGES} <= covered
    assert sc.mentioned_commands() <= set(fg.command_names())  # nothing invented


def test_required_topics_present():
    text = " ".join(
        " ".join([c["title"], c["tagline"]] + [f"{l} {t}" for _, l, t in c["items"]]) for c in sc.CARDS).lower()
    for word in ("voice tracking", "recovery", "team split", "rematch", "regroup", "mute", "lock", "private",
                 "approval", "extend", "reminder", "cleanup", "who was with me", "my vc report", "profile",
                 "heatmap", "dashboard", "settings", "doctor", "birthday", "whatsapp", "announcement",
                 "points", "coinflip", "slots", "dice", "help"):
        assert word in text or word in " ".join(m for m in sc.MOCKS), word
    dashboard_tabs = ("dashboard", "teams", "lock", "stats", "log", "settings")
    card = next(c for c in sc.CARDS if c["key"] == "dashboard")
    blob = " ".join(f"{l} {t}" for _, l, t in card["items"]).lower()
    assert all(t in blob for t in dashboard_tabs)
    assert any(c["key"] == "overview" for c in sc.CARDS) and sc.CARDS[-1]["key"] == "overview"
    assert sc.CARDS[0]["key"] == "welcome"


def test_unbuilt_features_are_not_advertised():
    text = " ".join(
        " ".join([c["title"], c["tagline"], c["caption"]] + [f"{l} {t}" for _, l, t in c["items"]]) for c in sc.CARDS
    ).lower() + sc.INTRO_TEXT.lower()
    for item in fg.PLANNED:
        assert item[0].lower() not in text, item[0]
    for bad in ("excel", "health panel", "monthly report", "vc-monthly-report"):
        assert bad not in text, bad


def test_showcase_text_has_no_secrets_links_or_ids():
    blob = [sc.INTRO_TEXT]
    for c in sc.CARDS:
        blob += [c["title"], c["tagline"], c["caption"]] + [f"{l} {t}" for _, l, t in c["items"]]
        blob += [f"{n} {d}" for n, d in sc.card_commands(c)]
    for m in sc.MOCKS.values():
        blob += [m["title"]] + m.get("lines", []) + m.get("selects", []) + m.get("boxes", []) + m.get("flow", [])
        blob += [lbl for row in m.get("buttons", []) for lbl, _ in row]
    text = "\n".join(blob).lower()
    for bad in ("postgres", "database_url", "token", "api_key", "http://", "https://"):
        assert bad not in text, bad
    assert not re.search(r"\b\d{17,20}\b", text)
    for c in sc.CARDS:
        assert len(c["caption"]) <= 1900  # Discord message limit is 2000
    assert len(sc.INTRO_TEXT) <= 1900


def test_mockups_are_labelled_and_screens_are_real_files():
    assert sc.LABEL_MOCK == "ILLUSTRATIVE MOCKUP" and sc.LABEL_REAL == "REAL SCREENSHOT"
    for c in sc.CARDS:
        for key in c["mocks"]:
            assert key in sc.MOCKS
        for slot in c["screens"]:
            fname, _ = sc.SCREENS[slot]
            assert os.path.isfile(os.path.join(SHOWCASE_DIR, "screenshots", fname)), fname
    src = open(os.path.join(HERE, "tools", "build_showcase.py"), encoding="utf-8").read()
    assert "sc.LABEL_MOCK" in src and "sc.LABEL_REAL" in src


def test_mock_button_labels_exist_in_the_bot_code():
    """The drawn buttons use the real labels from app.py (no invented controls)."""
    for key, m in sc.MOCKS.items():
        for row in m.get("buttons", []):
            for label, _ in row:
                core = label.replace("\U0001F512 ", "").replace("\U0001F50A ", "")
                escaped = core.replace("＋", "\\uFF0B")
                assert core in APP_SRC or escaped in APP_SRC, (key, label)


def test_commands_cards_split_the_catalog_exactly_once():
    seen = []
    for c in sc.CARDS:
        seen += [n for n, _ in sc.card_commands(c)]
    assert len(seen) == len(set(seen))
    # together the command images list every command exactly once
    assert sorted(seen) == sorted(fg.command_names())
    user_card = next(c for c in sc.CARDS if c["key"] == "commands_user")
    assert all(not fg.find_command(n)[4] for n, _ in sc.card_commands(user_card))
