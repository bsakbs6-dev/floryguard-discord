import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    STAFF_HIERARCHY,
    STAFF_ACCESS_ROLE_ID,
    WARN_ACCESS_ROLE_ID,
    CURATORS_AUDIT_CHANNEL_ID,
    ATTESTATION_VOICE_CHANNELS,
    ATTESTATION_MAX_FAILS,
    ATTESTATION_COOLDOWN_DAYS,
    ROLE_ID_TO_MC_GROUP,
    AUTHORIZED_GUILDS,
    HIGHER_ADMIN_IDS,
    STAFF_ROLE_DAILY_NORM_HOURS,
    DEFAULT_DAILY_NORM_HOURS,
)
from core.permissions import (
    is_authorized_guild,
    can_schedule_attestation,
    can_unblock_attestation,
    get_member_staff_rank,
    is_senior_admin,
    is_higher_admin,
    is_bot_owner,
)
from database.db import db
from utils.time_parser import parse_msk_datetime, format_msk, to_discord_timestamp
from utils.embeds import (
    create_security_embed,
    attestation_dm_scheduled_embed,
    attestation_audit_embed,
    attestation_reminder_embed,
    attestation_result_dm_embed,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_FLORY_ORANGE,
)
from utils.logger import logger

# Ceiling role index for standard curator promotions & attestations:
# 3 = "Гл. Модер | Зам. Куратора" (1548961652899061841)
CURATOR_ATTESTATION_CEILING_IDX = 3

RANK_EMOJIS = {
    "Высшая Администрация": "👑",
    "Администрация": "⭐",
    "Куратор": "⚜️",
    "Гл. Модер | Зам. Куратора": "🛡️",
    "Ст. Модер": "⚔️",
    "Модер": "📜",
    "Хелпер": "🔰",
    "Стажер": "🌱",
}


def _create_design_embed(
    title: str,
    description: str,
    color: int = COLOR_PRIMARY,
    footer_text: str = "FloryGuard • Attestation System",
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text=footer_text,
        icon_url="https://cdn.discordapp.com/embed/avatars/0.png",
    )
    return embed


async def grant_attestation_voice_access(
    guild: discord.Guild, voice_id: int, candidate: discord.Member
) -> bool:
    """Grants temporary connect & view permissions to the candidate on the designated voice channel."""
    try:
        channel = guild.get_channel(voice_id)
        if channel and isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await channel.set_permissions(
                candidate,
                connect=True,
                speak=True,
                view_channel=True,
                reason="Назначена квалификационная аттестация стаффа FloryMine",
            )
            logger.info(f"Granted voice access to {candidate.id} on channel {voice_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to grant voice access to {candidate.id} on channel {voice_id}: {e}")
    return False


async def revoke_attestation_voice_access(
    guild: discord.Guild, voice_id: int, candidate: discord.Member
) -> bool:
    """Safely revokes temporary voice permissions from the candidate."""
    try:
        channel = guild.get_channel(voice_id)
        if channel and isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await channel.set_permissions(
                candidate,
                overwrite=None,
                reason="Завершение/отмена квалификационной аттестации стаффа FloryMine",
            )
            logger.info(f"Revoked voice access from {candidate.id} on channel {voice_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to revoke voice access from {candidate.id} on channel {voice_id}: {e}")
    return False


# =========================================================================
# 1. ATTESTATION SCHEDULING FLOW (ROLE -> VOICE -> TIME -> CONFIRM)
# =========================================================================

class AttestationRoleSelect(discord.ui.Select):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        eligible_ranks: List[Tuple[int, int, str]],
        mc_nick: str,
    ):
        self.bot = bot
        self.invoker = invoker
        self.candidate = candidate
        self.mc_nick = mc_nick
        self.eligible_ranks = eligible_ranks

        options = []
        for idx, role_id, role_name in eligible_ranks:
            emoji = RANK_EMOJIS.get(role_name, "🔰")
            lp_group = ROLE_ID_TO_MC_GROUP.get(role_id, "unknown")
            options.append(
                discord.SelectOption(
                    label=role_name,
                    value=str(role_id),
                    description=f"Minecraft LuckPerms: [{lp_group}]",
                    emoji=emoji,
                )
            )

        super().__init__(
            placeholder="Выберите целевую должность для аттестации...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Вы не можете взаимодействовать с этой панелью.", ephemeral=True)
            return

        selected_role_id = int(self.values[0])
        selected_role_name = next(name for _, r_id, name in self.eligible_ranks if r_id == selected_role_id)

        # Proceed to Step 2: Voice Channel Selection
        view = AttestationVoiceSelectView(
            bot=self.bot,
            invoker=self.invoker,
            candidate=self.candidate,
            target_role_id=selected_role_id,
            target_role_name=selected_role_name,
            mc_nick=self.mc_nick,
        )
        embed = _create_design_embed(
            title="🎙️ ВЫБОР ГОЛОСОВОГО КАНАЛА ДЛЯ АТТЕСТАЦИИ",
            description=(
                f"👤 **Кандидат:** {self.candidate.mention} (`{self.candidate.name}` | MC: `{self.mc_nick}`)\n"
                f"🏷️ **Целевая должность:** **{selected_role_name}**\n\n"
                f"Выберите голосовой канал, в котором будет проходить аттестация:\n"
                f"*(Кандидат автоматически получит персональный доступ к выбранному войсу)*"
            ),
            color=COLOR_PRIMARY,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class AttestationVoiceSelect(discord.ui.Select):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        target_role_id: int,
        target_role_name: str,
        mc_nick: str,
    ):
        self.bot = bot
        self.invoker = invoker
        self.candidate = candidate
        self.target_role_id = target_role_id
        self.target_role_name = target_role_name
        self.mc_nick = mc_nick

        options = []
        guild = candidate.guild
        for cid in ATTESTATION_VOICE_CHANNELS:
            ch = guild.get_channel(cid)
            if ch and isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                online_cnt = len(ch.members)
                options.append(
                    discord.SelectOption(
                        label=ch.name[:100],
                        value=str(ch.id),
                        description=f"Онлайн сейчас: {online_cnt} чел. | ID: {ch.id}",
                        emoji="🔊",
                    )
                )
            else:
                options.append(
                    discord.SelectOption(
                        label=f"Войс-канал #{cid}",
                        value=str(cid),
                        description=f"ID: {cid}",
                        emoji="🎙️",
                    )
                )

        super().__init__(
            placeholder="Выберите голосовой канал для собеседования...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Вы не можете взаимодействовать с этой панелью.", ephemeral=True)
            return

        selected_voice_id = int(self.values[0])
        guild = interaction.guild
        ch = guild.get_channel(selected_voice_id)
        voice_name = ch.name if ch else f"Войс #{selected_voice_id}"

        # Proceed to Step 3: Modal for Date & Time
        modal = AttestationTimeModal(
            bot=self.bot,
            invoker=self.invoker,
            candidate=self.candidate,
            target_role_id=self.target_role_id,
            target_role_name=self.target_role_name,
            voice_channel_id=selected_voice_id,
            voice_channel_name=voice_name,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)


class AttestationVoiceSelectView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        target_role_id: int,
        target_role_name: str,
        mc_nick: str,
    ):
        super().__init__(timeout=300)
        self.add_item(
            AttestationVoiceSelect(
                bot=bot,
                invoker=invoker,
                candidate=candidate,
                target_role_id=target_role_id,
                target_role_name=target_role_name,
                mc_nick=mc_nick,
            )
        )


class AttestationRoleSelectView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        eligible_ranks: List[Tuple[int, int, str]],
        mc_nick: str,
    ):
        super().__init__(timeout=300)
        self.add_item(
            AttestationRoleSelect(
                bot=bot,
                invoker=invoker,
                candidate=candidate,
                eligible_ranks=eligible_ranks,
                mc_nick=mc_nick,
            )
        )


class AttestationTimeModal(discord.ui.Modal, title="⏱️ Время и дата аттестации (МСК)"):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        target_role_id: int,
        target_role_name: str,
        voice_channel_id: int,
        voice_channel_name: str,
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.invoker = invoker
        self.candidate = candidate
        self.target_role_id = target_role_id
        self.target_role_name = target_role_name
        self.voice_channel_id = voice_channel_id
        self.voice_channel_name = voice_channel_name
        self.mc_nick = mc_nick

        self.time_input = discord.ui.TextInput(
            label="Дата и время аттестации (по Москве)",
            style=discord.TextStyle.short,
            placeholder="Например: 28.10 19:30 или 28.10.2026 19:30",
            min_length=5,
            max_length=25,
            required=True,
        )
        self.note_input = discord.ui.TextInput(
            label="Примечание / Ссылка на тикет (необязательно)",
            style=discord.TextStyle.short,
            placeholder="Тикет #142, заявка одобрена",
            max_length=150,
            required=False,
        )
        self.add_item(self.time_input)
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw_time = self.time_input.value.strip()
        ok, scheduled_dt, err_msg = parse_msk_datetime(raw_time)

        if not ok or scheduled_dt is None:
            await interaction.response.send_message(
                f"❌ **Ошибка ввода времени:**\n{err_msg}",
                ephemeral=True,
            )
            return

        note = self.note_input.value.strip()

        # Step 4: Final Confirmation Card
        view = AttestationConfirmScheduleView(
            bot=self.bot,
            invoker=self.invoker,
            candidate=self.candidate,
            target_role_id=self.target_role_id,
            target_role_name=self.target_role_name,
            voice_channel_id=self.voice_channel_id,
            voice_channel_name=self.voice_channel_name,
            scheduled_dt=scheduled_dt,
            note=note,
            mc_nick=self.mc_nick,
        )

        history = await db.get_candidate_history(interaction.guild.id, self.candidate.id)
        fails = int(history.get("consecutive_fails", 0)) if history else 0
        attempt_num = min(fails + 1, 3)

        desc = (
            f"👤 **Кандидат:** {self.candidate.mention} (`{self.candidate.name}` | MC: `{self.mc_nick}`)\n"
            f"🏷️ **Назначаемая должность:** **{self.target_role_name}**\n"
            f"🎙️ **Голосовой канал:** <#{self.voice_channel_id}> (`{self.voice_channel_name}`)\n"
            f"⏱️ **Дата и время:** `{format_msk(scheduled_dt)}`\n"
            f"⏳ **Таймер Discord:** {to_discord_timestamp(scheduled_dt, 'F')} ({to_discord_timestamp(scheduled_dt, 'R')})\n"
            f"📊 **Попытка кандидата:** `{attempt_num}` из `{ATTESTATION_MAX_FAILS}`\n"
        )
        if note:
            desc += f"📝 **Примечание:** *{note}*\n"
        desc += (
            f"\n⚠️ **Действия при подтверждении:**\n"
            f"1. Кандидату будет **автоматически выдан доступ** к голосовому каналу.\n"
            f"2. В ЛС кандидата будет отправлена подробная памятка с временем и инструкциями.\n"
            f"3. Бот напомнит вам и кандидату **за 15 и 5 минут** до начала."
        )

        embed = _create_design_embed(
            title="📋 ПОДТВЕРЖДЕНИЕ НАЗНАЧЕНИЯ АТТЕСТАЦИИ",
            description=desc,
            color=COLOR_FLORY_ORANGE,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AttestationConfirmScheduleView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        target_role_id: int,
        target_role_name: str,
        voice_channel_id: int,
        voice_channel_name: str,
        scheduled_dt: datetime,
        note: str,
        mc_nick: str,
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.invoker = invoker
        self.candidate = candidate
        self.target_role_id = target_role_id
        self.target_role_name = target_role_name
        self.voice_channel_id = voice_channel_id
        self.voice_channel_name = voice_channel_name
        self.scheduled_dt = scheduled_dt
        self.note = note
        self.mc_nick = mc_nick

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Вы не можете использовать эту кнопку.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Подтвердить и назначить", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        guild = interaction.guild

        if self.candidate.id in HIGHER_ADMIN_IDS or is_higher_admin(self.candidate.id, guild.id):
            await interaction.followup.send(
                "❌ Назначение аттестации для Высшей Администрации сервера заблокировано системой безопасности.",
                ephemeral=True,
            )
            return

        # 1. Check if candidate was blacklisted in the meantime
        history = await db.get_candidate_history(guild.id, self.candidate.id)
        if history and history.get("is_blacklisted"):
            bl_until = history.get("blacklisted_until")
            bl_msg = f" (до {bl_until})" if bl_until else ""
            await interaction.followup.send(
                f"❌ Невозможно назначить аттестацию: кандидат находится в черном списке{bl_msg}.",
                ephemeral=True,
            )
            return

        # 2. Check if candidate already has an active pending attestation
        active = await db.get_active_attestation(guild.id, self.candidate.id)
        if active:
            await interaction.followup.send(
                f"❌ У кандидата уже есть активная назначенная аттестация на должность **{active['target_role_name']}**.",
                ephemeral=True,
            )
            return

        # 3. Save attestation to SQLite
        scheduled_iso = self.scheduled_dt.isoformat()
        attestation_id = await db.create_attestation(
            guild_id=guild.id,
            candidate_id=self.candidate.id,
            candidate_nick=self.candidate.name,
            curator_id=self.invoker.id,
            curator_nick=self.invoker.name,
            target_role_id=self.target_role_id,
            target_role_name=self.target_role_name,
            voice_channel_id=self.voice_channel_id,
            scheduled_at_iso=scheduled_iso,
        )

        # 4. Grant voice access
        voice_ch = guild.get_channel(self.voice_channel_id)
        voice_granted = await grant_attestation_voice_access(guild, self.voice_channel_id, self.candidate)

        # 5. Send DM to Candidate
        dm_delivered = False
        attempt_num = min((int(history.get("consecutive_fails", 0)) if history else 0) + 1, 3)
        try:
            candidate_embed = attestation_dm_scheduled_embed(
                candidate=self.candidate,
                curator=self.invoker,
                target_role_name=self.target_role_name,
                voice_channel=voice_ch or self.candidate,
                scheduled_dt=self.scheduled_dt,
                attempt_num=attempt_num,
                max_attempts=ATTESTATION_MAX_FAILS,
                note=self.note,
            )
            await self.candidate.send(embed=candidate_embed)
            dm_delivered = True
        except discord.Forbidden:
            logger.warning(f"Could not send DM to candidate {self.candidate.id} (DMs closed)")
        except Exception as e:
            logger.error(f"Error sending DM to candidate {self.candidate.id}: {e}")

        # 6. Send Audit Log
        try:
            audit_embed = attestation_audit_embed(
                action_title="🎓 НАЗНАЧЕНА АТТЕСТАЦИЯ КАНДИДАТА",
                candidate=self.candidate,
                curator=self.invoker,
                target_role_name=self.target_role_name,
                voice_channel=voice_ch,
                scheduled_dt=self.scheduled_dt,
                status_text="PENDING (Ожидает проведения)",
                color=COLOR_FLORY_ORANGE,
                details=f"Попытка: {attempt_num}/{ATTESTATION_MAX_FAILS}\nПримечание: {self.note or 'Нет'}",
            )
            await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
        except Exception as e:
            logger.error(f"Failed to post to audit channel {CURATORS_AUDIT_CHANNEL_ID}: {e}")

        # 7. Response to Curator
        status_note = ""
        if not dm_delivered:
            status_note += "\n⚠️ **Предупреждение:** У кандидата закрыты личные сообщения Discord. Уведомление не было доставлено в ЛС. Свяжитесь с ним лично."
        if not voice_granted:
            status_note += "\n⚠️ **Предупреждение:** Не удалось автоматически настроить права в голосовом канале (проверьте права бота)."

        success_embed = _create_design_embed(
            title="✅ АТТЕСТАЦИЯ УСПЕШНО НАЗНАЧЕНА",
            description=(
                f"Квалификационная аттестация для {self.candidate.mention} успешно зарегистрирована!\n\n"
                f"🏷️ **Должность:** **{self.target_role_name}**\n"
                f"🎙️ **Канал:** <#{self.voice_channel_id}>\n"
                f"⏱️ **Время проведения:** `{format_msk(self.scheduled_dt)}` ({to_discord_timestamp(self.scheduled_dt, 'R')})\n"
                f"🆔 **ID записи:** `#{attestation_id}`\n"
                f"{status_note}"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.edit_original_response(embed=success_embed, view=None)

    @discord.ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        cancel_embed = _create_design_embed(
            title="🛑 НАЗНАЧЕНИЕ ОТМЕНЕНО",
            description="Процесс назначения аттестации был отменен куратором.",
            color=COLOR_WARNING,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


# =========================================================================
# 2. VERDICT FLOW (PASSED / FAILED / SKIPPED / CANCELLED)
# =========================================================================

class AttestationVerdictView(discord.ui.View):
    """Attached to /staff-profile when viewing a user with an active PENDING attestation."""
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        candidate: discord.Member,
        attestation: Dict[str, Any],
        mc_nick: str,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.candidate = candidate
        self.attestation = attestation
        self.mc_nick = mc_nick

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not can_schedule_attestation(interaction.user):
            await interaction.response.send_message("❌ Только кураторы могут выставлять вердикт по аттестации.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Аттестация пройдена", style=discord.ButtonStyle.success)
    async def pass_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AttestationPassModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            candidate=self.candidate,
            attestation=self.attestation,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Аттестация провалена", style=discord.ButtonStyle.danger)
    async def fail_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AttestationFailModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            candidate=self.candidate,
            attestation=self.attestation,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⚠️ Аттестация пропущена", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AttestationSkipModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            candidate=self.candidate,
            attestation=self.attestation,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🛑 Отменить запись", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        att_id = int(self.attestation["id"])
        voice_id = int(self.attestation["voice_channel_id"])
        guild = interaction.guild

        await db.cancel_attestation(att_id, reason=f"Отменено куратором {self.invoker.name}")
        await revoke_attestation_voice_access(guild, voice_id, self.candidate)

        try:
            audit_embed = attestation_audit_embed(
                action_title="🛑 АТТЕСТАЦИЯ ОТМЕНЕНА КУРАТОРОМ",
                candidate=self.candidate,
                curator=self.invoker,
                target_role_name=self.attestation.get("target_role_name", "Не указана"),
                voice_channel=guild.get_channel(voice_id),
                scheduled_dt=None,
                status_text="CANCELLED (Отменена)",
                color=COLOR_WARNING,
                details=f"Причина: Отменено куратором {self.invoker.name}",
            )
            await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
        except Exception as e:
            logger.error(f"Failed to post cancel audit: {e}")

        try:
            if interaction.message:
                await interaction.message.edit(view=None)
        except Exception:
            pass

        embed = _create_design_embed(
            title="🛑 АТТЕСТАЦИЯ ОТМЕНЕНА",
            description=(
                f"Аттестация `#{att_id}` для {self.candidate.mention} была успешно отменена.\n"
                f"Временные права на голосовой канал аннулированы."
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AttestationPassModal(discord.ui.Modal, title="✅ Успешное прохождение аттестации"):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        candidate: discord.Member,
        attestation: Dict[str, Any],
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.candidate = candidate
        self.attestation = attestation
        self.mc_nick = mc_nick

        self.nick_input = discord.ui.TextInput(
            label="Minecraft Никнейм кандидата",
            style=discord.TextStyle.short,
            default=mc_nick if mc_nick and mc_nick != candidate.name else "",
            placeholder="Игровой ник принимаемого сотрудника (НЕ куратора)",
            min_length=3,
            max_length=20,
            required=True,
        )
        self.comment_input = discord.ui.TextInput(
            label="Отзыв / Комментарий куратора",
            style=discord.TextStyle.paragraph,
            placeholder="Собеседование сдано на отлично, регламент усвоен...",
            required=False,
            max_length=300,
        )
        self.add_item(self.nick_input)
        self.add_item(self.comment_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            guild = interaction.guild
            att_id = int(self.attestation["id"])
            target_role_id = int(self.attestation["target_role_id"])
            target_role_name = self.attestation["target_role_name"]
            voice_id = int(self.attestation["voice_channel_id"])
            final_nick = self.nick_input.value.strip()
            comment = self.comment_input.value.strip()

            # Remove verdict buttons so they cannot be clicked multiple times
            try:
                if interaction.message:
                    await interaction.message.edit(view=None)
            except Exception:
                pass

            # Validation: prevent curator from mistakenly giving the rank to themselves
            if (
                self.candidate.id != self.invoker.id
                and (
                    final_nick.lower() == self.invoker.name.lower()
                    or (self.invoker.id in HIGHER_ADMIN_IDS and final_nick.lower() == "devil_first_more")
                )
            ):
                await interaction.followup.send(
                    "❌ **Ошибка:** Вы указали собственный никнейм куратора / администратора (`Devil_First_More`)!\n"
                    "В поле *'Minecraft Никнейм кандидата'* нужно указать игровой никнейм самого кандидата, которого вы принимаете в персонал.",
                    ephemeral=True,
                )
                return

            # 1. Update DB attestation record & reset candidate fail counter
            await db.set_attestation_result(att_id, "PASSED", comment)
            await db.reset_candidate_fails(guild.id, self.candidate.id)

            # 2. Revoke voice access
            await revoke_attestation_voice_access(guild, voice_id, self.candidate)

            # 3. Role assignment & rotation in Discord
            candidate_member = guild.get_member(self.candidate.id)
            if not candidate_member:
                try:
                    candidate_member = await guild.fetch_member(self.candidate.id)
                except Exception:
                    candidate_member = self.candidate

            target_role = guild.get_role(target_role_id)
            staff_access_role = guild.get_role(STAFF_ACCESS_ROLE_ID)
            if not target_role or not staff_access_role:
                try:
                    await guild.fetch_roles()
                    target_role = guild.get_role(target_role_id)
                    staff_access_role = guild.get_role(STAFF_ACCESS_ROLE_ID)
                except Exception:
                    pass

            roles_to_add = []
            if target_role and hasattr(candidate_member, "roles") and target_role not in candidate_member.roles:
                roles_to_add.append(target_role)
            if staff_access_role and hasattr(candidate_member, "roles") and staff_access_role not in candidate_member.roles:
                roles_to_add.append(staff_access_role)

            # Remove lower staff roles
            roles_to_remove = []
            target_idx = next((idx for idx, (r_id, _) in enumerate(STAFF_HIERARCHY) if r_id == target_role_id), 999)
            if hasattr(candidate_member, "roles"):
                for r in candidate_member.roles:
                    for idx, (h_id, _) in enumerate(STAFF_HIERARCHY):
                        if r.id == h_id and idx > target_idx:
                            roles_to_remove.append(r)

            role_warning = None
            bot_member = guild.me
            if not bot_member.guild_permissions.manage_roles:
                role_warning = "У бота нет права 'Управление ролями' (Manage Roles)!"
                logger.error(f"Cannot assign roles for {candidate_member.id}: Bot lacks manage_roles permission")
            elif any(r >= bot_member.top_role for r in roles_to_add):
                role_warning = f"Роль бота ({bot_member.top_role.name}) находится ниже назначаемой роли персонала в иерархии Discord!"
                logger.error(f"Cannot assign roles for {candidate_member.id}: Role hierarchy issue ({role_warning})")
            else:
                try:
                    if hasattr(candidate_member, "remove_roles") and roles_to_remove:
                        await candidate_member.remove_roles(*roles_to_remove, reason=f"Аттестация сдана: повышение до {target_role_name}")
                    if hasattr(candidate_member, "add_roles") and roles_to_add:
                        await candidate_member.add_roles(*roles_to_add, reason=f"Аттестация сдана: назначение {target_role_name}")
                except Exception as e:
                    logger.error(f"Error updating Discord roles for candidate {candidate_member.id}: {e}")
                    role_warning = f"Ошибка Discord при выдаче ролей: {e}"

            # 4. Sync LuckPerms Minecraft group
            new_lp_group = ROLE_ID_TO_MC_GROUP.get(target_role_id, "intern")
            staff_cog = self.cog or self.bot.get_cog("Staff")
            if staff_cog and hasattr(staff_cog, "_dispatch_mc_command"):
                await staff_cog._dispatch_mc_command(guild, f"lp user {final_nick} parent add {new_lp_group}")

            # 5. Save Staff Member into Database (staff_stats, admins table & security log)
            daily_norm_sec = int(STAFF_ROLE_DAILY_NORM_HOURS.get(target_role_id, DEFAULT_DAILY_NORM_HOURS) * 3600)
            existing_by_nick = await db.get_staff_stats(guild.id, 0, fallback_nicks=[final_nick])
            on_sec = existing_by_nick.get("online_seconds", 0) if existing_by_nick else 0
            w_iss = existing_by_nick.get("warns_issued", 0) if existing_by_nick else 0
            b_iss = existing_by_nick.get("bans_issued", 0) if existing_by_nick else 0
            r_acc = existing_by_nick.get("reports_accepted", 0) if existing_by_nick else 0
            m_iss = existing_by_nick.get("mutes_issued", 0) if existing_by_nick else 0

            await db.upsert_staff_stats(
                guild_id=guild.id,
                user_id=candidate_member.id,
                minecraft_nick=final_nick,
                primary_group=new_lp_group,
                online_seconds=on_sec,
                norm_seconds=daily_norm_sec,
                warns_issued=w_iss,
                bans_issued=b_iss,
                reports_accepted=r_acc,
                mutes_issued=m_iss,
            )

            await db.add_admin(
                guild_id=guild.id,
                user_id=candidate_member.id,
                added_by=self.invoker.id,
                title=target_role_name,
            )

            await db.log_security_event(
                guild.id,
                candidate_member.id,
                "STAFF_HIRED",
                f"Аттестация сдана куратору {self.invoker.display_name}. Назначена должность {target_role_name} (MC: {final_nick}, LP: {new_lp_group})",
            )

            # 6. Send DM to Candidate
            try:
                pass_embed = attestation_result_dm_embed(
                    candidate=candidate_member,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    status="PASSED",
                    reason=comment,
                )
                await candidate_member.send(embed=pass_embed)
            except Exception:
                pass

            # 7. Post to Curators Audit Channel
            try:
                audit_details = (
                    f"MC Ник: `{final_nick}`\n"
                    f"LP Группа: `{new_lp_group}`\n"
                    f"Отзыв: {comment or 'Без замечаний'}"
                )
                if role_warning:
                    audit_details += f"\n⚠️ **Внимание Discord:** {role_warning}"

                audit_embed = attestation_audit_embed(
                    action_title="🎉 АТТЕСТАЦИЯ УСПЕШНО СДАНА",
                    candidate=candidate_member,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    voice_channel=guild.get_channel(voice_id),
                    scheduled_dt=None,
                    status_text="PASSED (Сдана)",
                    color=COLOR_SUCCESS,
                    details=audit_details,
                )
                await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
            except Exception as e:
                logger.error(f"Failed to post pass audit: {e}")

            success_desc = (
                f"Сотрудник {candidate_member.mention} успешно аттестован!\n\n"
                f"🏷️ **Должность:** **{target_role_name}**\n"
                f"🎮 **Minecraft Ник:** `{final_nick}`\n"
                f"⚡ **LuckPerms:** `lp user {final_nick} parent add {new_lp_group}`\n"
                f"🛡️ **Временные права:** аннулированы из войса."
            )
            if role_warning:
                success_desc += f"\n\n⚠️ **Внимание:** {role_warning}\n*Пожалуйста, выдайте роль `{target_role_name}` вручную в Discord или поднимите роль бота выше.*"

            success_embed = _create_design_embed(
                title="🎉 АТТЕСТАЦИЯ УСПЕШНО ПРИНЯТА",
                description=success_desc,
                color=COLOR_SUCCESS if not role_warning else COLOR_WARNING,
            )
            await interaction.followup.send(embed=success_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error handling attestation pass modal: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ **Произошла ошибка при фиксации сдачи аттестации:**\n`{e}`",
                ephemeral=True,
            )


class AttestationFailModal(discord.ui.Modal, title="❌ Фиксация провала аттестации"):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        candidate: discord.Member,
        attestation: Dict[str, Any],
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.candidate = candidate
        self.attestation = attestation
        self.mc_nick = mc_nick

        self.reason_input = discord.ui.TextInput(
            label="Причина непрохождения аттестации",
            style=discord.TextStyle.paragraph,
            placeholder="Недостаточные знания правил модерации, ошибки в терминах...",
            min_length=5,
            max_length=300,
            required=True,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            guild = interaction.guild
            att_id = int(self.attestation["id"])
            target_role_name = self.attestation["target_role_name"]
            voice_id = int(self.attestation["voice_channel_id"])
            reason = self.reason_input.value.strip()

            # Remove verdict buttons
            try:
                if interaction.message:
                    await interaction.message.edit(view=None)
            except Exception:
                pass

            # 1. Update attestation record
            await db.set_attestation_result(att_id, "FAILED", reason)

            # 2. Revoke voice access
            await revoke_attestation_voice_access(guild, voice_id, self.candidate)

            # 3. Record fail in candidate history (3-Strike Rule)
            fails_cnt, is_blacklisted, bl_until_iso = await db.record_candidate_fail(
                guild_id=guild.id,
                candidate_id=self.candidate.id,
                reason=reason,
                max_fails=ATTESTATION_MAX_FAILS,
                cooldown_days=ATTESTATION_COOLDOWN_DAYS,
            )

            bl_str = None
            if bl_until_iso:
                try:
                    bl_dt = datetime.fromisoformat(bl_until_iso)
                    bl_str = format_msk(bl_dt)
                except Exception:
                    bl_str = bl_until_iso

            # 4. Notify Candidate in DM
            try:
                fail_embed = attestation_result_dm_embed(
                    candidate=self.candidate,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    status="FAILED",
                    reason=reason,
                    consecutive_fails=fails_cnt,
                    is_blacklisted=is_blacklisted,
                    blacklisted_until_str=bl_str,
                )
                await self.candidate.send(embed=fail_embed)
            except Exception:
                pass

            # 5. Curators Audit Log
            try:
                details_text = f"Неудачных попыток подряд: {fails_cnt}/{ATTESTATION_MAX_FAILS}\nПричина: {reason}"
                if is_blacklisted:
                    details_text += f"\n🚨 **КАНДИДАТ В ЧС НА 1 МЕСЯЦ** (до {bl_str})"

                audit_embed = attestation_audit_embed(
                    action_title="❌ АТТЕСТАЦИЯ ПРОВАЛЕНА",
                    candidate=self.candidate,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    voice_channel=guild.get_channel(voice_id),
                    scheduled_dt=None,
                    status_text="FAILED (Провалена)",
                    color=COLOR_DANGER,
                    details=details_text,
                )
                await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
            except Exception as e:
                logger.error(f"Failed to post fail audit: {e}")

            res_desc = (
                f"Аттестация для {self.candidate.mention} помечена как **проваленная**.\n\n"
                f"📊 **Счетчик неудач подряд:** `{fails_cnt}` из `{ATTESTATION_MAX_FAILS}`\n"
                f"📝 **Причина:** `{reason}`\n"
            )
            if is_blacklisted:
                res_desc += (
                    f"\n🚫 **ВНИМАНИЕ:** Кандидат накопил 3 неудачи подряд и автоматически заблокирован "
                    f"для новых аттестаций **на 1 месяц** (до `{bl_str}`)."
                )

            resp_embed = _create_design_embed(
                title="❌ ВЕРДИКТ: АТТЕСТАЦИЯ ПРОВАЛЕНА",
                description=res_desc,
                color=COLOR_DANGER,
            )
            await interaction.followup.send(embed=resp_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error handling attestation fail modal: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ **Произошла ошибка при фиксации провала аттестации:**\n`{e}`",
                ephemeral=True,
            )


class AttestationSkipModal(discord.ui.Modal, title="⚠️ Фиксация пропуска аттестации"):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        candidate: discord.Member,
        attestation: Dict[str, Any],
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.candidate = candidate
        self.attestation = attestation
        self.mc_nick = mc_nick

        self.reason_input = discord.ui.TextInput(
            label="Причина неявки",
            style=discord.TextStyle.short,
            default="Кандидат не явился в голосовой канал",
            max_length=150,
            required=True,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            guild = interaction.guild
            att_id = int(self.attestation["id"])
            target_role_name = self.attestation["target_role_name"]
            voice_id = int(self.attestation["voice_channel_id"])
            reason = self.reason_input.value.strip()

            # Remove verdict buttons
            try:
                if interaction.message:
                    await interaction.message.edit(view=None)
            except Exception:
                pass

            # 1. Update attestation record
            await db.set_attestation_result(att_id, "SKIPPED", reason)

            # 2. Revoke voice access
            await revoke_attestation_voice_access(guild, voice_id, self.candidate)

            # 3. Record fail in candidate history (3-Strike Rule)
            fails_cnt, is_blacklisted, bl_until_iso = await db.record_candidate_fail(
                guild_id=guild.id,
                candidate_id=self.candidate.id,
                reason=reason,
                max_fails=ATTESTATION_MAX_FAILS,
                cooldown_days=ATTESTATION_COOLDOWN_DAYS,
            )

            bl_str = None
            if bl_until_iso:
                try:
                    bl_dt = datetime.fromisoformat(bl_until_iso)
                    bl_str = format_msk(bl_dt)
                except Exception:
                    bl_str = bl_until_iso

            # 4. Notify Candidate in DM
            try:
                skip_embed = attestation_result_dm_embed(
                    candidate=self.candidate,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    status="SKIPPED",
                    reason=reason,
                    consecutive_fails=fails_cnt,
                    is_blacklisted=is_blacklisted,
                    blacklisted_until_str=bl_str,
                )
                await self.candidate.send(embed=skip_embed)
            except Exception:
                pass

            # 5. Audit Log
            try:
                audit_embed = attestation_audit_embed(
                    action_title="⚠️ АТТЕСТАЦИЯ ПРОПУЩЕНА (НЕЯВКА)",
                    candidate=self.candidate,
                    curator=self.invoker,
                    target_role_name=target_role_name,
                    voice_channel=guild.get_channel(voice_id),
                    scheduled_dt=None,
                    status_text="SKIPPED (Пропущена)",
                    color=COLOR_WARNING,
                    details=f"Неудачных попыток подряд: {fails_cnt}/{ATTESTATION_MAX_FAILS}\nПричина: {reason}",
                )
                await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
            except Exception as e:
                logger.error(f"Failed to post skip audit: {e}")

            res_desc = (
                f"Зафиксирована неявка {self.candidate.mention} на аттестацию.\n\n"
                f"📊 **Счетчик неудач подряд:** `{fails_cnt}` из `{ATTESTATION_MAX_FAILS}`\n"
                f"📌 **Причина:** `{reason}`\n"
            )
            if is_blacklisted:
                res_desc += (
                    f"\n🚫 **ВНИМАНИЕ:** Кандидат накопил 3 пропуска подряд и заблокирован "
                    f"для аттестаций **на 1 месяц** (до `{bl_str}`)."
                )

            resp_embed = _create_design_embed(
                title="⚠️ ВЕРДИКТ: АТТЕСТАЦИЯ ПРОПУЩЕНА",
                description=res_desc,
                color=COLOR_WARNING,
            )
            await interaction.followup.send(embed=resp_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error handling attestation skip modal: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ **Произошла ошибка при фиксации пропуска аттестации:**\n`{e}`",
                ephemeral=True,
            )


# =========================================================================
# 3. CANDIDATE INITIAL VIEW (FOR NON-STAFF PROFILE OR RE-ATTESTATION)
# =========================================================================

class CandidateAttestationStartView(discord.ui.View):
    """Shown in /staff-profile when viewing a user without staff roles or eligible for attestation."""
    def __init__(
        self,
        bot: commands.Bot,
        invoker: discord.Member,
        candidate: discord.Member,
        mc_nick: str,
        eligible_ranks: List[Tuple[int, int, str]],
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.invoker = invoker
        self.candidate = candidate
        self.mc_nick = mc_nick
        self.eligible_ranks = eligible_ranks

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not can_schedule_attestation(interaction.user):
            await interaction.response.send_message("❌ Назначать аттестацию могут только кураторы и руководство.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📝 Назначить аттестацию", style=discord.ButtonStyle.primary)
    async def schedule_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.candidate.id in HIGHER_ADMIN_IDS or is_higher_admin(self.candidate.id, interaction.guild.id):
            await interaction.response.send_message(
                "❌ Назначение аттестации для Высшей Администрации сервера невозможно.",
                ephemeral=True,
            )
            return

        # 1. Check blacklist status
        history = await db.get_candidate_history(interaction.guild.id, self.candidate.id)
        if history and history.get("is_blacklisted"):
            bl_until_iso = history.get("blacklisted_until")
            bl_str = bl_until_iso
            if bl_until_iso:
                try:
                    bl_dt = datetime.fromisoformat(bl_until_iso)
                    bl_str = format_msk(bl_dt)
                except Exception:
                    pass

            embed = _create_design_embed(
                title="🚫 КАНДИДАТ В ЧЕРНОМ СПИСКЕ АТТЕСТАЦИЙ",
                description=(
                    f"Кандидат {self.candidate.mention} получил **3 неудачные попытки подряд** и заблокирован для аттестаций.\n\n"
                    f"⏳ **Срок блокировки:** до `{bl_str}`\n"
                    f"👑 **Досрочное снятие:** доступно только Высшей Администрации сервера."
                ),
                color=COLOR_DANGER,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Check if active attestation exists
        active = await db.get_active_attestation(interaction.guild.id, self.candidate.id)
        if active:
            embed = _create_design_embed(
                title="⚠️ АКТИВНАЯ АТТЕСТАЦИЯ УЖЕ СУЩЕСТВУЕТ",
                description=(
                    f"У кандидата {self.candidate.mention} уже есть назначенная аттестация на должность "
                    f"**{active['target_role_name']}**.\n\n"
                    f"Используйте кнопки вердикта в профиле сотрудника для её завершения."
                ),
                color=COLOR_WARNING,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Present Rank Selection
        view = AttestationRoleSelectView(
            bot=self.bot,
            invoker=self.invoker,
            candidate=self.candidate,
            eligible_ranks=self.eligible_ranks,
            mc_nick=self.mc_nick,
        )
        embed = _create_design_embed(
            title="🎓 НАЗНАЧЕНИЕ АТТЕСТАЦИИ • ЭТАП 1: ДОЛЖНОСТЬ",
            description=(
                f"👤 **Кандидат:** {self.candidate.mention} (`{self.candidate.name}` | MC: `{self.mc_nick}`)\n\n"
                f"Выберите должность, на которую будет проводиться аттестация:"
            ),
            color=COLOR_PRIMARY,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AttestationUnblockView(discord.ui.View):
    """Button available strictly for Higher Administration to unblock a candidate from 3-strike blacklist."""
    def __init__(self, bot: commands.Bot, candidate: discord.Member):
        super().__init__(timeout=300)
        self.bot = bot
        self.candidate = candidate

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not can_unblock_attestation(interaction.user):
            await interaction.response.send_message("❌ Досрочно снять блокировку аттестаций может только Высшая Администрация.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔓 Снять блокировку аттестаций", style=discord.ButtonStyle.success)
    async def unblock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await db.unblock_candidate(interaction.guild.id, self.candidate.id, interaction.user.id)

        # Audit Log
        try:
            audit_embed = _create_design_embed(
                title="🔓 ДОСРОЧНОЕ СНЯТИЕ БЛОКИРОВКИ АТТЕСТАЦИЙ",
                description=(
                    f"👑 **Администратор:** {interaction.user.mention} (`{interaction.user.name}`)\n"
                    f"👤 **Кандидат:** {self.candidate.mention} (`{self.candidate.name}` | ID: `{self.candidate.id}`)\n\n"
                    f"Счётчик неудач сброшен в `0`. Кандидат вновь допущен к аттестациям."
                ),
                color=COLOR_SUCCESS,
            )
            await self.bot.send_channel_log(interaction.guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
        except Exception:
            pass

        confirm_embed = _create_design_embed(
            title="🔓 БЛОКИРОВКА УСПЕШНО СНЯТА",
            description=f"Кандидат {self.candidate.mention} успешно разблокирован. Теперь ему можно назначить аттестацию.",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)


# =========================================================================
# 4. ATTESTATION COG & BACKGROUND SCHEDULER
# =========================================================================

class AttestationCog(commands.Cog, name="Attestation"):
    """Manages interview and attestation scheduling, voice permissions, and background reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        """Scans for pending attestations and delivers 15m and 5m notifications."""
        await self.bot.wait_until_ready()
        try:
            pending_list = await db.get_pending_reminders()
            if not pending_list:
                return

            now_utc = datetime.now(timezone.utc)

            for att in pending_list:
                att_id = int(att["id"])
                scheduled_iso = att["scheduled_at"]
                try:
                    scheduled_dt = datetime.fromisoformat(scheduled_iso)
                    if scheduled_dt.tzinfo is None:
                        scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
                except Exception as e:
                    logger.error(f"Error parsing scheduled_at for attestation {att_id}: {e}")
                    continue

                diff_seconds = (scheduled_dt - now_utc).total_seconds()
                diff_minutes = diff_seconds / 60.0

                guild = self.bot.get_guild(int(att["guild_id"]))
                if not guild:
                    continue

                candidate = guild.get_member(int(att["candidate_id"]))
                curator = guild.get_member(int(att["curator_id"])) or await self.bot.fetch_user(int(att["curator_id"]))
                voice_ch = guild.get_channel(int(att["voice_channel_id"]))
                target_role_name = att["target_role_name"]

                if not candidate:
                    continue

                # 1. 15-Minute Reminder
                if 0 < diff_minutes <= 15.0 and not att.get("reminded_15m"):
                    await db.mark_reminder_sent(att_id, "15m")
                    try:
                        rem_embed = attestation_reminder_embed(
                            candidate=candidate,
                            curator=curator or candidate,
                            target_role_name=target_role_name,
                            voice_channel=voice_ch or candidate,
                            scheduled_dt=scheduled_dt,
                            minutes_left=15,
                        )
                        await candidate.send(content=f"🔔 {candidate.mention}, напоминание о скорой аттестации!", embed=rem_embed)
                    except Exception:
                        pass

                    if curator and hasattr(curator, "send"):
                        try:
                            cur_rem = _create_design_embed(
                                title="⏱️ НАПОМИНАНИЕ КУРАТОРУ: АТТЕСТАЦИЯ ЧЕРЕЗ 15 МИНУТ",
                                description=(
                                    f"У вас запланирована аттестация кандидата {candidate.mention} (`{candidate.name}`) "
                                    f"на должность **{target_role_name}** через **15 минут**!\n\n"
                                    f"🎙️ **Канал:** <#{att['voice_channel_id']}>\n"
                                    f"⏳ **Время:** {to_discord_timestamp(scheduled_dt, 't')} ({to_discord_timestamp(scheduled_dt, 'R')})"
                                ),
                                color=COLOR_WARNING,
                            )
                            await curator.send(embed=cur_rem)
                        except Exception:
                            pass

                # 2. 5-Minute Urgent Reminder
                if 0 < diff_minutes <= 5.0 and not att.get("reminded_5m"):
                    await db.mark_reminder_sent(att_id, "5m")
                    try:
                        rem_embed_5 = attestation_reminder_embed(
                            candidate=candidate,
                            curator=curator or candidate,
                            target_role_name=target_role_name,
                            voice_channel=voice_ch or candidate,
                            scheduled_dt=scheduled_dt,
                            minutes_left=5,
                        )
                        await candidate.send(content=f"🚨 **{candidate.mention}, аттестация начнется через 5 минут! Зайдите в войс!**", embed=rem_embed_5)
                    except Exception:
                        pass

                    if curator and hasattr(curator, "send"):
                        try:
                            cur_rem_5 = _create_design_embed(
                                title="🚨 СРОЧНОЕ НАПОМИНАНИЕ: АТТЕСТАЦИЯ ЧЕРЕЗ 5 МИНУТ!",
                                description=(
                                    f"Аттестация кандидата {candidate.mention} на должность **{target_role_name}** "
                                    f"начнется через **5 минут**!\n\n"
                                    f"🎙️ **Канал:** <#{att['voice_channel_id']}>"
                                ),
                                color=COLOR_DANGER,
                            )
                            await curator.send(embed=cur_rem_5)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Error in attestation reminder loop: {e}", exc_info=True)

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    # =========================================================================
    # SLASH COMMAND FOR QUICK UNBLOCK BY SENIOR ADMIN
    # =========================================================================
    @app_commands.command(
        name="attestation-unblock",
        description="🔓 Снять блокировку аттестаций (3-Strike Blacklist) с кандидата (Высшая Администрация)"
    )
    @app_commands.describe(user="Кандидат, с которого необходимо снять блокировку")
    async def attestation_unblock_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if not can_unblock_attestation(interaction.user):
            await interaction.response.send_message(
                "❌ Досрочное снятие блокировки аттестаций разрешено только Высшей Администрации.",
                ephemeral=True,
            )
            return

        history = await db.get_candidate_history(interaction.guild.id, user.id)
        if not history or not history.get("is_blacklisted"):
            await interaction.response.send_message(
                f"ℹ️ Кандидат {user.mention} не находится в черном списке аттестаций.",
                ephemeral=True,
            )
            return

        await db.unblock_candidate(interaction.guild.id, user.id, interaction.user.id)

        # Audit Log
        try:
            audit_embed = _create_design_embed(
                title="🔓 ДОСРОЧНОЕ СНЯТИЕ БЛОКИРОВКИ АТТЕСТАЦИЙ",
                description=(
                    f"👑 **Администратор:** {interaction.user.mention} (`{interaction.user.name}`)\n"
                    f"👤 **Кандидат:** {user.mention} (`{user.name}` | ID: `{user.id}`)\n\n"
                    f"Счётчик неудач сброшен в `0`. Кандидат вновь допущен к аттестациям."
                ),
                color=COLOR_SUCCESS,
            )
            await self.bot.send_channel_log(interaction.guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)
        except Exception:
            pass

        embed = _create_design_embed(
            title="🔓 БЛОКИРОВКА УСПЕШНО СНЯТА",
            description=f"Кандидат {user.mention} успешно разблокирован. Теперь ему можно назначить аттестацию.",
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AttestationCog(bot))
