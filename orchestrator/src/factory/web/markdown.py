from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markupsafe import Markup

from factory.textsafe import escape_control

_ALLOWED_SCHEMES = ("http:", "https:", "mailto:")


def _is_allowed_link(url: str) -> bool:
    return url.strip().lower().startswith(_ALLOWED_SCHEMES)


def _open_link_in_new_tab(self, tokens, idx, options, env):
    tokens[idx].attrSet("target", "_blank")
    tokens[idx].attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


def _render_image_as_alt_text(self, tokens, idx, options, env):
    return escapeHtml(self.renderInlineAsText(tokens[idx].children, options, env))


def _new_markdown() -> MarkdownIt:
    markdown = MarkdownIt("commonmark", {"html": False})
    markdown.validateLink = _is_allowed_link
    markdown.add_render_rule("link_open", _open_link_in_new_tab)
    markdown.add_render_rule("image", _render_image_as_alt_text)
    return markdown


_MARKDOWN = _new_markdown()


def render_markdown(text: str) -> Markup:
    # Escape the rendered HTML, not just the input: entities and percent-encoded links decode to control characters.
    return Markup(escape_control(_MARKDOWN.render(text)))
