import discord
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Color constants
COLOR_PRIMARY = 0x5865F2    # Blurple
COLOR_SUCCESS = 0x2ECC71    # Green
COLOR_WARNING = 0xF1C40F    # Yellow / Orange
COLOR_DANGER = 0xE74C3C     # Red
COLOR_DARK = 0x1E1F22       # Dark Gray

# FloryMine Signature Orange-Yellow Palette
COLOR_FLORY_ORANGE = 0xFFA500  # ⚡ FloryMine Signature Vibrant Orange
COLOR_FLORY_GOLD = 0xFFB703    # ⚡ FloryMine Warm Gold / Yellow
COLOR_FLORY_AMBER = 0xFB8500   # ⚡ FloryMine Deep Amber

DEFAULT_FOOTER_ICON = "https://cdn.discordapp.com/embed/avatars/0.png"


def create_security_embed(
    title: str,
    description: str,
    color: int = COLOR_DANGER,
    footer: str = "FloryGuard Security System • Anti-Nuke & Protection"
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=footer, icon_url=DEFAULT_FOOTER_ICON)
    return embed


def warn_dm_embed(warn_count: int, max_warns: int = 5, reason: str = "Несанкционированное изменение структуры сервера") -> discord.Embed:
    embed = discord.Embed(
        title="⚠️ ПРЕДУПРЕЖДЕНИЕ БЕЗОПАСНОСТИ",
        description=(
            f"**Вам выдано предупреждение {warn_count}/{max_warns}**\n\n"
            f"📌 **Причина:** `{reason}`\n"
            f"⏳ **Срок действия:** Предупреждение снимется через неделю (7 дней).\n\n"
            f"🚨 **Внимание:** Если вы накопите **{max_warns} предупреждений**, вы будете **сняты со всех должностей и ролей**."
        ),
        color=COLOR_WARNING if warn_count < max_warns else COLOR_DANGER,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="FloryGuard Security Bot", icon_url=DEFAULT_FOOTER_ICON)
    return embed


def staff_warn_dm_embed(
    guild: discord.Guild,
    moderator: discord.Member | discord.User,
    duration_text: str,
    situation: str,
    reason: str,
    warn_count: int,
    max_warns: int,
    demoted: bool = False,
) -> discord.Embed:
    """
    Creates the sleek dark DM embed matching the reference screenshot for /warn.
    """
    title_text = "Вам выдали Выговор" if not demoted else "Вам выдали Выговор (Снятие с должности)"
    clean_situation = (situation or reason).replace("```", "'''").strip()
    clean_reason = reason.replace("```", "'''").strip()
    clean_duration = duration_text.replace("```", "'''").strip()

    extra_demote = ""
    if demoted:
        extra_demote = (
            f"\n⚠️ **Достигнут лимит выговоров ({warn_count}/{max_warns}) — вы были автоматически сняты с должности стаффа.**"
        )

    description = (
        f"> **Исполнитель:**\n"
        f"• {moderator.mention}\n"
        f"• **Имя:** {moderator.name}\n"
        f"• **ID:** {moderator.id}\n"
        f"> **Длительность:**\n"
        f"```{clean_duration}```\n"
        f"**Описание ситуации**\n"
        f"```{clean_situation}```\n"
        f"> **Причина:**\n"
        f"```{clean_reason}```"
        f"{extra_demote}"
    )

    embed = discord.Embed(
        title=title_text,
        description=description,
        color=COLOR_DARK,
        timestamp=datetime.now(timezone.utc),
    )
    icon_url = guild.icon.url if (guild and guild.icon) else DEFAULT_FOOTER_ICON
    embed.set_author(name="FloryMine", icon_url=icon_url)
    return embed


def anti_nuke_alert_embed(
    action_type: str,
    executor: discord.User | discord.Member,
    target_info: str,
    rollback_status: str,
    warn_count: Optional[int] = None,
    immediate_demote: bool = False
) -> discord.Embed:
    color = COLOR_DANGER if (immediate_demote or (warn_count and warn_count >= 5)) else COLOR_WARNING
    embed = discord.Embed(
        title=f"🛡️ [Anti-Nuke] Зафиксировано нарушение: {action_type}",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=executor.display_avatar.url)
    embed.add_field(name="👤 Нарушитель", value=f"{executor.mention} (`{executor.name}` | `ID: {executor.id}`)", inline=False)
    embed.add_field(name="🎯 Объект действия", value=f"`{target_info}`", inline=False)
    embed.add_field(name="🔄 Статус отката (Rollback)", value=f"`{rollback_status}`", inline=True)

    if immediate_demote:
        embed.add_field(name="⚡ Санкция", value="🔴 **МГНОВЕННОЕ СНЯТИЕ ВСЕХ РОЛЕЙ + КАРАНТИН**", inline=False)
    elif warn_count is not None:
        if warn_count >= 5:
            embed.add_field(name="⚡ Санкция", value=f"🔴 **ЛИМИТ ПРЕВЫШЕН ({warn_count}/5)** — Все роли сняты + Карантин!", inline=False)
        else:
            embed.add_field(name="⚡ Санкция", value=f"🟡 **Предупреждение {warn_count}/5** (Откат + Уведомление в ЛС)", inline=False)

    embed.set_footer(text="FloryGuard Audit System", icon_url=DEFAULT_FOOTER_ICON)
    return embed


def automod_alert_embed(
    member: discord.Member | discord.User,
    channel: discord.abc.GuildChannel,
    threat_type: str,
    matched_content: str,
    action: str = "Удалено"
) -> discord.Embed:
    embed = discord.Embed(
        title="🛑 [AutoMod 2.0] Предотвращена угроза в чате",
        color=COLOR_WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Автор", value=f"{member.mention} (`{member.name}` | `{member.id}`)", inline=True)
    embed.add_field(name="💬 Канал", value=channel.mention, inline=True)
    embed.add_field(name="🔍 Тип угрозы", value=f"`{threat_type}`", inline=False)

    # Trim content if too long
    safe_content = matched_content[:400] + ("..." if len(matched_content) > 400 else "")
    embed.add_field(name="📝 Содержимое", value=f"```{safe_content or 'Пусто'}```", inline=False)
    embed.add_field(name="⚙️ Действие", value=f"✅ {action}", inline=True)

    embed.set_footer(text="FloryGuard AutoMod", icon_url=DEFAULT_FOOTER_ICON)
    return embed


def profile_embed(
    member: discord.Member,
    is_owner: bool,
    is_senior_admin: bool,
    is_admin: bool,
    is_whitelisted: bool,
    warnings: List[Dict[str, Any]],
    admin_info: Optional[Dict[str, Any]] = None
) -> discord.Embed:
    # Determine Highest Security Rank
    if is_owner:
        rank_badge = "👑 **Владелец (Owner)**"
    elif is_senior_admin:
        rank_badge = "💎 **Высший Администратор (Senior Admin)**"
    elif admin_info and admin_info.get("rank") == "admin":
        title = admin_info.get("title") or "Администратор Безопасности"
        rank_badge = f"🛡️ **{title}**"
    elif is_admin:
        rank_badge = "🛡️ **Администратор Безопасности (Security Admin)**"
    elif is_whitelisted:
        rank_badge = "⭐ **В Белом Списке (WhiteList)**"
    else:
        rank_badge = "👤 **Участник (Member)**"

    embed = discord.Embed(
        title=f"📋 Профиль безопасности: {member.display_name}",
        color=COLOR_PRIMARY if not warnings else (COLOR_WARNING if len(warnings) < 5 else COLOR_DANGER),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(name="🆔 ID Пользователя", value=f"`{member.id}`", inline=True)
    embed.add_field(name="📅 Дата регистрации", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="📥 Вход на сервер", value=f"<t:{int(member.joined_at.timestamp()) if member.joined_at else 0}:R>", inline=True)
    embed.add_field(name="🔰 Уровень доступа / Должность", value=rank_badge, inline=False)

    # Show Granular Permissions for administrators
    if admin_info and admin_info.get("rank") in ("owner", "senior_admin", "admin", "whitelist"):
        if admin_info.get("is_full"):
            perms_display = "⭐ **Полный доступ (Все права)**"
        elif admin_info.get("permissions"):
            from core.permissions import AVAILABLE_PERMISSIONS
            perm_lines = []
            for p in admin_info["permissions"]:
                if p in AVAILABLE_PERMISSIONS:
                    meta = AVAILABLE_PERMISSIONS[p]
                    perm_lines.append(f"• {meta['emoji']} **{meta['label']}**")
                elif p != "none":
                    perm_lines.append(f"• `{p}`")
            perms_display = "\n".join(perm_lines) if perm_lines else "🔒 *Все права отключены*"
        else:
            perms_display = "🔒 *Все права отключены*"
        embed.add_field(name="📜 Выданные полномочия", value=perms_display, inline=False)

    warn_count = len(warnings)
    status_emoji = "🟢" if warn_count == 0 else ("🟡" if warn_count < 5 else "🔴")
    embed.add_field(
        name="⚠️ Активные предупреждения (Варны)",
        value=f"{status_emoji} **{warn_count}/5 активных предупреждений**",
        inline=False
    )

    if warnings:
        warn_lines = []
        for w in warnings[:5]:
            created_ts = int(datetime.fromisoformat(w["created_at"]).timestamp())
            expires_ts = int(datetime.fromisoformat(w["expires_at"]).timestamp())
            warn_lines.append(
                f"• **[ID: {w['id']}]** `{w['reason']}`\n  ├ Выдан: <t:{created_ts}:d> | Истекает: <t:{expires_ts}:R>"
            )
        embed.add_field(name="📜 Список нарушений", value="\n".join(warn_lines), inline=False)
    else:
        embed.add_field(name="📜 Список нарушений", value="*Нарушений не зафиксировано, история чиста.*", inline=False)

    # Roles summary
    roles_list = [r.mention for r in member.roles if not r.is_default()]
    roles_str = ", ".join(roles_list[:10]) if roles_list else "*Нет ролей*"
    if len(roles_list) > 10:
        roles_str += f" *(и ещё {len(roles_list) - 10})*"
    embed.add_field(name="🎭 Роли", value=roles_str, inline=False)

    embed.set_footer(text="FloryGuard Profile System • Данные конфиденциальны")
    return embed


def build_flory_permissions_embed(
    target: discord.Member,
    perms_set: set,
    title: str,
    is_owner_or_senior: bool = False
) -> discord.Embed:
    embed = discord.Embed(
        title="⚡ Панель настройки прав безопасности • FloryMine",
        color=COLOR_FLORY_ORANGE,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    def status_badge(perm: str) -> str:
        if is_owner_or_senior or perm in perms_set:
            return "🟧 **Разрешено**"
        return "⬛ *Запрещено (Защита)*"

    owner_banner = ""
    if is_owner_or_senior:
        owner_banner = "👑 **Владелец сервера / Высшая Администрация**\n*(Обладает безусловным полным доступом ко всем системам защиты)*\n\n"

    desc = (
        f"{owner_banner}"
        f"👤 **Сотрудник:** {target.mention} (`{target.name}`)\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"🏷️ **Должность:** `{title}`\n"
        f"🌐 **Сервер:** `⚡ FloryMine ⚡`\n\n"
        f"**👤 Управление ролями:**\n"
        f"• Выдача ролей (ПКМ): {status_badge('roles_assign')}\n"
        f"• Правка ролей: {status_badge('roles_edit')}\n"
        f"• Создание ролей: {status_badge('roles_create')}\n"
        f"• Удаление ролей: {status_badge('roles_delete')}\n\n"
        f"**🔨 Модерация участников:**\n"
        f"• Блокировка (Бан): {status_badge('ban_members')}\n"
        f"• Изгнание (Кик): {status_badge('kick_members')}\n"
        f"• Мут / Таймаут: {status_badge('timeout_members')}\n"
        f"• Иммунитет AutoMod: {status_badge('automod_bypass')}\n\n"
        f"**📁 Каналы и категории:**\n"
        f"• Создание каналов: {status_badge('channels_create')}\n"
        f"• Правка каналов: {status_badge('channels_edit')}\n"
        f"• Удаление каналов: {status_badge('channels_delete')}\n\n"
        f"**⚙️ Сервер и интеграции:**\n"
        f"• Настройки сервера: {status_badge('server_edit')}\n"
        f"• Управление вебхуками: {status_badge('webhooks_manage')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Нажимайте на кнопки ниже для переключения разрешений в реальном времени.*"
    )

    embed.description = desc
    embed.set_footer(
        text="⚡ FloryMine ⚡ • FloryGuard Security Matrix",
        icon_url=DEFAULT_FOOTER_ICON
    )
    return embed


# =========================================================================
# ATTESTATION & INTERVIEW EMBEDS (DESIGN-ENGINEER GRADE)
# =========================================================================

def attestation_dm_scheduled_embed(
    candidate: discord.User | discord.Member,
    curator: discord.User | discord.Member,
    target_role_name: str,
    voice_channel: discord.VoiceChannel | discord.abc.GuildChannel,
    scheduled_dt: datetime,
    attempt_num: int = 1,
    max_attempts: int = 3,
    note: str = "",
) -> discord.Embed:
    from utils.time_parser import format_msk, to_discord_timestamp

    desc = (
        f"Здравствуйте, {candidate.mention}!\n"
        f"Вам назначено квалификационное собеседование (аттестация) в команду персонала **FloryMine**.\n\n"
        f"🏷️ **Целевая должность:** **{target_role_name}**\n\n"
        f"📋 **ПАРАМЕТРЫ СОБЕСЕДОВАНИЯ:**\n"
        f"• **Ответственный куратор:** {curator.mention} (`{curator.name}`)\n"
        f"• **Голосовой канал:** {voice_channel.mention} (`{voice_channel.name}`)\n"
        f"• **Дата и время:** `{format_msk(scheduled_dt)}`\n"
        f"• **Таймер Discord:** {to_discord_timestamp(scheduled_dt, 'F')} ({to_discord_timestamp(scheduled_dt, 'R')})\n"
        f"• **Попытка:** `{attempt_num}` из `{max_attempts}`\n"
    )
    if note:
        desc += f"• **Примечание:** *{note}*\n"

    desc += (
        f"\nℹ️ **ВАЖНАЯ ИНФОРМАЦИЯ ДЛЯ КАНДИДАТА:**\n"
        f"• Вам **автоматически выдан доступ** к голосовому каналу {voice_channel.mention}.\n"
        f"• Бот отправит вам личные напоминания **за 15 минут** и **за 5 минут** до начала.\n"
        f"• Пожалуйста, настройте микрофон заранее и ознакомьтесь с регламентом сервера.\n"
        f"• Всего дается **{max_attempts} попытки**. 3 пропуска/провала подряд влекут блокировку на 1 месяц."
    )

    embed = discord.Embed(
        title="🎓 НАЗНАЧЕНА АТТЕСТАЦИЯ В СТАФФ • FLORYMINE",
        description=desc,
        color=COLOR_FLORY_ORANGE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1234567890.png" if False else DEFAULT_FOOTER_ICON)
    embed.set_footer(
        text="FloryGuard Staff Management • Личный кабинет кандидата",
        icon_url=DEFAULT_FOOTER_ICON,
    )
    return embed


def attestation_audit_embed(
    action_title: str,
    candidate: discord.User | discord.Member,
    curator: discord.User | discord.Member,
    target_role_name: str,
    voice_channel: Optional[discord.VoiceChannel | discord.abc.GuildChannel],
    scheduled_dt: Optional[datetime],
    status_text: str,
    color: int = COLOR_PRIMARY,
    details: str = "",
) -> discord.Embed:
    from utils.time_parser import format_msk, to_discord_timestamp

    desc = (
        f"👤 **Кандидат:** {candidate.mention} (`{candidate.name}` | ID: `{candidate.id}`)\n"
        f"🛡️ **Куратор:** {curator.mention} (`{curator.name}` | ID: `{curator.id}`)\n"
        f"🏷️ **Должность:** **{target_role_name}**\n"
        f"📊 **Статус:** **{status_text}**\n"
    )
    if voice_channel:
        desc += f"🎙️ **Канал:** {voice_channel.mention} (`{voice_channel.name}`)\n"
    if scheduled_dt:
        desc += f"⏱️ **Время проведения:** `{format_msk(scheduled_dt)}` ({to_discord_timestamp(scheduled_dt, 'R')})\n"
    if details:
        desc += f"\n📝 **Детали / Причина:**\n{details}\n"

    embed = discord.Embed(
        title=action_title,
        description=desc,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text="FloryGuard Security Audit • Attestation System",
        icon_url=DEFAULT_FOOTER_ICON,
    )
    return embed


def attestation_reminder_embed(
    candidate: discord.User | discord.Member,
    curator: discord.User | discord.Member,
    target_role_name: str,
    voice_channel: discord.VoiceChannel | discord.abc.GuildChannel,
    scheduled_dt: datetime,
    minutes_left: int,
) -> discord.Embed:
    from utils.time_parser import to_discord_timestamp

    urgent = minutes_left <= 5
    title = "🚨 НАПОМИНАНИЕ: АТТЕСТАЦИЯ ЧЕРЕЗ 5 МИНУТ!" if urgent else "⏱️ НАПОМИНАНИЕ О СКОРОЙ АТТЕСТАЦИИ"
    color = COLOR_DANGER if urgent else COLOR_WARNING

    desc = (
        f"Внимание, {candidate.mention}!\n\n"
        f"До начала вашей аттестации на должность **{target_role_name}** осталось ровно **{minutes_left} минут**!\n\n"
        f"🎙️ **Голосовой канал:** {voice_channel.mention}\n"
        f"👤 **Проводящий куратор:** {curator.mention}\n"
        f"⏳ **Время начала:** {to_discord_timestamp(scheduled_dt, 't')} ({to_discord_timestamp(scheduled_dt, 'R')})\n\n"
        f"⚠️ Пожалуйста, зайдите в указанный голосовой канал и ожидайте подключения куратора."
    )

    embed = discord.Embed(
        title=title,
        description=desc,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text="FloryGuard Staff Management • Автоматическое напоминание",
        icon_url=DEFAULT_FOOTER_ICON,
    )
    return embed


def attestation_result_dm_embed(
    candidate: discord.User | discord.Member,
    curator: discord.User | discord.Member,
    target_role_name: str,
    status: str,
    reason: str,
    consecutive_fails: int = 0,
    is_blacklisted: bool = False,
    blacklisted_until_str: Optional[str] = None,
) -> discord.Embed:
    if status == "PASSED":
        title = "🎉 АТТЕСТАЦИЯ УСПЕШНО ПРОЙДЕНА!"
        color = COLOR_SUCCESS
        desc = (
            f"Поздравляем, {candidate.mention}!\n\n"
            f"Вы успешно сдали квалификационную аттестацию на должность **{target_role_name}**!\n"
            f"Вам выданы соответствующие роли на сервере Discord и привилегии на серверах Minecraft.\n\n"
            f"👤 **Принимающий куратор:** {curator.mention}\n"
        )
        if reason:
            desc += f"📝 **Отзыв куратора:** *{reason}*\n"
        desc += "\n🚀 Желаем успешной, честной и продуктивной работы на проекте FloryMine!"
    elif status == "FAILED":
        title = "❌ АТТЕСТАЦИЯ НЕ СДАНА"
        color = COLOR_DANGER
        desc = (
            f"Здравствуйте, {candidate.mention}.\n\n"
            f"К сожалению, ваша аттестация на должность **{target_role_name}** не была сдана.\n"
            f"👤 **Куратор:** {curator.mention}\n"
            f"📌 **Причина / Замечания:** `{reason or 'Не набран проходной балл'}`\n\n"
        )
        if is_blacklisted:
            desc += (
                f"🚫 **ВНИМАНИЕ (3-Strike Rule):**\n"
                f"Вы получили 3 неудачные попытки подряд. На ваш аккаунт наложен **запрет на участие в аттестациях на 1 месяц**"
            )
            if blacklisted_until_str:
                desc += f" (до `{blacklisted_until_str}`).\n"
            else:
                desc += ".\n"
            desc += "Досрочный пересмотр возможен только через обращение к Высшей Администрации сервера."
        else:
            remaining = max(0, 3 - consecutive_fails)
            desc += f"📊 Неудачных попыток подряд: `{consecutive_fails}/3` (Осталось попыток: `{remaining}`)."
    else:  # SKIPPED
        title = "⚠️ АТТЕСТАЦИЯ ПРОПУЩЕНА (НЕЯВКА)"
        color = COLOR_WARNING
        desc = (
            f"Здравствуйте, {candidate.mention}.\n\n"
            f"Зафиксирована неявка на аттестацию на должность **{target_role_name}**.\n"
            f"👤 **Куратор:** {curator.mention}\n"
            f"📌 **Причина:** `{reason or 'Кандидат не явился в голосовой канал'}`\n\n"
        )
        if is_blacklisted:
            desc += (
                f"🚫 **ВНИМАНИЕ (3-Strike Rule):**\n"
                f"Вы накопили 3 пропуска/провала подряд. Вам установлен **запрет на аттестации на 1 месяц**.\n"
                f"Снять блокировку может только Высшая Администрация."
            )
        else:
            remaining = max(0, 3 - consecutive_fails)
            desc += f"📊 Неудачных попыток подряд: `{consecutive_fails}/3` (Осталось попыток: `{remaining}`)."

    embed = discord.Embed(
        title=title,
        description=desc,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text="FloryGuard Staff Management • Итоги аттестации",
        icon_url=DEFAULT_FOOTER_ICON,
    )
    return embed

