import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import Literal

from core.permissions import is_bot_owner
from config import UNAUTHORIZED_MESSAGE
from utils.logger import logger
from utils.embeds import create_security_embed, COLOR_DANGER, COLOR_SUCCESS

class TransferCog(commands.Cog, name="Transfer"):
    """
    Commands to prepare server for ownership transfer (wiping channels, roles, categories).
    """
    def __init__(self, bot):
        self.bot = bot

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        from core.permissions import is_authorized_guild
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

    @app_commands.command(name="transfer_wipe", description="Очистить все каналы, категории и роли (ОПАСНО!)")
    @app_commands.checks.cooldown(1, 86400, key=lambda i: (i.guild_id, i.user.id)) # 1 time per day cooldown
    async def transfer_wipe(self, interaction: discord.Interaction, confirm: Literal["Я ПОДТВЕРЖДАЮ", "ОТМЕНА"] = "ОТМЕНА"):
        if not await self._check_guild_auth(interaction):
            return

        # Restrict to specific user ID
        if interaction.user.id != 1398717669607473254:
            await interaction.response.send_message("⛔ Эта команда доступна только специальному администратору.", ephemeral=True)
            return

        if confirm != "Я ПОДТВЕРЖДАЮ":
            await interaction.response.send_message("❌ Действие отменено.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)
        guild = interaction.guild

        embed = create_security_embed(
            title="⚠️ НАЧАТА ПОЛНАЯ ОЧИСТКА СЕРВЕРА",
            description="Запущен процесс удаления всех каналов, категорий и ролей для передачи сервера. Ожидайте...",
            color=COLOR_DANGER
        )
        msg = await interaction.followup.send(embed=embed, wait=True)

        logger.warning(f"[{guild.name}] Server wipe initiated by {interaction.user}")

        # 1. Create a safe channel first so the server isn't left with 0 channels
        try:
            safe_channel = await guild.create_text_channel("transfer-info")
        except Exception as e:
            logger.error(f"Could not create safe channel: {e}")
            safe_channel = interaction.channel # fallback

        # 2. Delete channels (discord.py will handle HTTP ratelimits automatically and as fast as permitted)
        channels_to_delete = [ch for ch in guild.channels if ch != safe_channel]
        
        async def delete_channel(ch):
            try:
                await ch.delete()
                return True
            except discord.HTTPException:
                return False

        channel_results = await asyncio.gather(*(delete_channel(ch) for ch in channels_to_delete))
        deleted_channels = sum(1 for res in channel_results if res)

        # 3. Delete roles
        bot_highest = guild.me.top_role
        roles_to_delete = [r for r in guild.roles if r.name != "@everyone" and not r.managed and r < bot_highest]

        async def delete_role(r):
            try:
                await r.delete()
                return True
            except discord.HTTPException:
                return False

        role_results = await asyncio.gather(*(delete_role(r) for r in roles_to_delete))
        deleted_roles = sum(1 for res in role_results if res)

        logger.info(f"[{guild.name}] Wipe complete. Channels: {deleted_channels}, Roles: {deleted_roles}")

        success_embed = create_security_embed(
            title="✅ ОЧИСТКА ЗАВЕРШЕНА",
            description=f"Сервер успешно подготовлен к передаче!\n\n**Удалено:**\nКаналов и категорий: `{deleted_channels}`\nРолей: `{deleted_roles}`",
            color=COLOR_SUCCESS
        )
        
        try:
            await safe_channel.send(f"{interaction.user.mention}", embed=success_embed)
        except Exception:
            pass

    @transfer_wipe.error
    async def transfer_wipe_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ Команда на перезарядке. Попробуйте снова через {int(error.retry_after//3600)} часов.", ephemeral=True)
        else:
            logger.error(f"Error in transfer_wipe: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Произошла ошибка при выполнении команды.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TransferCog(bot))
