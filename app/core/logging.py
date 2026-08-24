import logging


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
