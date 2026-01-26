import atexit
import json
import logging
import logging.handlers
import sys
from datetime import datetime
from queue import Queue

from app.core.settings import settings

# Global listener to ensure it stays alive
_log_listener = None


class JSONFormatter(logging.Formatter):
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
    Configures Non-Blocking Logging via QueueHandler.
    """
    global _log_listener

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    if root_logger.handlers:
        root_logger.handlers = []

    # 1. The Actual Destination (Blocking)
    console_handler = logging.StreamHandler(sys.stdout)
    if settings.ENV == "prod":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(ColorFormatter())

    # 2. The Queue (Buffer)
    log_queue = Queue(-1)  # Unlimited size

    # 3. The QueueHandler (Non-Blocking Interface)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger.addHandler(queue_handler)

    # 4. The Listener (Background Thread)
    _log_listener = logging.handlers.QueueListener(log_queue, console_handler)
    _log_listener.start()

    # Ensure clean shutdown of logging thread
    atexit.register(_log_listener.stop)

    # Silence noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
