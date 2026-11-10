import time
from collections.abc import Callable

import httpx

POLL_SECONDS = 0.5
FINISHED = ("succeeded", "failed", "interrupted")


class ServerUnreachable(Exception):
    """Nothing is listening where the client looked."""


class ServerRefused(Exception):
    def __init__(self, status: int, error: str, detail: str):
        super().__init__(f"{error}: {detail}" if detail else error)
        self.status, self.error, self.detail = status, error, detail


class ServerClient:
    def __init__(self, http: httpx.Client, token: str, sleep: Callable[[float], None] = time.sleep):
        self._http, self._headers, self._sleep = http, {"Authorization": f"Bearer {token}"}, sleep

    def get(self, path: str, **params) -> dict:
        return self._send("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> dict:
        return self._send("POST", path, json=body or {})

    def wait(self, job_id: str) -> dict:
        while True:
            job = self.get(f"/jobs/{job_id}")
            if job["state"] in FINISHED:
                return job
            self._sleep(POLL_SECONDS)

    def _send(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, headers=self._headers, **kwargs)
        except httpx.ConnectError as error:
            raise ServerUnreachable(str(error)) from None
        body = response.json()
        if response.status_code >= 400:
            raise ServerRefused(response.status_code, body.get("error", "error"), body.get("detail", ""))
        return body
