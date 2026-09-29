"""Dark mode by default with a light-mode toggle (task 3.6; R2.8, R2.9, D13).

The click behaviour and the remembered choice run in the browser (theme.js) and are
checked by hand at Checkpoint 3; these tests cover everything the server sends.
"""
import re
from pathlib import Path

import pytest

from app import create_app
from tests.web import login, make_login_user

APP_DIR = Path(__file__).resolve().parent.parent / "app"
FIXED_COLOUR_CLASSES = re.compile(
    r"\b(bg-white|bg-light|bg-dark|bg-black|text-dark|text-white|text-black|text-light|"
    r"navbar-light|navbar-dark|table-light|table-dark|btn-light|btn-dark)\b"
)


@pytest.fixture
def anon(valid_env):
    return create_app(valid_env).test_client()


@pytest.mark.parametrize("path", ["/signin", "/no-such-page"])
def test_pages_are_dark_by_default(anon, path):  # R2.8
    assert '<html lang="en" data-bs-theme="dark">' in anon.get(path).get_data(as_text=True)


def test_theme_script_loads_in_the_head_before_the_styles(anon):  # no light flash for light-mode users
    html = anon.get("/signin").get_data(as_text=True)
    head = html.split("</head>")[0]
    assert '<script src="/static/js/theme.js"></script>' in head
    assert head.index("js/theme.js") < head.index("bootstrap.min.css")


def test_theme_script_is_served(anon):
    response = anon.get("/static/js/theme.js")
    assert response.status_code == 200 and "javascript" in response.content_type


def theme_buttons(html):
    return re.findall(r"<button[^>]*data-theme-toggle[^>]*>.*?</button>", html, re.S)


def check_theme_button(button):
    assert 'aria-label="Switch to light mode"' in button  # starts dark, so it offers light
    assert "theme-icon-sun" in button and "theme-icon-moon" in button
    assert 'aria-hidden="true"' in button  # the icons are decoration; the label is the name
    assert 'class="btn ' not in button  # plain nav-link spacing, so it lines up with the links


def test_theme_button_on_the_login_page(anon):  # R2.9
    buttons = theme_buttons(anon.get("/signin").get_data(as_text=True))
    assert len(buttons) == 1
    check_theme_button(buttons[0])


@pytest.mark.db
def test_theme_button_on_logged_in_pages(db, client):  # R2.9
    make_login_user(db, "correct horse battery", email="casey@example.test")
    login(client, "casey@example.test", "correct horse battery")
    html = client.get("/account").get_data(as_text=True)
    buttons = theme_buttons(html)
    assert len(buttons) == 2  # one per layout (user feedback, Checkpoint 3)
    for button in buttons:
        check_theme_button(button)

    # Phones: in the top bar next to the menu button, hidden on wider screens.
    phone = buttons[0]
    assert "d-lg-none" in phone
    assert html.index(phone) < html.index('class="navbar-toggler"') < html.index('id="main-nav"')

    # Wider screens: just left of Profile, in a list item hidden on phones.
    desktop_at = html.index(buttons[1])
    assert html.rindex('<li class="nav-item d-none d-lg-block">', 0, desktop_at) > html.index(">History</a>")
    assert html.index(">History</a>") < desktop_at < html.index(">Profile</button>") < html.index(">Log out<")


def test_theme_script_updates_every_theme_button():
    js = (APP_DIR / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    assert "querySelectorAll(\"[data-theme-toggle]\")" in js


def test_css_shows_the_sun_in_dark_mode_and_the_moon_in_light_mode():
    css = (APP_DIR / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert '[data-bs-theme="dark"] .theme-icon-moon' in css
    assert '[data-bs-theme="light"] .theme-icon-sun' in css


def test_templates_use_only_colour_mode_aware_classes():  # D13: both themes must work
    for template in (APP_DIR / "templates").rglob("*.html"):
        match = FIXED_COLOUR_CLASSES.search(template.read_text(encoding="utf-8"))
        assert not match, f"{template.name} uses {match.group(0)}; use bg-body / text-body style classes"


def test_theme_script_is_safe():
    js = (APP_DIR / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    for risky in ("eval(", "innerHTML", "document.write", "new Function"):
        assert risky not in js
    assert '"light"' in js and '"dark"' in js and "localStorage" in js
