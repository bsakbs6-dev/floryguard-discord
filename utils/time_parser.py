import re
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional

try:
    from zoneinfo import ZoneInfo
    try:
        MSK_TZ = ZoneInfo("Europe/Moscow")
    except Exception:
        MSK_TZ = timezone(timedelta(hours=3), name="MSK")
except ImportError:
    MSK_TZ = timezone(timedelta(hours=3), name="MSK")

from config import ATTESTATION_MAX_SCHEDULE_DAYS

MONTH_NAMES_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def parse_msk_datetime(input_text: str) -> Tuple[bool, Optional[datetime], str]:
    """
    Parses date and time input from Moscow Time (Europe/Moscow, UTC+3)
    and converts it to a timezone-aware UTC datetime for storage and comparison.

    Supported patterns:
      - DD.MM.YYYY HH:MM (e.g. '28.10.2026 19:00', '28.10.2026 в 19:00')
      - DD.MM HH:MM (e.g. '28.10 19:00', '28.10 в 19:00')

    Returns:
      (success: bool, utc_datetime: Optional[datetime], error_message: str)
    """
    if not input_text or not input_text.strip():
        return False, None, "Поле даты и времени не может быть пустым."

    clean = input_text.strip()
    # Normalize separators: replace 'в', '@', 'T' with space
    clean = re.sub(r"\s+(?:в|at|@|t)\s+", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean)

    now_msk = datetime.now(MSK_TZ)
    parsed_msk: Optional[datetime] = None

    # Pattern 1: DD.MM.YYYY HH:MM or DD/MM/YYYY HH:MM or DD-MM-YYYY HH:MM
    match_full = re.match(
        r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\s+(\d{1,2}):(\d{2})$", clean
    )
    if match_full:
        day, month, year, hour, minute = map(int, match_full.groups())
        try:
            parsed_msk = datetime(year, month, day, hour, minute, tzinfo=MSK_TZ)
        except ValueError as e:
            return False, None, f"Некорректная календарная дата или время: {e}."

    # Pattern 2: DD.MM HH:MM or DD/MM HH:MM
    if not parsed_msk:
        match_short = re.match(
            r"^(\d{1,2})[./\-](\d{1,2})\s+(\d{1,2}):(\d{2})$", clean
        )
        if match_short:
            day, month, hour, minute = map(int, match_short.groups())
            year = now_msk.year
            try:
                candidate = datetime(year, month, day, hour, minute, tzinfo=MSK_TZ)
                # If date in current year has already passed by more than 30 days, assume next year
                if candidate < now_msk - timedelta(days=30):
                    candidate = datetime(year + 1, month, day, hour, minute, tzinfo=MSK_TZ)
                parsed_msk = candidate
            except ValueError as e:
                return False, None, f"Некорректная календарная дата или время: {e}."

    if not parsed_msk:
        return (
            False,
            None,
            "Неверный формат даты и времени.\n"
            "Используйте формат: `ДД.ММ ЧЧ:ММ` (например: `28.10 19:00`) "
            "или `ДД.ММ.ГГГГ ЧЧ:ММ` (например: `28.10.2026 19:00`).",
        )

    # Convert to UTC datetime
    utc_dt = parsed_msk.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)

    # Validation: time cannot be in the past (minimum +2 minutes ahead)
    if utc_dt <= now_utc + timedelta(minutes=2):
        return (
            False,
            None,
            "Нельзя назначить аттестацию на прошедшее время. "
            "Укажите время хотя бы на 2-3 минуты позже текущего момента по МСК.",
        )

    # Validation: cannot exceed max schedule forward days
    max_forward = now_utc + timedelta(days=ATTESTATION_MAX_SCHEDULE_DAYS)
    if utc_dt > max_forward:
        return (
            False,
            None,
            f"Нельзя назначать аттестацию более чем на {ATTESTATION_MAX_SCHEDULE_DAYS} дней вперед.",
        )

    return True, utc_dt, ""


def format_msk(dt: datetime, full: bool = True) -> str:
    """Formats any datetime into formatted Moscow Time (UTC+3) representation."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_msk = dt.astimezone(MSK_TZ)

    month_str = MONTH_NAMES_RU.get(dt_msk.month, f"{dt_msk.month:02d}")
    if full:
        return f"{dt_msk.day} {month_str} {dt_msk.year} г. в {dt_msk.hour:02d}:{dt_msk.minute:02d} МСК"
    return f"{dt_msk.day:02d}.{dt_msk.month:02d}.{dt_msk.year} {dt_msk.hour:02d}:{dt_msk.minute:02d} МСК"


def to_discord_timestamp(dt: datetime, style: str = "F") -> str:
    """Converts datetime to native dynamic Discord timestamp string <t:UNIX:style>."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    unix_ts = int(dt.timestamp())
    return f"<t:{unix_ts}:{style}>"
