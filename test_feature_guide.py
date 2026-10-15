"""Tests for the feature guide (/features) and /help.

Part 1 is pure (no database). Part 2 drives the real slash-command callbacks and is
skipped unless TEST_DATABASE_URL is set (same convention as the integration tests).
"""
import asyncio, importlib.util, os, re, sys

import pytest

import feature_guide as fg

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
        self.sent, self.edits = [], []

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))

    async def edit_message(self, **kw):
        self.edits.append(kw)


class Chan:
    id = 4242
    mention = "<#4242>"

    def __init__(self):
        self.posts = []

    async def send(self, **kw):
        self.posts.append(kw)


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


@needs_db
def test_admin_sets_channel_and_posts_guide(app):
    chan = Chan()
    i = Inter(member(True), Guild(chan))
    run(cmd(app, "post-feature-guide")(Inter(member(True), Guild(chan))))  # not set yet or set earlier
    chan.posts.clear()
    run(cmd(app, "set-feature-guide-channel")(i, chan))
    assert app.get_feature_guide_channel_id() == 4242
    i = Inter(member(True), Guild(chan))
    run(cmd(app, "post-feature-guide")(i))
    assert len(chan.posts) == 1
    embed = chan.posts[0]["embed"]
    assert embed.footer.text.startswith("Page 1/")
    assert isinstance(chan.posts[0]["view"], app.FeatureGuideView)


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
