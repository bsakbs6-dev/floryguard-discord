import asyncio
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
)
from core.permissions import (
    is_authorized_guild,
    has_staff_access,
    has_warn_access,
    is_senior_admin,
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
# MODALS FOR CURATOR CONTROLS IN /staff-profile
# =========================================================================
class PromoteStaffModal(discord.ui.Modal, title="⬆️ Повышение сотрудника Стаффа"):
    reason = discord.ui.TextInput(
        label="Причина повышения",
        placeholder="Например: Отличная работа, закрытие нормы онлайна",
        min_length=5,
        max_length=256,
        required=True,
    )
    confirm = discord.ui.TextInput(
        label="Подтверждение действия",
        placeholder="Введите 'ПОДТВЕРЖДАЮ'",
        min_length=12,
        max_length=12,
        required=True,
    )

    def __init__(self, bot: commands.Bot, cog, invoker: discord.Member, target: discord.Member, current_idx: int, mc_nick: str):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip() != "ПОДТВЕРЖДАЮ":
            await interaction.response.send_message("❌ Действие отменено: неверное подтверждение.", ephemeral=True)
            return

        if self.current_idx <= 0:
            await interaction.response.send_message("❌ Сотрудник уже занимает наивысшую должность.", ephemeral=True)
            return

        new_idx = self.current_idx - 1
        new_role_id, new_rank_name = STAFF_HIERARCHY[new_idx]
        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]

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
            if new_idx <= 2:
                warn_acc = guild.get_role(WARN_ACCESS_ROLE_ID)
                if warn_acc and warn_acc not in self.target.roles:
                    await self.target.add_roles(warn_acc)
        except Exception as e:
            logger.error(f"Failed to update Discord roles on promote: {e}")

        # Sync with Minecraft LuckPerms: add new group, remove old group
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        if self.mc_nick:
            await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent add {new_lp_group}")
            if old_lp_group != new_lp_group:
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {old_lp_group}")

        reason_text = self.reason.value.strip()

        # DM notification to target member
        try:
            dm_embed = create_security_embed(
                title="⚡ ПОВЫШЕНИЕ В ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Поздравляем! Ваша должность в стаффе была повышена.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Новая должность:** **{new_rank_name}**\n"
                    f"📝 **Причина:** `{reason_text}`"
                ),
                color=COLOR_SUCCESS,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_security_embed(
            title="⬆️ ПОВЫШЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Ранг:** `{old_rank_name}` ➔ **`{new_rank_name}`**\n"
                f"🎮 **Группа LuckPerms:** `{lp_group}`\n"
                f"📝 **Причина:** `{reason_text}`"
            ),
            color=COLOR_SUCCESS,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        await interaction.response.send_message(
            f"✅ Сотрудник {self.target.mention} успешно повышен до должности **{new_rank_name}**.",
            ephemeral=True,
        )


class DemoteStaffModal(discord.ui.Modal, title="⬇️ Понижение сотрудника Стаффа"):
    reason = discord.ui.TextInput(
        label="Причина понижения",
        placeholder="Например: Невыполнение нормы, ошибки в модерации",
        min_length=5,
        max_length=256,
        required=True,
    )
    confirm = discord.ui.TextInput(
        label="Подтверждение действия",
        placeholder="Введите 'ПОДТВЕРЖДАЮ'",
        min_length=12,
        max_length=12,
        required=True,
    )

    def __init__(self, bot: commands.Bot, cog, invoker: discord.Member, target: discord.Member, current_idx: int, mc_nick: str):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.current_idx = current_idx
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip() != "ПОДТВЕРЖДАЮ":
            await interaction.response.send_message("❌ Действие отменено: неверное подтверждение.", ephemeral=True)
            return

        if self.current_idx + 1 >= len(STAFF_HIERARCHY):
            await interaction.response.send_message(
                "❌ Сотрудник занимает начальную должность (Стажер). Для полного снятия используйте кнопку «Снять с должности».",
                ephemeral=True,
            )
            return

        new_idx = self.current_idx + 1
        new_role_id, new_rank_name = STAFF_HIERARCHY[new_idx]
        old_role_id, old_rank_name = STAFF_HIERARCHY[self.current_idx]

        guild = interaction.guild
        old_role = guild.get_role(old_role_id)
        new_role = guild.get_role(new_role_id)

        try:
            if old_role and old_role in self.target.roles:
                await self.target.remove_roles(old_role, reason=f"Понижение куратором {self.invoker}")
            if new_role and new_role not in self.target.roles:
                await self.target.add_roles(new_role, reason=f"Понижение куратором {self.invoker}")

            # If demoted below Curator (new_idx > 2), strip warn access
            if new_idx > 2:
                warn_acc = guild.get_role(WARN_ACCESS_ROLE_ID)
                if warn_acc and warn_acc in self.target.roles:
                    await self.target.remove_roles(warn_acc)
        except Exception as e:
            logger.error(f"Failed to update Discord roles on demote: {e}")

        # Sync with Minecraft LuckPerms: add new group, remove old group
        old_lp_group = ROLE_ID_TO_MC_GROUP.get(old_role_id, "intern")
        new_lp_group = ROLE_ID_TO_MC_GROUP.get(new_role_id, "intern")
        if self.mc_nick:
            await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent add {new_lp_group}")
            if old_lp_group != new_lp_group:
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {old_lp_group}")

        reason_text = self.reason.value.strip()

        # DM notification
        try:
            dm_embed = create_security_embed(
                title="⚠️ ПОНИЖЕНИЕ В ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Ваша должность в стаффе была понижена.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Новая должность:** **{new_rank_name}**\n"
                    f"📝 **Причина:** `{reason_text}`"
                ),
                color=COLOR_WARNING,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_security_embed(
            title="⬇️ ПОНИЖЕНИЕ СОТРУДНИКА СТАФФА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Ранг:** `{old_rank_name}` ➔ **`{new_rank_name}`**\n"
                f"🎮 **Группа LuckPerms:** `{lp_group}`\n"
                f"📝 **Причина:** `{reason_text}`"
            ),
            color=COLOR_WARNING,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        await interaction.response.send_message(
            f"⚠️ Сотрудник {self.target.mention} понижен до должности **{new_rank_name}**.",
            ephemeral=True,
        )


class FireStaffModal(discord.ui.Modal, title="❌ Снятие с должности Стаффа"):
    reason = discord.ui.TextInput(
        label="Причина снятия",
        placeholder="Например: Грубое нарушение, неактив, собственное желание",
        min_length=5,
        max_length=256,
        required=True,
    )
    confirm = discord.ui.TextInput(
        label="Подтверждение действия",
        placeholder="Введите 'ПОДТВЕРЖДАЮ'",
        min_length=12,
        max_length=12,
        required=True,
    )

    def __init__(self, bot: commands.Bot, cog, invoker: discord.Member, target: discord.Member, rank_name: str, mc_nick: str):
        super().__init__()
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.rank_name = rank_name
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip() != "ПОДТВЕРЖДАЮ":
            await interaction.response.send_message("❌ Действие отменено: неверное подтверждение.", ephemeral=True)
            return

        guild = interaction.guild
        roles_to_strip = [r for r in self.target.roles if r.id in STAFF_ROLE_IDS or r.id == WARN_ACCESS_ROLE_ID or r.id == STAFF_ACCESS_ROLE_ID]
        try:
            if roles_to_strip:
                await self.target.remove_roles(*roles_to_strip, reason=f"Снятие со стаффа куратором {self.invoker}")
        except Exception as e:
            logger.error(f"Failed to strip staff roles on fire: {e}")

        # Remove LuckPerms staff groups in Minecraft (leaving donate / default groups untouched)
        if self.mc_nick:
            for grp in set(ROLE_ID_TO_MC_GROUP.values()):
                await self.cog._dispatch_mc_command(guild, f"lp user {self.mc_nick} parent remove {grp}")

        reason_text = self.reason.value.strip()

        # DM notification
        try:
            dm_embed = create_security_embed(
                title="🔴 СНЯТИЕ С ДОЛЖНОСТИ • FLORYMINE",
                description=(
                    f"Вы были сняты с должности Стаффа на сервере FloryMine.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"📊 **Бывшая должность:** `{self.rank_name}`\n"
                    f"📝 **Причина:** `{reason_text}`"
                ),
                color=COLOR_DANGER,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_security_embed(
            title="❌ СНЯТИЕ СО СТАФФА (УВОЛЬНЕНИЕ)",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"📊 **Бывшая должность:** `{self.rank_name}`\n"
                f"🎮 **Сброс LuckPerms:** `default`\n"
                f"📝 **Причина:** `{reason_text}`"
            ),
            color=COLOR_DANGER,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        await interaction.response.send_message(
            f"🔴 Сотрудник {self.target.mention} снят со всех должностей стаффа.",
            ephemeral=True,
        )


class DeductStatsModal(discord.ui.Modal, title="🧹 Списание статистики сотрудника"):
    stat_type = discord.ui.TextInput(
        label="Что списать? (баны / муты / варны / всё)",
        placeholder="Напишите: баны, муты, варны или всё",
        min_length=3,
        max_length=20,
        required=True,
    )
    amount = discord.ui.TextInput(
        label="Количество для списания (число)",
        placeholder="Например: 2 (для 'всё' напишите 0)",
        min_length=1,
        max_length=6,
        required=True,
    )
    reason = discord.ui.TextInput(
        label="Причина списания",
        placeholder="Например: Накрутка на друзьях / Ошибочные наказания",
        min_length=5,
        max_length=256,
        required=True,
    )
    confirm = discord.ui.TextInput(
        label="Подтверждение действия",
        placeholder="Введите 'ПОДТВЕРЖДАЮ'",
        min_length=12,
        max_length=12,
        required=True,
    )

    def __init__(self, bot: commands.Bot, invoker: discord.Member, target: discord.Member, mc_nick: str):
        super().__init__()
        self.bot = bot
        self.invoker = invoker
        self.target = target
        self.mc_nick = mc_nick

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip() != "ПОДТВЕРЖДАЮ":
            await interaction.response.send_message("❌ Действие отменено: неверное подтверждение.", ephemeral=True)
            return

        raw_type = self.stat_type.value.strip().lower()
        try:
            num = max(0, int(self.amount.value.strip()))
        except ValueError:
            await interaction.response.send_message("❌ Укажите корректное число для списания.", ephemeral=True)
            return

        guild = interaction.guild
        action_desc = ""

        if "бан" in raw_type or "ban" in raw_type:
            num = max(1, num)
            await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, bans_delta=-num)
            action_desc = f"Списано **{num}** бан(ов)"
        elif "мут" in raw_type or "mute" in raw_type:
            num = max(1, num)
            await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, mutes_delta=-num)
            action_desc = f"Списано **{num}** мут(ов)"
        elif "варн" in raw_type or "warn" in raw_type:
            num = max(1, num)
            warns = await db.get_active_warnings(guild.id, self.target.id)
            cleared_cnt = 0
            for w in warns[:num]:
                if await db.remove_warning(w["id"], guild.id):
                    cleared_cnt += 1
            action_desc = f"Снято **{cleared_cnt}** выговор(ов) из базы данных"
        elif "вс" in raw_type or "all" in raw_type:
            await db.adjust_staff_stats(guild.id, user_id=self.target.id, mc_nick=self.mc_nick, bans_delta=-9999, mutes_delta=-9999, warns_delta=-9999)
            await db.clear_warnings(guild.id, self.target.id)
            action_desc = "Полное обнуление игровой статистики и выговоров"
        else:
            await interaction.response.send_message("❌ Неизвестный тип статистики. Укажите: `баны`, `муты`, `варны` или `всё`.", ephemeral=True)
            return

        reason_text = self.reason.value.strip()

        # DM notification
        try:
            dm_embed = create_security_embed(
                title="📊 КОРРЕКТИРОВКА СТАТИСТИКИ • FLORYMINE",
                description=(
                    f"Ваша статистика модерации была изменена Куратором.\n\n"
                    f"👑 **Куратор:** {self.invoker.mention}\n"
                    f"⚡ **Изменение:** {action_desc}\n"
                    f"📝 **Причина:** `{reason_text}`"
                ),
                color=COLOR_WARNING,
            )
            await self.target.send(embed=dm_embed)
        except Exception:
            pass

        # Log into #curators-audit
        audit_embed = create_security_embed(
            title="🧹 СПИСАНИЕ СТАТИСТИКИ СОТРУДНИКА",
            description=(
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.name}` | MC: `{self.mc_nick}`)\n"
                f"👑 **Куратор:** {self.invoker.mention}\n"
                f"⚡ **Действие:** {action_desc}\n"
                f"📝 **Причина:** `{reason_text}`"
            ),
            color=COLOR_PRIMARY,
        )
        await self.bot.send_channel_log(guild, CURATORS_AUDIT_CHANNEL_ID, audit_embed)

        await interaction.response.send_message(
            f"✅ Статистика для {self.target.mention} скорректирована: {action_desc}.",
            ephemeral=True,
        )


# =========================================================================
# INTERACTIVE CURATOR CONTROL VIEW UNDER /staff-profile
# =========================================================================
class CuratorStaffControlView(discord.ui.View):
    def __init__(self, bot: commands.Bot, cog, invoker: discord.Member, target: discord.Member, target_rank_name: str, target_idx: int, mc_nick: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.cog = cog
        self.invoker = invoker
        self.target = target
        self.target_rank_name = target_rank_name
        self.target_idx = target_idx
        self.mc_nick = mc_nick

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message("❌ Только куратор, открывший профиль, или Владелец может использовать эту панель.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⬆️ Повысить", style=discord.ButtonStyle.success)
    async def promote_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PromoteStaffModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.target_idx,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⬇️ Понизить", style=discord.ButtonStyle.secondary)
    async def demote_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DemoteStaffModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            current_idx=self.target_idx,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Снять с должности", style=discord.ButtonStyle.danger)
    async def fire_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FireStaffModal(
            bot=self.bot,
            cog=self.cog,
            invoker=self.invoker,
            target=self.target,
            rank_name=self.target_rank_name,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🧹 Списать статистику", style=discord.ButtonStyle.primary)
    async def deduct_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DeductStatsModal(
            bot=self.bot,
            invoker=self.invoker,
            target=self.target,
            mc_nick=self.mc_nick,
        )
        await interaction.response.send_modal(modal)


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

    async def _dispatch_mc_command(self, guild: discord.Guild, command: str) -> bool:
        """Sends an execution packet (__FLORY_EXEC__) to Minecraft through the bridge log channel."""
        guild_cfg = AUTHORIZED_GUILDS.get(guild.id, {})
        log_channel_id = guild_cfg.get("log_channel_id", 1391723887846490224)
        channel = guild.get_channel(log_channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"__FLORY_EXEC__ {command}")
                logger.info(f"Dispatched MC command to bridge: {command}")
                return True
            except Exception as e:
                logger.error(f"Failed to dispatch MC command '{command}': {e}")
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
                    if is_valid_for_stats == 1 and punishment_type == "BAN" and random.random() < SPOT_CHECK_PROBABILITY:
                        audit_req = 1

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

                    # 1.6 Spot-Check Alert (~8% random chance for valid BAN)
                    if audit_req == 1 and target_guild:
                        mod_mention = f"<@{moderator_id}>" if moderator_id else f"`{moderator_nick}`"
                        spot_embed = create_security_embed(
                            title="🎲 ВЫБОРОЧНЫЙ АУДИТ НАКАЗАНИЯ",
                            description=(
                                f"👤 **Модератор:** {mod_mention} (`{moderator_nick}`)\n"
                                f"🔨 **Наказан:** `{target_nick}`\n"
                                f"📌 **Причина:** `{reason}`\n"
                                f"⏳ **Срок:** `{dur_text}`\n\n"
                                f"⚠️ **Требование:** Модератор обязан предоставить доказательства (скриншот/видео) в течение 24 часов."
                            ),
                            color=COLOR_WARNING,
                        )
                        await self.bot.send_channel_log(target_guild, CURATORS_AUDIT_CHANNEL_ID, spot_embed)

                        # DM to moderator if found
                        if moderator_id:
                            mod_user = target_guild.get_member(moderator_id)
                            if mod_user:
                                try:
                                    await mod_user.send(embed=spot_embed)
                                except Exception:
                                    pass

                    # 1.7 Send to dedicated logging channels
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
                            await self.bot.send_channel_log(target_guild, MUTES_LOG_CHANNEL_ID, mute_embed)

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
                            await self.bot.send_channel_log(target_guild, BANS_LOG_CHANNEL_ID, ban_embed)

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

        mc_group = (stats.get("primary_group") or "").lower() if stats else ""
        if not target.bot and mc_group in MC_GROUP_TO_RANK:
            mc_rank_name, mc_role_id = MC_GROUP_TO_RANK[mc_group]
            rank_name = mc_rank_name
            max_warns = TRAINEE_MAX_WARNS if mc_role_id == TRAINEE_ROLE_ID else DEFAULT_STAFF_MAX_WARNS
            role_norm_seconds = int(STAFF_ROLE_DAILY_NORM_HOURS.get(mc_role_id, 3.0) * 3600)
            target_idx = 7 if mc_role_id == TRAINEE_ROLE_ID else 5

        is_staff = (not target.bot) and (rank_name != "Участник") and (target_idx < 100)
        active_warns = await db.get_active_warn_count(interaction.guild.id, target.id) if is_staff else 0

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

        # 4. Check if caller can see curator controls (Viewing someone else's profile with superior rank)
        control_view = None
        if target.id != interaction.user.id and (has_warn_access(interaction.user) or is_senior_admin(interaction.user.id, interaction.guild.id)):
            _, _, _, caller_idx = get_member_staff_rank(interaction.user)
            # Friendly-fire shield: caller must be strictly higher in rank (caller_idx < target_idx)
            if caller_idx < target_idx and is_staff:
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
                warns_issued=warns_issued,
                bans_issued=bans_issued,
                reports_accepted=reports_accepted,
                mutes_issued=mutes_issued,
                is_staff=is_staff,
            )
            file = discord.File(fp=image_buffer, filename=f"staff_profile_{target.id}.png")
            if control_view:
                await interaction.followup.send(file=file, view=control_view)
            else:
                await interaction.followup.send(file=file)
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
