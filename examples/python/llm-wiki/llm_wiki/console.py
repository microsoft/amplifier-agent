"""Progress lines on stdout, each prefixed with the step that prints it, such as `ingest> ` or `usage> `."""


def say(label: str, message: str) -> None:
    print(f"{label}> {message}", flush=True)
