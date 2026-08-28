import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta

from core.permissions import is_authorized_guild
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
        if not is_authorized_guild(guild):
            return

        now = datetime.now(timezone.utc)

        # 1. ACCOUNT AGE CHECK (< 24 HOURS OLD)
        account_age = now - member.created_at
        if account_age < timedelta(hours=24):
            logger.warning(f"Suspicious young account joined: {member.name} ({member.id}), age: {account_age}")
            
            # Quarantine fresh account
            quarantine_role_id = self.bot.get_quarantine_role_id(guild.id)
            if quarantine_role_id:
                quarantine_role = guild.get_role(quarantine_role_id)
                if quarantine_role:
                    try:
                        await member.add_roles(quarantine_role, reason="FloryGuard Anti-Raid: Аккаунт создан менее 24 часов назад")
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
        is_spike = self.bot.raid_tracker.record_join(guild.id)
        if is_spike:
            logger.critical(f"JOIN SPIKE DETECTED in {guild.name}! Activating server quarantine...")
            
            embed = create_security_embed(
                title="🚨 [ANTI-RAID] ЗАФИКСИРОВАНА АТАКА (JOIN SPIKE)",
                description=(
                    f"За последние 5 секунд зафиксирован массовый вход ботов/аккаунтов!\n"
                    f"🔒 **Сервер переведен в режим повышенной защиты.**\n"
                    f"Используйте `/lockdown all` или `/panic` при необходимости."
                ),
                color=COLOR_DANGER
            )
            await self.bot.send_security_log(guild, embed)


async def setup(bot):
    await bot.add_cog(AntiRaidCog(bot))
