"""Sentence-case check for rendered web pages (plan 91), on the stdlib HTML parser."""
from html.parser import HTMLParser

DATA_PREFIX = "zdata-"  # seeded data values; shown exactly as stored, so the check skips text that starts with one
_TRACKED = {"p", "li", "h1", "h2", "h3", "th", "td", "button", "label", "title", "a", "strong"}
_SKIPPED = {"code", "pre", "textarea", "script", "style"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.violations: list[str] = []
        self._open: list[list[str]] = []  # [tag, text so far] of each open tracked element
        self._skipping = 0
        self._plan_depth = 0  # inside the rendered plan, which is agent-written markdown

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div" and (self._plan_depth or "plan" in (attributes.get("class") or "").split()):
            self._plan_depth += 1
        if tag in _SKIPPED:
            self._skipping += 1
        if tag in _TRACKED:
            self._open.append([tag, ""])
        if attributes.get("aria-label"):
            self._check("aria-label", attributes["aria-label"])

    def handle_data(self, data):
        if not self._skipping and not self._plan_depth:
            for element in self._open:
                element[1] += data

    def handle_endtag(self, tag):
        if tag == "div" and self._plan_depth:
            self._plan_depth -= 1
        if tag in _SKIPPED:
            self._skipping -= 1
        if tag in _TRACKED:
            for index in range(len(self._open) - 1, -1, -1):
                if self._open[index][0] == tag:
                    name, text = self._open[index]
                    del self._open[index:]
                    self._check(name, text)
                    break

    def _check(self, name: str, text: str):
        text = text.strip()
        if text and not text.startswith(DATA_PREFIX) and text[0].isalpha() and text[0].islower():
            self.violations.append(f"{name}: {text}")


def sentence_case_violations(html: str) -> list[str]:
    """'tag: text' for each element of the page whose text starts with a lower-case letter."""
    checker = _Checker()
    checker.feed(html)
    checker.close()
    return checker.violations
