import asyncio
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, Tuple, Union

from config import TRUSTED_BOT_IDS
from core.permissions import is_module_enabled, is_whitelisted, has_permission
from utils.logger import logger
from utils.embeds import anti_nuke_alert_embed


class AntiNukeCog(commands.Cog, name="AntiNuke"):
    """
    Real-time protection against unauthorized server modifications, channel/role tampering,
    mass bans, and unauthorized role assignments.
    """
    def __init__(self, bot):
        self.bot = bot

    async def _get_audit_executor(
        self,
        guild: discord.Guild,
        action: Union[discord.AuditLogAction, Tuple[discord.AuditLogAction, ...]],
        target_id: Optional[int] = None,
        max_age_seconds: float = 6.0
    ) -> Optional[Union[discord.Member, discord.User]]:
        """
        Safely look up the responsible moderator in the server's audit logs.
        Supports either a single AuditLogAction or a tuple of actions.
        """
        await asyncio.sleep(0.6)  # Give Discord audit logs a moment to register
        now = datetime.now(timezone.utc)

        try:
            if isinstance(action, tuple):
                entries_iter = guild.audit_logs(limit=10)
                allowed_actions = set(action)
            else:
                entries_iter = guild.audit_logs(limit=5, action=action)
                allowed_actions = {action}

            async for entry in entries_iter:
                if entry.action not in allowed_actions:
                    continue

                time_diff = (now - entry.created_at).total_seconds()
                if time_diff > max_age_seconds:
                    continue

                if target_id is not None and entry.target and getattr(entry.target, "id", None) != target_id:
                    continue

                if entry.user:
                    # Ignore bot's own automated actions and trusted project bots (FloryAuthentication / FloryModerators)
                    if entry.user.id == self.bot.user.id or entry.user.id in TRUSTED_BOT_IDS:
                        return None

                    member = guild.get_member(entry.user.id)
                    if member:
                        return member
                    return entry.user
        except discord.Forbidden:
            logger.error(f"Cannot read Audit Logs in {guild.name} (Missing 'View Audit Log' permission)")
        except Exception as e:
            logger.error(f"Error reading audit log in {guild.name}: {e}")
        return None

    # ==========================================
    # 1. MEMBER BAN PROTECTION (UNBAN & DEMOTE)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.ban, target_id=user.id)
        if not executor or executor.id == self.bot.user.id:
            return

        # Check granular permission: ban_members
        if await has_permission(guild.id, executor.id, "ban_members"):
            return

        logger.warning(f"UNAUTHORIZED BAN detected in {guild.name} by {executor.name} ({executor.id}) against {user.name}")

        # Immediate Rollback: Unban victim
        unban_ok = False
        try:
            await guild.unban(user, reason=f"FloryGuard Anti-Nuke: Несанкционированная блокировка от {executor.name}")
            unban_ok = True
        except Exception as e:
            logger.error(f"Failed to unban victim {user.name}: {e}")

        # Immediate Demote Action
        demoted = False
        if isinstance(executor, discord.Member):
            demoted = await self.bot.quarantine_member(guild, executor, "Несанкционированная блокировка пользователя (Anti-Nuke)")

        # Send Security Log
        embed = anti_nuke_alert_embed(
            action_type="Блокировка участника (Ban)",
            executor=executor,
            target_info=f"{user.name} ({user.id})",
            rollback_status="Участник разбанен (Откат выполнен)" if unban_ok else "Ошибка разбана",
            immediate_demote=demoted
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 1.1 MEMBER KICK PROTECTION (DEMOTE)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.kick, target_id=member.id)
        if not executor or executor.id == self.bot.user.id:
            return

        # Check granular permission: kick_members
        if await has_permission(guild.id, executor.id, "kick_members"):
            return

        logger.warning(f"UNAUTHORIZED KICK in {guild.name} by {executor.name} ({executor.id}) against {member.name}")

        demoted = False
        if isinstance(executor, discord.Member):
            demoted = await self.bot.quarantine_member(guild, executor, f"Несанкционированное изгнание (кик) {member.name}")

        embed = anti_nuke_alert_embed(
            action_type="Изгнание участника (Kick)",
            executor=executor,
            target_info=f"{member.name} ({member.id})",
            rollback_status="Участник изгнан (нарушитель изолирован)",
            immediate_demote=demoted
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 1.2 SERVER EDIT PROTECTION (ROLLBACK & DEMOTE)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if not is_module_enabled(after, "anti_nuke_enabled"):
            return

        changed = (before.name != after.name or before.icon != after.icon or before.description != after.description)
        if not changed:
            return

        executor = await self._get_audit_executor(after, discord.AuditLogAction.guild_update)
        if not executor or executor.id == self.bot.user.id:
            return

        # Check granular permission: server_edit
        if await has_permission(after.id, executor.id, "server_edit"):
            return

        logger.warning(f"UNAUTHORIZED SERVER UPDATE in {after.name} by {executor.name}")

        rollback_ok = False
        try:
            icon_bytes = None
            if before.icon:
                try:
                    icon_bytes = await before.icon.read()
                except Exception:
                    pass
            await after.edit(
                name=before.name,
                description=before.description,
                icon=icon_bytes,
                reason="FloryGuard Anti-Nuke: Откат несанкционированного изменения параметров сервера"
            )
            rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to rollback server edit: {e}")

        demoted = False
        if isinstance(executor, discord.Member):
            demoted = await self.bot.quarantine_member(after, executor, "Несанкционированное изменение настроек сервера")

        embed = anti_nuke_alert_embed(
            action_type="Изменение настроек сервера",
            executor=executor,
            target_info=f"Сервер {before.name}",
            rollback_status="Настройки сервера возвращены в исходное состояние" if rollback_ok else "Ошибка отката",
            immediate_demote=demoted
        )
        await self.bot.send_security_log(after, embed)

    # ==========================================
    # 2. CHANNEL DELETION PROTECTION (ROLLBACK & WARN)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_delete, target_id=channel.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "channels_delete"):
            return

        logger.warning(f"UNAUTHORIZED CHANNEL DELETE in {guild.name} by {executor.name} ({executor.id}): #{channel.name}")

        # 1. Rollback: Recreate channel with identical properties
        rollback_ok = False
        try:
            category = channel.category
            overwrites = channel.overwrites
            position = channel.position

            if isinstance(channel, discord.TextChannel):
                await guild.create_text_channel(
                    name=channel.name,
                    category=category,
                    topic=channel.topic,
                    slowmode_delay=channel.slowmode_delay,
                    nsfw=channel.nsfw,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление удаленного канала"
                )
                rollback_ok = True
            elif isinstance(channel, discord.VoiceChannel):
                await guild.create_voice_channel(
                    name=channel.name,
                    category=category,
                    bitrate=channel.bitrate,
                    user_limit=channel.user_limit,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление удаленного голосового канала"
                )
                rollback_ok = True
            elif isinstance(channel, discord.CategoryChannel):
                await guild.create_category(
                    name=channel.name,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление категории"
                )
                rollback_ok = True
            elif isinstance(channel, discord.StageChannel):
                await guild.create_stage_channel(
                    name=channel.name,
                    category=category,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление трибуны"
                )
                rollback_ok = True
            elif isinstance(channel, discord.ForumChannel):
                await guild.create_forum(
                    name=channel.name,
                    category=category,
                    topic=channel.topic,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление форума"
                )
                rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to rollback deleted channel {channel.name}: {e}")

        # 2. Issue Warning (1/5, 2/5...) and demote on 5/5
        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Удаление канала #{channel.name}"
            )

        # 3. Log alert
        embed = anti_nuke_alert_embed(
            action_type="Удаление канала",
            executor=executor,
            target_info=f"#{channel.name} (ID: {channel.id})",
            rollback_status="Канал успешно восстановлен" if rollback_ok else "Ошибка восстановления",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 3. CHANNEL CREATION PROTECTION (ROLLBACK & WARN)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_create, target_id=channel.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "channels_create"):
            return

        logger.warning(f"UNAUTHORIZED CHANNEL CREATE in {guild.name} by {executor.name}: #{channel.name}")

        # 1. Rollback: Delete unauthorized channel
        rollback_ok = False
        try:
            await channel.delete(reason="FloryGuard Anti-Nuke: Удаление несанкционированного канала")
            rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to delete unauthorized channel #{channel.name}: {e}")

        # 2. Issue Warning
        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Создание канала #{channel.name} без прав"
            )

        # 3. Log alert
        embed = anti_nuke_alert_embed(
            action_type="Создание канала",
            executor=executor,
            target_info=f"#{channel.name}",
            rollback_status="Несанкционированный канал удален" if rollback_ok else "Ошибка удаления",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 4. CHANNEL UPDATE / PERMISSIONS PROTECTION
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        guild = after.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        # Check if meaningful properties changed
        changed = False
        if before.name != after.name or getattr(before, "topic", "") != getattr(after, "topic", ""):
            changed = True
        elif before.overwrites != after.overwrites:
            changed = True
        elif getattr(before, "nsfw", False) != getattr(after, "nsfw", False):
            changed = True
        elif getattr(before, "slowmode_delay", 0) != getattr(after, "slowmode_delay", 0):
            changed = True

        if not changed:
            return

        executor = await self._get_audit_executor(
            guild,
            (
                discord.AuditLogAction.channel_update,
                discord.AuditLogAction.channel_overwrite_create,
                discord.AuditLogAction.channel_overwrite_update,
                discord.AuditLogAction.channel_overwrite_delete,
            ),
            target_id=after.id
        )

        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "channels_edit"):
            return

        logger.warning(f"UNAUTHORIZED CHANNEL UPDATE in {guild.name} by {executor.name}: #{after.name}")

        # Rollback properties
        rollback_ok = False
        try:
            if isinstance(after, discord.TextChannel) and isinstance(before, discord.TextChannel):
                await after.edit(
                    name=before.name,
                    topic=before.topic,
                    nsfw=before.nsfw,
                    slowmode_delay=before.slowmode_delay,
                    overwrites=before.overwrites,
                    reason="FloryGuard Anti-Nuke: Откат настроек канала"
                )
                rollback_ok = True
            elif isinstance(after, (discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel, discord.ForumChannel)):
                await after.edit(
                    name=before.name,
                    overwrites=before.overwrites,
                    reason="FloryGuard Anti-Nuke: Откат настроек канала"
                )
                rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to rollback channel update #{after.name}: {e}")

        # Warn executor
        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Изменение настроек/прав канала #{after.name}"
            )

        embed = anti_nuke_alert_embed(
            action_type="Изменение настроек канала",
            executor=executor,
            target_info=f"#{after.name}",
            rollback_status="Настройки канала возвращены в исходное состояние" if rollback_ok else "Ошибка отката",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 5. ROLE DELETION PROTECTION (ROLLBACK & WARN)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_delete, target_id=role.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "roles_delete"):
            return

        logger.warning(f"UNAUTHORIZED ROLE DELETE in {guild.name} by {executor.name}: @{role.name}")

        # 1. Rollback: Recreate role
        rollback_ok = False
        try:
            new_role = await guild.create_role(
                name=role.name,
                permissions=role.permissions,
                color=role.color,
                hoist=role.hoist,
                mentionable=role.mentionable,
                reason="FloryGuard Anti-Nuke: Восстановление удаленной роли"
            )
            # Try to place near previous position
            try:
                await new_role.edit(position=max(1, min(role.position, guild.me.top_role.position - 1)))
            except Exception:
                pass
            rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to restore deleted role {role.name}: {e}")

        # 2. Issue Warning
        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Удаление роли @{role.name}"
            )

        embed = anti_nuke_alert_embed(
            action_type="Удаление роли",
            executor=executor,
            target_info=f"@{role.name} (ID: {role.id})",
            rollback_status="Роль заново создана с аналогичными правами" if rollback_ok else "Ошибка восстановления роли",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 6. ROLE CREATION PROTECTION
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        guild = role.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_create, target_id=role.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "roles_create"):
            return

        logger.warning(f"UNAUTHORIZED ROLE CREATE in {guild.name} by {executor.name}: @{role.name}")

        # Rollback
        rollback_ok = False
        try:
            await role.delete(reason="FloryGuard Anti-Nuke: Удаление несанкционированной роли")
            rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to delete unauthorized role {role.name}: {e}")

        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Создание новой роли @{role.name}"
            )

        embed = anti_nuke_alert_embed(
            action_type="Создание роли",
            executor=executor,
            target_info=f"@{role.name}",
            rollback_status="Несанкционированная роль удалена" if rollback_ok else "Ошибка удаления роли",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 7. ROLE UPDATE PROTECTION (PERMISSIONS / NAME / COLOR)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        if (
            before.permissions == after.permissions
            and before.name == after.name
            and before.color == after.color
            and before.hoist == after.hoist
            and before.mentionable == after.mentionable
        ):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_update, target_id=after.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "roles_edit"):
            return

        logger.warning(f"UNAUTHORIZED ROLE UPDATE in {guild.name} by {executor.name}: @{after.name}")

        rollback_ok = False
        try:
            await after.edit(
                name=before.name,
                permissions=before.permissions,
                color=before.color,
                hoist=before.hoist,
                mentionable=before.mentionable,
                reason="FloryGuard Anti-Nuke: Откат изменений роли"
            )
            rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to rollback role update @{after.name}: {e}")

        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Изменение прав/названий роли @{after.name}"
            )

        embed = anti_nuke_alert_embed(
            action_type="Изменение параметров роли",
            executor=executor,
            target_info=f"@{after.name}",
            rollback_status="Параметры и права роли возвращены" if rollback_ok else "Ошибка отката",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 8. MEMBER ROLE & TIMEOUT TAMPERING (ПКМ / CONTEXT MENU)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        # 8.1 Check if Timeout changed
        if before.timed_out_until != after.timed_out_until:
            executor = await self._get_audit_executor(guild, discord.AuditLogAction.member_update, target_id=after.id)
            if not executor or executor.id == self.bot.user.id:
                return

            if await has_permission(guild.id, executor.id, "timeout_members"):
                return

            logger.warning(f"UNAUTHORIZED TIMEOUT CHANGE in {guild.name} by {executor.name} on target {after.name}")
            rollback_ok = False
            try:
                await after.timeout(before.timed_out_until, reason="FloryGuard Anti-Nuke: Откат несанкционированного таймаута")
                rollback_ok = True
            except Exception as e:
                logger.error(f"Failed to rollback timeout for {after.name}: {e}")

            warn_count = 0
            if isinstance(executor, discord.Member):
                _, warn_count, _ = await self.bot.issue_warning_and_check(
                    guild=guild,
                    member=executor,
                    reason=f"Несанкционированное изменение таймаута у {after.name}"
                )

            embed = anti_nuke_alert_embed(
                action_type="Выдача/Снятие таймаута (Мут)",
                executor=executor,
                target_info=f"{after.name} ({after.id})",
                rollback_status="Статус таймаута возвращен" if rollback_ok else "Ошибка отката",
                warn_count=warn_count
            )
            await self.bot.send_security_log(guild, embed)
            return

        # 8.2 Check if roles were added or removed
        if set(before.roles) == set(after.roles):
            return

        guild_role_ids = {r.id for r in guild.roles}
        added_roles = [
            r for r in (set(after.roles) - set(before.roles))
            if not r.is_default() and not r.managed
        ]
        removed_roles = [
            r for r in (set(before.roles) - set(after.roles))
            if not r.is_default() and not r.managed and r.id in guild_role_ids
        ]

        if not added_roles and not removed_roles:
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.member_role_update, target_id=after.id)
        if not executor or executor.id == self.bot.user.id or executor.id in TRUSTED_BOT_IDS:
            return

        if await has_permission(guild.id, executor.id, "roles_assign"):
            return

        logger.warning(f"UNAUTHORIZED ROLE ASSIGNMENT in {guild.name} by {executor.name} on target {after.name}")

        # Rollback roles on the target member safely using add_roles / remove_roles
        rollback_ok = False
        bot_top_role = guild.me.top_role
        try:
            removable = [r for r in added_roles if r < bot_top_role]
            restorable = [r for r in removed_roles if r < bot_top_role]
            if removable:
                await after.remove_roles(*removable, reason="FloryGuard Anti-Nuke: Откат выдачи ролей")
                rollback_ok = True
            if restorable:
                await after.add_roles(*restorable, reason="FloryGuard Anti-Nuke: Откат снятия ролей")
                rollback_ok = True
        except Exception as e:
            logger.error(f"Failed to rollback roles for {after.name}: {e}")

        # Issue warning to the executor
        warn_count = 0
        if isinstance(executor, discord.Member):
            _, warn_count, _ = await self.bot.issue_warning_and_check(
                guild=guild,
                member=executor,
                reason=f"Несанкционированная выдача/снятие ролей у {after.name}"
            )

        embed = anti_nuke_alert_embed(
            action_type="Выдача/Снятие ролей участнику",
            executor=executor,
            target_info=f"{after.name} ({after.id})",
            rollback_status="Роли участника возвращены в исходное состояние" if rollback_ok else "Ошибка отката",
            warn_count=warn_count
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 9. ROGUE BOT ADD PROTECTION (INSTANT BOT BAN & DEMOTE)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        if not member.bot:
            return

        # Look up who invited the bot
        executor = await self._get_audit_executor(guild, discord.AuditLogAction.bot_add, target_id=member.id)
        if not executor or executor.id == self.bot.user.id:
            return

        # Check permission: If the bot itself is in whitelist/admin, or the executor is whitelisted/admin:
        if await is_whitelisted(guild.id, member.id):
            return
        if await is_whitelisted(guild.id, executor.id):
            return

        logger.critical(f"UNAUTHORIZED BOT INVITATION in {guild.name}: {member.name} ({member.id}) by {executor.name} ({executor.id})")

        # 1. Immediately BAN the unauthorized bot
        banned_ok = False
        try:
            await member.ban(reason="FloryGuard Anti-Nuke: Несанкционированное добавление стороннего бота (Защита от краша)")
            banned_ok = True
        except Exception as e:
            logger.error(f"Failed to ban unauthorized bot {member.name}: {e}")

        # 2. Immediately Demote / Quarantine the executor who added the bot
        demoted_ok = False
        if isinstance(executor, discord.Member):
            demoted_ok = await self.bot.quarantine_member(
                guild=guild,
                member=executor,
                reason="Несанкционированное добавление стороннего бота (Попытка краша/рейда)"
            )

        # 3. Send Critical Security Log
        embed = anti_nuke_alert_embed(
            action_type="🚨 Внедрение стороннего бота (Bot Add)",
            executor=executor,
            target_info=f"Бот: {member.name} (`{member.id}`)",
            rollback_status="Сторонний бот заблокирован (BAN)" if banned_ok else "Ошибка бана бота",
            immediate_demote=demoted_ok
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 10. WEBHOOK CREATION PROTECTION
    # ==========================================
    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not is_module_enabled(guild, "anti_nuke_enabled"):
            return

        # Note: In Discord Audit Log for webhook_create, entry.target is the Webhook (not the channel)
        executor = await self._get_audit_executor(guild, discord.AuditLogAction.webhook_create, target_id=None)
        if not executor or executor.id == self.bot.user.id:
            return

        if await has_permission(guild.id, executor.id, "webhooks_manage"):
            return

        logger.critical(f"UNAUTHORIZED WEBHOOK CREATED in {guild.name} in #{channel.name} by {executor.name}")

        # Delete unauthorized webhooks in channel
        deleted_count = 0
        try:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
                webhooks = await channel.webhooks()
                for wh in webhooks:
                    if wh.user and wh.user.id != self.bot.user.id and not await has_permission(guild.id, wh.user.id, "webhooks_manage"):
                        await wh.delete(reason="FloryGuard Anti-Nuke: Удаление несанкционированного вебхука")
                        deleted_count += 1
        except Exception as e:
            logger.error(f"Failed to delete rogue webhooks in #{channel.name}: {e}")

        # Quarantine executor
        demoted = False
        if isinstance(executor, discord.Member):
            demoted = await self.bot.quarantine_member(guild, executor, "Создание несанкционированного вебхука")

        embed = anti_nuke_alert_embed(
            action_type="🚨 Создание несанкционированного вебхука",
            executor=executor,
            target_info=f"Канал #{channel.name} (Удалено вебхуков: {deleted_count})",
            rollback_status=f"Удалено вебхуков: {deleted_count}",
            immediate_demote=demoted
        )
        await self.bot.send_security_log(guild, embed)


async def setup(bot):
    await bot.add_cog(AntiNukeCog(bot))
