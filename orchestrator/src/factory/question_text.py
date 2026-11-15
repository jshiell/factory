def questions_text(questions) -> str:
    """The questions as plain text, one block each; callers escape or fence it."""
    return "\n\n".join(
        f"{q['number']}. {q['title']}\n{q['body']}\nrecommended: {q['recommended']}" for q in questions
    )
