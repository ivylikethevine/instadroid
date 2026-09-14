"""Secrets from the environment or from a file, shared by the scraper (instadroid/config.py) and the
feed server (app.py), which deliberately doesn't import the scraper package.

NAME_FILE=/run/secrets/name reads the value from that file instead of NAME, the convention Docker
secrets and many official images follow, so a password never has to sit in `.env` or show up in
`docker inspect`."""

import os
from collections.abc import Mapping


def env_secret(name: str, environ: Mapping[str, str] = os.environ) -> str:
    """The value of `name`, or the contents of the file `name`_FILE points at (one trailing newline
    stripped, since `echo pw > file` adds one). Setting both, or a file that can't be read, raises:
    a misconfigured secret should stop the process with a clear message, not run on an empty one."""
    path = environ.get(f"{name}_FILE", "")
    if not path:
        return environ.get(name, "")
    if environ.get(name):
        raise RuntimeError(f"both {name} and {name}_FILE are set; use one")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read()
    except OSError as e:
        raise RuntimeError(f"{name}_FILE={path!r} can't be read: {e.strerror}") from None
    return value.removesuffix("\n").removesuffix("\r")
