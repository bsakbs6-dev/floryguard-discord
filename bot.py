import discord
from discord.ext import commands
import os
import sys
from typing import Optional, List, Dict, Any, Tuple

from config import COMMAND_PREFIX, AUTHORIZED_GUILDS, UNAUTHORIZED_MESSAGE, OWNER_IDS, SENIOR_ADMIN_IDS
from database.db import db
from utils.logger import logger
from utils.embeds import (
    create_security_embed,
    warn_dm_embed,
    anti_nuke_alert_embed,
    COLOR_DANGER,
    COLOR_WARNING,
    COLOR_SUCCESS,
)
from core.rate_limiter import TokenBucketRateLimiter, MessageHistoryCache, JoinSpikeTracker


class FloryGuardBot(commands.Bot):
    def __init__(self):
        # Configure comprehensive security intents
        intents = discord.Intents.all()
        
        super().__init__(
            command_prefix=commands.when_mentioned_or(COMMAND_PREFIX),
            intents=intents,
            help_command=None,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="🛡️ FloryMine Security System"
            ),
            status=discord.Status.online
        )

        # In-memory security rate limiters and trackers
        self.rate_limiter = TokenBucketRateLimiter(max_tokens=4, window_seconds=3.0)
        self.msg_history = MessageHistoryCache(max_history=5, ttl_seconds=60.0)
        self.raid_tracker = JoinSpikeTracker(threshold=5, window_seconds=5.0)

    async def setup_hook(self):
        """Called automatically before the bot starts accepting events."""
        logger.info("Initializing Database...")
        await db.init_db()

        logger.info("Loading Security Cogs...")
        cogs_list = [
            "cogs.events",
            "cogs.anti_nuke",
            "cogs.automod",
            "cogs.anti_raid",
            "cogs.moderation",
            "cogs.admin_guard",
        ]

        for cog in cogs_list:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded Cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)

    async def on_ready(self):
        logger.info(f"==================================================")
        logger.info(f"🛡️ FloryGuardBot is ONLINE as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s):")
        for g in self.guilds:
            status = "AUTHORIZED" if g.id in AUTHORIZED_GUILDS else "UNAUTHORIZED"
            logger.info(f" - [{status}] {g.name} (ID: {g.id}) | Members: {g.member_count}")
        logger.info(f"==================================================")

        # Sync application commands (Global + Direct Guild Sync for 0-second instant updates)
        try:
            # 1. Sync to each authorized guild immediately
            for g in self.guilds:
                if g.id in AUTHORIZED_GUILDS:
                    self.tree.copy_global_to(guild=g)
                    synced_guild = await self.tree.sync(guild=g)
                    logger.info(f"Instant synced {len(synced_guild)} Slash Commands to '{g.name}'")

            # 2. Sync globally
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} Slash Commands globally.")
        except Exception as e:
            logger.error(f"Error syncing slash commands: {e}")

    def get_quarantine_role_id(self, guild_id: int) -> Optional[int]:
        """Fetch configured quarantine role ID for guild."""
        if guild_id in AUTHORIZED_GUILDS:
            return AUTHORIZED_GUILDS[guild_id].get("quarantine_role_id")
        return None

    def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Find or determine the security log channel."""
        cfg_channel_id = AUTHORIZED_GUILDS.get(guild.id, {}).get("log_channel_id")
        if cfg_channel_id:
            ch = guild.get_channel(cfg_channel_id)
            if ch and isinstance(ch, discord.TextChannel):
                return ch

        # Search by common security log names
        for ch in guild.text_channels:
            if ch.name in ["security-logs", "audit-logs", "guard-logs", "floryguard-logs", "логи-безопасности", "logs"]:
                return ch
        return None

    async def send_security_log(self, guild: discord.Guild, embed: discord.Embed):
        """Send embed to the guild's security log channel."""
        try:
            log_ch = self.get_log_channel(guild)
            if log_ch and log_ch.permissions_for(guild.me).send_messages:
                await log_ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send security log in {guild.name}: {e}")

    async def quarantine_member(self, guild: discord.Guild, member: discord.Member, reason: str) -> bool:
        """
        Strips all removable roles from the member and assigns the Quarantine role.
        """
        quarantine_role_id = self.get_quarantine_role_id(guild.id)
        if not quarantine_role_id:
            logger.warning(f"No quarantine role configured for guild {guild.id} ({guild.name})")
            return False

        quarantine_role = guild.get_role(quarantine_role_id)
        if not quarantine_role:
            logger.warning(f"Quarantine role ID {quarantine_role_id} not found in {guild.name}")
            return False

        bot_member = guild.me
        if not bot_member.guild_permissions.manage_roles:
            logger.error(f"Bot lacks 'Manage Roles' permission in {guild.name}")
            return False

        # Filter roles that bot can remove (strictly lower than bot's highest role)
        roles_to_remove = [
            r for r in member.roles
            if r.name != "@everyone" and not r.managed and r < bot_member.top_role and r.id != quarantine_role_id
        ]

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=f"FloryGuard Quarantine: {reason}")
            
            # Add quarantine role
            if quarantine_role not in member.roles:
                await member.add_roles(quarantine_role, reason=f"FloryGuard Quarantine: {reason}")

            # Notify user via DM
            try:
                dm_embed = create_security_embed(
                    title="🚨 ВЫ БЫЛИ СНЯТЫ СО ВСЕХ РОЛЕЙ И ИЗОЛИРОВАНЫ",
                    description=(
                        f"Сервер: **{guild.name}**\n\n"
                        f"🔒 **Причина:** `{reason}`\n"
                        f"🛡️ С вас были сняты все права и выдана роль изоляции.\n"
                        f"Если вы считаете, что это ошибка, обратитесь к высшей администрации."
                    ),
                    color=COLOR_DANGER
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass

            # Log to DB
            await db.log_security_event(guild.id, member.id, "QUARANTINE_DEMOTE", reason)
            return True
        except discord.Forbidden:
            logger.error(f"Forbidden: Cannot modify roles for {member.name} ({member.id}) in {guild.name} (Check Role Hierarchy)")
            return False
        except Exception as e:
            logger.error(f"Error while quarantining {member.id}: {e}")
            return False

    async def issue_warning_and_check(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        moderator_id: int = 0
    ) -> Tuple[int, int, bool]:
        """
        Issues a warning with 7-day expiration, sends DM to the member,
        and demotes if warnings reach 5.
        Returns: (warn_id, active_warn_count, was_demoted)
        """
        warn_id, active_count = await db.add_warning(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=moderator_id,
            reason=reason,
            expiry_days=7
        )

        demoted = False
        if active_count >= 5:
            # 5 warns reached -> Demote + Quarantine
            await self.quarantine_member(guild, member, f"Накоплено {active_count}/5 предупреждений")
            demoted = True
        else:
            # Send standard DM
            try:
                dm_emb = warn_dm_embed(warn_count=active_count, max_warns=5, reason=reason)
                await member.send(embed=dm_emb)
            except Exception:
                pass

        return warn_id, active_count, demoted
