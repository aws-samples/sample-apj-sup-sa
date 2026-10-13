"""Structured logging to a file + stderr (T011).

Per project guideline: make use of log files when debugging. The loop is timing-sensitive,
so every log line carries a millisecond timestamp.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(log_file: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("voice_worker")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger
