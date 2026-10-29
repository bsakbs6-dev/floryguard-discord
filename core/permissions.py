import discord
from typing import Optional, Tuple

from config import (
    OWNER_IDS,
    SENIOR_ADMIN_IDS,
    HIGHER_ADMIN_IDS,
    AUTHORIZED_GUILDS,
    UNAUTHORIZED_MESSAGE,
    STAFF_ACCESS_ROLE_ID,
    WARN_ACCESS_ROLE_ID,
    TRAINEE_ROLE_ID,
    TRAINEE_MAX_WARNS,
    DEFAULT_STAFF_MAX_WARNS,
    DEFAULT_DAILY_NORM_HOURS,
    STAFF_HIERARCHY,
    STAFF_ROLE_DAILY_NORM_HOURS,
    STAFF_ROLE_IDS,
    MODERATION_CMD_CHANNEL_ID,
    PRIVILEGED_ROLES_UNRESTRICTED_CHANNELS,
)
from database.db import db


def is_authorized_guild(guild: Optional[discord.Guild]) -> bool:
    """Checks if the server is in the authorized servers list."""
    if guild is None:
        return False
    return guild.id in AUTHORIZED_GUILDS


def is_module_enabled(guild: Optional[discord.Guild], module_key: str) -> bool:
    """Checks if a specific security module (automod_enabled, anti_nuke_enabled, anti_raid_enabled) is enabled."""
    if not is_authorized_guild(guild):
        return False
    return bool(AUTHORIZED_GUILDS[guild.id].get(module_key, True))


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


def is_higher_admin(user_id: int, guild_id: Optional[int] = None) -> bool:
    """
    Checks if the user is an absolute Higher Administrator (1398717669607473254, 1291370925303795733,
    or configured senior admin/owner). They enjoy total immunity from moderation, dismissal, and demotion.
    """
    if user_id in HIGHER_ADMIN_IDS or user_id in OWNER_IDS or user_id in SENIOR_ADMIN_IDS:
        return True
    return is_senior_admin(user_id, guild_id)


def is_senior_admin(user_id: int, guild_id: Optional[int] = None) -> bool:
    """
    Checks if the user is a designated senior administrator:
    - User ID in HIGHER_ADMIN_IDS (1398717669607473254, 1291370925303795733)
    - Server Owner / Bot Owner globally or on the specific guild
    - Global Senior Administrator
    - Server-specific Senior Administrator
    """
    if user_id in HIGHER_ADMIN_IDS or user_id in SENIOR_ADMIN_IDS:
        return True
    if is_bot_owner(user_id, guild_id):
        return True
    if guild_id and guild_id in AUTHORIZED_GUILDS:
        guild_senior_admins = set(AUTHORIZED_GUILDS[guild_id].get("senior_admin_ids", []))
        if user_id in guild_senior_admins:
            return True
    return False


def has_staff_access(member: discord.Member) -> bool:
    """
    Checks if member has access to staff commands:
    Requires role 1548650810622222387 (STAFF_ACCESS_ROLE_ID), any role in STAFF_HIERARCHY,
    or Senior Admin / Bot Owner status.
    """
    if is_senior_admin(member.id, member.guild.id):
        return True
    member_role_ids = {r.id for r in member.roles}
    if STAFF_ACCESS_ROLE_ID in member_role_ids:
        return True
    if member_role_ids & STAFF_ROLE_IDS:
        return True
    return False


def has_warn_access(member: discord.Member) -> bool:
    """
    Checks if member can issue/manage staff reprimands (/warn):
    Requires role 1454703598834421831 (WARN_ACCESS_ROLE_ID) or Senior Admin / Owner.
    """
    if is_senior_admin(member.id, member.guild.id):
        return True
    member_role_ids = {r.id for r in member.roles}
    return WARN_ACCESS_ROLE_ID in member_role_ids


def can_schedule_attestation(member: discord.Member) -> bool:
    """
    Checks if member can schedule or grade staff attestations:
    Requires curator role 1454703598834421831 (WARN_ACCESS_ROLE_ID),
    any privileged role, or Senior Admin / Owner.
    """
    return has_warn_access(member)


def can_unblock_attestation(member: discord.Member) -> bool:
    """
    Checks if member can prematurely unblock a candidate from 1-month attestation blacklist:
    Strictly limited to Senior Administrators, Bot Owners, or Higher Administration (1391723885900333214).
    """
    guild_id = member.guild.id if member.guild else None
    if is_bot_owner(member.id, guild_id) or is_senior_admin(member.id, guild_id):
        return True
    member_role_ids = {r.id for r in member.roles}
    return 1391723885900333214 in member_role_ids



def check_moderation_channel(interaction: discord.Interaction) -> Tuple[bool, Optional[str]]:
    """
    Enforces the moderation command execution channel policy:
    1. If the user has any privileged role (1550434344588288030, 1548646933101420564,
       1555624705342308403, 1391723885900333214) or is Server Owner / Bot Owner:
       -> Allowed in ANY channel.
    2. If the user has base curator role 1454703598834421831 (WARN_ACCESS_ROLE_ID):
       -> Allowed ONLY in channel 1564362994216083456 (MODERATION_CMD_CHANNEL_ID).
    3. Otherwise:
       -> Denied access.
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False, "❌ Команда доступна только участникам сервера."

    guild_id = interaction.guild_id or (interaction.guild.id if interaction.guild else None)
    if is_bot_owner(member.id, guild_id) or is_senior_admin(member.id, guild_id):
        return True, None

    member_role_ids = {r.id for r in member.roles}
    if member_role_ids & PRIVILEGED_ROLES_UNRESTRICTED_CHANNELS:
        return True, None

    if WARN_ACCESS_ROLE_ID in member_role_ids:
        if interaction.channel_id == MODERATION_CMD_CHANNEL_ID:
            return True, None
        return False, f"❌ Команды модерации для вашей должности разрешены только в канале <#{MODERATION_CMD_CHANNEL_ID}>."

    return False, "❌ У вас нет прав для выполнения команд модерации."


def get_member_staff_rank(member: discord.Member) -> Tuple[str, int, bool, int]:
    """
    Determines the staff member's highest rank from the 8-tier STAFF_HIERARCHY.
    Returns: (rank_name, max_warns, is_trainee, hierarchy_index)
    Lower hierarchy_index means higher authority (-2 = Владелец, -1 = Высшая Администрация, 0 = Высшая Администрация (роль), 7 = Стажер).
    """
    guild_id = member.guild.id if member.guild else None

    # 1. Absolute Higher Administration & Owner immunity (independent of roles, donate, or links)
    if is_bot_owner(member.id, guild_id):
        return "Владелец", DEFAULT_STAFF_MAX_WARNS, False, -2
    if is_higher_admin(member.id, guild_id):
        return "Высшая Администрация", DEFAULT_STAFF_MAX_WARNS, False, -1

    member_role_ids = {r.id for r in member.roles}

    # 2. Staff Hierarchy roles
    for idx, (role_id, role_name) in enumerate(STAFF_HIERARCHY):
        if role_id in member_role_ids:
            is_trainee = (role_id == TRAINEE_ROLE_ID)
            max_warns = TRAINEE_MAX_WARNS if is_trainee else DEFAULT_STAFF_MAX_WARNS
            return role_name, max_warns, is_trainee, idx

    # 3. Privileged, Curator, or Base Staff roles
    if member_role_ids & PRIVILEGED_ROLES_UNRESTRICTED_CHANNELS:
        return "Администрация", DEFAULT_STAFF_MAX_WARNS, False, 1
    if WARN_ACCESS_ROLE_ID in member_role_ids:
        return "Куратор", DEFAULT_STAFF_MAX_WARNS, False, 2
    if STAFF_ACCESS_ROLE_ID in member_role_ids:
        return "Стафф", DEFAULT_STAFF_MAX_WARNS, False, 99

    return "Участник", DEFAULT_STAFF_MAX_WARNS, False, 100


def get_member_daily_norm_seconds(member: discord.Member) -> int:
    """
    Returns the daily online norm in seconds based on the member's highest staff role.
    Each rank in STAFF_HIERARCHY can have its own daily_norm_hours configured.
    """
    member_role_ids = {r.id for r in member.roles}
    for role_id, _ in STAFF_HIERARCHY:
        if role_id in member_role_ids:
            hours = STAFF_ROLE_DAILY_NORM_HOURS.get(role_id, DEFAULT_DAILY_NORM_HOURS)
            return max(60, int(hours * 3600))
    return max(60, int(DEFAULT_DAILY_NORM_HOURS * 3600))


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
        "description": "Создание, правка и удаление вебхуками в каналах",
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
        if perms_str == "none":
            perms_list = []
        else:
            perms_list = [p.strip() for p in perms_str.split(",") if p.strip() and p.strip() != "none"]
        if permission in perms_list:
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
        if is_full:
            perms_list = list(AVAILABLE_PERMISSIONS.keys())
        elif perms_str == "none":
            perms_list = []
        else:
            perms_list = [p.strip() for p in perms_str.split(",") if p.strip() and p.strip() != "none"]
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
