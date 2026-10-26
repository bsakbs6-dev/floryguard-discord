import discord
from discord.ext import commands
import datetime

from config import CONFIG
from core.permissions import is_module_enabled, has_permission
from utils.logger import logger
from utils.text_scanner import scan_for_links, levenshtein_similarity
from utils.embeds import automod_alert_embed, create_security_embed, COLOR_WARNING


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


def _can_timeout_member(guild: discord.Guild, member: discord.Member) -> bool:
    """Checks if the bot can legally apply a timeout to the target member."""
    if member.bot or member.id == guild.owner_id:
        return False
    if member.guild_permissions.administrator:
        return False
    if not guild.me.guild_permissions.moderate_members:
        return False
    return member.top_role < guild.me.top_role


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
        if not is_module_enabled(guild, "automod_enabled"):
            return

        # Ignore bot itself and official bot webhooks (/post)
        if message.author.id == self.bot.user.id:
            return
        if message.webhook_id and message.webhook_id in getattr(self.bot, "trusted_webhook_ids", set()):
            return

        author = message.author

        # Check granular permission for automod_bypass
        if await has_permission(guild.id, author.id, "automod_bypass"):
            return

        # Extract full content including Rich Embeds
        full_text = extract_all_message_text(message)

        # Detect if this message was sent via a User-Installed App / Application Command
        trigger_user = None
        if hasattr(message, "interaction_metadata") and message.interaction_metadata:
            trigger_user = getattr(message.interaction_metadata, "user", None)
        elif hasattr(message, "interaction") and message.interaction:
            trigger_user = getattr(message.interaction, "user", None)

        if trigger_user and await has_permission(guild.id, trigger_user.id, "automod_bypass"):
            return

        target_member = None
        if trigger_user:
            target_member = guild.get_member(trigger_user.id)
        elif isinstance(author, discord.Member):
            target_member = author

        automod_cfg = CONFIG.get("automod", {})
        max_mentions = int(automod_cfg.get("max_mentions", 3))
        dup_threshold = float(automod_cfg.get("duplicate_similarity_threshold", 0.85))

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

            if target_member and _can_timeout_member(guild, target_member):
                try:
                    await target_member.timeout(
                        discord.utils.utcnow() + datetime.timedelta(hours=24),
                        reason="FloryGuard: Массовая спам-рассылка по каналам"
                    )
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
                threat_type="🚨 Массовая рассылка по каналам (Cross-Channel Raid)",
                matched_content=full_text[:300] if full_text else "Спам-рассылка",
                action="Сообщение удалено + Тайм-аут на 24 часа"
            )
            await self.bot.send_security_log(guild, embed)
            return

        # ----------------------------------------------------
        # 2. MASS MENTIONS PROTECTION (@everyone, @here, >= max_mentions)
        # ----------------------------------------------------
        total_mentions = len(message.mentions) + len(message.role_mentions)
        has_everyone = message.mention_everyone or "@everyone" in full_text.lower() or "@here" in full_text.lower()

        if has_everyone or total_mentions >= max_mentions:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked mass mentions from {author.name} in #{message.channel.name}")
            except Exception:
                pass

            if target_member and _can_timeout_member(guild, target_member):
                try:
                    await target_member.timeout(
                        discord.utils.utcnow() + datetime.timedelta(hours=1),
                        reason="FloryGuard: Массовые упоминания (@everyone / масс-пинг)"
                    )
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
        is_malicious, threat_type, _ = scan_for_links(full_text)
        if is_malicious:
            try:
                await message.delete()
                logger.info(f"AutoMod blocked link/IP/ad ({threat_type}) from {author.name} in #{message.channel.name}")
            except Exception as e:
                logger.error(f"Failed to delete malicious message: {e}")

            if target_member and not target_member.bot:
                if _can_timeout_member(guild, target_member):
                    try:
                        mute_until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
                        via_suffix = " (через User-App)" if trigger_user else ""
                        await target_member.timeout(mute_until, reason=f"FloryGuard AutoMod: {threat_type}{via_suffix}")
                    except Exception as e:
                        logger.error(f"Failed to apply 5m timeout: {e}")

                try:
                    warn_emb = create_security_embed(
                        title="🔇 ВЫ ПОЛУЧИЛИ МУТ НА 5 МИНУТ",
                        description=(
                            f"Ваше сообщение в канале {message.channel.mention} было удалено, а вам выдан **тайм-аут на 5 минут**.\n\n"
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
        # 4. RATE LIMITING / TOKEN BUCKET (Max N msgs / T sec)
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
            elif isinstance(author, discord.Member) and _can_timeout_member(guild, author):
                try:
                    await author.timeout(
                        discord.utils.utcnow() + datetime.timedelta(minutes=5),
                        reason="FloryGuard: Флуд / Превышение лимита сообщений"
                    )
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
        # 5. DUPLICATE MESSAGE SPAM (Levenshtein >= threshold)
        # ----------------------------------------------------
        if len(full_text) > 10:
            recent_msgs = self.bot.msg_history.get_recent_messages(guild.id, author.id)
            for prev_msg in recent_msgs:
                similarity = levenshtein_similarity(full_text, prev_msg)
                if similarity >= dup_threshold:
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
        if after.webhook_id and after.webhook_id in getattr(self.bot, "trusted_webhook_ids", set()):
            return

        guild = after.guild
        if not is_module_enabled(guild, "automod_enabled"):
            return

        # Check granular permission for automod_bypass
        if await has_permission(guild.id, after.author.id, "automod_bypass"):
            return

        before_text = extract_all_message_text(before)
        full_text = extract_all_message_text(after)
        if before_text == full_text:
            return

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
                if _can_timeout_member(guild, author):
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
