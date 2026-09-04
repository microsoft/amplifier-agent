"""Run the HTTP face with host settings from the environment."""

import sys

from ._settings import Settings


def main() -> None:
    try:
        import uvicorn

        from ._app import create_app
    except ModuleNotFoundError as error:
        if error.name and error.name.split(".")[0] in {"uvicorn", "starlette"}:
            print(
                "HTTP dependencies are missing. Install amplifier-agent-http and start the service again.",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        raise
    try:
        settings = Settings.from_environment()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    uvicorn.run(create_app(settings), host=settings.bind, port=settings.port)


if __name__ == "__main__":
    main()
