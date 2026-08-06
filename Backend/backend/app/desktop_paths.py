import os
from pathlib import Path


APP_NAME = "Planix"


def user_data_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".planix"


def resolve_database_path() -> Path:
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))

    explicit_path = os.getenv("PLANIX_DB_PATH")
    if explicit_path:
        return Path(explicit_path)

    if os.getenv("PLANIX_ENV") == "desktop" or os.getenv("PLANIX_USE_USER_DATA") == "1":
        return user_data_dir() / "planix.db"

    return Path("data/planix.db")
