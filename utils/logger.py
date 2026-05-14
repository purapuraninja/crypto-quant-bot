"""
utils/logger.py — Structured logger with file + console output.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def setup_logger(name: str = "trading_bot") -> logging.Logger:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console — INFO and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # File — DEBUG and above
    date_str = datetime.now().strftime("%Y%m%d")
    file_handler = logging.FileHandler(log_dir / f"bot_{date_str}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
