import logging
import sys
from pathlib import Path
from colorama import Fore, Style, init

init(autoreset=True)

# Custom Formatter with colors
class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, Fore.WHITE)
        orig_levelname = record.levelname
        record.levelname = f"{color}{orig_levelname:<8}{Style.RESET_ALL}"
        formatted = super().format(record)
        record.levelname = orig_levelname  # Restore original so FileHandler gets clean plain text
        return formatted


def setup_logger(name: str = "FloryGuard", log_file: str = "floryguard.log") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # Console handler
        c_handler = logging.StreamHandler(sys.stdout)
        c_format = ColoredFormatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        c_handler.setFormatter(c_format)
        logger.addHandler(c_handler)

        # File handler
        log_path = Path(log_file)
        f_handler = logging.FileHandler(log_path, encoding="utf-8")
        f_format = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        f_handler.setFormatter(f_format)
        logger.addHandler(f_handler)

    return logger


logger = setup_logger()
