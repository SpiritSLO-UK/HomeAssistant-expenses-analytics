"""Structured logging setup.

Keeps logging dependency-free for the MVP. JSON-ish single-line records make
add-on logs easy to read in the Home Assistant supervisor panel.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quieten noisy libraries; keep our own loggers at the configured level.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _CONFIGURED = True


def set_level(level: str) -> None:
    """Change the runtime log level (e.g. from the Settings UI). Affects the root
    logger, so all of our loggers follow; takes effect immediately."""
    logging.getLogger().setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
