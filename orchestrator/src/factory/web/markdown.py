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


def _remember_link_target(self, tokens, idx, options, env):
    env.setdefault("hrefs", []).append(tokens[idx].attrGet("href"))
    return _open_link_in_new_tab(self, tokens, idx, options, env)


def _show_link_target(self, tokens, idx, options, env):
    return f"</a> ({escapeHtml(env['hrefs'].pop())})"


def _new_markdown(show_targets: bool) -> MarkdownIt:
    markdown = MarkdownIt("commonmark", {"html": False})
    markdown.validateLink = _is_allowed_link
    markdown.add_render_rule("link_open", _remember_link_target if show_targets else _open_link_in_new_tab)
    if show_targets:
        markdown.add_render_rule("link_close", _show_link_target)
    markdown.add_render_rule("image", _render_image_as_alt_text)
    return markdown


_MARKDOWN = _new_markdown(show_targets=False)
_MARKDOWN_WITH_TARGETS = _new_markdown(show_targets=True)


def render_markdown(text: str) -> Markup:
    # Escape the rendered HTML, not just the input: entities and percent-encoded links decode to control characters.
    return Markup(escape_control(_MARKDOWN.render(text)))


def render_markdown_with_urls(text: str) -> Markup:
    """For pages where a person decides: every link shows where it really goes."""
    return Markup(escape_control(_MARKDOWN_WITH_TARGETS.render(text)))
