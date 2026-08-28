import discord
from discord.ext import commands
from discord import app_commands
import uuid
from typing import Optional, Literal
from datetime import datetime, timezone

from config import UNAUTHORIZED_MESSAGE, AUTHORIZED_GUILDS
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
    create_security_embed,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER
)


class AdminGuardCog(commands.Cog, name="AdminGuard"):
    """
    Administration, Whitelist, Administrator management, and Snapshot restoration.
    """
    def __init__(self, bot):
        self.bot = bot

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

    # ==========================================
    # /WHITELIST GROUP (ADD / REMOVE / LIST)
    # ==========================================
    wl_group = app_commands.Group(name="whitelist", description="Управление белым списком доверенных лиц")

    @wl_group.command(name="add", description="Добавить пользователя в WhiteList")
    @app_commands.describe(user="Пользователь", reason="Причина добавления")
    async def wl_add(self, interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = "Trusted Member"):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы и Владелец** могут управлять WhiteList.", ephemeral=True)
            return

        await db.add_whitelist(interaction.guild.id, user.id, interaction.user.id, reason)
        embed = create_security_embed(
            title="⭐ ПОЛЬЗОВАТЕЛЬ ДОБАВЛЕН В WHITELIST",
            description=f"Пользователь {user.mention} (`{user.id}`) добавлен в доверенный список.\n📌 **Причина:** `{reason}`",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed)
        await self.bot.send_security_log(interaction.guild, embed)

    @wl_group.command(name="remove", description="Удалить пользователя из WhiteList")
    @app_commands.describe(user="Пользователь")
    async def wl_remove(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы и Владелец** могут управлять WhiteList.", ephemeral=True)
            return

        removed = await db.remove_whitelist(interaction.guild.id, user.id)
        if removed:
            embed = create_security_embed(
                title="⭐ ПОЛЬЗОВАТЕЛЬ УДАЛЕН ИЗ WHITELIST",
                description=f"Пользователь {user.mention} (`{user.id}`) удален из доверенного списка.",
                color=COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed)
            await self.bot.send_security_log(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"❌ Пользователь {user.mention} не находился в WhiteList.", ephemeral=True)

    @wl_group.command(name="list", description="Показать всех участников WhiteList")
    async def wl_list(self, interaction: discord.Interaction):
        if not await self._check_guild_auth(interaction):
            return

        wl_users = await db.get_whitelist(interaction.guild.id)
        if not wl_users:
            await interaction.response.send_message("ℹ️ Белый список пуст.", ephemeral=True)
            return

        lines = []
        for item in wl_users:
            lines.append(f"• <@{item['user_id']}> (`ID: {item['user_id']}`) — *{item.get('reason', 'Нет описания')}*")

        embed = create_security_embed(
            title=f"⭐ Доверенный список (WhiteList) [{len(wl_users)}]",
            description="\n".join(lines),
            color=COLOR_PRIMARY
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # /ADMIN GROUP (ADD / REMOVE / LIST)
    # ==========================================
    admin_group = app_commands.Group(name="admin", description="Управление Администраторами Безопасности")

    @admin_group.command(name="add", description="Назначить Администратора Безопасности")
    @app_commands.describe(user="Пользователь")
    async def admin_add(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы и Владелец** могут назначать администраторов.", ephemeral=True)
            return

        await db.add_admin(interaction.guild.id, user.id, interaction.user.id)
        embed = create_security_embed(
            title="🛡️ НАЗНАЧЕН АДМИНИСТРАТОР БЕЗОПАСНОСТИ",
            description=f"Пользователь {user.mention} (`{user.id}`) назначен администратором безопасности.\nЕго действия не подлежат откату.",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed)
        await self.bot.send_security_log(interaction.guild, embed)

    @admin_group.command(name="remove", description="Снять статус Администратора Безопасности")
    @app_commands.describe(user="Пользователь")
    async def admin_remove(self, interaction: discord.Interaction, user: discord.Member):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы и Владелец** могут управлять администраторами.", ephemeral=True)
            return

        removed = await db.remove_admin(interaction.guild.id, user.id)
        if removed:
            embed = create_security_embed(
                title="🛡️ СНЯТ АДМИНИСТРАТОР БЕЗОПАСНОСТИ",
                description=f"С пользователя {user.mention} снят статус администратора безопасности.",
                color=COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed)
            await self.bot.send_security_log(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"❌ Пользователь {user.mention} не является администратором.", ephemeral=True)

    @admin_group.command(name="list", description="Показать всех действующих администраторов")
    async def admin_list(self, interaction: discord.Interaction):
        if not await self._check_guild_auth(interaction):
            return

        admins = await db.get_admins(interaction.guild.id)
        lines = []

        # List hardcoded senior admins / owners
        lines.append("👑 **Владелец бота:** <@1398717669607473254>")
        lines.append("💎 **Высший Администратор:** <@1291370925303795733>")
        lines.append("\n🛡️ **Назначенные администраторы:**")

        if admins:
            for a in admins:
                lines.append(f"• <@{a['user_id']}> (`ID: {a['user_id']}`)")
        else:
            lines.append("*Дополнительных администраторов нет.*")

        embed = create_security_embed(
            title="🛡️ Состав Администрации Безопасности",
            description="\n".join(lines),
            color=COLOR_PRIMARY
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # /SNAPSHOT & /RESTORE (BACKUP & ROLLBACK)
    # ==========================================
    snapshot_group = app_commands.Group(name="snapshot", description="Создание и просмотр снимков сервера")

    @snapshot_group.command(name="create", description="Создать мгновенный снимок структуры каналов и ролей")
    async def snapshot_create(self, interaction: discord.Interaction):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы** могут создавать снимки.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # Collect Roles
        roles_data = []
        for r in guild.roles:
            if r.name != "@everyone":
                roles_data.append({
                    "id": r.id,
                    "name": r.name,
                    "color": r.color.value,
                    "hoist": r.hoist,
                    "position": r.position,
                    "permissions": r.permissions.value,
                    "mentionable": r.mentionable
                })

        # Collect Channels
        channels_data = []
        for ch in guild.channels:
            channels_data.append({
                "id": ch.id,
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
                "category": ch.category.name if ch.category else None,
                "topic": getattr(ch, "topic", None)
            })

        snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"
        payload = {
            "guild_name": guild.name,
            "guild_id": guild.id,
            "roles": roles_data,
            "channels": channels_data
        }

        await db.save_snapshot(snapshot_id, guild.id, interaction.user.id, payload)

        embed = create_security_embed(
            title="💾 СНИМОК СЕРВЕРА СОЗДАН",
            description=(
                f"ID Снимка: `{snapshot_id}`\n"
                f"Сохранено ролей: **{len(roles_data)}**\n"
                f"Сохранено каналов: **{len(channels_data)}**\n\n"
                f"Используйте `/restore {snapshot_id}` для восстановления."
            ),
            color=COLOR_SUCCESS
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @snapshot_group.command(name="list", description="Показать список доступных снимков сервера")
    async def snapshot_list(self, interaction: discord.Interaction):
        if not await self._check_guild_auth(interaction):
            return

        snaps = await db.list_snapshots(interaction.guild.id)
        if not snaps:
            await interaction.response.send_message("ℹ️ Нет сохраненных снимков сервера.", ephemeral=True)
            return

        lines = []
        for s in snaps:
            created_ts = int(datetime.fromisoformat(s["created_at"]).timestamp())
            lines.append(f"• **`{s['id']}`** — Создан: <t:{created_ts}:f> (<@{s['created_by']}>)")

        embed = create_security_embed(
            title=f"💾 Снимки сервера [{len(snaps)}]",
            description="\n".join(lines),
            color=COLOR_PRIMARY
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="restore", description="Восстановить структуру каналов и ролей из снимка")
    @app_commands.describe(snapshot_id="ID снимка (например snap-1a2b3c4d)")
    async def restore_command(self, interaction: discord.Interaction, snapshot_id: str):
        if not await self._check_guild_auth(interaction):
            return

        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message("❌ Только **Высшие Администраторы** могут запускать восстановление.", ephemeral=True)
            return

        snap = await db.get_snapshot(snapshot_id, interaction.guild.id)
        if not snap:
            await interaction.response.send_message(f"❌ Снимок `{snapshot_id}` не найден.", ephemeral=True)
            return

        await interaction.response.defer()
        guild = interaction.guild
        data = snap["data"]

        # Restore Roles
        created_roles = 0
        existing_role_names = {r.name: r for r in guild.roles}
        for r_info in data.get("roles", []):
            if r_info["name"] not in existing_role_names:
                try:
                    await guild.create_role(
                        name=r_info["name"],
                        color=discord.Color(r_info["color"]),
                        hoist=r_info["hoist"],
                        permissions=discord.Permissions(r_info["permissions"]),
                        mentionable=r_info["mentionable"],
                        reason=f"FloryGuard Restore from {snapshot_id}"
                    )
                    created_roles += 1
                except Exception:
                    pass

        # Restore Channels
        created_channels = 0
        existing_ch_names = {c.name: c for c in guild.channels}
        for ch_info in data.get("channels", []):
            if ch_info["name"] not in existing_ch_names:
                try:
                    ch_type = ch_info.get("type", "")
                    if "text" in ch_type:
                        await guild.create_text_channel(name=ch_info["name"], topic=ch_info.get("topic"), reason=f"FloryGuard Restore from {snapshot_id}")
                        created_channels += 1
                    elif "voice" in ch_type:
                        await guild.create_voice_channel(name=ch_info["name"], reason=f"FloryGuard Restore from {snapshot_id}")
                        created_channels += 1
                except Exception:
                    pass

        embed = create_security_embed(
            title="🔄 ВОССТАНОВЛЕНИЕ ИЗ СНИМКА ЗАВЕРШЕНО",
            description=(
                f"Снимок: `{snapshot_id}`\n"
                f"Восстановлено недостающих ролей: **{created_roles}**\n"
                f"Восстановлено недостающих каналов: **{created_channels}**"
            ),
            color=COLOR_SUCCESS
        )
        await interaction.followup.send(embed=embed)

    # ==========================================
    # /ADDROLE & /REMOVEROLE (EXCLUSIVE TO OWNER & SENIOR ADMIN)
    # ==========================================
    @app_commands.command(name="addrole", description="Выдать роль пользователю (Только Владелец и Высший Админ)")
    @app_commands.describe(user="Пользователь, которому выдать роль", role="Роль для выдачи")
    async def add_role_command(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        if not await self._check_guild_auth(interaction):
            return

        # Check permission: Only Senior Admin & Bot Owner
        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ Эта команда доступна **только Владельцу бота и Высшим Администраторам**.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        bot_member = guild.me

        # Check bot permissions
        if not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ У бота нет права `Управлять ролями` (Manage Roles).", ephemeral=True)
            return

        # Check role hierarchy
        if role >= bot_member.top_role:
            await interaction.response.send_message(
                f"❌ Роль {role.mention} находится **выше или на одном уровне** с ролью бота. "
                "Поднимите роль бота выше в настройках сервера.",
                ephemeral=True
            )
            return

        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Нельзя выдать системную или управляемую интеграцией роль.", ephemeral=True)
            return

        if role in user.roles:
            await interaction.response.send_message(f"ℹ️ У пользователя {user.mention} уже есть роль {role.mention}.", ephemeral=True)
            return

        try:
            await user.add_roles(role, reason=f"FloryGuard: /addrole от {interaction.user} (ID: {interaction.user.id})")
            
            embed = create_security_embed(
                title="🎭 РОЛЬ УСПЕШНО ВЫДАНА",
                description=(
                    f"👤 **Пользователь:** {user.mention} (`{user.id}`)\n"
                    f"🏷️ **Роль:** {role.mention} (`{role.name}`)\n"
                    f"👑 **Исполнитель:** {interaction.user.mention}"
                ),
                color=COLOR_SUCCESS
            )
            await interaction.response.send_message(embed=embed)
            await self.bot.send_security_log(guild, embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ошибка прав Discord: невозможно выдать роль из-за иерархии.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка при выдаче роли: {e}", ephemeral=True)

    @app_commands.command(name="removerole", description="Снять роль у пользователя (Только Владелец и Высший Админ)")
    @app_commands.describe(user="Пользователь, у которого снять роль", role="Роль для снятия")
    async def remove_role_command(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        if not await self._check_guild_auth(interaction):
            return

        # Check permission: Only Senior Admin & Bot Owner
        if not is_senior_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ Эта команда доступна **только Владельцу бота и Высшим Администраторам**.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        bot_member = guild.me

        # Check bot permissions
        if not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ У бота нет права `Управлять ролями` (Manage Roles).", ephemeral=True)
            return

        # Check role hierarchy
        if role >= bot_member.top_role:
            await interaction.response.send_message(
                f"❌ Роль {role.mention} находится **выше или на одном уровне** с ролью бота. "
                "Поднимите роль бота выше в настройках сервера.",
                ephemeral=True
            )
            return

        if role.is_default() or role.managed:
            await interaction.response.send_message("❌ Нельзя снять системную или управляемую интеграцией роль.", ephemeral=True)
            return

        if role not in user.roles:
            await interaction.response.send_message(f"ℹ️ У пользователя {user.mention} нет роли {role.mention}.", ephemeral=True)
            return

        try:
            await user.remove_roles(role, reason=f"FloryGuard: /removerole от {interaction.user} (ID: {interaction.user.id})")
            
            embed = create_security_embed(
                title="🎭 РОЛЬ УСПЕШНО СНЯТА",
                description=(
                    f"👤 **Пользователь:** {user.mention} (`{user.id}`)\n"
                    f"🏷️ **Роль:** {role.mention} (`{role.name}`)\n"
                    f"👑 **Исполнитель:** {interaction.user.mention}"
                ),
                color=COLOR_WARNING
            )
            await interaction.response.send_message(embed=embed)
            await self.bot.send_security_log(guild, embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ошибка прав Discord: невозможно снять роль из-за иерархии.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка при снятии роли: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminGuardCog(bot))
