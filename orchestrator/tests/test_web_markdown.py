from markupsafe import Markup

from factory.web.markdown import render_markdown


def test_bold_text_renders_as_strong_in_a_paragraph():
    html = render_markdown("**b**")

    assert html == Markup("<p><strong>b</strong></p>\n")
