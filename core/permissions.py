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


def is_bot_owner(user_id: int) -> bool:
    """Checks if the user is a designated bot owner."""
    return user_id in OWNER_IDS


def is_senior_admin(user_id: int, guild_id: Optional[int] = None) -> bool:
    """
    Checks if the user is a designated senior administrator globally,
    or specifically for the given guild_id.
    """
    if is_bot_owner(user_id) or user_id in SENIOR_ADMIN_IDS:
        return True
    if guild_id and guild_id in AUTHORIZED_GUILDS:
        guild_senior_admins = set(AUTHORIZED_GUILDS[guild_id].get("senior_admin_ids", []))
        if user_id in guild_senior_admins:
            return True
    return False


async def is_admin(guild_id: int, user_id: int) -> bool:
    """
    Checks if the user is an authorized security administrator
    (Bot Owner, Senior Admin, or added to DB admins table).
    """
    if is_senior_admin(user_id, guild_id):
        return True
    return await db.is_admin(guild_id, user_id)


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
