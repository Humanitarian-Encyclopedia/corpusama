import logging

logger = logging.getLogger(__name__)

__version__ = "0.0.1"


def current():
    """Logs the library's current version."""

    logger.debug(__version__)
