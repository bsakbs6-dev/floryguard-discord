import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Literal

from config import UNAUTHORIZED_MESSAGE, MAX_WARNINGS
from core.permissions import (
    is_authorized_guild,
    is_senior_admin,
    is_bot_owner,
    is_admin,
    is_whitelisted
)
from database.db import db
from utils.logger import logger
from utils.embeds import (
    profile_embed,
    create_security_embed,
    warn_dm_embed,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER
)


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
            # Fallback if cannot delete directly
            await interaction.edit_original_response(content="*Профиль закрыт.*", embed=None, view=None)


class ModerationCog(commands.Cog, name="Moderation"):
    """
    Moderation, profile inspection, and warning management commands.
    """
    def __init__(self, bot):
        self.bot = bot

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

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

        # Fetch ranks
        is_owner = is_bot_owner(target_member.id)
        is_sr_adm = is_senior_admin(target_member.id)
        is_adm = await is_admin(interaction.guild.id, target_member.id)
        is_wl = await is_whitelisted(interaction.guild.id, target_member.id)

        # Fetch active warnings
        warnings = await db.get_active_warnings(interaction.guild.id, target_member.id)

        embed = profile_embed(
            member=target_member,
            is_owner=is_owner,
            is_senior_admin=is_sr_adm,
            is_admin=is_adm,
            is_whitelisted=is_wl,
            warnings=warnings
        )

        view = DeleteMessageView(target_user_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==========================================
    # /WARN SYSTEM (ADD / REMOVE / LIST / CLEAR)
    # ==========================================
    warn_group = app_commands.Group(name="warn", description="Управление предупреждениями пользователей")

    @warn_group.command(name="add", description="Выдать предупреждение пользователю (действует 7 дней)")
    @app_commands.describe(user="Кому выдать варн", reason="Причина предупреждения")
    async def warn_add(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not await self._check_guild_auth(interaction):
            return

        # Permissions check: Admins & Senior Admins & Owner
        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ Только **Администраторы безопасности** могут выдавать предупреждения.", ephemeral=True)
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ Вы не можете выдать предупреждение самому себе.", ephemeral=True)
            return

        # Check target immunity
        if is_senior_admin(user.id):
            await interaction.response.send_message("❌ Невозможно выдать варн Высшему Администратору / Владельцу.", ephemeral=True)
            return

        if await is_admin(interaction.guild.id, user.id) and not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы** могут выдавать варны другим администраторам.", ephemeral=True)
            return

        warn_id, warn_count, demoted = await self.bot.issue_warning_and_check(
            guild=interaction.guild,
            member=user,
            reason=reason,
            moderator_id=interaction.user.id
        )

        if demoted:
            desc = (
                f"🔴 **Пользователю {user.mention} выдано предупреждение #{warn_id} ({warn_count}/{MAX_WARNINGS})**\n\n"
                f"⚠️ **Лимит предупреждений превышен!**\n"
                f"С пользователя сняты все роли и выдана роль изоляции."
            )
            color = COLOR_DANGER
        else:
            desc = (
                f"🟡 **Пользователю {user.mention} выдано предупреждение #{warn_id} ({warn_count}/{MAX_WARNINGS})**\n\n"
                f"📌 **Причина:** `{reason}`\n"
                f"⏳ Предупреждение автоматически снимется через 7 дней."
            )
            color = COLOR_WARNING

        embed = create_security_embed(title="⚠️ ВЫДАНО ПРЕДУПРЕЖДЕНИЕ", description=desc, color=color)
        await interaction.response.send_message(embed=embed)
        await self.bot.send_security_log(interaction.guild, embed)

    @warn_group.command(name="remove", description="Снять конкретное предупреждение по ID")
    @app_commands.describe(warn_id="ID предупреждения")
    async def warn_remove(self, interaction: discord.Interaction, warn_id: int):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ Только **Администраторы безопасности** могут снимать предупреждения.", ephemeral=True)
            return

        success = await db.remove_warning(warn_id, interaction.guild.id)
        if success:
            embed = create_security_embed(
                title="✅ ПРЕДУПРЕЖДЕНИЕ СНЯТО",
                description=f"Предупреждение **[ID: {warn_id}]** успешно аннулировано модератором {interaction.user.mention}.",
                color=COLOR_SUCCESS
            )
            await interaction.response.send_message(embed=embed)
            await self.bot.send_security_log(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"❌ Предупреждение с ID `{warn_id}` не найдено или уже неактивно.", ephemeral=True)

    @warn_group.command(name="clear", description="Очистить все предупреждения пользователя")
    @app_commands.describe(user="Пользователь для очистки варнов")
    async def warn_clear(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ Только **Администраторы безопасности** могут очищать предупреждения.", ephemeral=True)
            return

        cleared_count = await db.clear_warnings(interaction.guild.id, user.id)
        embed = create_security_embed(
            title="🧹 ПРЕДУПРЕЖДЕНИЯ ОЧИЩЕНЫ",
            description=f"С пользователя {user.mention} снято **{cleared_count}** активных предупреждений.",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed)
        await self.bot.send_security_log(interaction.guild, embed)

    @warn_group.command(name="list", description="Показать список всех активных предупреждений пользователя")
    @app_commands.describe(user="Пользователь")
    async def warn_list(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        if not await is_admin(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("❌ Только **Администраторы безопасности** могут просматривать список варнов.", ephemeral=True)
            return

        warnings = await db.get_active_warnings(interaction.guild.id, user.id)
        if not warnings:
            await interaction.response.send_message(f"ℹ️ У пользователя {user.mention} нет активных предупреждений.", ephemeral=True)
            return

        lines = []
        for w in warnings:
            lines.append(f"• **[ID: {w['id']}]** `{w['reason']}` | Выдан: <@{w['moderator_id']}>")

        embed = create_security_embed(
            title=f"📜 Предупреждения: {user.display_name} ({len(warnings)}/{MAX_WARNINGS})",
            description="\n".join(lines),
            color=COLOR_WARNING
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
                await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.defer()
            count = 0
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).manage_channels:
                    try:
                        ow = ch.overwrites_for(everyone)
                        if ow.send_messages is not False:
                            ow.send_messages = False
                            await ch.set_permissions(ow, reason=f"FloryGuard Server Lockdown by {interaction.user}")
                            count += 1
                    except Exception:
                        pass
            
            embed = create_security_embed(
                title="🚨 СЕРВЕР ПОЛНОСТЬЮ ЗАБЛОКИРОВАН (GLOBAL LOCKDOWN)",
                description=f"Заблокировано текстовых каналов: **{count}**.\nОтправка сообщений для `@everyone` запрещена.",
                color=COLOR_DANGER
            )
            await interaction.followup.send(embed=embed)

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
                await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.defer()
            count = 0
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).manage_channels:
                    try:
                        ow = ch.overwrites_for(everyone)
                        if ow.send_messages is False:
                            ow.send_messages = None
                            await ch.set_permissions(ow, reason=f"FloryGuard Server Unlock by {interaction.user}")
                            count += 1
                    except Exception:
                        pass
            
            embed = create_security_embed(
                title="🔓 СЕРВЕР РАЗБЛОКИРОВАН",
                description=f"Разблокировано текстовых каналов: **{count}**.",
                color=COLOR_SUCCESS
            )
            await interaction.followup.send(embed=embed)

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
