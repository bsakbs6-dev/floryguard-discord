import datetime
import discord
from discord.ext import commands
from typing import Optional, Tuple

from config import (
    COMMAND_PREFIX,
    AUTHORIZED_GUILDS,
    CONFIG,
    MAX_WARNINGS,
    WARN_EXPIRY_DAYS,
)
from database.db import db
from utils.logger import logger
from utils.embeds import (
    create_security_embed,
    warn_dm_embed,
    COLOR_DANGER,
)
from core.rate_limiter import (
    TokenBucketRateLimiter,
    MessageHistoryCache,
    JoinSpikeTracker,
    CrossChannelSpamTracker,
)


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

        automod_cfg = CONFIG.get("automod", {})
        antiraid_cfg = CONFIG.get("anti_raid", {})

        # In-memory security rate limiters and trackers configured from config.json
        self.rate_limiter = TokenBucketRateLimiter(
            max_tokens=int(automod_cfg.get("rate_limit_messages", 4)),
            window_seconds=float(automod_cfg.get("rate_limit_seconds", 3.0))
        )
        self.msg_history = MessageHistoryCache(max_history=5, ttl_seconds=60.0)
        self.raid_tracker = JoinSpikeTracker(
            threshold=int(antiraid_cfg.get("join_spike_threshold", 5)),
            window_seconds=float(antiraid_cfg.get("join_spike_window_seconds", 5.0))
        )
        self.cross_channel_tracker = CrossChannelSpamTracker(max_channels=3, window_seconds=4.0)
        self.trusted_webhook_ids: set[int] = set()
        self.synced = False

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
            "cogs.transfer",
            "cogs.permissions",
            "cogs.staff",
            "cogs.attestation",
            "cogs.posts",
        ]

        for cog in cogs_list:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded Cog: {cog}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)

    async def on_ready(self):
        logger.info("==================================================")
        logger.info(f"🛡️ FloryGuardBot is ONLINE as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s):")
        for g in self.guilds:
            status = "AUTHORIZED" if g.id in AUTHORIZED_GUILDS else "UNAUTHORIZED"
            logger.info(f" - [{status}] {g.name} (ID: {g.id}) | Members: {g.member_count}")
        logger.info("==================================================")

        # Sync application commands
        if not self.synced:
            try:
                # Очищаем локальные команды гильдий, чтобы убрать дубликаты
                for g in self.guilds:
                    self.tree.clear_commands(guild=g)
                    await self.tree.sync(guild=g)
                    logger.info(f"Cleared duplicate guild commands for '{g.name}' ({g.id}).")

                # Регистрируем глобальные команды
                synced_global = await self.tree.sync()
                logger.info(f"Synced {len(synced_global)} Slash Commands globally.")
                self.synced = True
            except Exception as e:
                logger.error(f"Error syncing slash commands: {e}")

    def get_quarantine_role_id(self, guild_id: int) -> Optional[int]:
        """Fetch configured quarantine role ID for guild."""
        if guild_id in AUTHORIZED_GUILDS:
            return AUTHORIZED_GUILDS[guild_id].get("quarantine_role_id")
        return None

    def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Find or determine the security log channel."""
        if guild.id in AUTHORIZED_GUILDS:
            cfg_channel_id = AUTHORIZED_GUILDS[guild.id].get("log_channel_id")
            if cfg_channel_id:
                ch = guild.get_channel(cfg_channel_id)
                if ch and isinstance(ch, discord.TextChannel):
                    return ch
            # If log_channel_id is null / None, logs are completely disabled for this server
            return None
        return None

    async def send_security_log(self, guild: discord.Guild, embed: discord.Embed):
        """Send embed to the guild's security log channel."""
        try:
            log_ch = self.get_log_channel(guild)
            if log_ch and log_ch.permissions_for(guild.me).send_messages:
                await log_ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send security log in {guild.name}: {e}")

    async def send_channel_log(self, guild: discord.Guild, channel_id: int, embed: discord.Embed) -> bool:
        """Send embed to a specific designated channel by ID."""
        try:
            ch = guild.get_channel(channel_id)
            if ch and isinstance(ch, discord.TextChannel) and ch.permissions_for(guild.me).send_messages:
                await ch.send(embed=embed)
                return True
        except Exception as e:
            logger.error(f"Failed to send log to channel {channel_id} in {guild.name}: {e}")
        return False

    async def quarantine_member(self, guild: discord.Guild, member: discord.Member, reason: str) -> bool:
        """
        Strips all removable roles from the member and assigns the Quarantine role (if available).
        Always strips dangerous roles even if the Quarantine role ID is missing on the server.
        """
        bot_member = guild.me
        if not bot_member.guild_permissions.manage_roles:
            logger.error(f"Bot lacks 'Manage Roles' permission in {guild.name}")
            return False

        quarantine_role_id = self.get_quarantine_role_id(guild.id)
        quarantine_role = guild.get_role(quarantine_role_id) if quarantine_role_id else None

        # Filter roles that bot can remove (strictly lower than bot's highest role)
        roles_to_remove = [
            r for r in member.roles
            if not r.is_default() and not r.managed and r < bot_member.top_role and r.id != quarantine_role_id
        ]

        action_succeeded = False
        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=f"FloryGuard Quarantine: {reason}")
                action_succeeded = True

            # Add quarantine role if it exists and is below bot's highest role
            if quarantine_role and quarantine_role < bot_member.top_role:
                if quarantine_role not in member.roles:
                    await member.add_roles(quarantine_role, reason=f"FloryGuard Quarantine: {reason}")
                action_succeeded = True
            else:
                if quarantine_role_id and not quarantine_role:
                    logger.warning(f"Quarantine role ID {quarantine_role_id} not found in {guild.name}; applying fallback timeout.")
                # Fallback isolation via timeout if quarantine role is unavailable
                if not member.guild_permissions.administrator and member.top_role < bot_member.top_role and member.id != guild.owner_id:
                    try:
                        await member.timeout(
                            discord.utils.utcnow() + datetime.timedelta(hours=24),
                            reason=f"FloryGuard Quarantine Fallback: {reason}"
                        )
                        action_succeeded = True
                    except Exception:
                        pass

            # Notify user via DM
            try:
                dm_embed = create_security_embed(
                    title="🚨 ВЫ БЫЛИ СНЯТЫ СО ВСЕХ РОЛЕЙ И ИЗОЛИРОВАНЫ",
                    description=(
                        f"Сервер: **{guild.name}**\n\n"
                        f"🔒 **Причина:** `{reason}`\n"
                        f"🛡️ С вас были сняты все права и выдана изоляция.\n"
                        f"Если вы считаете, что это ошибка, обратитесь к высшей администрации."
                    ),
                    color=COLOR_DANGER
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass

            # Log to DB
            await db.log_security_event(guild.id, member.id, "QUARANTINE_DEMOTE", reason)
            return action_succeeded
        except discord.Forbidden:
            logger.error(f"Forbidden: Cannot modify roles for {member.name} ({member.id}) in {guild.name} (Check Role Hierarchy)")
            return action_succeeded
        except Exception as e:
            logger.error(f"Error while quarantining {member.id}: {e}")
            return action_succeeded

    async def issue_warning_and_check(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        moderator_id: int = 0
    ) -> Tuple[int, int, bool]:
        """
        Issues a warning with configured expiration, sends DM to the member,
        and demotes if warnings reach MAX_WARNINGS.
        Returns: (warn_id, active_warn_count, was_demoted)
        """
        warn_id, active_count = await db.add_warning(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=moderator_id,
            reason=reason,
            expiry_days=WARN_EXPIRY_DAYS
        )

        demoted = False
        if active_count >= MAX_WARNINGS:
            # Max warns reached -> Demote + Quarantine
            await self.quarantine_member(guild, member, f"Накоплено {active_count}/{MAX_WARNINGS} предупреждений")
            demoted = True
        else:
            # Send standard DM if enabled in config
            if CONFIG.get("security", {}).get("dm_user_on_warn", True):
                try:
                    dm_emb = warn_dm_embed(warn_count=active_count, max_warns=MAX_WARNINGS, reason=reason)
                    await member.send(embed=dm_emb)
                except Exception:
                    pass

        return warn_id, active_count, demoted
