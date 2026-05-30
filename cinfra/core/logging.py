import logging
import os

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int | str | None = None) -> None:
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    logging.basicConfig(
        level=level,
        format=DEFAULT_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
    )


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name)
