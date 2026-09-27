import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Set

from config import UNAUTHORIZED_MESSAGE
from core.permissions import (
    is_authorized_guild,
    is_senior_admin,
    is_bot_owner,
    AVAILABLE_PERMISSIONS,
)
from database.db import db
from utils.embeds import (
    build_flory_permissions_embed,
    create_security_embed,
    COLOR_FLORY_ORANGE,
    COLOR_SUCCESS,
    COLOR_DANGER,
)
from utils.logger import logger


class PermissionToggleView(discord.ui.View):
    def __init__(
        self,
        bot,
        invoker: discord.Member,
        target: discord.Member,
        perms_set: Set[str],
        title: str,
        is_owner_or_senior: bool = False,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.invoker = invoker
        self.target = target
        self.perms_set = perms_set
        self.title = title
        self.is_owner_or_senior = is_owner_or_senior
        self._build_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id and not is_senior_admin(interaction.user.id, interaction.guild.id):
            await interaction.response.send_message(
                "❌ Только администратор, вызвавший данную панель, или Владелец сервера может переключать права.",
                ephemeral=True,
            )
            return False
        return True

    def _build_buttons(self) -> None:
        self.clear_items()

        # Layout: 4 categories + 1 row for presets
        matrix = [
            # Row 0: Roles
            (
                0,
                [
                    ("roles_assign", "Роли (ПКМ)", "👤"),
                    ("roles_edit", "Правка ролей", "🎭"),
                    ("roles_create", "+ Роль", "➕"),
                    ("roles_delete", "- Роль", "🗑️"),
                ],
            ),
            # Row 1: Moderation
            (
                1,
                [
                    ("ban_members", "Бан", "🔨"),
                    ("kick_members", "Кик", "👢"),
                    ("timeout_members", "Мут / Таймаут", "⏳"),
                    ("automod_bypass", "Обход AutoMod", "💬"),
                ],
            ),
            # Row 2: Channels
            (
                2,
                [
                    ("channels_create", "+ Канал", "📁"),
                    ("channels_edit", "Правка канала", "📝"),
                    ("channels_delete", "- Канал", "❌"),
                ],
            ),
            # Row 3: Server & Webhooks
            (
                3,
                [
                    ("server_edit", "Настройки сервера", "⚙️"),
                    ("webhooks_manage", "Вебхуки", "🔗"),
                ],
            ),
        ]

        for row_idx, items in matrix:
            for perm_key, short_label, emoji in items:
                is_granted = self.is_owner_or_senior or (perm_key in self.perms_set)
                style = discord.ButtonStyle.success if is_granted else discord.ButtonStyle.secondary
                btn = discord.ui.Button(
                    label=short_label,
                    style=style,
                    emoji=emoji,
                    row=row_idx,
                    disabled=self.is_owner_or_senior,
                )
                btn.callback = self._create_toggle_callback(perm_key)
                self.add_item(btn)

        # Row 4: Quick Action Presets
        if not self.is_owner_or_senior:
            btn_grant_all = discord.ui.Button(
                label="Выдать все права",
                style=discord.ButtonStyle.primary,
                emoji="⭐",
                row=4,
            )
            btn_grant_all.callback = self._on_grant_all_clicked
            self.add_item(btn_grant_all)

            btn_lock_all = discord.ui.Button(
                label="Запретить все (Сброс)",
                style=discord.ButtonStyle.danger,
                emoji="🔒",
                row=4,
            )
            btn_lock_all.callback = self._on_lock_all_clicked
            self.add_item(btn_lock_all)

    def _create_toggle_callback(self, perm_key: str):
        async def callback(interaction: discord.Interaction):
            if perm_key in self.perms_set:
                self.perms_set.remove(perm_key)
                action_text = f"Запрещено: `{AVAILABLE_PERMISSIONS.get(perm_key, {}).get('label', perm_key)}`"
            else:
                self.perms_set.add(perm_key)
                action_text = f"Разрешено: `{AVAILABLE_PERMISSIONS.get(perm_key, {}).get('label', perm_key)}`"

            await db.set_user_permissions(
                guild_id=self.target.guild.id,
                user_id=self.target.id,
                permissions=self.perms_set,
                updated_by=interaction.user.id,
                title=self.title,
            )

            self._build_buttons()
            embed = build_flory_permissions_embed(
                target=self.target,
                perms_set=self.perms_set,
                title=self.title,
                is_owner_or_senior=self.is_owner_or_senior,
            )
            await interaction.response.edit_message(embed=embed, view=self)

            # Audit log
            log_embed = create_security_embed(
                title="⚡ ИЗМЕНЕНИЕ ПРАВ FLORYGUARD",
                description=(
                    f"👑 **Инициатор:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"👤 **Сотрудник:** {self.target.mention} (`{self.target.id}`)\n"
                    f"⚙️ **Действие:** {action_text}\n"
                    f"📊 **Активных прав:** `{len(self.perms_set)}/{len(AVAILABLE_PERMISSIONS)}`"
                ),
                color=COLOR_FLORY_ORANGE,
            )
            await self.bot.send_security_log(self.target.guild, log_embed)

        return callback

    async def _on_grant_all_clicked(self, interaction: discord.Interaction):
        self.perms_set = set(AVAILABLE_PERMISSIONS.keys())
        await db.set_user_permissions(
            guild_id=self.target.guild.id,
            user_id=self.target.id,
            permissions=self.perms_set,
            updated_by=interaction.user.id,
            title=self.title,
        )

        self._build_buttons()
        embed = build_flory_permissions_embed(
            target=self.target,
            perms_set=self.perms_set,
            title=self.title,
            is_owner_or_senior=self.is_owner_or_senior,
        )
        await interaction.response.edit_message(embed=embed, view=self)

        log_embed = create_security_embed(
            title="⭐ ВЫДАН ПОЛНЫЙ ДОСТУП FLORYGUARD",
            description=(
                f"👑 **Инициатор:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.id}`)\n"
                f"⚡ **Статус:** Предоставлены все права безопасности (Full Access)"
            ),
            color=COLOR_SUCCESS,
        )
        await self.bot.send_security_log(self.target.guild, log_embed)

    async def _on_lock_all_clicked(self, interaction: discord.Interaction):
        self.perms_set.clear()
        await db.set_user_permissions(
            guild_id=self.target.guild.id,
            user_id=self.target.id,
            permissions=self.perms_set,
            updated_by=interaction.user.id,
            title=self.title,
        )

        self._build_buttons()
        embed = build_flory_permissions_embed(
            target=self.target,
            perms_set=self.perms_set,
            title=self.title,
            is_owner_or_senior=self.is_owner_or_senior,
        )
        await interaction.response.edit_message(embed=embed, view=self)

        log_embed = create_security_embed(
            title="🔒 ПОЛНЫЙ СБРОС ПРАВ FLORYGUARD",
            description=(
                f"👑 **Инициатор:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"👤 **Сотрудник:** {self.target.mention} (`{self.target.id}`)\n"
                f"⛔ **Статус:** Все права безопасности аннулированы (Защита активна)"
            ),
            color=COLOR_DANGER,
        )
        await self.bot.send_security_log(self.target.guild, log_embed)


class PermissionsCog(commands.Cog, name="Permissions"):
    """
    ⚡ Granular Security Permissions Matrix for FloryMine.
    Provides interactive UI for granting/revoking Anti-Nuke bypass permissions.
    """
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _handle_permissions(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Команда доступна только на сервере.", ephemeral=True)
            return

        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return

        # Check invoker authorization: Server Owner or Senior Admin
        is_owner = is_bot_owner(interaction.user.id, interaction.guild.id) or interaction.guild.owner_id == interaction.user.id
        is_senior = is_senior_admin(interaction.user.id, interaction.guild.id)

        if not (is_owner or is_senior):
            await interaction.response.send_message(
                "❌ Настройка матрицы прав безопасности доступна исключительно **Владельцу сервера и Высшей Администрации**.",
                ephemeral=True,
            )
            return

        target = member if member is not None else interaction.user

        await interaction.response.defer(ephemeral=True)

        # Check if target is owner or senior admin
        target_is_owner_or_senior = is_bot_owner(target.id, interaction.guild.id) or is_senior_admin(target.id, interaction.guild.id) or interaction.guild.owner_id == target.id

        perms_set, title = await db.get_user_permissions(interaction.guild.id, target.id)
        if target_is_owner_or_senior:
            title = "Владелец сервера" if (target.id == interaction.guild.owner_id or is_bot_owner(target.id, interaction.guild.id)) else "Высший Администратор"
            perms_set = set(AVAILABLE_PERMISSIONS.keys())

        embed = build_flory_permissions_embed(
            target=target,
            perms_set=perms_set,
            title=title,
            is_owner_or_senior=target_is_owner_or_senior,
        )

        view = PermissionToggleView(
            bot=self.bot,
            invoker=interaction.user,
            target=target,
            perms_set=perms_set,
            title=title,
            is_owner_or_senior=target_is_owner_or_senior,
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="permissions",
        description="⚡ Интерактивная настройка прав доступа и исключений FloryGuard (FloryMine)",
    )
    @app_commands.describe(member="Сотрудник, для которого настраиваются права (по умолчанию: вы)")
    async def permissions_command(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        await self._handle_permissions(interaction, member)

    @app_commands.command(
        name="permission",
        description="⚡ Интерактивная настройка прав доступа и исключений FloryGuard (FloryMine)",
    )
    @app_commands.describe(member="Сотрудник, для которого настраиваются права (по умолчанию: вы)")
    async def permission_command(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        await self._handle_permissions(interaction, member)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PermissionsCog(bot))
