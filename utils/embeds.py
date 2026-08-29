import discord
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Color constants
COLOR_PRIMARY = 0x5865F2    # Blurple
COLOR_SUCCESS = 0x2ECC71    # Green
COLOR_WARNING = 0xF1C40F    # Yellow / Orange
COLOR_DANGER = 0xE74C3C     # Red
COLOR_DARK = 0x1E1F22       # Dark Gray


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
    embed.set_footer(text=footer, icon_url="https://cdn.discordapp.com/emojis/1069279565509312512.webp?size=96")
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
    embed.set_footer(text="FloryGuard Security Bot", icon_url="https://cdn.discordapp.com/emojis/1069279565509312512.webp?size=96")
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

    embed.set_footer(text="FloryGuard Audit System", icon_url="https://cdn.discordapp.com/emojis/1069279565509312512.webp?size=96")
    return embed


def automod_alert_embed(
    member: discord.Member,
    channel: discord.TextChannel,
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
    embed.add_field(name="📝 Содержимое", value=f"```{safe_content}```", inline=False)
    embed.add_field(name="⚙️ Действие", value=f"✅ {action}", inline=True)

    embed.set_footer(text="FloryGuard AutoMod", icon_url="https://cdn.discordapp.com/emojis/1069279565509312512.webp?size=96")
    return embed


def profile_embed(
    member: discord.Member,
    is_owner: bool,
    is_senior_admin: bool,
    is_admin: bool,
    is_whitelisted: bool,
    warnings: List[Dict[str, Any]]
) -> discord.Embed:
    # Determine Highest Security Rank
    if is_owner:
        rank_badge = "👑 **Владелец (Owner)**"
    elif is_senior_admin:
        rank_badge = "💎 **Высший Администратор (Senior Admin)**"
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
    embed.add_field(name="🔰 Уровень доступа", value=rank_badge, inline=False)

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
    roles_list = [r.mention for r in member.roles if r.name != "@everyone"]
    roles_str = ", ".join(roles_list[:10]) if roles_list else "*Нет ролей*"
    if len(roles_list) > 10:
        roles_str += f" *(и ещё {len(roles_list) - 10})*"
    embed.add_field(name="🎭 Роли", value=roles_str, inline=False)

    embed.set_footer(text="FloryGuard Profile System • Данные конфиденциальны")
    return embed
