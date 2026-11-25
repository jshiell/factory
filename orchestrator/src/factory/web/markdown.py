from markdown_it import MarkdownIt
from markupsafe import Markup

from factory.textsafe import escape_control

_MARKDOWN = MarkdownIt("commonmark", {"html": False})


def render_markdown(text: str) -> Markup:
    # Escape the rendered HTML, not just the input: entities and percent-encoded links decode to control characters.
    return Markup(escape_control(_MARKDOWN.render(text)))
