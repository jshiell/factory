from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from factory import service


def create_app(ctx) -> Starlette:
    async def create_task(request: Request) -> JSONResponse:
        body = await request.json()
        task = service.create_task(ctx.store, ctx.clock, body["goal"])
        return JSONResponse({"id": task.id}, status_code=201)

    return Starlette(routes=[Route("/tasks", create_task, methods=["POST"])])
