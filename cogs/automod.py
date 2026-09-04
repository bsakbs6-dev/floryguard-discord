import discord
from discord.ext import commands
import time
import datetime

from core.permissions import is_authorized_guild, is_whitelisted, is_admin
from utils.logger import logger
from utils.text_scanner import scan_for_links, levenshtein_similarity, normalize_text
from utils.embeds import automod_alert_embed, create_security_embed, COLOR_WARNING, COLOR_DANGER


def extract_all_message_text(message: discord.Message) -> str:
    """Extracts text from both message content and all embedded rich content."""
    parts = []
    if message.content:
        parts.append(message.content)
    for emb in message.embeds:
        if emb.title:
            parts.append(emb.title)
        if emb.description:
            parts.append(emb.description)
        if emb.url:
            parts.append(emb.url)
        for field in emb.fields:
            parts.append(f"{field.name} {field.value}")
        if emb.footer and emb.footer.text:
            parts.append(emb.footer.text)
        if emb.author and emb.author.name:
            parts.append(emb.author.name)
    return " ".join(parts).strip()


class AutoModCog(commands.Cog, name="AutoMod"):
    """
    Automated chat defense: Anti-Spam, Anti-Ad, Phishing protection,
    Rogue Bot Killer, Cross-Channel Raid protection, and Mass mentions filter.
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

        # Whitelist and Admins bypass chat filters (humans only)
        if not author.bot and await is_whitelisted(guild.id, author.id):
            return

        # Extract full content including Rich Embeds
        full_text = extract_all_message_text(message)

        # Detect if this message was sent via a User-Installed App / Application Command
        trigger_user = None
        if hasattr(message, "interaction_metadata") and message.interaction_metadata:
            trigger_user = getattr(message.interaction_metadata, "user", None)
        elif hasattr(message, "interaction") and message.interaction:
            trigger_user = getattr(message.interaction, "user", None)

        target_member = None
        if trigger_user:
            target_member = guild.get_member(trigger_user.id)
        elif isinstance(author, discord.Member):
            target_member = author

        # ----------------------------------------------------
        # 1. CROSS-CHANNEL SPAM / RAID BROADCASTER KILLER
        # ----------------------------------------------------
        tracker_user_id = trigger_user.id if trigger_user else author.id
        is_cross_spam = self.bot.cross_channel_tracker.record_channel_message(guild.id, tracker_user_id, message.channel.id)
        if is_cross_spam:
            try:
                await message.delete()
            except Exception:
                pass

            if target_member and not target_member.bot:
                try:
                    await target_member.timeout(discord.utils.utcnow() + datetime.timedelta(hours=24), reason="FloryGuard: Спам-рассылка через User-App/Бота по каналам")
                except Exception:
                    pass

            if author.bot and author.id != self.bot.user.id and guild.get_member(author.id):
                try:
                    await guild.ban(author, reason="FloryGuard Anti-Nuke: Массовая рассылка по каналам (Raid Bot Killer)")
                except Exception:
                    pass

            embed = automod_alert_embed(
                member=target_member or author,
                channel=message.channel,
                threat_type="🚨 Массовая рассылка по каналам (Cross-Channel User-App / Bot Raid)",
                matched_content=full_text[:300] if full_text else "Спам-рассылка",
                action="Сообщение удалено + Тайм-аут на 24 часа"
            )
            await self.bot.send_security_log(guild, embed)
            return

        # ----------------------------------------------------
        # 2. MASS MENTIONS PROTECTION (@everyone, @here, >2 mentions)
        # ----------------------------------------------------
        total_mentions = len(message.mentions) + len(message.role_mentions)
        has_everyone = message.mention_everyone or "@everyone" in full_text.lower() or "@here" in full_text.lower()

        if has_everyone or total_mentions > 2:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked mass mentions from {author.name} in #{message.channel.name}")
            except Exception:
                pass

            if target_member and not target_member.bot:
                try:
                    await target_member.timeout(discord.utils.utcnow() + datetime.timedelta(hours=1), reason="FloryGuard: Массовый пинг через User-App / бота")
                except Exception:
                    pass

            if author.bot and author.id != self.bot.user.id and guild.get_member(author.id):
                try:
                    await guild.ban(author, reason="FloryGuard Anti-Nuke: Попытка массового пинга (@everyone) от бота")
                except Exception:
                    pass

            embed = automod_alert_embed(
                member=target_member or author,
                channel=message.channel,
                threat_type=f"Массовые упоминания ({total_mentions} mentions / @everyone)",
                matched_content=full_text[:300],
                action="Сообщение удалено + Мут на 1 час"
            )
            await self.bot.send_security_log(guild, embed)
            return

        # ----------------------------------------------------
        # 3. SCAN FOR PROHIBITED LINKS / INVITES / SCAM / IPS / EMBEDS
        # ----------------------------------------------------
        is_malicious, threat_type, matched_snippet = scan_for_links(full_text)
        if is_malicious:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked link/IP/ad ({threat_type}) from {author.name} in #{message.channel.name}")
            except Exception as e:
                logger.error(f"Failed to delete malicious message: {e}")

            if target_member and not target_member.bot:
                try:
                    mute_until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
                    await target_member.timeout(mute_until, reason=f"FloryGuard AutoMod: {threat_type} (через User-App)")
                except Exception as e:
                    logger.error(f"Failed to apply 5m timeout: {e}")

                try:
                    warn_emb = create_security_embed(
                        title="🔇 ВЫ ПОЛУЧИЛИ МУТ НА 5 МИНУТ",
                        description=(
                            f"Ваше сообщение/команда в канале {message.channel.mention} было удалено, а вам выдан **тайм-аут на 5 минут**.\n\n"
                            f"📌 **Причина:** `{threat_type}`\n"
                            f"⚠️ Публикация сторонних ссылок, IP-адресов серверов и рекламы строго запрещена."
                        ),
                        color=COLOR_WARNING
                    )
                    await target_member.send(embed=warn_emb)
                except Exception:
                    pass

            if author.bot and author.id != self.bot.user.id and guild.get_member(author.id):
                try:
                    await guild.ban(author, reason=f"FloryGuard Anti-Nuke: Бот рассылает рекламу/ссылки ({threat_type})")
                except Exception:
                    pass

            embed = automod_alert_embed(
                member=target_member or author,
                channel=message.channel,
                threat_type=f"{threat_type} (User-App / Бот)" if trigger_user else threat_type,
                matched_content=full_text[:300],
                action="🗑️ Сообщение удалено + 🔇 Тайм-аут (мут) на 5 минут"
            )
            await self.bot.send_security_log(guild, embed)
            return

        # ----------------------------------------------------
        # 4. RATE LIMITING / TOKEN BUCKET (Max 4 msgs / 3 sec)
        # ----------------------------------------------------
        if self.bot.rate_limiter.is_rate_limited(guild.id, author.id):
            try:
                await message.delete()
                logger.info(f"AutoMod rate-limited {author.name} in #{message.channel.name}")
            except Exception:
                pass

            if author.bot and author.id != self.bot.user.id:
                try:
                    await guild.ban(author, reason="FloryGuard Anti-Nuke: Флуд-атака от стороннего бота")
                except Exception:
                    pass
            elif isinstance(author, discord.Member):
                try:
                    await author.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=5), reason="FloryGuard: Флуд / Превышение лимита сообщений")
                except Exception:
                    pass

            embed = automod_alert_embed(
                member=author,
                channel=message.channel,
                threat_type="Спам-флуд (Rate Limit Exceeded)",
                matched_content=full_text[:300],
                action="⛔ Бот заблокирован" if author.bot else "Сообщение удалено + Тайм-аут на 5 минут"
            )
            await self.bot.send_security_log(guild, embed)
            return

        # ----------------------------------------------------
        # 5. DUPLICATE MESSAGE SPAM (Levenshtein > 85%)
        # ----------------------------------------------------
        if len(full_text) > 10:
            recent_msgs = self.bot.msg_history.get_recent_messages(guild.id, author.id)
            for prev_msg in recent_msgs:
                similarity = levenshtein_similarity(full_text, prev_msg)
                if similarity >= 0.85:
                    try:
                        await message.delete()
                        logger.info(f"AutoMod blocked duplicate message ({similarity:.2f}) from {author.name}")
                    except Exception:
                        pass

                    if author.bot and author.id != self.bot.user.id:
                        try:
                            await guild.ban(author, reason="FloryGuard Anti-Nuke: Спам повторяющимися сообщениями от бота")
                        except Exception:
                            pass
                    elif isinstance(author, discord.Member):
                        embed = automod_alert_embed(
                            member=author,
                            channel=message.channel,
                            threat_type=f"Дубликат сообщений (Схожесть {int(similarity * 100)}%)",
                            matched_content=full_text[:300],
                            action="Сообщение удалено"
                        )
                        await self.bot.send_security_log(guild, embed)
                    return

            self.bot.msg_history.add_message(guild.id, author.id, full_text)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Scan edited messages to prevent post-edit link bypass."""
        if not after.guild or after.author.id == self.bot.user.id:
            return

        guild = after.guild
        if not is_authorized_guild(guild):
            return

        if not after.author.bot and await is_whitelisted(guild.id, after.author.id):
            return

        full_text = extract_all_message_text(after)
        is_malicious, threat_type, _ = scan_for_links(full_text)
        if is_malicious:
            try:
                await after.delete()
                logger.info(f"AutoMod deleted edited link/IP/ad ({threat_type}) from {after.author.name}")
            except Exception:
                pass

            author = after.author
            if author.bot and author.id != self.bot.user.id:
                try:
                    await guild.ban(author, reason=f"FloryGuard Anti-Nuke: Редактирование на запрещенную ссылку/рекламу ({threat_type})")
                except Exception:
                    pass
            elif isinstance(author, discord.Member):
                try:
                    mute_until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
                    await author.timeout(mute_until, reason=f"FloryGuard AutoMod Edit: {threat_type} (Мут 5 минут)")
                except Exception:
                    pass

                embed = automod_alert_embed(
                    member=author,
                    channel=after.channel,
                    threat_type=f"{threat_type} (Через редактирование)",
                    matched_content=full_text[:300],
                    action="🗑️ Сообщение удалено + 🔇 Тайм-аут (мут) на 5 минут"
                )
                await self.bot.send_security_log(guild, embed)


async def setup(bot):
    await bot.add_cog(AutoModCog(bot))
