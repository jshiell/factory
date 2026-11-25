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


def test_raw_html_comes_out_as_escaped_text():
    html = render_markdown('<script>x()</script> <img src=x onerror=y()>')

    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
