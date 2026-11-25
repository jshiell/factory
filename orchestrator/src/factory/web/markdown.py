from markdown_it import MarkdownIt
from markupsafe import Markup

_MARKDOWN = MarkdownIt("commonmark", {"html": False})


def render_markdown(text: str) -> Markup:
    return Markup(_MARKDOWN.render(text))
