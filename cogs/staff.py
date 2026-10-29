import asyncio
import re
import json
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    UNAUTHORIZED_MESSAGE,
    CONFIG,
    AUTHORIZED_GUILDS,
    save_config,
    STAFF_HIERARCHY,
    STAFF_ROLE_DAILY_NORM_HOURS,
    STAFF_ROLE_IDS,
    STAFF_ACCESS_ROLE_ID,
    WARN_ACCESS_ROLE_ID,
    TRAINEE_ROLE_ID,
    TRAINEE_MAX_WARNS,
    DEFAULT_STAFF_MAX_WARNS,
    DEFAULT_DAILY_NORM_HOURS,
    CURATORS_AUDIT_CHANNEL_ID,
    MUTES_LOG_CHANNEL_ID,
    BANS_LOG_CHANNEL_ID,
    WARNS_LOG_CHANNEL_ID,
    MODERATION_CMD_CHANNEL_ID,
    MUTE_MIN_VALID_SECONDS,
    BAN_MIN_VALID_SECONDS,
    SPOT_CHECK_PROBABILITY,
    HIGHER_ADMIN_IDS,
)
from core.permissions import (
    is_authorized_guild,
    has_staff_access,
    has_warn_access,
    can_schedule_attestation,
    can_unblock_attestation,
    can_request_punishment_proof,
    is_senior_admin,
    is_higher_admin,
    is_bot_owner,
    get_member_staff_rank,
    get_member_daily_norm_seconds,
    check_moderation_channel,
)
from database.db import db
from utils.staff_card import generate_staff_profile_card
from utils.embeds import (
    create_security_embed,
    COLOR_FLORY_ORANGE,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_PRIMARY,
)
from utils.logger import logger

SYNC_PREFIX = "__FLORY_SYNC__"

# Map LuckPerms group names from FloryModerators / FloryAuthentication -> (Display Rank Name, Discord Role ID)
MC_GROUP_TO_RANK = {
    "ruk": ("Руководитель", 1544022496934756482),
    "admin": ("Администратор", 1548646933101420564),
    "developer": ("Разработчик", 1550434344588288030),
    "cur": ("Куратор", 1454703503292502155),
    "gl.moder": ("Гл. Модератор", 1548961652899061841),
    "st.moder": ("Ст. Модератор", 1478446161810690159),
    "moder": ("Модератор", 1454703438989492389),
    "helper": ("Хелпер", 1454703334459048000),
    "intern": ("Стажер", 1454703157677395969),
}

# Reverse mapping: Discord Role ID -> LuckPerms group name
ROLE_ID_TO_MC_GROUP = {
    1544022496934756482: "ruk",
    1391723885900333214: "ruk",
    1548646933101420564: "admin",
    1550434344588288030: "developer",
    1454703503292502155: "cur",
    1548961652899061841: "gl.moder",
    1478446161810690159: "st.moder",
    1454703438989492389: "moder",
    1454703334459048000: "helper",
    1454703157677395969: "intern",
}

KNOWN_DISCORD_TO_MC_NICK = {
    1398717669607473254: "Devil_First_More",
}


def _is_same_network_or_alt(ip1: Optional[str], ip2: Optional[str]) -> bool:
    """Checks if two IP addresses match or belong to the same /24 IPv4 subnet."""
    if not ip1 or not ip2:
        return False
    clean1 = ip1.strip()
    clean2 = ip2.strip()
    if not clean1 or not clean2:
        return False
    if clean1 == clean2:
        return True
    parts1 = clean1.split(".")
    parts2 = clean2.split(".")
    if len(parts1) == 4 and len(parts2) == 4:
        return parts1[:3] == parts2[:3]
    return False


def format_duration_seconds(seconds: int) -> str:
    """Formats duration in seconds into readable Russian text."""
    if seconds <= 0:
        return "Перманентно (Навсегда)"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days} дн.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    return " ".join(parts) if parts else f"{seconds} сек."


# =========================================================================
# DESIGN-ENGINEER GRADE INTERACTIVE STAFF CONTROLS (LINEAR / APPLE SPEC)
# =========================================================================
COLOR_MUTED = 0x4F545C
COLOR_DARK_SURFACE = 0x1E1F22


def get_invoker_allowed_promotion_ceiling_idx(invoker: discord.Member) -> int:
    """Returns the minimum rank index (highest rank) the invoker is permitted to promote a user TO.
    
    STAFF_HIERARCHY ordering (index 0 is highest, index 7 is lowest):
      0: Высшая Администрация
      1: Администрация
      2: Куратор
      3: Гл. Модер | Зам. Куратора
      4: Ст. Модер
      5: Модер
      6: Хелпер
      7: Стажер
    
    A base Curator (role 1454703598834421831 or 1454703503292502155) can promote at MAXIMUM
    to 'Гл. Модер | Зам. Куратора' (index 3).
    Higher administrators (Developer, Admin, Hidden Admin, Supreme Admin, Server Owner) can promote
    to higher positions up to their own rank level.
    """
    if is_bot_owner(invoker.id) or (invoker.guild and invoker.id == invoker.guild.owner_id):
        return 0

    privileged_roles = {
        1550434344588288030,  # Разработчик
        1548646933101420564,  # Администрация
        1555624705342308403,  # Скрытая Админка
        1391723885900333214,  # Высшая Администрация
    }
    user_roles = {r.id for r in invoker.roles}
    if user_roles & privileged_roles:
        _, _, _, invoker_idx = get_member_staff_rank(invoker)
        return max(0, invoker_idx)

    # Base Curator ceiling is strictly index 3 (Гл. Модератор | Зам. Куратора)
    return 3


def get_rank_emoji(role_id: int) -> str:
    """Returns an aesthetic visual icon matching the rank role."""
    mc_grp = ROLE_ID_TO_MC_GROUP.get(role_id, "default")
    emojis = {
        "ruk": "👑",
        "admin": "⭐",
        "developer": "💻",
        "cur": "⚜️",
        "gl.moder": "🛡️",
        "st.moder": "⚔️",
        "moder": "📜",
        "helper": "🔰",
        "intern": "🌱",
    }
    return emojis.get(mc_grp, "🔹")


def create_design_embed(
    title: str,
    description: str,
    color: int = COLOR_PRIMARY,
    footer_text: str = "FloryGuard • Staff Management System",
) -> discord.Embed:
    """Creates a Design-Engineer grade embed with consistent visual hierarchy."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=footer_text, icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
    return embed


# -------------------------------------------------------------------------
# 1. PROMOTE FLOW (Dropdown Selection ➔ Reason Modal ➔ 2-Button Confirm)
# -------------------------------------------------------------------------
class PromoteRankSelect(discord.ui.Select):
    def __init__(self, eligible_ranks: list[tuple[int, int, str]]):
        options = []
        for idx, r_id, r_name in eligible_ranks:
            lp_grp = ROLE_ID_TO_MC_GROUP.get(r_id, "intern")
            daily_h = STAFF_ROLE_DAILY_NORM_HOURS.get(r_id, DEFAULT_DAILY_NORM_HOURS)
            options.append(
                discord.SelectOption(
                    label=r_name,
                    value=str(idx),
                    description=f"Норма: {daily_h} ч./день • LuckPerms: {lp_grp}",
                    emoji=get_rank_emoji(r_id),
                )
            )
        super().__init__(
            placeholder="Выберите целевую должность для повышения...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        new_idx = int(self.values[0])
        modal = PromoteReasonModal(
            bot=self.view.bot,
            cog=self.view.cog,
            invoker=self.view.invoker,
            target=self.view.target,
            current_idx=self.view.current_idx,
            new_idx=new_idx,
            mc_nick=self.view.mc_nick,
        )
        await interaction.response.send_modal(modal)


class PromoteRankSelectView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        mc_nick: str,
        eligible_ranks: list[tuple[int, int, str]],
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.mc_nick = mc_nick
        self.add_item(PromoteRankSelect(eligible_ranks))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Повышение сотрудника отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


class PromoteReasonModal(discord.ui.Modal, title="⬆️ Причина повышения в должности"):
    reason = discord.ui.TextInput(
        label="Причина повышения сотрудника",
        placeholder="Например: Отличная активность за неделю, выполнение нормы онлайна",
        min_length=4,
        max_length=256,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        new_idx: int,
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.new_idx = new_idx
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]
        new_role_id, new_rank_name = STAFF_HIERARCHY[self.new_idx]
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        reason_text = self.reason.value.strip()

        view = PromoteConfirmView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.current_idx,
            new_idx=self.new_idx,
            mc_nick=self.mc_nick,
            reason_text=reason_text,
        )

        embed = create_design_embed(
            title="⚡ ПОДТВЕРЖДЕНИЕ ПОВЫШЕНИЯ СОТРУДНИКА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n\n"
                f"```diff\n"
                f"- Было:   {old_rank_name}\n"
                f"+ Станет: {new_rank_name}\n"
                f"```\n"
                f"⚙️ **Синхронизация:**\n"
                f"• **Discord:** Снятие `<@&{old_role_id}>` ➔ Выдача `<@&{new_role_id}>`\n"
                f"• **Minecraft:** `lp user {self.mc_nick} parent add {new_lp_group}`\n"
                f"              `lp user {self.mc_nick} parent remove {old_lp_group}`\n\n"
                f"📝 **Причина:** `{reason_text}`\n\n"
                f"Нажмите кнопку подтверждения ниже для выполнения действия:"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class PromoteConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        new_idx: int,
        mc_nick: str,
        reason_text: str,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.new_idx = new_idx
        self.mc_nick = mc_nick
        self.reason_text = reason_text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Подтвердить повышение", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Изменение должности Высшей Администрации сервера невозможно.", ephemeral=True)
            return

        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]
        new_role_id, new_rank_name = STAFF_HIERARCHY[self.new_idx]
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        guild = interaction.guild

        old_role = guild.get_role(old_role_id)
        new_role = guild.get_role(new_role_id)

        try:
            if old_role and old_role in self.target.roles:
                await self.target.remove_roles(old_role, reason=f"Повышение куратором {self.invoker}")
            if new_role and new_role not in self.target.roles:
                await self.target.add_roles(new_role, reason=f"Повышение куратором {self.invoker}")

            # Ensure staff access role
            staff_acc = guild.get_role(STAFF_ACCESS_ROLE_ID)
            if staff_acc and staff_acc not in self.target.roles:
                await self.target.add_roles(staff_acc)

            # If promoted to Curator or higher (idx <= 2), ensure warn access role
            if self.new_idx <= 2:
                warn_acc = guild.get_role(WARN_ACCESS_ROLE_ID)
                if warn_acc and warn_acc not in self.target.roles:
                    await self.target.add_roles(warn_acc)
        except Exception as e:
            logger.error(f"Failed to update Discord roles on promote: {e}")

        # Minecraft LuckPerms sync: parent add new, parent remove old
        if self.mc_nick:
            await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent add {new_lp_group}")
            if old_lp_group != new_lp_group:
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {old_lp_group}")

        # Send DM notification
        try:
            dm_embed = create_design_embed(
                title="⚡ ПОВЫШЕНИЕ В ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Поздравляем! Ваша должность в стаффе была повышена.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Новая должность:** **{new_rank_name}**\n"
                    f"📝 **Причина:** `{self.reason_text}`"
                ),
                color=COLOR_SUCCESS,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_design_embed(
            title="⬆️ ПОВЫШЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Ранг:** `{old_rank_name}` ➔ **`{new_rank_name}`**\n"
                f"🎮 **Группа LuckPerms:** `{new_lp_group}`\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_SUCCESS,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        success_embed = create_design_embed(
            title="✅ ПОВЫШЕНИЕ УСПЕШНО ВЫПОЛНЕНО",
            description=(
                f"Сотрудник {self.target.mention} (`{self.target.name}`) успешно повышен!\n\n"
                f"👑 **Новая должность:** **{new_rank_name}**\n"
                f"🎮 **LuckPerms:** `{new_lp_group}`\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=success_embed, view=None)

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Повышение сотрудника отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


# -------------------------------------------------------------------------
# 2. DEMOTE FLOW (Dropdown Selection ➔ Reason Modal ➔ 2-Button Confirm)
# -------------------------------------------------------------------------
class DemoteRankSelect(discord.ui.Select):
    def __init__(self, eligible_ranks: list[tuple[int, int, str]]):
        options = []
        for idx, r_id, r_name in eligible_ranks:
            lp_grp = ROLE_ID_TO_MC_GROUP.get(r_id, "intern")
            daily_h = STAFF_ROLE_DAILY_NORM_HOURS.get(r_id, DEFAULT_DAILY_NORM_HOURS)
            options.append(
                discord.SelectOption(
                    label=r_name,
                    value=str(idx),
                    description=f"Норма: {daily_h} ч./день • LuckPerms: {lp_grp}",
                    emoji=get_rank_emoji(r_id),
                )
            )
        super().__init__(
            placeholder="Выберите новую (более низкую) должность...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        new_idx = int(self.values[0])
        modal = DemoteReasonModal(
            bot=self.view.bot,
            cog=self.view.cog,
            invoker=self.view.invoker,
            target=self.view.target,
            current_idx=self.view.current_idx,
            new_idx=new_idx,
            mc_nick=self.view.mc_nick,
        )
        await interaction.response.send_modal(modal)


class DemoteRankSelectView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        mc_nick: str,
        eligible_ranks: list[tuple[int, int, str]],
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.mc_nick = mc_nick
        self.add_item(DemoteRankSelect(eligible_ranks))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Понижение сотрудника отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


class DemoteReasonModal(discord.ui.Modal, title="⬇️ Причина понижения в должности"):
    reason = discord.ui.TextInput(
        label="Причина понижения сотрудника",
        placeholder="Например: Систематический неактив, ошибки при рассмотрении жалоб",
        min_length=4,
        max_length=256,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        new_idx: int,
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.new_idx = new_idx
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]
        new_role_id, new_rank_name = STAFF_HIERARCHY[self.new_idx]
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        reason_text = self.reason.value.strip()

        view = DemoteConfirmView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.current_idx,
            new_idx=self.new_idx,
            mc_nick=self.mc_nick,
            reason_text=reason_text,
        )

        embed = create_design_embed(
            title="⚠️ ПОДТВЕРЖДЕНИЕ ПОНИЖЕНИЯ СОТРУДНИКА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n\n"
                f"```diff\n"
                f"- Было:   {old_rank_name}\n"
                f"- Станет: {new_rank_name}\n"
                f"```\n"
                f"⚙️ **Синхронизация:**\n"
                f"• **Discord:** Снятие `<@&{old_role_id}>` ➔ Выдача `<@&{new_role_id}>`\n"
                f"• **Minecraft:** `lp user {self.mc_nick} parent add {new_lp_group}`\n"
                f"              `lp user {self.mc_nick} parent remove {old_lp_group}`\n\n"
                f"📝 **Причина:** `{reason_text}`\n\n"
                f"Нажмите кнопку подтверждения ниже для выполнения действия:"
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class DemoteConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        current_idx: int,
        new_idx: int,
        mc_nick: str,
        reason_text: str,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.new_idx = new_idx
        self.mc_nick = mc_nick
        self.reason_text = reason_text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚠️ Подтвердить понижение", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие заблокировано: понижение Высшей Администрации невозможно.", ephemeral=True)
            return

        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]
        new_role_id, new_rank_name = STAFF_HIERARCHY[self.new_idx]
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        guild = interaction.guild

        old_role = guild.get_role(old_role_id)
        new_role = guild.get_role(new_role_id)

        try:
            if old_role and old_role in self.target.roles:
                await self.target.remove_roles(old_role, reason=f"Понижение куратором {self.invoker}")
            if new_role and new_role not in self.target.roles:
                await self.target.add_roles(new_role, reason=f"Понижение куратором {self.invoker}")

            # If demoted below Curator (new_idx > 2), strip warn access
            if self.new_idx > 2:
                warn_acc = guild.get_role(WARN_ACCESS_ROLE_ID)
                if warn_acc and warn_acc in self.target.roles:
                    await self.target.remove_roles(warn_acc)
        except Exception as e:
            logger.error(f"Failed to update Discord roles on demote: {e}")

        # Minecraft LuckPerms sync
        if self.mc_nick:
            await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent add {new_lp_group}")
            if old_lp_group != new_lp_group:
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {old_lp_group}")

        # Send DM notification
        try:
            dm_embed = create_design_embed(
                title="⚠️ ПОНИЖЕНИЕ В ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Ваша должность в стаффе была понижена.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Новая должность:** **{new_rank_name}**\n"
                    f"📝 **Причина:** `{self.reason_text}`"
                ),
                color=COLOR_WARNING,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_design_embed(
            title="⬇️ ПОНИЖЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Ранг:** `{old_rank_name}` ➔ **`{new_rank_name}`**\n"
                f"🎮 **Группа LuckPerms:** `{new_lp_group}`\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_WARNING,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        success_embed = create_design_embed(
            title="⚠️ ПОНИЖЕНИЕ ВЫПОЛНЕНО",
            description=(
                f"Сотрудник {self.target.mention} (`{self.target.name}`) понижен.\n\n"
                f"📊 **Новая должность:** **{new_rank_name}**\n"
                f"🎮 **LuckPerms:** `{new_lp_group}`\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.edit_message(embed=success_embed, view=None)

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Понижение сотрудника отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


# -------------------------------------------------------------------------
# 3. FIRE FLOW (Reason Modal ➔ Danger 2-Button Confirm)
# -------------------------------------------------------------------------
class FireReasonModal(discord.ui.Modal, title="❌ Снятие сотрудника с должности"):
    reason = discord.ui.TextInput(
        label="Причина снятия со стаффа",
        placeholder="Например: Грубое нарушение правил, слив информации, собственное желание",
        min_length=4,
        max_length=256,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        rank_name: str,
        mc_nick: str,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.rank_name = rank_name
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие заблокировано: снятие Высшей Администрации сервера абсолютно невозможно.", ephemeral=True)
            return

        reason_text = self.reason.value.strip()

        view = FireConfirmView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            rank_name=self.rank_name,
            mc_nick=self.mc_nick,
            reason_text=reason_text,
        )

        embed = create_design_embed(
            title="🚨 ПОДТВЕРЖДЕНИЕ СНЯТИЯ СО СТАФФА (УВОЛЬНЕНИЕ)",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Текущая должность:** **{self.rank_name}**\n\n"
                f"⚠️ **ВНИМАНИЕ! ПОСЛЕДСТВИЯ ДЕЙСТВИЯ:**\n"
                f"• Будут сняты все роли стаффа в Discord.\n"
                f"• В Minecraft LuckPerms будут удалены все родительские группы стаффа.\n"
                f"• Игровые донат-привилегии игрока останутся нетронутыми.\n\n"
                f"📝 **Причина снятия:** `{reason_text}`\n\n"
                f"Подтвердите выполнение действия нажатием на кнопку:"
            ),
            color=COLOR_DANGER,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class FireConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        rank_name: str,
        mc_nick: str,
        reason_text: str,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.rank_name = rank_name
        self.mc_nick = mc_nick
        self.reason_text = reason_text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔴 Снять с должности", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие заблокировано: снятие Высшей Администрации сервера абсолютно невозможно.", ephemeral=True)
            return

        guild = interaction.guild
        roles_to_strip = [
            r for r in self.target.roles
            if r.id in STAFF_ROLE_IDS or r.id == WARN_ACCESS_ROLE_ID or r.id == STAFF_ACCESS_ROLE_ID
        ]
        try:
            if roles_to_strip:
                await self.target.remove_roles(*roles_to_strip, reason=f"Снятие со стаффа куратором {self.invoker}")
        except Exception as e:
            logger.error(f"Failed to strip staff roles on fire: {e}")

        # Remove LuckPerms staff groups in Minecraft (leaving donate / default groups untouched)
        if self.mc_nick:
            for grp in set(ROLE_ID_TO_MC_GROUP.values()):
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {grp}")

        # Send DM notification
        try:
            dm_embed = create_design_embed(
                title="🔴 СНЯТИЕ С ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Вы были сняты с должности Стаффа на сервере FloryMine.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Бывшая должность:** `{self.rank_name}`\n"
                    f"📝 **Причина:** `{self.reason_text}`"
                ),
                color=COLOR_DANGER,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_design_embed(
            title="❌ СНЯТИЕ СО СТАФФА (УВОЛЬНЕНИЕ)",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Бывшая должность:** `{self.rank_name}`\n"
                f"🎮 **Сброс LuckPerms:** группы стаффа удалены\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_DANGER,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        success_embed = create_design_embed(
            title="🔴 СОТРУДНИК СНЯТ С ДОЛЖНОСТИ",
            description=(
                f"Сотрудник {self.target.mention} (`{self.target.name}`) успешно снят со всех должностей стаффа.\n\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_DANGER,
        )
        await interaction.response.edit_message(embed=success_embed, view=None)

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Снятие сотрудника отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


# -------------------------------------------------------------------------
# 4. ANNUL / DEDUCT PUNISHMENTS FLOW (Specific Selection or Counter Deduction)
# -------------------------------------------------------------------------
class AnnulPunishmentSelect(discord.ui.Select):
    def __init__(self, recent_punishments: list[dict]):
        options = []
        for p in recent_punishments[:20]:
            p_type = (p.get("punishment_type") or "PUNISH").upper()
            t_nick = p.get("target_nick") or "Unknown"
            reason = (p.get("reason") or "Без причины")[:28]
            dur_sec = int(p.get("duration_seconds") or 0)
            dur_str = format_duration_seconds(dur_sec)
            p_id = p["id"]

            icon = "🔨" if p_type == "BAN" else ("🔇" if p_type == "MUTE" else "⚠️")
            options.append(
                discord.SelectOption(
                    label=f"[{p_type}] Цель: {t_nick}",
                    value=f"punish_{p_id}",
                    description=f"{p_type}: {t_nick} | {dur_str} | {reason}",
                    emoji=icon,
                )
            )
        super().__init__(
            placeholder="Выберите конкретное выданное наказание для аннулирования...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        p_id = int(val.replace("punish_", ""))
        # Find item in cache
        item = next((p for p in self.view.recent_punishments if p["id"] == p_id), None)
        modal = AnnulReasonModal(
            bot=self.view.bot,
            cog=self.view.cog,
            invoker=self.view.invoker,
            target=self.view.target,
            mc_nick=self.view.mc_nick,
            action_type="specific_punishment",
            payload={"punishment": item, "punishment_id": p_id},
        )
        await interaction.response.send_modal(modal)


class AnnulPunishmentsView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        mc_nick: str,
        bans_cnt: int,
        mutes_cnt: int,
        warns_cnt: int,
        active_warns: list[dict],
        recent_punishments: list[dict],
    ):
        super().__init__(timeout=180)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.mc_nick = mc_nick
        self.bans_cnt = bans_cnt
        self.mutes_cnt = mutes_cnt
        self.warns_cnt = warns_cnt
        self.active_warns = active_warns
        self.recent_punishments = recent_punishments

        # If we have real punishment logs, render the dropdown selection
        if recent_punishments:
            self.add_item(AnnulPunishmentSelect(recent_punishments))

        # Add manual counter deduction buttons if specific records are missing or for quick adjustments
        if bans_cnt > 0:
            b_btn = discord.ui.Button(label="🔨 Списать 1 бан", style=discord.ButtonStyle.secondary, row=1)
            b_btn.callback = self._deduct_ban_callback
            self.add_item(b_btn)

        if mutes_cnt > 0:
            m_btn = discord.ui.Button(label="🔇 Списать 1 мут", style=discord.ButtonStyle.secondary, row=1)
            m_btn.callback = self._deduct_mute_callback
            self.add_item(m_btn)

        if active_warns:
            w_btn = discord.ui.Button(label=f"⚠️ Снять выговор ({len(active_warns)})", style=discord.ButtonStyle.secondary, row=1)
            w_btn.callback = self._remove_warn_callback
            self.add_item(w_btn)

        if is_senior_admin(invoker.id, invoker.guild.id):
            reset_btn = discord.ui.Button(label="🔄 Обнулить все счётчики", style=discord.ButtonStyle.danger, row=2)
            reset_btn.callback = self._reset_all_callback
            self.add_item(reset_btn)

        cancel = discord.ui.Button(label="Отмена", style=discord.ButtonStyle.secondary, row=2)
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    async def _deduct_ban_callback(self, interaction: discord.Interaction):
        modal = AnnulReasonModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            action_type="deduct_ban_1",
            payload={},
        )
        await interaction.response.send_modal(modal)

    async def _deduct_mute_callback(self, interaction: discord.Interaction):
        modal = AnnulReasonModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            action_type="deduct_mute_1",
            payload={},
        )
        await interaction.response.send_modal(modal)

    async def _remove_warn_callback(self, interaction: discord.Interaction):
        modal = AnnulReasonModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            action_type="remove_warn_1",
            payload={"warn_id": self.active_warns[0]["id"] if self.active_warns else None},
        )
        await interaction.response.send_modal(modal)

    async def _reset_all_callback(self, interaction: discord.Interaction):
        modal = AnnulReasonModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            action_type="reset_all",
            payload={},
        )
        await interaction.response.send_modal(modal)

    async def _cancel_callback(self, interaction: discord.Interaction):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Списание статистики отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


class AnnulReasonModal(discord.ui.Modal, title="📝 Причина списания / аннулирования"):
    reason = discord.ui.TextInput(
        label="Причина аннулирования",
        placeholder="Например: Апелляция игрока удовлетворена / Ошибка модератора",
        min_length=4,
        max_length=256,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        mc_nick: str,
        action_type: str,
        payload: dict,
    ):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.mc_nick = mc_nick
        self.action_type = action_type
        self.payload = payload

    async def on_submit(self, interaction: discord.Interaction):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие заблокировано: снятие Высшей Администрации сервера абсолютно невозможно.", ephemeral=True)
            return

        reason_text = self.reason.value.strip()

        view = AnnulConfirmView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            action_type=self.action_type,
            payload=self.payload,
            reason_text=reason_text,
        )

        # Formulate preview text
        if self.action_type == "specific_punishment":
            p = self.payload.get("punishment", {})
            p_type = (p.get("punishment_type") or "PUNISH").upper()
            t_nick = p.get("target_nick") or "Unknown"
            dur = format_duration_seconds(int(p.get("duration_seconds") or 0))
            p_reason = p.get("reason") or "Без причины"
            details = (
                f"```yaml\n"
                f"Тип: {p_type}\n"
                f"ID: #{p.get('id')}\n"
                f"Нарушитель: {t_nick}\n"
                f"Срок: {dur}\n"
                f"Причина наказания: {p_reason}\n"
                f"```\n"
                f"📉 **Действие:** Аннулирование в базе логов, списание -1 {p_type} и отправка отката на сервер."
            )
        elif self.action_type == "deduct_ban_1":
            details = "📉 **Действие:** Списание **1 бана** из игровой статистики модератора."
        elif self.action_type == "deduct_mute_1":
            details = "📉 **Действие:** Списание **1 мута** из игровой статистики модератора."
        elif self.action_type == "remove_warn_1":
            details = "📉 **Действие:** Снятие **1 активного выговора** из личного дела сотрудника."
        elif self.action_type == "reset_all":
            details = "🚨 **Действие:** **Полное обнуление** всех выданных наказаний и снятие всех выговоров."
        else:
            details = f"Действие: {self.action_type}"

        embed = create_design_embed(
            title="⚖️ ПОДТВЕРЖДЕНИЕ АННУЛИРОВАНИЯ",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n\n"
                f"{details}\n\n"
                f"📝 **Причина куратора:** `{reason_text}`\n\n"
                f"Подтвердите выполнение действия нажатием на кнопку:"
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class AnnulConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        mc_nick: str,
        action_type: str,
        payload: dict,
        reason_text: str,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.mc_nick = mc_nick
        self.action_type = action_type
        self.payload = payload
        self.reason_text = reason_text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Действие доступно только инициатору команды.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Подтвердить списание", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        action_desc = ""

        if self.action_type == "specific_punishment":
            p = self.payload.get("punishment", {})
            p_id = self.payload.get("punishment_id", 0)
            p_type = (p.get("punishment_type") or "BAN").upper()
            t_nick = p.get("target_nick") or "Unknown"

            # Invalidate in punishment_logs
            await db.invalidate_punishment(p_id, guild.id)

            # Deduct from staff_stats
            if p_type == "BAN":
                await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, bans_delta=-1)
            elif p_type == "MUTE":
                await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, mutes_delta=-1)
            elif p_type == "WARN":
                await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, warns_delta=-1)

            # Dispatch sync rollback to Minecraft plugin
            await self.cog._dispatch_sync_rollback(guild, p_type, self.mc_nick, t_nick, self.reason_text)
            action_desc = f"Аннулировано наказание #{p_id} ({p_type} для `{t_nick}`), списано -1 из статистики"

        elif self.action_type == "deduct_ban_1":
            await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, bans_delta=-1)
            action_desc = "Списан **1 бан** из статистики"
        elif self.action_type == "deduct_mute_1":
            await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, mutes_delta=-1)
            action_desc = "Списан **1 мут** из статистики"
        elif self.action_type == "remove_warn_1":
            w_id = self.payload.get("warn_id")
            if w_id:
                await db.remove_warning(w_id, guild.id)
            else:
                warns = await db.get_active_warnings(guild.id, self.target.id)
                if warns:
                    await db.remove_warning(warns[0]["id"], guild.id)
            action_desc = "Снят **1 выговор** из базы данных"
        elif self.action_type == "reset_all":
            await db.adjust_staff_stats(
                guild.id,
                user_id=self.target.id,
                mc_nick=self.mc_nick,
                bans_delta=-9999,
                mutes_delta=-9999,
                warns_delta=-9999,
            )
            await db.clear_warnings(guild.id, self.target.id)
            action_desc = "Полное обнуление игровой статистики и выговоров"

        # Send DM notification
        try:
            dm_embed = create_design_embed(
                title="📊 КОРРЕКТИРОВКА СТАТИСТИКИ • FLORYMINE",
                description=(
                    f"Ваша статистика модерации была изменена Куратором.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"⚡ **Изменение:** {action_desc}\n"
                    f"📝 **Причина:** `{self.reason_text}`"
                ),
                color=COLOR_PRIMARY,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_design_embed(
            title="🧹 СПИСАНИЕ СТАТИСТИКИ СОТРУДНИКА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"⚡ **Действие:** {action_desc}\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_PRIMARY,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        success_embed = create_design_embed(
            title="✅ СТАТИСТИКА УСПЕШНО СКОРРЕКТИРОВАНА",
            description=(
                f"Действие над сотрудником {self.target.mention} успешно выполнено!\n\n"
                f"⚡ **Результат:** {action_desc}\n"
                f"📝 **Причина:** `{self.reason_text}`"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.response.edit_message(embed=success_embed, view=None)

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cancel_embed = create_design_embed(
            title="⚪ ДЕЙСТВИЕ ОТМЕНЕНО",
            description="Списание статистики отменено.",
            color=COLOR_MUTED,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)


# -------------------------------------------------------------------------
# 5. MAIN CURATOR STAFF CONTROL VIEW (Attached to /staff-profile)
# -------------------------------------------------------------------------
class CuratorStaffControlView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        cog,
        invoker: discord.Member,
        target: discord.Member,
        target_rank_name: str,
        target_idx: int,
        mc_nick: str,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.target_rank_name = target_rank_name
        self.target_idx = target_idx
        self.mc_nick = mc_nick

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Взаимодействие и снятие Высшей Администрации сервера абсолютно невозможны.", ephemeral=True)
            return False
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Только куратор, открывший профиль, или Владелец может использовать эту панель.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⬆️ Повысить", style=discord.ButtonStyle.success)
    async def promote_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Взаимодействие с Высшей Администрацией невозможно.", ephemeral=True)
            return

        ceiling_idx = get_invoker_allowed_promotion_ceiling_idx(interaction.user)

        # Check ceiling limit
        if self.target_idx <= ceiling_idx:
            ceiling_name = STAFF_HIERARCHY[ceiling_idx][1]
            embed = create_design_embed(
                title="🛡️ ОГРАНИЧЕНИЕ ПОЛНОМОЧИЙ",
                description=(
                    f"Вы можете повышать сотрудников максимум до должности:\n"
                    f"👑 **{ceiling_name}**\n\n"
                    f"Сотрудник {self.target.mention} уже занимает ранг **{self.target_rank_name}**.\n"
                    f"Для дальнейшего повышения обратитесь к Высшей Администрации."
                ),
                color=COLOR_WARNING,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        eligible_ranks = [
            (idx, r_id, r_name)
            for idx, (r_id, r_name) in enumerate(STAFF_HIERARCHY)
            if ceiling_idx <= idx < self.target_idx
        ]
        if not eligible_ranks:
            await interaction.response.send_message("❌ Нет доступных должностей для повышения.", ephemeral=True)
            return

        view = PromoteRankSelectView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.target_idx,
            mc_nick=self.mc_nick,
            eligible_ranks=eligible_ranks,
        )
        embed = create_design_embed(
            title="⬆️ ПОВЫШЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"📊 **Текущая должность:** **{self.target_rank_name}**\n\n"
                f"Выберите целевую должность из выпадающего списка ниже:"
            ),
            color=COLOR_PRIMARY,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="⬇️ Понизить", style=discord.ButtonStyle.secondary)
    async def demote_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Понижение Высшей Администрации невозможно.", ephemeral=True)
            return

        if self.target_idx >= len(STAFF_HIERARCHY) - 1:
            await interaction.response.send_message(
                "❌ Сотрудник занимает начальную должность (**Стажер**). Для полного снятия используйте кнопку «Снять с должности».",
                ephemeral=True,
            )
            return

        eligible_ranks = [
            (idx, r_id, r_name)
            for idx, (r_id, r_name) in enumerate(STAFF_HIERARCHY)
            if idx > self.target_idx
        ]
        view = DemoteRankSelectView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.target_idx,
            mc_nick=self.mc_nick,
            eligible_ranks=eligible_ranks,
        )
        embed = create_design_embed(
            title="⬇️ ПОНИЖЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"📊 **Текущая должность:** **{self.target_rank_name}**\n\n"
                f"Выберите новую (более низкую) должность из списка:"
            ),
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="❌ Снять с должности", style=discord.ButtonStyle.danger)
    async def fire_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Снятие Высшей Администрации сервера абсолютно невозможно.", ephemeral=True)
            return

        modal = FireReasonModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            rank_name=self.target_rank_name,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⚖️ Аннулировать наказание", style=discord.ButtonStyle.primary)
    async def deduct_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target.id in HIGHER_ADMIN_IDS or is_higher_admin(self.target.id, interaction.guild.id):
            await interaction.response.send_message("❌ Взаимодействие с Высшей Администрацией невозможно.", ephemeral=True)
            return

        stats = await db.get_staff_stats(interaction.guild_id, self.target.id, fallback_nicks=[self.mc_nick])
        bans_cnt = int(stats.get("bans_issued") or 0) if stats else 0
        mutes_cnt = int(stats.get("mutes_issued") or 0) if stats else 0
        warns_cnt = int(stats.get("warns_issued") or 0) if stats else 0
        active_warns = await db.get_active_warnings(interaction.guild_id, self.target.id)
        recent_punishments = await db.get_recent_punishments_by_moderator(
            guild_id=interaction.guild_id,
            moderator_nick=self.mc_nick,
            moderator_id=self.target.id,
            limit=20,
        )

        total_punishments = bans_cnt + mutes_cnt + warns_cnt + len(active_warns) + len(recent_punishments)
        if total_punishments == 0:
            embed = create_design_embed(
                title="ℹ️ НЕТ НАКАЗАНИЙ ДЛЯ СПИСАНИЯ",
                description=(
                    f"У сотрудника {self.target.mention} (`{self.mc_nick}`) **нет наказаний для списания**.\n\n"
                    f"📊 **Показатели модератора:**\n"
                    f"• Выданных банов: `0`\n"
                    f"• Выданных мутов: `0`\n"
                    f"• Выданных варнов: `0`\n"
                    f"• Активных выговоров: `0`\n\n"
                    f"Списание недоступно, так как все счётчики чисты."
                ),
                color=COLOR_PRIMARY,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = AnnulPunishmentsView(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
            bans_cnt=bans_cnt,
            mutes_cnt=mutes_cnt,
            warns_cnt=warns_cnt,
            active_warns=active_warns,
            recent_punishments=recent_punishments,
        )
        embed = create_design_embed(
            title="⚖️ АННУЛИРОВАНИЕ НАКАЗАНИЙ СОТРУДНИКА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"📊 **Текущие показатели:** Банов: `{bans_cnt}` | Мутов: `{mutes_cnt}` | Выговоров: `{len(active_warns)}`\n\n"
                f"Выберите конкретное наказание из списка ниже для отката и списания:"
            ),
            color=COLOR_PRIMARY,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🎓 Аттестация", style=discord.ButtonStyle.secondary)
    async def attestation_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Check if active attestation exists
        active = await db.get_active_attestation(interaction.guild_id, self.target.id)
        if active:
            embed = create_design_embed(
                title="⚠️ АКТИВНАЯ АТТЕСТАЦИЯ УЖЕ НАЗНАЧЕНА",
                description=(
                    f"У сотрудника {self.target.mention} уже назначена аттестация на должность **{active['target_role_name']}**.\n\n"
                    f"Используйте панель вердикта в профиле сотрудника для подведения итогов."
                ),
                color=COLOR_WARNING,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Check blacklist status
        history = await db.get_candidate_history(interaction.guild_id, self.target.id)
        if history and history.get("is_blacklisted"):
            bl_until = history.get("blacklisted_until")
            embed = create_design_embed(
                title="🚫 СОТРУДНИК В ЧЕРНОМ СПИСКЕ АТТЕСТАЦИЙ",
                description=f"Сотрудник заблокирован для аттестаций до `{bl_until}` из-за 3 неудачных попыток подряд.",
                color=COLOR_DANGER,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Ceiling limit (Curators can schedule up to Gl. Moder - idx 3)
        ceiling_idx = 3
        if self.target_idx <= ceiling_idx:
            embed = create_design_embed(
                title="🛡️ ОГРАНИЧЕНИЕ ПОЛНОМОЧИЙ",
                description=(
                    f"Сотрудник {self.target.mention} уже занимает ранг **{self.target_rank_name}**.\n"
                    f"Аттестации на должности выше Гл. Модератора проводятся только Высшей Администрацией."
                ),
                color=COLOR_WARNING,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        eligible_ranks = [
            (idx, r_id, r_name)
            for idx, (r_id, r_name) in enumerate(STAFF_HIERARCHY)
            if ceiling_idx <= idx < self.target_idx
        ]
        if not eligible_ranks:
            await interaction.response.send_message("❌ Нет доступных должностей для аттестации.", ephemeral=True)
            return

        from cogs.attestation import AttestationRoleSelectView
        view = AttestationRoleSelectView(
            bot=self.bot,
            invoker=self.invoker,
            candidate=self.target,
            eligible_ranks=eligible_ranks,
            mc_nick=self.mc_nick,
        )
        embed = create_design_embed(
            title="🎓 АТТЕСТАЦИЯ СОТРУДНИКА СТАФФА НА ПОВЫШЕНИЕ",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"📊 **Текущая должность:** **{self.target_rank_name}**\n\n"
                f"Выберите желаемую целевую должность для проведения аттестации:"
            ),
            color=COLOR_PRIMARY,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



# =========================================================================
# WEEKLY ONLINE RESET BUTTON VIEW
# =========================================================================
class ResetWeeklyOnlineView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="🔄 Сбросить недельный онлайн", style=discord.ButtonStyle.danger, custom_id="flory_reset_weekly_online")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_warn_access(interaction.user) and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message(
                "❌ Только Кураторы и Высшая Администрация могут сбросить недельный онлайн.",
                ephemeral=True,
            )
            return

        cnt = await db.reset_weekly_staff_online(self.guild_id)
        button.disabled = True
        button.label = "✅ Недельный онлайн сброшен"
        button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

        embed = create_security_embed(
            title="🔄 НЕДЕЛЬНЫЙ ОНЛАЙН СБРОШЕН",
            description=f"Куратор {interaction.user.mention} сбросил недельный онлайн для всех сотрудников ({cnt} записей).\nНачался новый отчетный период.",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed)
        await self.bot.send_channel_log(interaction.guild, CURATORS_AUDIT_CHANNEL_ID, embed)


# =========================================================================
# PUNISHMENT PROOF & AUDIT VIEWS
# =========================================================================
class PunishmentProofRequestView(discord.ui.View):
    """View attached under every MUTE and BAN log with a button to request proof."""
    def __init__(self, punish_id: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="❓ Запросить доказательства",
                style=discord.ButtonStyle.secondary,
                custom_id=f"flory_req_proof:{punish_id}",
            )
        )


class PunishmentProofSubmitView(discord.ui.View):
    """View shown after proof has been requested with a button to submit proof."""
    def __init__(self, punish_id: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="📎 Прикрепить доказательства",
                style=discord.ButtonStyle.primary,
                custom_id=f"flory_submit_proof:{punish_id}",
            )
        )


class PunishmentProofDoneView(discord.ui.View):
    """View shown when proof has been successfully attached."""
    def __init__(self, proof_url: str):
        super().__init__(timeout=None)
        is_valid_url = bool(re.match(r"^https?://", proof_url, re.IGNORECASE))
        if is_valid_url:
            self.add_item(
                discord.ui.Button(
                    label="📎 Доказательства прикреплены",
                    style=discord.ButtonStyle.link,
                    url=proof_url,
                )
            )
        else:
            self.add_item(
                discord.ui.Button(
                    label="✅ Доказательства прикреплены",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
            )


class SubmitProofModal(discord.ui.Modal, title="📎 Предоставление доказательств"):
    def __init__(self, bot: commands.Bot, punish_id: int, punishment: dict):
        super().__init__()
        self.bot = bot
        self.punish_id = punish_id
        self.punishment = punishment

        self.url_input = discord.ui.TextInput(
            label="Ссылка на скриншот / видеозапись",
            style=discord.TextStyle.short,
            placeholder="https://imgur.com/... или https://youtu.be/...",
            min_length=7,
            max_length=250,
            required=True,
        )
        self.comment_input = discord.ui.TextInput(
            label="Комментарий / Пункт правил / Таймкод",
            style=discord.TextStyle.paragraph,
            placeholder="Пункт 2.4 (оскорбления), таймкод 0:35...",
            min_length=0,
            max_length=400,
            required=False,
        )
        self.add_item(self.url_input)
        self.add_item(self.comment_input)

    async def on_submit(self, interaction: discord.Interaction):
        proof_url = self.url_input.value.strip()
        comment = self.comment_input.value.strip()

        if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", proof_url, re.IGNORECASE):
            await interaction.response.send_message(
                "❌ Пожалуйста, укажите корректную ссылку на доказательства (начинающуюся с `http://` или `https://`).",
                ephemeral=True,
            )
            return

        guild = interaction.guild or (self.bot.guilds[0] if self.bot.guilds else None)
        guild_id = guild.id if guild else int(self.punishment.get("guild_id", 0))

        await db.submit_punishment_proof(self.punish_id, guild_id, proof_url, comment)

        # Update original message if interaction occurred in guild channel
        try:
            msg = interaction.message
            if msg and msg.embeds:
                orig_embed = msg.embeds[0]
                new_embed = orig_embed.copy()
                new_embed.add_field(
                    name="📎 Предоставленные доказательства",
                    value=f"[Нажмите для просмотра записи/скриншота]({proof_url})\n*{comment or 'Без комментария'}*",
                    inline=False,
                )
                done_view = PunishmentProofDoneView(proof_url)
                await msg.edit(embed=new_embed, view=done_view)
        except Exception as e:
            logger.warning(f"Could not edit message with proof: {e}")

        # Post audit embed to Curators channel
        if guild:
            target_nick = self.punishment.get("target_nick", "Unknown")
            mod_nick = self.punishment.get("moderator_nick", "Unknown")
            p_type = self.punishment.get("punishment_type", "НАКАЗАНИЕ")
            audit_embed = create_security_embed(
                title="✅ ПРЕДОСТАВЛЕНЫ ДОКАЗАТЕЛЬСТВА",
                description=(
                    f"👤 **Модератор:** `{mod_nick}` ({interaction.user.mention})\n"
                    f"🎯 **Игрок:** `{target_nick}`\n"
                    f"⚡ **Наказание:** `{p_type}`\n"
                    f"🔗 **Доказательства:** [Смотреть материалы]({proof_url})\n"
                    f"📝 **Комментарий:** `{comment or 'Отсутствует'}`"
                ),
                color=COLOR_SUCCESS,
            )
            await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        await interaction.response.send_message(
            f"✅ Доказательства по наказанию `#{self.punish_id}` успешно сохранены и отправлены на проверку.",
            ephemeral=True,
        )


# =========================================================================
# MAIN STAFF COG
# =========================================================================
class StaffCog(commands.Cog, name="Staff"):
    """
    ⚡ FloryMine Staff System:
    - /staff-profile: Dynamic graphic profile card + Interactive Curator Controls.
    - /staff-norm: Configure daily online hours norm per staff rank.
    - /staff-sync: Manual sync for staff member statistics.
    - Anti-Abuse Punishment Validation & Alt-Detection.
    - Weekly Staff Digest with automatic ranking and debtors list.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_digest_week: Optional[str] = None
        self.weekly_digest_task.start()

    def cog_unload(self):
        self.weekly_digest_task.cancel()

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

    @staticmethod
    async def _delayed_delete_message(message: discord.Message, delay: int = 15):
        """Silently deletes a bridge command packet after `delay` seconds if not already deleted by server."""
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _dispatch_mc_command(self, guild: discord.Guild, command: str) -> bool:
        """Sends an execution packet (__FLORY_EXEC__) to Minecraft through the bridge log channel."""
        guild_cfg = AUTHORIZED_GUILDS.get(guild.id, {})
        log_channel_id = guild_cfg.get("log_channel_id", 1391723887846490224)
        channel = guild.get_channel(log_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(log_channel_id)
            except Exception:
                channel = None

        if channel and isinstance(channel, discord.TextChannel):
            try:
                msg = await channel.send(f"__FLORY_EXEC__ {command}")
                logger.info(f"Dispatched MC command to bridge: {command}")
                # Auto-delete packet if Minecraft bridge hasn't deleted it after 15 seconds
                asyncio.create_task(self._delayed_delete_message(msg, delay=15))
                return True
            except Exception as e:
                logger.error(f"Failed to dispatch MC command '{command}': {e}")
        return False

    async def _dispatch_sync_rollback(
        self,
        guild: discord.Guild,
        punishment_type: str,
        moderator_nick: str,
        target_nick: str,
        reason: str,
    ) -> bool:
        """Sends a ROLLBACK event (__FLORY_SYNC__) to the bridge log channel."""
        guild_cfg = AUTHORIZED_GUILDS.get(guild.id, {})
        log_channel_id = guild_cfg.get("log_channel_id", 1391723887846490224)
        channel = guild.get_channel(log_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(log_channel_id)
            except Exception:
                channel = None

        if channel and isinstance(channel, discord.TextChannel):
            try:
                packet = {
                    "action": "ROLLBACK",
                    "guild_id": guild.id,
                    "punishment_type": punishment_type.upper(),
                    "moderator_nick": moderator_nick,
                    "target_nick": target_nick,
                    "reason": reason,
                }
                msg = await channel.send(f"__FLORY_SYNC__ {json.dumps(packet, ensure_ascii=False)}")
                logger.info(f"Dispatched ROLLBACK packet to bridge: {packet}")
                # Auto-delete rollback packet after 20 seconds
                asyncio.create_task(self._delayed_delete_message(msg, delay=20))
                return True
            except Exception as e:
                logger.error(f"Failed to dispatch ROLLBACK packet: {e}")
        return False

    async def _process_sync_payload(self, raw_text: str, default_guild_id: int) -> bool:
        """Parses __FLORY_SYNC__ JSON packet from FloryModerators, validates anti-abuse, and logs actions."""
        if not raw_text or not raw_text.startswith(SYNC_PREFIX):
            return False
        json_part = raw_text[len(SYNC_PREFIX):].strip()
        try:
            payload = json.loads(json_part)
            items = payload if isinstance(payload, list) else [payload]
            guild = self.bot.get_guild(default_guild_id)

            for item in items:
                action = (item.get("action") or "").upper()
                guild_id = int(item.get("guild_id") or default_guild_id)
                target_guild = self.bot.get_guild(guild_id) or guild

                # ---------------------------------------------------------
                # 1. SPECIFIC PUNISHMENT EVENT (BAN, MUTE, WARN, KICK)
                # ---------------------------------------------------------
                if action == "PUNISHMENT":
                    punishment_type = (item.get("punishment_type") or "BAN").upper()
                    moderator_nick = item.get("moderator_nick") or "Console"
                    moderator_id = int(item.get("moderator_id") or 0)
                    moderator_ip = item.get("moderator_ip") or ""
                    target_nick = item.get("target_nick") or "Unknown"
                    target_ip = item.get("target_ip") or ""
                    reason = item.get("reason") or "Нарушение правил"
                    duration_seconds = int(item.get("duration_seconds", 0))

                    # Fallback check for known moderator ID
                    if not moderator_id and moderator_nick.strip().lower() == "devil_first_more":
                        moderator_id = 1398717669607473254

                    # 1.1 Alt-Detection Check
                    is_alt = _is_same_network_or_alt(moderator_ip, target_ip)

                    # 1.2 Duration Threshold Check
                    is_valid_for_stats = 1
                    invalid_reason = ""
                    if is_alt:
                        is_valid_for_stats = 0
                        invalid_reason = "Сетевой абуз (одинаковый IP / подсеть)"
                    elif punishment_type == "MUTE" and duration_seconds < MUTE_MIN_VALID_SECONDS:
                        is_valid_for_stats = 0
                        invalid_reason = f"Срок мута меньше 15 минут ({duration_seconds} сек.)"
                    elif punishment_type == "BAN" and 0 < duration_seconds < BAN_MIN_VALID_SECONDS:
                        is_valid_for_stats = 0
                        invalid_reason = f"Срок бана меньше 7 дней ({duration_seconds // 86400} дн.)"

                    # 1.3 Insert into punishment_logs table
                    audit_req = 0

                    punish_id = await db.log_punishment(
                        guild_id=guild_id,
                        moderator_id=moderator_id,
                        moderator_nick=moderator_nick,
                        target_nick=target_nick,
                        target_ip=target_ip,
                        punishment_type=punishment_type,
                        reason=reason,
                        duration_seconds=duration_seconds,
                        is_valid_for_stats=is_valid_for_stats,
                        audit_requested=audit_req,
                    )

                    # 1.4 If valid, increment moderator's counters
                    if is_valid_for_stats == 1:
                        if punishment_type == "BAN":
                            await db.adjust_staff_stats(guild_id, user_id=moderator_id, mc_nick=moderator_nick, bans_delta=1)
                        elif punishment_type == "MUTE":
                            await db.adjust_staff_stats(guild_id, user_id=moderator_id, mc_nick=moderator_nick, mutes_delta=1)
                        elif punishment_type == "WARN":
                            await db.adjust_staff_stats(guild_id, user_id=moderator_id, mc_nick=moderator_nick, warns_delta=1)

                    dur_text = format_duration_seconds(duration_seconds)

                    # 1.5 Alt-Detection Alert to #curators-audit
                    if is_alt and target_guild:
                        alert_embed = create_security_embed(
                            title="🚨 ПОДОЗРЕНИЕ НА НАКРУТКУ (Alt-Detection)",
                            description=(
                                f"👤 **Модератор:** `{moderator_nick}`\n"
                                f"🎯 **Цель:** `{target_nick}`\n"
                                f"🌐 **Совпадение IP/Сети:** `{target_ip}`\n"
                                f"⚡ **Действие:** `{punishment_type}`\n"
                                f"⚠️ **Статус:** Наказание отклонено и **НЕ** засчитано в статистику стаффа!"
                            ),
                            color=COLOR_DANGER,
                        )
                        await self.bot.send_channel_log(target_guild, CURATORS_AUDIT_CHANNEL_ID, alert_embed)

                    # 1.6 Send to dedicated logging channels (with interactive proof request view for Mute & Ban)
                    if target_guild:
                        status_badge = "✅ Засчитано в статистику" if is_valid_for_stats == 1 else f"❌ Не засчитано ({invalid_reason})"
                        if punishment_type == "MUTE":
                            mute_embed = create_security_embed(
                                title="🔇 БЛОКИРОВКА ЧАТА (МУТ)",
                                description=(
                                    f"🎯 **Игрок:** `{target_nick}`\n"
                                    f"👤 **Модератор:** `{moderator_nick}`\n"
                                    f"📌 **Причина:** `{reason}`\n"
                                    f"⏳ **Длительность:** `{dur_text}`\n"
                                    f"📊 **Статус:** {status_badge}"
                                ),
                                color=COLOR_WARNING if is_valid_for_stats == 1 else COLOR_PRIMARY,
                            )
                            proof_view = PunishmentProofRequestView(punish_id=punish_id) if punish_id else None
                            await self.bot.send_channel_log(target_guild, MUTES_LOG_CHANNEL_ID, mute_embed, view=proof_view)

                        elif punishment_type == "BAN":
                            ban_embed = create_security_embed(
                                title="🔨 БЛОКИРОВКА АККАУНТА (БАН)",
                                description=(
                                    f"🎯 **Игрок:** `{target_nick}`\n"
                                    f"👤 **Модератор:** `{moderator_nick}`\n"
                                    f"📌 **Причина:** `{reason}`\n"
                                    f"⏳ **Срок:** `{dur_text}`\n"
                                    f"📊 **Статус:** {status_badge}"
                                ),
                                color=COLOR_DANGER if is_valid_for_stats == 1 else COLOR_PRIMARY,
                            )
                            proof_view = PunishmentProofRequestView(punish_id=punish_id) if punish_id else None
                            await self.bot.send_channel_log(target_guild, BANS_LOG_CHANNEL_ID, ban_embed, view=proof_view)

                        elif punishment_type == "WARN":
                            warn_embed = create_security_embed(
                                title="⚠️ ПРЕДУПРЕЖДЕНИЕ ИГРОКУ (ВАРН)",
                                description=(
                                    f"🎯 **Игрок:** `{target_nick}`\n"
                                    f"👤 **Модератор:** `{moderator_nick}`\n"
                                    f"📌 **Причина:** `{reason}`\n"
                                    f"📊 **Статус:** {status_badge}"
                                ),
                                color=COLOR_WARNING,
                            )
                            await self.bot.send_channel_log(target_guild, WARNS_LOG_CHANNEL_ID, warn_embed)

                # ---------------------------------------------------------
                # 2. ROLLBACK / UNBAN / UNMUTE EVENT
                # ---------------------------------------------------------
                elif action in ("ROLLBACK", "UNBAN", "UNMUTE"):
                    punishment_type = (item.get("punishment_type") or ("BAN" if action == "UNBAN" else "MUTE")).upper()
                    moderator_nick = item.get("moderator_nick") or "Unknown"
                    target_nick = item.get("target_nick") or "Unknown"
                    reason = item.get("reason") or "Снятие наказания / ошибка"

                    if punishment_type == "BAN":
                        await db.adjust_staff_stats(guild_id, mc_nick=moderator_nick, bans_delta=-1)
                    elif punishment_type == "MUTE":
                        await db.adjust_staff_stats(guild_id, mc_nick=moderator_nick, mutes_delta=-1)

                    if target_guild:
                        rollback_embed = create_security_embed(
                            title=f"🔄 ОТКАТ НАКАЗАНИЯ ({punishment_type})",
                            description=(
                                f"🎯 **Игрок:** `{target_nick}`\n"
                                f"👤 **Модератор:** `{moderator_nick}`\n"
                                f"📝 **Причина отката:** `{reason}`\n"
                                f"📉 **Статистика:** Списано 1 наказание у `{moderator_nick}`."
                            ),
                            color=COLOR_SUCCESS,
                        )
                        await self.bot.send_channel_log(target_guild, CURATORS_AUDIT_CHANNEL_ID, rollback_embed)

                # ---------------------------------------------------------
                # 3. REGULAR ONLINE & STATS SYNC
                # ---------------------------------------------------------
                else:
                    user_id = int(item.get("user_id") or 0)
                    mc_nick = item.get("minecraft_nick")
                    if not user_id and mc_nick and mc_nick.strip().lower() == "devil_first_more":
                        user_id = 1398717669607473254

                    await db.upsert_staff_stats(
                        guild_id=guild_id,
                        user_id=user_id,
                        minecraft_nick=mc_nick,
                        primary_group=item.get("primary_group"),
                        online_seconds=int(item.get("online_seconds", 0)),
                        norm_seconds=int(item.get("norm_seconds", 0)),
                        warns_issued=int(item.get("warns_issued", 0)),
                        bans_issued=int(item.get("bans_issued", 0)),
                        reports_accepted=int(item.get("reports_accepted", 0)),
                        mutes_issued=int(item.get("mutes_issued", 0)),
                    )
            return True
        except Exception as e:
            logger.warning(f"Failed to parse __FLORY_SYNC__ packet: {e}", exc_info=True)
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Real-time Cloud Sync listener for FloryModerators packets."""
        if not message.guild or not message.content:
            return
        if message.content.startswith(SYNC_PREFIX):
            await self._process_sync_payload(message.content, message.guild.id)
            try:
                await message.delete()
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Dynamic interaction listener for punishment proof request & submission buttons."""
        if interaction.type != discord.InteractionType.component:
            return
        if interaction.response.is_done():
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id:
            return

        # ---------------------------------------------------------------------
        # 1. REQUEST PROOF BUTTON (Curators, Admins, Higher Admins, Head Moders, 'Отвечает | Staff')
        # ---------------------------------------------------------------------
        if custom_id.startswith("flory_req_proof:"):
            if not isinstance(interaction.user, discord.Member) or not can_request_punishment_proof(interaction.user):
                await interaction.response.send_message(
                    "❌ У вас нет прав запрашивать доказательства по данному наказанию.\n"
                    "Данное действие доступно только Кураторам, Администрации, Гл. Модераторам и сотрудникам с ролью `Отвечает | Staff`.",
                    ephemeral=True,
                )
                return

            try:
                punish_id = int(custom_id.split(":")[1])
            except (IndexError, ValueError):
                await interaction.response.send_message("❌ Некорректный ID наказания.", ephemeral=True)
                return

            guild_id = interaction.guild.id if interaction.guild else (self.bot.guilds[0].id if self.bot.guilds else 0)
            punishment = await db.get_punishment_by_id(punish_id, guild_id)
            if not punishment:
                await interaction.response.send_message("❌ Запись о наказании не найдена в базе данных.", ephemeral=True)
                return

            # Check if proof already requested
            if punishment.get("audit_requested") and punishment.get("proof_requested_by"):
                req_by = punishment.get("proof_requested_by")
                await interaction.response.send_message(
                    f"⚠️ Доказательства по данному наказанию уже были запрошены ранее (<@{req_by}>).",
                    ephemeral=True,
                )
                return

            # Persist proof request in database
            await db.request_punishment_proof(punish_id, guild_id, interaction.user.id)

            # Update original log message in-place
            msg = interaction.message
            if msg and msg.embeds:
                orig_embed = msg.embeds[0]
                new_embed = orig_embed.copy()
                new_embed.add_field(
                    name="❓ Запрос доказательств",
                    value=f"Запросил: {interaction.user.mention}\nСтатус: ⏳ Ожидание материалов от модератора",
                    inline=False,
                )
                await msg.edit(embed=new_embed, view=PunishmentProofSubmitView(punish_id))

            await interaction.response.send_message(
                f"✅ Вы успешно запросили доказательства по наказанию `#{punish_id}`.",
                ephemeral=True,
            )

            # Retrieve punishment details
            mod_id = int(punishment.get("moderator_id") or 0)
            mod_nick = punishment.get("moderator_nick", "Unknown")
            target_nick = punishment.get("target_nick", "Unknown")
            p_type = punishment.get("punishment_type", "НАКАЗАНИЕ")
            reason = punishment.get("reason", "Без причины")
            guild = interaction.guild or (self.bot.guilds[0] if self.bot.guilds else None)

            # Lookup Discord ID from staff stats by nickname if not present
            if not mod_id and guild:
                staff_row = await db.get_staff_stats(guild.id, 0, fallback_nicks=[mod_nick])
                if staff_row and staff_row.get("user_id"):
                    mod_id = int(staff_row["user_id"])

            # Send private message to moderator if found
            if guild and mod_id:
                mod_member = guild.get_member(mod_id)
                if mod_member:
                    dm_embed = create_security_embed(
                        title="⚠️ ЗАПРОС ДОКАЗАТЕЛЬСТВ ПО НАКАЗАНИЮ",
                        description=(
                            f"Здравствуйте, {mod_member.mention}!\n\n"
                            f"Куратор / Администратор {interaction.user.mention} запросил у вас доказательства (видеозапись или скриншот) по выданному наказанию:\n"
                            f"⚡ **Тип:** `{p_type}` (ID: `#{punish_id}`)\n"
                            f"🎯 **Игрок:** `{target_nick}`\n"
                            f"📌 **Причина:** `{reason}`\n\n"
                            f"Нажмите на кнопку ниже, чтобы отправить ссылку на доказательства."
                        ),
                        color=COLOR_WARNING,
                    )
                    try:
                        await mod_member.send(embed=dm_embed, view=PunishmentProofSubmitView(punish_id))
                    except Exception as dm_err:
                        logger.warning(f"Could not send DM to moderator {mod_id}: {dm_err}")

            # Send audit notification to CURATORS_AUDIT_CHANNEL_ID (1391723887846490224)
            if guild:
                mod_mention = f"<@{mod_id}>" if mod_id else f"`{mod_nick}`"
                curators_embed = create_security_embed(
                    title="❓ ЗАПРОШЕНЫ ДОКАЗАТЕЛЬСТВА",
                    description=(
                        f"👮 **Запросил:** {interaction.user.mention}\n"
                        f"👤 **Модератор:** {mod_mention} (`{mod_nick}`)\n"
                        f"🎯 **Игрок:** `{target_nick}`\n"
                        f"⚡ **Наказание:** `{p_type}` (ID: `#{punish_id}`)\n"
                        f"📌 **Причина:** `{reason}`"
                    ),
                    color=COLOR_WARNING,
                )
                await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, curators_embed)

        # ---------------------------------------------------------------------
        # 2. SUBMIT PROOF BUTTON (Moderator who issued punishment or Staff Management)
        # ---------------------------------------------------------------------
        elif custom_id.startswith("flory_submit_proof:"):
            try:
                punish_id = int(custom_id.split(":")[1])
            except (IndexError, ValueError):
                await interaction.response.send_message("❌ Некорректный ID наказания.", ephemeral=True)
                return

            guild_id = interaction.guild.id if interaction.guild else (self.bot.guilds[0].id if self.bot.guilds else 0)
            punishment = await db.get_punishment_by_id(punish_id, guild_id)
            if not punishment:
                await interaction.response.send_message("❌ Запись о наказании не найдена в базе данных.", ephemeral=True)
                return

            mod_id = int(punishment.get("moderator_id") or 0)
            mod_nick = punishment.get("moderator_nick", "Unknown")
            is_mod = (interaction.user.id == mod_id)
            is_staff_mgmt = isinstance(interaction.user, discord.Member) and can_request_punishment_proof(interaction.user)

            if not is_mod and not is_staff_mgmt:
                # Check if current user is linked to moderator nick
                staff_row = await db.get_staff_stats(guild_id, interaction.user.id, fallback_nicks=[mod_nick])
                if staff_row and (staff_row.get("minecraft_nick") or "").lower() == mod_nick.lower():
                    is_mod = True

            if not is_mod and not is_staff_mgmt:
                await interaction.response.send_message(
                    "❌ Только модератор, выдавший наказание, или руководство стаффа может прикрепить доказательства.",
                    ephemeral=True,
                )
                return

            modal = SubmitProofModal(bot=self.bot, punish_id=punish_id, punishment=punishment)
            await interaction.response.send_modal(modal)

    async def _ingest_pending_sync_messages(self, guild: discord.Guild):
        """Checks the guild log channel for any unprocessed __FLORY_SYNC__ packets."""
        guild_cfg = AUTHORIZED_GUILDS.get(guild.id, {})
        log_channel_id = guild_cfg.get("log_channel_id", 1391723887846490224)
        channel = guild.get_channel(log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            async for msg in channel.history(limit=15):
                if msg.content and msg.content.startswith(SYNC_PREFIX):
                    await self._process_sync_payload(msg.content, guild.id)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Could not scan sync history: {e}")

    # =========================================================================
    # WEEKLY AUTO-DIGEST BACKGROUND TASK (SUNDAY 23:59 MSK)
    # =========================================================================
    @tasks.loop(minutes=1)
    async def weekly_digest_task(self):
        msk_now = datetime.now(timezone(timedelta(hours=3)))
        if msk_now.weekday() == 6 and msk_now.hour == 23 and msk_now.minute == 59:
            week_key = msk_now.strftime("%Y-%W")
            if self._last_digest_week == week_key:
                return
            self._last_digest_week = week_key
            for guild_id in AUTHORIZED_GUILDS:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    try:
                        await self.send_weekly_digest(guild)
                    except Exception as e:
                        logger.error(f"Error executing weekly digest for {guild.name}: {e}")

    @weekly_digest_task.before_loop
    async def before_weekly_digest(self):
        await self.bot.wait_until_ready()

    async def send_weekly_digest(self, guild: discord.Guild):
        all_stats = await db.get_all_staff_stats(guild.id)
        if not all_stats:
            return

        # Top 3 staff by online
        top3 = all_stats[:3]
        top_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(top3):
            nick = s.get("minecraft_nick") or (f"<@{s['user_id']}>" if s.get("user_id") else "Неизвестный")
            hrs = round(s.get("online_seconds", 0) / 3600, 1)
            bans = s.get("bans_issued", 0)
            mutes = s.get("mutes_issued", 0)
            reports = s.get("reports_accepted", 0)
            top_lines.append(f"{medals[i]} **{nick}** — `{hrs} ч.` (Баны: `{bans}` | Муты: `{mutes}` | Репорты: `{reports}`)")

        # Debtors list (< 70% weekly norm)
        debtors_lines = []
        for s in all_stats:
            on_sec = s.get("online_seconds", 0)
            norm_sec = s.get("norm_seconds", 0) or int(DEFAULT_DAILY_NORM_HOURS * 3600)
            weekly_norm_sec = norm_sec * 7
            pct = round((on_sec / max(1, weekly_norm_sec)) * 100, 1)
            if pct < 70.0:
                nick = s.get("minecraft_nick") or (f"<@{s['user_id']}>" if s.get("user_id") else "Неизвестный")
                on_hrs = round(on_sec / 3600, 1)
                norm_hrs = round(weekly_norm_sec / 3600, 1)
                grp = s.get("primary_group") or "стафф"
                debtors_lines.append(f"• **{nick}** (`{grp}`): `{on_hrs} ч.` из `{norm_hrs} ч.` (**{pct}%**)")

        desc_parts = [
            "📊 **ИТОГИ НЕДЕЛЬНОЙ АКТИВНОСТИ СТАФФА FLORYMINE**\n",
            "🏆 **ТОП-3 СОТРУДНИКА НЕДЕЛИ:**",
            "\n".join(top_lines) if top_lines else "• Нет данных",
            "",
            "⚠️ **ШТРАФНАЯ ЗОНА (Должники < 70% нормы):**",
            "\n".join(debtors_lines[:15]) if debtors_lines else "🎉 Все сотрудники успешно выполнили норму!",
            "",
            "💡 *Рекомендация: выдать выговоры `/warn add` должникам или нажать кнопку сброса онлайна ниже.*",
        ]

        embed = create_security_embed(
            title="📅 НЕДЕЛЬНЫЙ ДАЙДЖЕСТ СТАФФА",
            description="\n".join(desc_parts),
            color=COLOR_FLORY_ORANGE,
        )

        view = ResetWeeklyOnlineView(self.bot, guild.id)
        audit_ch = guild.get_channel(CURATORS_AUDIT_CHANNEL_ID)
        if audit_ch and isinstance(audit_ch, discord.TextChannel):
            await audit_ch.send(embed=embed, view=view)

    # =========================================================================
    # SLASH COMMANDS
    # =========================================================================
    @app_commands.command(
        name="staff-profile",
        description="⚡ Посмотреть графическую карточку профиля сотрудника Стаффа FloryMine"
    )
    @app_commands.describe(user="Сотрудник стаффа (по умолчанию: вы)")
    async def staff_profile_command(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        if not await self._check_guild_auth(interaction):
            return

        if not isinstance(interaction.user, discord.Member) or not has_staff_access(interaction.user):
            await interaction.response.send_message(
                "❌ Команды стаффа доступны только персоналу сервера (требуется роль Стаффа).",
                ephemeral=True,
            )
            return

        target = user or interaction.user
        if not isinstance(target, discord.Member):
            target = interaction.guild.get_member(target.id) or interaction.user

        await interaction.response.defer(ephemeral=False)

        # Ingest any pending cloud sync messages from FloryModerators
        await self._ingest_pending_sync_messages(interaction.guild)

        # 1. Fetch Minecraft moderation & online stats
        candidate_nicks = [target.name, target.display_name, getattr(target, "global_name", None)]
        if target.id in KNOWN_DISCORD_TO_MC_NICK:
            candidate_nicks.insert(0, KNOWN_DISCORD_TO_MC_NICK[target.id])

        stats = await db.get_staff_stats(interaction.guild.id, target.id, fallback_nicks=candidate_nicks)

        # 2. Determine Staff Rank, Reprimand Limit & Daily Norm
        rank_name, max_warns, _, target_idx = get_member_staff_rank(target)
        role_norm_seconds = get_member_daily_norm_seconds(target)

        # Higher Admin unconditional enforcement (regardless of MC link or donate/other roles)
        if target.id in HIGHER_ADMIN_IDS or is_higher_admin(target.id, interaction.guild.id):
            if is_bot_owner(target.id, interaction.guild.id):
                rank_name = "Владелец"
                target_idx = -2
            else:
                rank_name = "Высшая Администрация"
                target_idx = -1
            max_warns = DEFAULT_STAFF_MAX_WARNS
            is_staff = True
            is_trainee = False
        else:
            mc_group = (stats.get("primary_group") or "").lower() if stats else ""
            if not target.bot and mc_group in MC_GROUP_TO_RANK:
                mc_rank_name, mc_role_id = MC_GROUP_TO_RANK[mc_group]
                rank_name = mc_rank_name
                max_warns = TRAINEE_MAX_WARNS if mc_role_id == TRAINEE_ROLE_ID else DEFAULT_STAFF_MAX_WARNS
                role_norm_seconds = int(STAFF_ROLE_DAILY_NORM_HOURS.get(mc_role_id, 3.0) * 3600)
                target_idx = 7 if mc_role_id == TRAINEE_ROLE_ID else 5

            is_staff = (not target.bot) and (rank_name != "Участник") and (target_idx < 100)
            is_trainee = is_staff and ((rank_name == "Стажер") or (target_idx == 7) or (max_warns == TRAINEE_MAX_WARNS))

        active_warns = await db.get_active_warn_count(interaction.guild.id, target.id) if is_staff else 0

        # Calculate days in staff (for staff) or days on server (for regular players)
        now_utc = datetime.now(timezone.utc)
        joined_at = target.joined_at if hasattr(target, "joined_at") and target.joined_at else None
        created_at = target.created_at if hasattr(target, "created_at") and target.created_at else now_utc

        if is_staff:
            staff_since = None
            admin_rec = await db.get_admin(interaction.guild.id, target.id)
            if admin_rec and admin_rec.get("added_at"):
                try:
                    staff_since = datetime.fromisoformat(admin_rec["added_at"])
                except Exception:
                    pass
            if not staff_since and stats and stats.get("updated_at"):
                try:
                    staff_since = datetime.fromisoformat(stats["updated_at"])
                except Exception:
                    pass
            if not staff_since:
                staff_since = joined_at or created_at

            if staff_since.tzinfo is None:
                staff_since = staff_since.replace(tzinfo=timezone.utc)
            days_count = max(1, (now_utc - staff_since).days)
        else:
            base_date = joined_at or created_at
            if base_date.tzinfo is None:
                base_date = base_date.replace(tzinfo=timezone.utc)
            days_count = max(1, (now_utc - base_date).days)

        display_nick = (
            (stats.get("minecraft_nick") if stats and stats.get("minecraft_nick") else None)
            or KNOWN_DISCORD_TO_MC_NICK.get(target.id)
            or target.name
        )
        online_seconds = int(stats.get("online_seconds", 0)) if stats else 0
        norm_seconds = int(stats["norm_seconds"]) if (stats and stats.get("norm_seconds")) else role_norm_seconds
        warns_issued = int(stats.get("warns_issued") or 0) if (stats and is_staff) else (0 if is_staff else None)
        bans_issued = int(stats.get("bans_issued") or 0) if (stats and is_staff) else (0 if is_staff else None)
        reports_accepted = int(stats.get("reports_accepted") or 0) if (stats and is_staff) else (0 if is_staff else None)
        mutes_issued = int(stats.get("mutes_issued") or 0) if (stats and is_staff) else (0 if is_staff else None)

        # 3. Download user's Discord avatar
        avatar_bytes = None
        for member_for_avatar in (target, interaction.user):
            try:
                avatar_bytes = await member_for_avatar.display_avatar.with_format("png").with_size(512).read()
                if avatar_bytes:
                    break
            except Exception as e:
                logger.warning(f"Failed to download avatar for {member_for_avatar.id}: {e}")

        # 4. Check if caller can see curator controls (Viewing someone else's profile)
        control_view = None
        attestation_banner_embed = None

        if target.id != interaction.user.id and (has_warn_access(interaction.user) or is_senior_admin(interaction.user.id, interaction.guild.id)):
            # Higher Administration is totally immune: interactions and removals are strictly impossible
            if target.id in HIGHER_ADMIN_IDS or is_higher_admin(target.id, interaction.guild.id):
                control_view = None
                attestation_banner_embed = None
            else:
                _, _, _, caller_idx = get_member_staff_rank(interaction.user)

                # A. Check if target has an active pending attestation
                active_att = await db.get_active_attestation(interaction.guild.id, target.id)

                # B. Check if target is blacklisted from attestations (3-Strike Rule)
                history = await db.get_candidate_history(interaction.guild.id, target.id)
                is_blacklisted = False
                bl_str = ""
                if history and history.get("is_blacklisted"):
                    bl_until_iso = history.get("blacklisted_until")
                    if bl_until_iso:
                        try:
                            bl_dt = datetime.fromisoformat(bl_until_iso)
                            if bl_dt.tzinfo is None:
                                bl_dt = bl_dt.replace(tzinfo=timezone.utc)
                            if bl_dt > datetime.now(timezone.utc):
                                is_blacklisted = True
                                from utils.time_parser import format_msk
                                bl_str = format_msk(bl_dt)
                        except Exception:
                            is_blacklisted = True
                            bl_str = str(bl_until_iso)

                if active_att:
                    from cogs.attestation import AttestationVerdictView
                    from utils.time_parser import format_msk, to_discord_timestamp
                    control_view = AttestationVerdictView(
                        bot=self.bot,
                        cog=self,
                        invoker=interaction.user,
                        candidate=target,
                        attestation=active_att,
                        mc_nick=display_nick,
                    )
                    try:
                        sch_dt = datetime.fromisoformat(active_att["scheduled_at"])
                        if sch_dt.tzinfo is None:
                            sch_dt = sch_dt.replace(tzinfo=timezone.utc)
                        sch_text = f"`{format_msk(sch_dt)}` ({to_discord_timestamp(sch_dt, 'R')})"
                    except Exception:
                        sch_text = f"`{active_att['scheduled_at']}`"

                    attestation_banner_embed = create_design_embed(
                        title="🎓 НАЗНАЧЕНА АКТИВНАЯ АТТЕСТАЦИЯ",
                        description=(
                            f"👤 **Кандидат:** {target.mention} (`{display_nick}`)\n"
                            f"🏷️ **Должность:** **{active_att['target_role_name']}**\n"
                            f"🎙️ **Канал:** <#{active_att['voice_channel_id']}>\n"
                            f"⏱️ **Время:** {sch_text}\n"
                            f"🛡️ **Назначил:** <@{active_att['curator_id']}>\n\n"
                            f"Используйте кнопки ниже для вынесения вердикта по результатам собеседования:"
                        ),
                        color=COLOR_FLORY_ORANGE,
                    )
                elif is_blacklisted:
                    from cogs.attestation import AttestationUnblockView
                    if can_unblock_attestation(interaction.user):
                        control_view = AttestationUnblockView(bot=self.bot, candidate=target)
                    attestation_banner_embed = create_design_embed(
                        title="🚫 КАНДИДАТ В ЧЕРНОМ СПИСКЕ АТТЕСТАЦИЙ",
                        description=(
                            f"Участник {target.mention} получил **3 неудачные попытки подряд** и заблокирован для аттестаций.\n\n"
                            f"⏳ **Срок окончания ЧС:** `{bl_str}`\n"
                            f"👑 Досрочное снятие блокировки доступно только Высшей Администрации сервера."
                        ),
                        color=COLOR_DANGER,
                    )
                elif not is_staff:
                    # Regular user (Ticket King candidate) -> Show [ 📝 Назначить аттестацию ]
                    from cogs.attestation import CandidateAttestationStartView
                    eligible_ranks = [
                        (idx, r_id, r_name)
                        for idx, (r_id, r_name) in enumerate(STAFF_HIERARCHY)
                        if 3 <= idx <= 7
                    ]
                    control_view = CandidateAttestationStartView(
                        bot=self.bot,
                        invoker=interaction.user,
                        candidate=target,
                        mc_nick=display_nick,
                        eligible_ranks=eligible_ranks,
                    )
                elif caller_idx < target_idx and is_staff:
                    # Friendly-fire shield: caller must be strictly higher in rank
                    control_view = CuratorStaffControlView(
                        bot=self.bot,
                        cog=self,
                        invoker=interaction.user,
                        target=target,
                        target_rank_name=rank_name,
                        target_idx=target_idx,
                        mc_nick=display_nick,
                    )

        # 5. Render profile card in background thread
        try:
            image_buffer = await asyncio.to_thread(
                generate_staff_profile_card,
                username=display_nick,
                rank_name=rank_name,
                avatar_bytes=avatar_bytes,
                active_warns=active_warns,
                max_warns=max_warns,
                online_seconds=online_seconds,
                norm_seconds=norm_seconds,
                days_count=days_count,
                bans_issued=bans_issued,
                reports_accepted=reports_accepted,
                mutes_issued=mutes_issued,
                is_staff=is_staff,
                is_trainee=is_trainee,
            )
            file = discord.File(fp=image_buffer, filename=f"staff_profile_{target.id}.png")
            kwargs = {"file": file}
            if attestation_banner_embed:
                kwargs["embed"] = attestation_banner_embed
            if control_view:
                kwargs["view"] = control_view
            await interaction.followup.send(**kwargs)
        except Exception as e:
            logger.error(f"Error generating staff profile card: {e}", exc_info=True)
            await interaction.followup.send("❌ Ошибка при генерации карточки профиля стаффа.", ephemeral=True)

    @app_commands.command(
        name="staff-norm",
        description="⏱️ Настроить дневную норму онлайна (в часах) для должности Стаффа"
    )
    @app_commands.describe(
        role="Роль должности из иерархии стаффа",
        daily_hours="Норма онлайна в часах в день (например: 2.5 = 2 ч. 30 мин.)",
    )
    async def staff_norm_command(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
        daily_hours: Optional[float] = None,
    ):
        if not await self._check_guild_auth(interaction):
            return

        can_run, err_msg = check_moderation_channel(interaction)
        if not can_run:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not has_warn_access(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для настройки нормы часов стаффа (требуется роль <@&1454703598834421831>).",
                ephemeral=True,
            )
            return

        if role is not None and daily_hours is not None:
            if daily_hours <= 0 or daily_hours > 24:
                await interaction.response.send_message(
                    "❌ Укажите корректное количество часов в день (от `0.5` до `24`).",
                    ephemeral=True,
                )
                return

            STAFF_ROLE_DAILY_NORM_HOURS[role.id] = round(daily_hours, 2)
            staff_cfg = CONFIG.setdefault("staff", {})
            hierarchy_list = staff_cfg.get("roles_hierarchy", [])
            updated = False
            for item in hierarchy_list:
                if int(item.get("role_id", 0)) == role.id:
                    item["daily_norm_hours"] = round(daily_hours, 2)
                    updated = True
                    break
            if not updated:
                hierarchy_list.append({
                    "role_id": role.id,
                    "name": role.name,
                    "daily_norm_hours": round(daily_hours, 2),
                })
            save_config(CONFIG)

        lines = []
        for r_id, r_name in STAFF_HIERARCHY:
            hrs = STAFF_ROLE_DAILY_NORM_HOURS.get(r_id, 3.0)
            h_int = int(hrs)
            m_int = int(round((hrs - h_int) * 60))
            time_str = f"{h_int} ч. {m_int} мин." if m_int > 0 else f"{h_int} ч."
            lines.append(f"• <@&{r_id}> (**{r_name}**) — `{hrs} ч./день` ({time_str} = 100%)")

        embed = create_security_embed(
            title="⏱️ ДНЕВНАЯ НОРМА АКТИВНОСТИ СТАФФА",
            description=(
                "Процент **Активности** в `/staff-profile` вычисляется относительно дневной нормы должности:\n\n"
                + "\n".join(lines)
            ),
            color=COLOR_FLORY_ORANGE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="staff-sync",
        description="⚙️ Обновить/привязать игровую статистику сотрудника (Мост FloryModerators)"
    )
    @app_commands.describe(
        user="Сотрудник стаффа",
        minecraft_nick="Привязанный ник в Minecraft",
        online_minutes="Отыгранный онлайн в минутах (например: 275 = 4 ч. 35 мин.)",
        norm_minutes="Индивидуальная норма онлайна в минутах (0 = использовать норму должности)",
        warns_issued="Выдано варнов в игре",
        bans_issued="Выдано банов в игре",
        reports_accepted="Принятые репорты в игре",
        mutes_issued="Выдано мутов в игре",
    )
    async def staff_sync_command(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        minecraft_nick: Optional[str] = None,
        online_minutes: Optional[int] = None,
        norm_minutes: Optional[int] = None,
        warns_issued: Optional[int] = None,
        bans_issued: Optional[int] = None,
        reports_accepted: Optional[int] = None,
        mutes_issued: Optional[int] = None,
    ):
        if not await self._check_guild_auth(interaction):
            return

        can_run, err_msg = check_moderation_channel(interaction)
        if not can_run:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not has_warn_access(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав для редактирования игровой статистики стаффа.",
                ephemeral=True,
            )
            return

        online_sec = online_minutes * 60 if online_minutes is not None else None
        if norm_minutes is not None:
            norm_sec = get_member_daily_norm_seconds(user) if norm_minutes <= 0 else max(60, norm_minutes * 60)
        else:
            norm_sec = None

        await db.upsert_staff_stats(
            guild_id=interaction.guild.id,
            user_id=user.id,
            minecraft_nick=minecraft_nick,
            online_seconds=online_sec,
            norm_seconds=norm_sec,
            warns_issued=warns_issued,
            bans_issued=bans_issued,
            reports_accepted=reports_accepted,
            mutes_issued=mutes_issued,
        )

        embed = create_security_embed(
            title="⚡ ДАННЫЕ СОТРУДНИКА ОБНОВЛЕНЫ",
            description=(
                f"Статистика для {user.mention} успешно синхронизирована.\n"
                f"Проверьте отображение карточки через `/staff-profile`."
            ),
            color=COLOR_FLORY_ORANGE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffCog(bot))
