"""Configure consistent application logging."""

import logging
import sys


def setup_logger(name: str) -> logging.Logger:
    """Create or return a configured logger.

    Reuses an existing logger if it has already been configured to avoid
    attaching duplicate handlers.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

    return logger