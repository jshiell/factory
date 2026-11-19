from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route


async def _ui_page(request: Request) -> HTMLResponse:
    return HTMLResponse("<!doctype html><title>factory</title><p>factory</p>")


async def _ui_without_slash(request: Request) -> RedirectResponse:
    return RedirectResponse("/ui/", status_code=308)


def ui_routes() -> list[Route]:
    return [Route("/ui", _ui_without_slash, methods=["GET"]), Route("/ui/", _ui_page, methods=["GET"])]
