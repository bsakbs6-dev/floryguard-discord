import discord
from typing import Optional

from config import OWNER_IDS, SENIOR_ADMIN_IDS, AUTHORIZED_GUILDS, UNAUTHORIZED_MESSAGE
from database.db import db


def is_authorized_guild(guild: Optional[discord.Guild]) -> bool:
    """Checks if the server is in the authorized servers list."""
    if guild is None:
        return False
    return guild.id in AUTHORIZED_GUILDS


def get_guild_quarantine_role_id(guild: discord.Guild) -> Optional[int]:
    """Retrieve the quarantine/demotion role ID for the guild."""
    if guild.id in AUTHORIZED_GUILDS:
        return AUTHORIZED_GUILDS[guild.id].get("quarantine_role_id")
    return None


def is_bot_owner(user_id: int, guild_id: Optional[int] = None) -> bool:
    """
    Checks if the user is a designated bot owner globally (everywhere),
    or a designated local server owner for the specific guild_id.
    """
    if user_id in OWNER_IDS:
        return True
    if guild_id and guild_id in AUTHORIZED_GUILDS:
        guild_owners = set(AUTHORIZED_GUILDS[guild_id].get("owner_ids", []))
        if user_id in guild_owners:
            return True
    return False


def is_senior_admin(user_id: int, guild_id: Optional[int] = None) -> bool:
    """
    Checks if the user is a designated senior administrator:
    - Server Owner on the specific guild
    - Global Senior Administrator (1291370925303795733)
    - Server-specific Senior Administrator
    """
    if guild_id and is_bot_owner(user_id, guild_id):
        return True
    if user_id in SENIOR_ADMIN_IDS:
        return True
    if guild_id and guild_id in AUTHORIZED_GUILDS:
        guild_senior_admins = set(AUTHORIZED_GUILDS[guild_id].get("senior_admin_ids", []))
        if user_id in guild_senior_admins:
            return True
    return False


AVAILABLE_PERMISSIONS = {
    # Роли
    "roles_assign": {
        "label": "Выдача ролей через ПКМ",
        "emoji": "👤",
        "description": "Выдача и снятие ролей участникам через контекстное меню Discord",
        "category": "roles"
    },
    "roles_edit": {
        "label": "Правка ролей",
        "emoji": "🎭",
        "description": "Изменение прав, названий, цветов и параметров ролей",
        "category": "roles"
    },
    "roles_create": {
        "label": "Создание ролей",
        "emoji": "➕",
        "description": "Создание новых ролей на сервере",
        "category": "roles"
    },
    "roles_delete": {
        "label": "Удаление ролей",
        "emoji": "🗑️",
        "description": "Удаление существующих ролей с сервера",
        "category": "roles"
    },
    # Модерация
    "ban_members": {
        "label": "Блокировка (Бан)",
        "emoji": "🔨",
        "description": "Право банить участников без автоматического отката Anti-Nuke",
        "category": "moderation"
    },
    "kick_members": {
        "label": "Изгнание (Кик)",
        "emoji": "👢",
        "description": "Право выгонять (кикать) участников без наказания Anti-Nuke",
        "category": "moderation"
    },
    "timeout_members": {
        "label": "Мут / Таймаут",
        "emoji": "⏳",
        "description": "Выдача и снятие таймаутов участникам",
        "category": "moderation"
    },
    "automod_bypass": {
        "label": "Обход AutoMod",
        "emoji": "💬",
        "description": "Полный иммунитет от фильтров ссылок, спама и мата",
        "category": "moderation"
    },
    # Каналы
    "channels_create": {
        "label": "Создание каналов",
        "emoji": "📁",
        "description": "Создание текстовых, голосовых каналов и категорий",
        "category": "channels"
    },
    "channels_edit": {
        "label": "Правка каналов",
        "emoji": "📝",
        "description": "Изменение названий, прав и параметров каналов",
        "category": "channels"
    },
    "channels_delete": {
        "label": "Удаление каналов",
        "emoji": "❌",
        "description": "Удаление каналов и категорий",
        "category": "channels"
    },
    # Сервер
    "server_edit": {
        "label": "Настройки сервера",
        "emoji": "⚙️",
        "description": "Изменение названия сервера, иконки, описания и параметров",
        "category": "server"
    },
    "webhooks_manage": {
        "label": "Управление вебхуками",
        "emoji": "🔗",
        "description": "Создание, правка и удаление вебхуков в каналах",
        "category": "server"
    },
}


async def is_admin(guild_id: int, user_id: int) -> bool:
    """
    Checks if the user is an authorized security administrator
    (Bot Owner, Senior Admin, or added to DB admins table).
    """
    if is_senior_admin(user_id, guild_id):
        return True
    return await db.is_admin(guild_id, user_id)


async def has_permission(guild_id: int, user_id: int, permission: str) -> bool:
    """
    Checks if user has a specific granular security permission.
    - Bot Owner & Senior Admins have full access to everything.
    - Appointed admins have permissions specified in DB ('all' or specific flags).
    - Whitelisted users have automod_bypass.
    """
    if is_senior_admin(user_id, guild_id):
        return True

    admin = await db.get_admin(guild_id, user_id)
    if admin:
        perms_str = admin.get("permissions") or "all"
        if perms_str == "all" or "*" in perms_str:
            return True
        perms_list = [p.strip() for p in perms_str.split(",") if p.strip()]
        if permission in perms_list:
            return True

        # Fallback mappings for backwards-compatibility
        if permission in ("channels_create", "channels_edit", "channels_delete") and "channels_manage" in perms_list:
            return True
        if permission in ("roles_create", "roles_delete") and "roles_edit" in perms_list:
            return True
        if permission == "channels_manage" and any(p in perms_list for p in ("channels_create", "channels_edit", "channels_delete")):
            return True
        if permission == "roles_edit" and any(p in perms_list for p in ("roles_create", "roles_delete")):
            return True

    if await db.is_whitelisted(guild_id, user_id):
        if permission in ("automod_bypass",):
            return True

    return False


async def get_admin_info(guild_id: int, user_id: int) -> dict:
    """Returns rank name, custom title, and list of permissions for display."""
    if is_bot_owner(user_id, guild_id):
        return {
            "rank": "owner",
            "title": "Создатель/Владелец бота" if user_id in OWNER_IDS else "Владелец сервера",
            "permissions": list(AVAILABLE_PERMISSIONS.keys()),
            "is_full": True
        }
    if user_id in SENIOR_ADMIN_IDS or (guild_id in AUTHORIZED_GUILDS and user_id in AUTHORIZED_GUILDS[guild_id].get("senior_admin_ids", [])):
        return {
            "rank": "senior_admin",
            "title": "Высший Администратор",
            "permissions": list(AVAILABLE_PERMISSIONS.keys()),
            "is_full": True
        }

    admin = await db.get_admin(guild_id, user_id)
    if admin:
        title = admin.get("title") or "Администратор Безопасности"
        perms_str = admin.get("permissions") or "all"
        is_full = (perms_str == "all" or "*" in perms_str)
        perms_list = list(AVAILABLE_PERMISSIONS.keys()) if is_full else [p.strip() for p in perms_str.split(",") if p.strip()]
        return {
            "rank": "admin",
            "title": title,
            "permissions": perms_list,
            "is_full": is_full
        }

    if await db.is_whitelisted(guild_id, user_id):
        return {
            "rank": "whitelist",
            "title": "В Белом Списке",
            "permissions": ["automod_bypass"],
            "is_full": False
        }

    return {
        "rank": "member",
        "title": "Участник",
        "permissions": [],
        "is_full": False
    }


async def is_whitelisted(guild_id: int, user_id: int) -> bool:
    """
    Checks if the user is whitelisted or higher rank.
    Whitelisted users don't get their roles stripped.
    """
    if await is_admin(guild_id, user_id):
        return True
    return await db.is_whitelisted(guild_id, user_id)


async def can_manage_security(guild_id: int, user_id: int) -> bool:
    """Check if user can manage whitelist and admins (Owner & Senior Admins)."""
    return is_senior_admin(user_id, guild_id)

