import pytest
from markupsafe import Markup

from factory.web.markdown import render_markdown


def test_bold_text_renders_as_strong_in_a_paragraph():
    html = render_markdown("**b**")

    assert html == Markup("<p><strong>b</strong></p>\n")


def test_entities_and_encoded_links_never_yield_raw_format_characters():
    html = render_markdown("a&#x202e;b &zwnj; <https://x.test/%E2%80%AE>")

    assert "‮" not in html
    assert "‌" not in html


def test_an_escape_character_in_the_input_reaches_the_output_as_literal_text():
    html = render_markdown("a\x1bb")

    assert "\x1b" not in html
    assert "\\x1b" in html


@pytest.mark.parametrize("target", [
    "javascript:alert(1)", "vbscript:x", "data:image/png;base64,AAAA", "/relative", "//evil.test/x", "ftp://x.test/f",
    "intent://x",
])
def test_links_outside_the_allowlist_render_as_text(target):
    html = render_markdown(f"[go]({target})")

    assert "<a" not in html


@pytest.mark.parametrize("target", ["https://x.test/a", "http://x.test/a", "mailto:a@x.test"])
def test_allowlisted_links_render_as_anchors(target):
    assert f'href="{target}"' in render_markdown(f"[go]({target})")


def test_links_open_in_a_new_tab_without_opener_or_referrer():
    html = render_markdown("[go](https://x.test/a)")

    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_an_image_renders_as_its_alt_text_only():
    html = render_markdown("![the alt](https://x.test/y.png)")

    assert "<img" not in html
    assert "the alt" in html
    assert "!" not in html


def test_markup_in_image_alt_text_is_escaped():
    html = render_markdown("![<b>x</b> & y](https://x.test/y.png)")

    assert "<b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in html


def test_raw_html_comes_out_as_escaped_text():
    html = render_markdown('<script>x()</script> <img src=x onerror=y()>')

    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
