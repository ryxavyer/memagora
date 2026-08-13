"""``agora-server`` entrypoint.

Deliberately thin: uvicorn is invoked programmatically so the container has one
process and one obvious command. Operators who want gunicorn workers or a
different ASGI server can point theirs at ``agora.app:create_app`` instead.
"""

import logging

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(config=config),
        host=config.host,
        port=config.port,
        log_level=config.log_level,
    )


if __name__ == "__main__":
    main()
