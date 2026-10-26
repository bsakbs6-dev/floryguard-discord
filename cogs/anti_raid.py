import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta

from config import CONFIG
from core.permissions import is_module_enabled, is_whitelisted
from utils.logger import logger
from utils.embeds import create_security_embed, COLOR_DANGER, COLOR_WARNING


class AntiRaidCog(commands.Cog, name="AntiRaid"):
    """
    Protection against bot raids, mass account joins, and account age screening.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not is_module_enabled(guild, "anti_raid_enabled"):
            return

        # Ignore bots here (AntiNukeCog handles rogue bot additions)
        if member.bot:
            return

        # Whitelisted members are completely exempt
        if await is_whitelisted(guild.id, member.id):
            return

        antiraid_cfg = CONFIG.get("anti_raid", {})
        min_age_hours = int(antiraid_cfg.get("min_account_age_hours", 24))
        auto_lockdown = bool(antiraid_cfg.get("auto_lockdown_on_spike", True))

        now = datetime.now(timezone.utc)

        # 1. ACCOUNT AGE CHECK (< min_account_age_hours OLD)
        account_age = now - member.created_at
        if account_age < timedelta(hours=min_age_hours):
            logger.warning(f"Suspicious young account joined: {member.name} ({member.id}), age: {account_age}")

            # Quarantine fresh account
            quarantine_role_id = self.bot.get_quarantine_role_id(guild.id)
            if quarantine_role_id:
                quarantine_role = guild.get_role(quarantine_role_id)
                if quarantine_role and quarantine_role < guild.me.top_role:
                    try:
                        await member.add_roles(
                            quarantine_role,
                            reason=f"FloryGuard Anti-Raid: Аккаунт создан менее {min_age_hours} часов назад"
                        )
                    except Exception as e:
                        logger.error(f"Failed to assign quarantine role on join: {e}")

            # Send Security Alert
            embed = create_security_embed(
                title="🚨 [Anti-Raid] Подозрительный новый аккаунт",
                description=(
                    f"Участник: {member.mention} (`{member.name}` | `ID: {member.id}`)\n"
                    f"Возраст аккаунта: **{account_age.days} дн. {int(account_age.seconds / 3600)} ч.**\n"
                    f"Действие: Наложена изоляция (роль карантина)."
                ),
                color=COLOR_WARNING
            )
            await self.bot.send_security_log(guild, embed)

        # 2. JOIN SPIKE DETECTION (e.g., 5 joins in 5 seconds)
        was_in_lockdown = self.bot.raid_tracker.is_in_lockdown(guild.id)
        is_spike = self.bot.raid_tracker.record_join(guild.id)
        if is_spike:
            logger.critical(f"JOIN SPIKE DETECTED in {guild.name}! Activating server quarantine...")

            # Isolate the joining raid account as well
            quarantine_role_id = self.bot.get_quarantine_role_id(guild.id)
            if quarantine_role_id:
                quarantine_role = guild.get_role(quarantine_role_id)
                if quarantine_role and quarantine_role < guild.me.top_role and quarantine_role not in member.roles:
                    try:
                        await member.add_roles(quarantine_role, reason="FloryGuard Anti-Raid: Массовый рейд (Join Spike)")
                    except Exception:
                        pass

            # Only run global channel lockdown and alert once per spike transition
            if not was_in_lockdown:
                locked_channels = 0
                if auto_lockdown:
                    everyone = guild.default_role
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).manage_channels:
                            try:
                                ow = ch.overwrites_for(everyone)
                                if ow.send_messages is not False:
                                    ow.send_messages = False
                                    await ch.set_permissions(everyone, overwrite=ow, reason="FloryGuard Anti-Raid: Auto-Lockdown on Join Spike")
                                    locked_channels += 1
                            except Exception:
                                pass

                lockdown_note = (
                    f"🔒 **Авто-блокировка активирована (закрыто каналов: {locked_channels}).**\n"
                    f"Используйте `/unlock all`, когда угроза минует."
                    if auto_lockdown else
                    "🔒 **Сервер переведен в режим повышенной защиты.**\nИспользуйте `/lockdown all` при необходимости."
                )

                embed = create_security_embed(
                    title="🚨 [ANTI-RAID] ЗАФИКСИРОВАНА АТАКА (JOIN SPIKE)",
                    description=(
                        f"За последние секунды зафиксирован массовый вход аккаунтов!\n"
                        f"{lockdown_note}"
                    ),
                    color=COLOR_DANGER
                )
                await self.bot.send_security_log(guild, embed)


async def setup(bot):
    await bot.add_cog(AntiRaidCog(bot))
