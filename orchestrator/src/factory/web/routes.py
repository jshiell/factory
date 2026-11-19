from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from factory.web.auth import session_cookie_name


async def _ui_page(request: Request) -> HTMLResponse:
    return HTMLResponse("<!doctype html><title>factory</title><p>factory</p>")


async def _ui_without_slash(request: Request) -> RedirectResponse:
    return RedirectResponse("/ui/", status_code=308)


async def _login(request: Request) -> HTMLResponse:
    state = request.app.state
    if not state.login_codes.redeem(request.query_params.get("code", "")):
        return HTMLResponse("<!doctype html><title>factory</title><p>that login link is used or expired; run <code>factory ui</code></p>",
                            status_code=401)
    response = HTMLResponse('<!doctype html><title>factory</title><p><a href="/ui/">open the factory UI</a></p>')
    response.set_cookie(session_cookie_name(request.headers.get("host", "")), state.sessions.create(),
                        httponly=True, samesite="strict", path="/ui")
    return response


STATIC_DIR = Path(__file__).parent / "static"


def ui_routes() -> list[Route | Mount]:
    return [Route("/ui", _ui_without_slash, methods=["GET"]), Route("/ui/", _ui_page, methods=["GET"]),
            Route("/ui/login", _login, methods=["GET"]), Mount("/ui/static", StaticFiles(directory=STATIC_DIR))]
