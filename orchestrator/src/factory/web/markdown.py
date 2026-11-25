from markdown_it import MarkdownIt
from markupsafe import Markup

from factory.textsafe import escape_control

_ALLOWED_SCHEMES = ("http:", "https:", "mailto:")


def _is_allowed_link(url: str) -> bool:
    return url.strip().lower().startswith(_ALLOWED_SCHEMES)


def _new_markdown() -> MarkdownIt:
    markdown = MarkdownIt("commonmark", {"html": False})
    markdown.validateLink = _is_allowed_link
    return markdown


_MARKDOWN = _new_markdown()


def render_markdown(text: str) -> Markup:
    # Escape the rendered HTML, not just the input: entities and percent-encoded links decode to control characters.
    return Markup(escape_control(_MARKDOWN.render(text)))
