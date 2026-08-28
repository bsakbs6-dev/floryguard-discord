import asyncio
import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from core.permissions import is_authorized_guild, is_admin, is_whitelisted
from utils.logger import logger
from utils.embeds import anti_nuke_alert_embed


class AntiNukeCog(commands.Cog, name="AntiNuke"):
    """
    Real-time protection against unauthorized server modifications, channel/role tampering,
    mass bans, and unauthorized role assignments.
    """
    def __init__(self, bot):
        self.bot = bot
        # Debounce/lock cache to prevent duplicate audit log triggers
        self._processed_events = set()

    async def _get_audit_executor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: Optional[int] = None,
        max_age_seconds: float = 6.0
    ) -> Optional[discord.Member]:
        """
        Safely look up the responsible moderator in the server's audit logs.
        """
        await asyncio.sleep(0.6)  # Give Discord audit logs a moment to register
        now = datetime.now(timezone.utc)

        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                time_diff = (now - entry.created_at).total_seconds()
                if time_diff > max_age_seconds:
                    continue

                if target_id is not None and entry.target and getattr(entry.target, "id", None) != target_id:
                    continue

                if entry.user:
                    # Ignore bot's own automated actions
                    if entry.user.id == self.bot.user.id:
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
    # 1. MEMBER BAN PROTECTION (IMMEDIATE DEMOTE)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        if not is_authorized_guild(guild):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.ban, target_id=user.id)
        if not executor or executor.id == self.bot.user.id:
            return

        # Check if executor is an authorized Admin
        if await is_admin(guild.id, executor.id):
            return

        logger.warning(f"UNAUTHORIZED BAN detected in {guild.name} by {executor.name} ({executor.id}) against {user.name}")

        # Immediate Demote Action
        demoted = False
        if isinstance(executor, discord.Member):
            # If user is not whitelisted, strip roles immediately
            if not await is_whitelisted(guild.id, executor.id):
                demoted = await self.bot.quarantine_member(guild, executor, "Несанкционированная блокировка пользователя (Anti-Nuke)")

        # Send Security Log
        embed = anti_nuke_alert_embed(
            action_type="Блокировка участника (Ban)",
            executor=executor,
            target_info=f"{user.name} ({user.id})",
            rollback_status="Заблокирован (ручная проверка)",
            immediate_demote=True
        )
        await self.bot.send_security_log(guild, embed)

    # ==========================================
    # 2. CHANNEL DELETION PROTECTION (ROLLBACK & WARN)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        if not is_authorized_guild(guild):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_delete, target_id=channel.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
            return

        logger.warning(f"UNAUTHORIZED CHANNEL DELETE in {guild.name} by {executor.name} ({executor.id}): #{channel.name}")

        # 1. Rollback: Recreate channel with identical properties
        rollback_ok = False
        try:
            category = channel.category
            overwrites = channel.overwrites
            position = channel.position

            if isinstance(channel, discord.TextChannel):
                new_ch = await guild.create_text_channel(
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
                new_ch = await guild.create_voice_channel(
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
                new_ch = await guild.create_category(
                    name=channel.name,
                    overwrites=overwrites,
                    position=position,
                    reason="FloryGuard Anti-Nuke: Восстановление категории"
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
        if not is_authorized_guild(guild):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_create, target_id=channel.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
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
        if not is_authorized_guild(guild):
            return

        # Check if meaningful properties changed
        changed = False
        if before.name != after.name or getattr(before, "topic", "") != getattr(after, "topic", ""):
            changed = True
        elif before.overwrites != after.overwrites:
            changed = True

        if not changed:
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_update, target_id=after.id)
        if not executor:
            executor = await self._get_audit_executor(guild, discord.AuditLogAction.channel_overwrite_update, target_id=after.id)

        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
            return

        logger.warning(f"UNAUTHORIZED CHANNEL UPDATE in {guild.name} by {executor.name}: #{after.name}")

        # Rollback properties
        rollback_ok = False
        try:
            if isinstance(after, discord.TextChannel) and isinstance(before, discord.TextChannel):
                await after.edit(
                    name=before.name,
                    topic=before.topic,
                    overwrites=before.overwrites,
                    reason="FloryGuard Anti-Nuke: Откат настроек канала"
                )
                rollback_ok = True
            elif isinstance(after, discord.VoiceChannel) and isinstance(before, discord.VoiceChannel):
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
        if not is_authorized_guild(guild):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_delete, target_id=role.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
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
                await new_role.edit(position=max(1, role.position))
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
        if not is_authorized_guild(guild):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_create, target_id=role.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
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
    # 7. ROLE UPDATE PROTECTION (PERMISSIONS / NAME)
    # ==========================================
    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        if not is_authorized_guild(guild):
            return

        if before.permissions == after.permissions and before.name == after.name:
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.role_update, target_id=after.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
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
    # 8. MEMBER ROLE TAMPERING (ПКМ / CONTEXT MENU)
    # ==========================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if not is_authorized_guild(guild):
            return

        # Note: Timeouts are explicitly allowed! (timed_out_until check)
        if before.timed_out_until != after.timed_out_until:
            return

        # Check if roles were added or removed
        if set(before.roles) == set(after.roles):
            return

        executor = await self._get_audit_executor(guild, discord.AuditLogAction.member_role_update, target_id=after.id)
        if not executor or executor.id == self.bot.user.id:
            return

        if await is_admin(guild.id, executor.id):
            return

        # Roles were altered by someone who is not in Admin list
        logger.warning(f"UNAUTHORIZED ROLE ASSIGNMENT in {guild.name} by {executor.name} on target {after.name}")

        # Rollback roles on the target member
        rollback_ok = False
        try:
            await after.edit(roles=before.roles, reason="FloryGuard Anti-Nuke: Откат выдачи/снятия ролей")
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


async def setup(bot):
    await bot.add_cog(AntiNukeCog(bot))
