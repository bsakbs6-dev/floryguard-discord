import re
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Literal, Tuple

from config import UNAUTHORIZED_MESSAGE, STAFF_ROLE_IDS, WARNS_LOG_CHANNEL_ID, HIGHER_ADMIN_IDS
from core.permissions import (
    is_authorized_guild,
    is_senior_admin,
    is_higher_admin,
    is_bot_owner,
    is_admin,
    is_whitelisted,
    get_admin_info,
    has_warn_access,
    get_member_staff_rank,
    check_moderation_channel,
)
from database.db import db
from utils.logger import logger
from utils.embeds import (
    profile_embed,
    create_security_embed,
    staff_warn_dm_embed,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
)


def parse_duration(raw: str) -> Tuple[int, str]:
    """
    Parses a duration string like '15d', '7 дн', '24h', '30m', '2w', or '15'
    into (total_seconds, formatted_russian_text).
    Defaults to 7 days if format is unrecognized.
    """
    if not raw or not raw.strip():
        return 7 * 86400, "7 дн."

    text = raw.strip().lower()
    match = re.match(r"^(\d+)\s*([a-zа-яё]*)$", text)
    if not match:
        return 7 * 86400, raw.strip()

    value = max(1, int(match.group(1)))
    unit = match.group(2)

    if unit in ("", "d", "day", "days", "д", "дн", "дн.", "день", "дня", "дней"):
        return value * 86400, f"{value} дн."
    if unit in ("h", "hr", "hrs", "hour", "hours", "ч", "ч.", "час", "часа", "часов"):
        return value * 3600, f"{value} ч."
    if unit in ("m", "min", "mins", "м", "мин", "мин.", "минут", "минуты"):
        return value * 60, f"{value} мин."
    if unit in ("w", "wk", "week", "weeks", "н", "нед", "нед."):
        days = value * 7
        return days * 86400, f"{days} дн."

    return value * 86400, f"{value} дн."


class DeleteMessageView(discord.ui.View):
    """Interactive view with a button to dismiss/delete ephemeral profile message."""
    def __init__(self, target_user_id: int):
        super().__init__(timeout=180)
        self.target_user_id = target_user_id

    @discord.ui.button(label="🗑️ Нажмите, чтобы убрать", style=discord.ButtonStyle.secondary)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ Вы не можете закрыть чужое меню.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
        except Exception:
            await interaction.edit_original_response(content="*Профиль закрыт.*", embed=None, view=None)


class ModerationCog(commands.Cog, name="Moderation"):
    """
    Moderation, profile inspection, and staff reprimand (/warn) management commands.
    """
    def __init__(self, bot):
        self.bot = bot

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

    async def _strip_staff_roles(self, guild: discord.Guild, member: discord.Member, reason: str) -> bool:
        """
        Strips all staff roles (STAFF_ACCESS_ROLE_ID and all 8 hierarchy roles) from the member
        when they reach their reprimand limit (1/1 for Стажер, 3/3 for other staff).
        """
        if member.id in HIGHER_ADMIN_IDS or is_higher_admin(member.id, guild.id):
            logger.warning(f"Blocked attempt to strip staff roles from Higher Administrator {member.id}")
            return False

        bot_member = guild.me
        if not bot_member.guild_permissions.manage_roles:
            return False

        roles_to_strip = [
            r for r in member.roles
            if r.id in STAFF_ROLE_IDS and not r.is_default() and not r.managed and r < bot_member.top_role
        ]
        try:
            if roles_to_strip:
                await member.remove_roles(*roles_to_strip, reason=reason)
            await db.remove_admin(guild.id, member.id)
            await db.log_security_event(guild.id, member.id, "STAFF_WARN_DEMOTE", reason)
            return True
        except Exception as e:
            logger.error(f"Failed to strip staff roles from {member.id}: {e}")
            return False

    # ==========================================
    # /PROFILE COMMAND (EPHEMERAL + DISMISS BUTTON)
    # ==========================================
    @app_commands.command(name="profile", description="Посмотреть профиль безопасности и историю предупреждений")
    @app_commands.describe(user="Пользователь для просмотра (по умолчанию вы)")
    async def profile_command(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if not await self._check_guild_auth(interaction):
            return

        target_member = user or interaction.user
        if not isinstance(target_member, discord.Member):
            target_member = interaction.guild.get_member(target_member.id) or interaction.user

        is_owner = is_bot_owner(target_member.id, interaction.guild.id)
        is_sr_adm = is_senior_admin(target_member.id, interaction.guild.id)
        is_adm = await is_admin(interaction.guild.id, target_member.id)
        is_wl = await is_whitelisted(interaction.guild.id, target_member.id)
        admin_info = await get_admin_info(interaction.guild.id, target_member.id)

        warnings = await db.get_active_warnings(interaction.guild.id, target_member.id)

        embed = profile_embed(
            member=target_member,
            is_owner=is_owner,
            is_senior_admin=is_sr_adm,
            is_admin=is_adm,
            is_whitelisted=is_wl,
            warnings=warnings,
            admin_info=admin_info
        )

        view = DeleteMessageView(target_user_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==========================================
    # /WARN COMMAND (ROLE 1454703598834421831 + SCREENSHOT 3 DM + 1/1 TRAINEE DEMOTE)
    # ==========================================
    @app_commands.command(
        name="warn",
        description="Выдать выговор сотруднику стаффа (Доступно с ролью 1454703598834421831)"
    )
    @app_commands.describe(
        user="Сотрудник/пользователь Discord, которому выдается выговор",
        reason="Причина / пункт правил (например: 1.2)",
        duration="Через сколько спадет выговор (например: 15d, 7d, 24h)",
        situation="Подробное описание ситуации (по умолчанию совпадает с причиной)"
    )
    async def warn_command(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        duration: str = "15d",
        situation: Optional[str] = None,
    ):
        if not await self._check_guild_auth(interaction):
            return

        can_run, err_msg = check_moderation_channel(interaction)
        if not can_run:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not has_warn_access(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет доступа к выдаче выговоров (требуется роль `1454703598834421831`).",
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "❌ Невозможно выдать выговор боту.",
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ Вы не можете выдать выговор самому себе.", ephemeral=True)
            return

        if user.id in HIGHER_ADMIN_IDS or is_higher_admin(user.id, interaction.guild.id):
            await interaction.response.send_message(
                "❌ Невозможно применить дисциплинарные меры к Высшей Администрации сервера (абсолютный иммунитет).",
                ephemeral=True,
            )
            return

        # Check hierarchy order & ensure target has a moderation position
        _, _, _, invoker_idx = get_member_staff_rank(interaction.user)
        target_rank_name, max_warns, is_trainee, target_idx = get_member_staff_rank(user)

        if target_rank_name == "Участник" or target_idx >= 100:
            await interaction.response.send_message(
                "❌ Обычные игроки без должности модерации проекта не могут получить выговор стаффа!",
                ephemeral=True,
            )
            return

        if not is_senior_admin(interaction.user.id, interaction.guild.id) and target_idx <= invoker_idx:
            await interaction.response.send_message(
                f"❌ Вы не можете выдать выговор сотруднику с должностью **{target_rank_name}** (равной или выше вашей).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        expiry_seconds, duration_text = parse_duration(duration)
        situation_text = (situation or reason).strip()

        warn_id, warn_count = await db.add_warning(
            guild_id=interaction.guild.id,
            user_id=user.id,
            moderator_id=interaction.user.id,
            reason=reason.strip(),
            expiry_seconds=expiry_seconds,
            situation=situation_text,
            duration_text=duration_text,
        )

        # Check if limit reached (1/1 for Стажер, 3/3 for other staff)
        demoted = False
        if warn_count >= max_warns:
            demote_reason = (
                f"FloryGuard Staff: Снятие со стажера (выговор {warn_count}/{max_warns}: {reason})"
                if is_trainee
                else f"FloryGuard Staff: Достигнут лимит выговоров ({warn_count}/{max_warns}: {reason})"
            )
            await self._strip_staff_roles(interaction.guild, user, demote_reason)
            demoted = True

        # Send DM to the warned user in the exact style of Screenshot 3
        dm_sent = False
        try:
            dm_embed = staff_warn_dm_embed(
                guild=interaction.guild,
                moderator=interaction.user,
                duration_text=duration_text,
                situation=situation_text,
                reason=reason.strip(),
                warn_count=warn_count,
                max_warns=max_warns,
                demoted=demoted,
            )
            await user.send(embed=dm_embed)
            dm_sent = True
        except Exception:
            pass

        dm_badge = "📩 Уведомление отправлено в ЛС" if dm_sent else "⚠️ ЛС пользователя закрыты"

        if demoted:
            desc = (
                f"🔴 **Сотруднику {user.mention} (`{target_rank_name}`) выдан выговор #{warn_id} ({warn_count}/{max_warns})**\n\n"
                f"📌 **Причина:** `{reason}`\n"
                f"📝 **Ситуация:** `{situation_text}`\n"
                f"⏳ **Длительность:** `{duration_text}`\n"
                f"⚡ **Санкция:** **АВТОМАТИЧЕСКОЕ СНЯТИЕ С ДОЛЖНОСТИ** (достигнут лимит `{warn_count}/{max_warns}`)\n"
                f"{dm_badge}"
            )
            color = COLOR_DANGER
        else:
            desc = (
                f"🟡 **Сотруднику {user.mention} (`{target_rank_name}`) выдан выговор #{warn_id} ({warn_count}/{max_warns})**\n\n"
                f"📌 **Причина:** `{reason}`\n"
                f"📝 **Ситуация:** `{situation_text}`\n"
                f"⏳ **Срок действия:** `{duration_text}`\n"
                f"{dm_badge}"
            )
            color = COLOR_WARNING

        embed = create_security_embed(title="⚠️ ВЫДАН ВЫГОВОР СТАФФА", description=desc, color=color)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.bot.send_channel_log(interaction.guild, WARNS_LOG_CHANNEL_ID, embed)
        log_ch = self.bot.get_log_channel(interaction.guild)
        if log_ch and log_ch.id != WARNS_LOG_CHANNEL_ID:
            await self.bot.send_security_log(interaction.guild, embed)

    @app_commands.command(name="unwarn", description="Снять выговор по ID или очистить все выговоры сотрудника")
    @app_commands.describe(
        user="Сотрудник, у которого снимается выговор",
        warn_id="ID конкретного выговора (оставьте пустым, чтобы снять все выговоры)"
    )
    async def unwarn_command(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        warn_id: Optional[int] = None,
    ):
        if not await self._check_guild_auth(interaction):
            return

        can_run, err_msg = check_moderation_channel(interaction)
        if not can_run:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not has_warn_access(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для снятия выговоров (требуется роль `1454703598834421831`).",
                ephemeral=True,
            )
            return

        if warn_id is not None:
            success = await db.remove_warning(warn_id, interaction.guild.id)
            if success:
                embed = create_security_embed(
                    title="✅ ВЫГОВОР СНЯТ",
                    description=f"Выговор **[ID: {warn_id}]** у {user.mention} снят куратором {interaction.user.mention}.",
                    color=COLOR_SUCCESS,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                await self.bot.send_channel_log(interaction.guild, WARNS_LOG_CHANNEL_ID, embed)
                log_ch = self.bot.get_log_channel(interaction.guild)
                if log_ch and log_ch.id != WARNS_LOG_CHANNEL_ID:
                    await self.bot.send_security_log(interaction.guild, embed)
            else:
                await interaction.response.send_message(
                    f"❌ Выговор с ID `{warn_id}` не найден или уже истек.",
                    ephemeral=True,
                )
        else:
            cleared = await db.clear_warnings(interaction.guild.id, user.id)
            embed = create_security_embed(
                title="🧹 ВЫГОВОРЫ ОЧИЩЕНЫ",
                description=f"С пользователя {user.mention} снято **{cleared}** активных выговоров.",
                color=COLOR_SUCCESS,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.bot.send_channel_log(interaction.guild, WARNS_LOG_CHANNEL_ID, embed)
            log_ch = self.bot.get_log_channel(interaction.guild)
            if log_ch and log_ch.id != WARNS_LOG_CHANNEL_ID:
                await self.bot.send_security_log(interaction.guild, embed)

    @app_commands.command(name="warns", description="Показать список активных выговоров сотрудника")
    @app_commands.describe(user="Сотрудник для проверки")
    async def warns_list_command(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        can_run, err_msg = check_moderation_channel(interaction)
        if not can_run:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not has_warn_access(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для просмотра списка выговоров.",
                ephemeral=True,
            )
            return

        rank_name, max_warns, _, target_idx = get_member_staff_rank(user)
        if user.bot or rank_name == "Участник" or target_idx >= 100:
            await interaction.response.send_message(
                f"❌ {user.mention} не имеет должности модерации проекта — обычные игроки не могут получать выговоры.",
                ephemeral=True,
            )
            return
        warnings = await db.get_active_warnings(interaction.guild.id, user.id)
        if not warnings:
            await interaction.response.send_message(
                f"ℹ️ У {user.mention} (`{rank_name}`) нет активных выговоров (`0/{max_warns}`).",
                ephemeral=True,
            )
            return

        lines = []
        for w in warnings:
            dur = w.get("duration_text") or "7 дн."
            sit = w.get("situation") or w["reason"]
            lines.append(
                f"• **[ID: {w['id']}]** Причина: `{w['reason']}` | Срок: `{dur}`\n"
                f"  ├ Ситуация: *{sit}*\n"
                f"  └ Выдал: <@{w['moderator_id']}>"
            )

        embed = create_security_embed(
            title=f"📜 Выговоры: {user.display_name} [{rank_name}] ({len(warnings)}/{max_warns})",
            description="\n".join(lines),
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # /LOCKDOWN & /UNLOCK COMMANDS
    # ==========================================
    @app_commands.command(name="lockdown", description="Заблокировать отправку сообщений (изоляция канала или сервера)")
    @app_commands.describe(target="Область блокировки (текущий канал или все каналы)")
    async def lockdown_command(self, interaction: discord.Interaction, target: Literal["channel", "all"]):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ У вас нет прав Администратора безопасности.", ephemeral=True)
            return

        guild = interaction.guild
        everyone = guild.default_role

        if target == "channel":
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                overwrites = channel.overwrites_for(everyone)
                overwrites.send_messages = False
                await channel.set_permissions(everyone, overwrite=overwrites, reason=f"FloryGuard Lockdown by {interaction.user}")

                embed = create_security_embed(
                    title="🔒 КАНАЛ ЗАМОРОЖЕН (LOCKDOWN)",
                    description=f"Канал {channel.mention} заблокирован для отправки сообщений.",
                    color=COLOR_DANGER
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.defer(ephemeral=True)
            self.bot.raid_tracker.set_lockdown(guild.id, True)
            count = 0
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).manage_channels:
                    try:
                        ow = ch.overwrites_for(everyone)
                        if ow.send_messages is not False:
                            ow.send_messages = False
                            await ch.set_permissions(everyone, overwrite=ow, reason=f"FloryGuard Server Lockdown by {interaction.user}")
                            count += 1
                    except Exception:
                        pass

            embed = create_security_embed(
                title="🚨 СЕРВЕР ПОЛНОСТЬЮ ЗАБЛОКИРОВАН (GLOBAL LOCKDOWN)",
                description=f"Заблокировано текстовых каналов: **{count}**.\nОтправка сообщений для `@everyone` запрещена.",
                color=COLOR_DANGER
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="unlock", description="Снять блокировку с канала или сервера")
    @app_commands.describe(target="Область разблокировки")
    async def unlock_command(self, interaction: discord.Interaction, target: Literal["channel", "all"]):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ У вас нет прав Администратора безопасности.", ephemeral=True)
            return

        guild = interaction.guild
        everyone = guild.default_role

        if target == "channel":
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                overwrites = channel.overwrites_for(everyone)
                overwrites.send_messages = None
                await channel.set_permissions(everyone, overwrite=overwrites, reason=f"FloryGuard Unlock by {interaction.user}")

                embed = create_security_embed(
                    title="🔓 КАНАЛ РАЗБЛОКИРОВАН",
                    description=f"Канал {channel.mention} снова доступен для общения.",
                    color=COLOR_SUCCESS
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.defer(ephemeral=True)
            self.bot.raid_tracker.set_lockdown(guild.id, False)
            count = 0
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).manage_channels:
                    try:
                        ow = ch.overwrites_for(everyone)
                        if ow.send_messages is False:
                            ow.send_messages = None
                            await ch.set_permissions(everyone, overwrite=ow, reason=f"FloryGuard Server Unlock by {interaction.user}")
                            count += 1
                    except Exception:
                        pass

            embed = create_security_embed(
                title="🔓 СЕРВЕР РАЗБЛОКИРОВАН",
                description=f"Разблокировано текстовых каналов: **{count}**.",
                color=COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ==========================================
    # /PURGE COMMAND (FAST CHAT CLEANER)
    # ==========================================
    @app_commands.command(name="purge", description="Быстрая очистка сообщений от спамеров, ботов или нарушителей")
    @app_commands.describe(
        amount="Количество сообщений для проверки (1-100)",
        user="Очищать только от конкретного пользователя",
        bots_only="Очищать только сообщения ботов"
    )
    async def purge_command(
        self,
        interaction: discord.Interaction,
        amount: int = 20,
        user: Optional[discord.Member] = None,
        bots_only: bool = False
    ):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ Недостаточно прав.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Команда работает только в текстовых каналах.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        def check_msg(msg: discord.Message) -> bool:
            if user and msg.author.id != user.id:
                return False
            if bots_only and not msg.author.bot:
                return False
            return True

        try:
            deleted = await channel.purge(limit=min(100, max(1, amount)), check=check_msg)
            await interaction.followup.send(f"✅ Успешно удалено **{len(deleted)}** сообщений.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка при очистке: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
