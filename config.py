import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Token and DB
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Persistent Data Directory (supports Docker/Bothost DATA_DIR volume)
DATA_DIR_ENV = os.getenv("DATA_DIR")
if DATA_DIR_ENV:
    DATA_DIRECTORY = Path(DATA_DIR_ENV)
else:
    DATA_DIRECTORY = BASE_DIR / "database"

DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = os.getenv("DATABASE_PATH", str(DATA_DIRECTORY / "floryguard.db"))

# Assets directory (Default avatars, fonts, etc.)
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_POST_AVATAR_PATH = ASSETS_DIR / "default_post_avatar.jpg"

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

# Staff system constants
_STAFF_CFG = CONFIG.get("staff", {})
STAFF_ACCESS_ROLE_ID = int(_STAFF_CFG.get("staff_access_role_id", 1548650810622222387))
WARN_ACCESS_ROLE_ID = int(_STAFF_CFG.get("warn_access_role_id", 1454703598834421831))
TRAINEE_ROLE_ID = int(_STAFF_CFG.get("trainee_role_id", 1454703157677395969))
TRAINEE_MAX_WARNS = int(_STAFF_CFG.get("trainee_max_warns", 1))
DEFAULT_STAFF_MAX_WARNS = int(_STAFF_CFG.get("default_staff_max_warns", 3))
DEFAULT_DAILY_NORM_HOURS = float(_STAFF_CFG.get("default_daily_norm_hours", 3.0))

_RAW_ROLES_HIERARCHY = _STAFF_CFG.get(
    "roles_hierarchy",
    [
        {"role_id": 1391723885900333214, "name": "Высшая Администрация", "daily_norm_hours": 4.0},
        {"role_id": 1548646933101420564, "name": "Администрация", "daily_norm_hours": 4.0},
        {"role_id": 1454703503292502155, "name": "Куратор", "daily_norm_hours": 3.5},
        {"role_id": 1548961652899061841, "name": "Гл. Модер | Зам. Куратора", "daily_norm_hours": 3.5},
        {"role_id": 1478446161810690159, "name": "Ст. Модер", "daily_norm_hours": 3.0},
        {"role_id": 1454703438989492389, "name": "Модер", "daily_norm_hours": 3.0},
        {"role_id": 1454703334459048000, "name": "Хелпер", "daily_norm_hours": 2.5},
        {"role_id": 1454703157677395969, "name": "Стажер", "daily_norm_hours": 2.0},
    ],
)

# Ordered from highest rank to lowest rank: (role_id, role_name)
STAFF_HIERARCHY = [
    (int(item["role_id"]), str(item["name"]))
    for item in _RAW_ROLES_HIERARCHY
]

# Daily norm in hours per role_id
STAFF_ROLE_DAILY_NORM_HOURS = {
    int(item["role_id"]): float(item.get("daily_norm_hours", DEFAULT_DAILY_NORM_HOURS))
    for item in _RAW_ROLES_HIERARCHY
}

STAFF_ROLE_IDS = {role_id for role_id, _ in STAFF_HIERARCHY} | {STAFF_ACCESS_ROLE_ID}
