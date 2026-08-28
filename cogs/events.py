import discord
from discord.ext import commands
from discord import app_commands

from config import AUTHORIZED_GUILDS, UNAUTHORIZED_MESSAGE
from core.permissions import is_authorized_guild
from utils.logger import logger
from utils.embeds import create_security_embed, COLOR_DANGER


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Handle bot being added to a new server."""
        if not is_authorized_guild(guild):
            logger.warning(f"Unauthorized guild join attempt: {guild.name} (ID: {guild.id})")
            
            # Send message to first available channel or system channel
            target_channel = guild.system_channel or next(
                (ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages),
                None
            )
            if target_channel:
                try:
                    await target_channel.send(f"⚠️ **{UNAUTHORIZED_MESSAGE}**")
                except Exception:
                    pass
            
            # Leave unauthorized guild to protect security
            try:
                await guild.leave()
                logger.info(f"Left unauthorized guild: {guild.name} (ID: {guild.id})")
            except Exception as e:
                logger.error(f"Failed to leave unauthorized guild {guild.id}: {e}")
        else:
            logger.info(f"Joined authorized guild: {guild.name} (ID: {guild.id})")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        logger.info(f"Slash Command /{command.name} executed by {interaction.user} (ID: {interaction.user.id}) in {interaction.guild}")


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
