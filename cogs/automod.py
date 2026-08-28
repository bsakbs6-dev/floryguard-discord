import discord
from discord.ext import commands
import time

from core.permissions import is_authorized_guild, is_whitelisted, is_admin
from utils.logger import logger
from utils.text_scanner import scan_for_links, levenshtein_similarity, normalize_text
from utils.embeds import automod_alert_embed, create_security_embed, COLOR_WARNING


class AutoModCog(commands.Cog, name="AutoMod"):
    """
    Automated chat defense: Anti-Spam, Anti-Ad, Phishing protection,
    Unicode bypass filter, Duplicate detector, and Mass mentions filter.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs
        if not message.guild:
            return

        guild = message.guild
        if not is_authorized_guild(guild):
            return

        # Ignore bot itself
        if message.author.id == self.bot.user.id:
            return

        author = message.author

        # Whitelist and Admins bypass chat filters
        if await is_whitelisted(guild.id, author.id):
            return

        content = message.content or ""

        # 1. SCAN FOR PROHIBITED LINKS / INVITES / SCAM
        is_malicious, threat_type, matched_snippet = scan_for_links(content)
        if is_malicious:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked link ({threat_type}) from {author.name} in #{message.channel.name}")
            except Exception as e:
                logger.error(f"Failed to delete malicious message: {e}")

            # Send alert to security logs
            if isinstance(author, discord.Member):
                embed = automod_alert_embed(
                    member=author,
                    channel=message.channel,
                    threat_type=threat_type,
                    matched_content=content,
                    action="Сообщение удалено (Фильтр ссылок)"
                )
                await self.bot.send_security_log(guild, embed)

                # Send direct warning
                try:
                    warn_emb = create_security_embed(
                        title="🛑 ССЫЛКА ЗАБЛОКИРОВАНА",
                        description=(
                            f"Ваше сообщение в канале {message.channel.mention} было удалено.\n\n"
                            f"📌 **Причина:** `{threat_type}`\n"
                            f"⚠️ Публикация ссылок, инвайтов и подозрительных доменов строго запрещена."
                        ),
                        color=COLOR_WARNING
                    )
                    await author.send(embed=warn_emb)
                except Exception:
                    pass
            return

        # 2. MASS MENTIONS PROTECTION (@everyone, @here or > 3 role/user mentions)
        total_mentions = len(message.mentions) + len(message.role_mentions)
        if message.mention_everyone or total_mentions > 3:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked mass mentions ({total_mentions}) from {author.name}")
            except Exception:
                pass

            if isinstance(author, discord.Member):
                embed = automod_alert_embed(
                    member=author,
                    channel=message.channel,
                    threat_type=f"Массовые упоминания ({total_mentions} mentions / @everyone)",
                    matched_content=content,
                    action="Сообщение удалено (Anti-MassMention)"
                )
                await self.bot.send_security_log(guild, embed)
            return

        # 3. RATE LIMITING / TOKEN BUCKET (Max 4 msgs / 3 sec)
        if self.bot.rate_limiter.is_rate_limited(guild.id, author.id):
            try:
                await message.delete()
                logger.info(f"AutoMod rate-limited {author.name} in #{message.channel.name}")
            except Exception:
                pass

            if isinstance(author, discord.Member):
                # Temporary 1-minute timeout if spamming rapidly
                try:
                    await author.timeout(discord.utils.utcnow() + discord.timedelta(minutes=1), reason="FloryGuard: Флуд / Превышение лимита сообщений")
                except Exception:
                    pass

                embed = automod_alert_embed(
                    member=author,
                    channel=message.channel,
                    threat_type="Спам-флуд (Rate Limit Exceeded)",
                    matched_content=content,
                    action="Сообщение удалено + Тайм-аут на 1 минуту"
                )
                await self.bot.send_security_log(guild, embed)
            return

        # 4. DUPLICATE MESSAGE SPAM (Levenshtein > 85%)
        if len(content) > 10:
            recent_msgs = self.bot.msg_history.get_recent_messages(guild.id, author.id)
            for prev_msg in recent_msgs:
                similarity = levenshtein_similarity(content, prev_msg)
                if similarity >= 0.85:
                    try:
                        await message.delete()
                        logger.info(f"AutoMod blocked duplicate message ({similarity:.2f}) from {author.name}")
                    except Exception:
                        pass

                    if isinstance(author, discord.Member):
                        embed = automod_alert_embed(
                            member=author,
                            channel=message.channel,
                            threat_type=f"Дубликат сообщений (Схожесть {int(similarity * 100)}%)",
                            matched_content=content,
                            action="Сообщение удалено"
                        )
                        await self.bot.send_security_log(guild, embed)
                    return

            self.bot.msg_history.add_message(guild.id, author.id, content)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Scan edited messages to prevent post-edit link bypass."""
        if not after.guild or after.author.id == self.bot.user.id:
            return

        guild = after.guild
        if not is_authorized_guild(guild):
            return

        if await is_whitelisted(guild.id, after.author.id):
            return

        content = after.content or ""
        is_malicious, threat_type, _ = scan_for_links(content)
        if is_malicious:
            try:
                await after.delete()
                logger.info(f"AutoMod deleted edited link ({threat_type}) from {after.author.name}")
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(AutoModCog(bot))
