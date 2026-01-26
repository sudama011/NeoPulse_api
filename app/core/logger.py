import json
import logging
import sys
from datetime import datetime

from app.core.settings import settings


class JSONFormatter(logging.Formatter):
    """
    Formats logs as JSON for production monitoring (Datadog/CloudWatch).
    """

    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


class ColorFormatter(logging.Formatter):
    """
    Formats logs with colors for local development.
    """

    grey = "\x1b[38;20m"
    green = "\x1b[32;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    FORMATS = {
        logging.DEBUG: grey + format_str + reset,
        logging.INFO: green + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


def setup_logging():
    """
    Configures the Root Logger. Call this ONCE at startup (main.py or scripts).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    # Remove default handlers to avoid duplicate logs
    if root_logger.handlers:
        root_logger.handlers = []

    stream_handler = logging.StreamHandler(sys.stdout)

    if settings.ENV == "prod":
        stream_handler.setFormatter(JSONFormatter())
    else:
        stream_handler.setFormatter(ColorFormatter())

    root_logger.addHandler(stream_handler)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
