import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from config import (
    UNAUTHORIZED_MESSAGE,
    CONFIG,
    save_config,
    STAFF_HIERARCHY,
    STAFF_ROLE_DAILY_NORM_HOURS,
)
from core.permissions import (
    is_authorized_guild,
    has_staff_access,
    has_warn_access,
    get_member_staff_rank,
    get_member_daily_norm_seconds,
)
from database.db import db
from utils.staff_card import generate_staff_profile_card
from utils.embeds import create_security_embed, COLOR_FLORY_ORANGE
from utils.logger import logger


class StaffCog(commands.Cog, name="Staff"):
    """
    ⚡ FloryMine Staff System:
    - /staff-profile: Dynamic yellow-orange graphic profile card for staff members.
    - /staff-norm: Configure daily online hours norm per staff rank for % calculation.
    - /staff-sync: Sync/update Minecraft moderation stats (FloryModerators bridge).
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _check_guild_auth(self, interaction: discord.Interaction) -> bool:
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return False
        return True

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

        # 1. Determine Staff Rank & Reprimand Limit (1 for Стажер, 3 for other staff)
        rank_name, max_warns, _, _ = get_member_staff_rank(target)
        role_norm_seconds = get_member_daily_norm_seconds(target)

        # 2. Fetch active reprimands count from Discord DB
        active_warns = await db.get_active_warn_count(interaction.guild.id, target.id)

        # 3. Fetch Minecraft moderation & online stats (from FloryModerators table)
        stats = await db.get_staff_stats(interaction.guild.id, target.id)
        display_nick = (stats.get("minecraft_nick") if stats and stats.get("minecraft_nick") else None) or target.name
        online_seconds = int(stats.get("online_seconds", 0)) if stats else 0
        # Use role-based daily norm unless explicitly overridden in stats
        norm_seconds = int(stats["norm_seconds"]) if (stats and stats.get("norm_seconds")) else role_norm_seconds
        warns_issued = stats.get("warns_issued") if stats else None
        bans_issued = stats.get("bans_issued") if stats else None
        reports_accepted = stats.get("reports_accepted") if stats else None
        mutes_issued = stats.get("mutes_issued") if stats else None

        # 4. Download user's Discord avatar (falls back to requester's avatar if needed)
        avatar_bytes = None
        for member_for_avatar in (target, interaction.user):
            try:
                avatar_bytes = await member_for_avatar.display_avatar.with_format("png").with_size(512).read()
                if avatar_bytes:
                    break
            except Exception as e:
                logger.warning(f"Failed to download avatar for {member_for_avatar.id}: {e}")

        # 5. Render image in background thread so event loop stays fast
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
            )
            file = discord.File(fp=image_buffer, filename=f"staff_profile_{target.id}.png")
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
            # Persist to config.json
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
