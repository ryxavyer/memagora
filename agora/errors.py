"""One error shape for the whole API.

Every non-2xx response body is ``{"error": "<stable_code>", "message": "..."}``.
Clients switch on ``error``; humans read ``message``. FastAPI's default
``{"detail": ...}`` envelope is replaced in ``app.py`` so validation failures
look the same as everything else.
"""

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code, "message": message})


def unauthorized(message: str = "valid API key required") -> HTTPException:
    return api_error(401, "unauthorized", message)


def bad_request(code: str, message: str) -> HTTPException:
    return api_error(400, code, message)


def payload_too_large(message: str) -> HTTPException:
    # Literal rather than starlette's constant: the name for 413 changed
    # between versions and this file should not care which one is installed.
    return api_error(413, "batch_too_large", message)
