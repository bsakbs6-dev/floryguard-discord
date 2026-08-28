import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Token and DB
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "database" / "floryguard.db"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!fg ")

# Load config.json
CONFIG_FILE = BASE_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Config] Error loading {CONFIG_FILE}: {e}")
    return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Config] Error saving {CONFIG_FILE}: {e}")


CONFIG = load_config()

# Fast access constants
OWNER_IDS = set(CONFIG.get("hierarchy", {}).get("owner_ids", [1398717669607473254]))
SENIOR_ADMIN_IDS = set(CONFIG.get("hierarchy", {}).get("senior_admin_ids", [1291370925303795733]))
AUTHORIZED_GUILDS = {int(k): v for k, v in CONFIG.get("authorized_guilds", {}).items()}
UNAUTHORIZED_MESSAGE = CONFIG.get(
    "unauthorized_message",
    "Данный бот не подключен, просим отписать за подключением к devilfade"
)

MAX_WARNINGS = CONFIG.get("security", {}).get("max_warnings", 5)
WARN_EXPIRY_DAYS = CONFIG.get("security", {}).get("warn_expiry_days", 7)
