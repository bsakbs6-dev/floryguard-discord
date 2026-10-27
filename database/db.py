import aiosqlite
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import DATABASE_PATH, WARN_EXPIRY_DAYS


class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        # Ensure parent directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self):
        """Initialize database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")

            # Table for Admins (guild-specific and global)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    added_at TIMESTAMP NOT NULL,
                    title TEXT DEFAULT 'Администратор Безопасности',
                    permissions TEXT DEFAULT 'all',
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

            # Safe migration: ensure title and permissions columns exist
            async with db.execute("PRAGMA table_info(admins);") as cursor:
                admin_cols = [row[1] for row in await cursor.fetchall()]
                if "title" not in admin_cols:
                    await db.execute("ALTER TABLE admins ADD COLUMN title TEXT DEFAULT 'Администратор Безопасности';")
                if "permissions" not in admin_cols:
                    await db.execute("ALTER TABLE admins ADD COLUMN permissions TEXT DEFAULT 'all';")

            # Table for Whitelist
            await db.execute("""
                CREATE TABLE IF NOT EXISTS whitelist (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    added_at TIMESTAMP NOT NULL,
                    reason TEXT,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

            # Table for Warnings / Reprimands (Anti-Nuke & Staff warnings with expiration)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    situation TEXT DEFAULT '',
                    duration_text TEXT DEFAULT '7 дн.',
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    is_active INTEGER DEFAULT 1
                );
            """)

            # Safe migration for warnings columns
            async with db.execute("PRAGMA table_info(warnings);") as cursor:
                warn_cols = [row[1] for row in await cursor.fetchall()]
                if "situation" not in warn_cols:
                    await db.execute("ALTER TABLE warnings ADD COLUMN situation TEXT DEFAULT '';")
                if "duration_text" not in warn_cols:
                    await db.execute("ALTER TABLE warnings ADD COLUMN duration_text TEXT DEFAULT '7 дн.';")

            # Table for Staff Profile & FloryModerators Minecraft Plugin Sync
            await db.execute("""
                CREATE TABLE IF NOT EXISTS staff_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    minecraft_nick TEXT,
                    primary_group TEXT,
                    online_seconds INTEGER DEFAULT 0,
                    norm_seconds INTEGER DEFAULT 0,
                    warns_issued INTEGER DEFAULT 0,
                    bans_issued INTEGER DEFAULT 0,
                    reports_accepted INTEGER DEFAULT 0,
                    mutes_issued INTEGER DEFAULT 0,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

            async with db.execute("PRAGMA table_info(staff_stats);") as cursor:
                staff_cols = [row[1] for row in await cursor.fetchall()]
                if "primary_group" not in staff_cols:
                    await db.execute("ALTER TABLE staff_stats ADD COLUMN primary_group TEXT;")

            # Table for Nickname-keyed Staff Stats (when FloryModerators syncs before Discord ID is known)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS staff_stats_by_nick (
                    guild_id INTEGER NOT NULL,
                    lower_nick TEXT NOT NULL,
                    minecraft_nick TEXT NOT NULL,
                    user_id INTEGER DEFAULT 0,
                    primary_group TEXT,
                    online_seconds INTEGER DEFAULT 0,
                    norm_seconds INTEGER DEFAULT 0,
                    warns_issued INTEGER DEFAULT 0,
                    bans_issued INTEGER DEFAULT 0,
                    reports_accepted INTEGER DEFAULT 0,
                    mutes_issued INTEGER DEFAULT 0,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (guild_id, lower_nick)
                );
            """)

            # Table for Server Snapshots (Channels & Roles backup)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    created_by INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
            """)

            # Table for Security Audit Logs
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL
                );
            """)

            # Table for Punishment Logs (FloryModerators bans, mutes, warns with anti-abuse verification)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS punishment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    moderator_id INTEGER DEFAULT 0,
                    moderator_nick TEXT NOT NULL,
                    target_nick TEXT NOT NULL,
                    target_ip TEXT DEFAULT '',
                    punishment_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    duration_seconds INTEGER DEFAULT 0,
                    is_valid_for_stats INTEGER DEFAULT 1,
                    created_at TIMESTAMP NOT NULL,
                    audit_requested INTEGER DEFAULT 0
                );
            """)

            # Table for Guild Settings override
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER,
                    quarantine_role_id INTEGER,
                    automod_enabled INTEGER DEFAULT 1,
                    anti_nuke_enabled INTEGER DEFAULT 1,
                    anti_raid_enabled INTEGER DEFAULT 1
                );
            """)

            await db.commit()

    # --- ADMIN MANAGEMENT ---
    async def add_admin(
        self,
        guild_id: int,
        user_id: int,
        added_by: int,
        title: str = "Администратор Безопасности",
        permissions: str = "all"
    ) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT OR REPLACE INTO admins 
                   (guild_id, user_id, added_by, added_at, title, permissions) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, user_id, added_by, now, title, permissions)
            )
            await db.commit()
            return True

    async def update_admin(
        self,
        guild_id: int,
        user_id: int,
        title: Optional[str] = None,
        permissions: Optional[str] = None
    ) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            updates = []
            params = []
            if title is not None:
                updates.append("title = ?")
                params.append(title)
            if permissions is not None:
                updates.append("permissions = ?")
                params.append(permissions)
            if not updates:
                return False
            params.extend([guild_id, user_id])
            query = f"UPDATE admins SET {', '.join(updates)} WHERE guild_id = ? AND user_id = ?"
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount > 0

    async def get_admin(self, guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM admins WHERE (guild_id = ? OR guild_id = 0) AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def remove_admin(self, guild_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM admins WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_admin(self, guild_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM admins WHERE (guild_id = ? OR guild_id = 0) AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def get_admins(self, guild_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM admins WHERE guild_id = ? OR guild_id = 0",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_user_permissions(self, guild_id: int, user_id: int) -> tuple[set[str], str]:
        """Returns (permissions_set, title) for the specified user."""
        admin = await self.get_admin(guild_id, user_id)
        if not admin:
            return set(), "Участник"
        perms_str = admin.get("permissions") or ""
        title = admin.get("title") or "Администратор Безопасности"
        if perms_str == "all" or "*" in perms_str:
            from core.permissions import AVAILABLE_PERMISSIONS
            return set(AVAILABLE_PERMISSIONS.keys()), title
        if perms_str == "none":
            return set(), title
        return {p.strip() for p in perms_str.split(",") if p.strip() and p.strip() != "none"}, title

    async def set_user_permissions(
        self,
        guild_id: int,
        user_id: int,
        permissions: set[str],
        updated_by: int,
        title: Optional[str] = None
    ) -> bool:
        """Saves updated permissions set for user in admins table."""
        from core.permissions import AVAILABLE_PERMISSIONS
        clean_perms = {p for p in permissions if p and p != "none"}
        if len(clean_perms) >= len(AVAILABLE_PERMISSIONS):
            perms_str = "all"
        elif not clean_perms:
            perms_str = "none"
        else:
            perms_str = ",".join(sorted(clean_perms))

        admin = await self.get_admin(guild_id, user_id)
        if admin:
            final_title = title if title is not None else admin.get("title", "Администратор Безопасности")
            return await self.update_admin(guild_id, user_id, title=final_title, permissions=perms_str)
        else:
            final_title = title or "Администратор Безопасности"
            return await self.add_admin(guild_id, user_id, updated_by, title=final_title, permissions=perms_str)

    # --- WHITELIST MANAGEMENT ---
    async def add_whitelist(self, guild_id: int, user_id: int, added_by: int, reason: str = "No reason provided") -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT OR REPLACE INTO whitelist (guild_id, user_id, added_by, added_at, reason) VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, added_by, now, reason)
            )
            await db.commit()
            return True

    async def remove_whitelist(self, guild_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM whitelist WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_whitelisted(self, guild_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM whitelist WHERE (guild_id = ? OR guild_id = 0) AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def get_whitelist(self, guild_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM whitelist WHERE guild_id = ? OR guild_id = 0",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # --- WARNINGS / REPRIMANDS SYSTEM ---
    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
        expiry_days: int = WARN_EXPIRY_DAYS,
        expiry_seconds: Optional[int] = None,
        situation: str = "",
        duration_text: Optional[str] = None,
    ) -> tuple[int, int]:
        """Add a warning/reprimand. Returns (new_warn_id, active_warns_count)."""
        now = datetime.now(timezone.utc)
        if expiry_seconds is not None:
            expires_at = now + timedelta(seconds=max(60, expiry_seconds))
        else:
            expires_at = now + timedelta(days=expiry_days)

        if not duration_text:
            duration_text = f"{expiry_days} дн."

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO warnings (guild_id, user_id, moderator_id, reason, situation, duration_text, created_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    guild_id,
                    user_id,
                    moderator_id,
                    reason,
                    situation or reason,
                    duration_text,
                    now.isoformat(),
                    expires_at.isoformat(),
                )
            )
            warn_id = cursor.lastrowid
            await db.commit()

        active_count = await self.get_active_warn_count(guild_id, user_id)
        return warn_id, active_count

    async def get_active_warnings(self, guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Fetch all non-expired active warnings for a user."""
        now_str = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Deactivate expired ones first
            await db.execute(
                "UPDATE warnings SET is_active = 0 WHERE is_active = 1 AND expires_at <= ?",
                (now_str,)
            )
            await db.commit()

            async with db.execute(
                """
                SELECT * FROM warnings
                WHERE guild_id = ? AND user_id = ? AND is_active = 1
                ORDER BY created_at DESC
                """,
                (guild_id, user_id)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_active_warn_count(self, guild_id: int, user_id: int) -> int:
        warns = await self.get_active_warnings(guild_id, user_id)
        return len(warns)

    async def remove_warning(self, warn_id: int, guild_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE warnings SET is_active = 0 WHERE id = ? AND guild_id = ?",
                (warn_id, guild_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE warnings SET is_active = 0 WHERE guild_id = ? AND user_id = ? AND is_active = 1",
                (guild_id, user_id)
            )
            await db.commit()
            return cursor.rowcount

    # --- STAFF STATS (FLORYMODERATORS MINECRAFT PLUGIN SYNC) ---
    async def get_staff_stats(self, guild_id: int, user_id: int, fallback_nicks: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Fetch Minecraft staff moderation & activity statistics for a staff member."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM staff_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)

            # Fallback: check staff_stats_by_nick by user_id or candidate nicknames
            async with db.execute(
                "SELECT * FROM staff_stats_by_nick WHERE guild_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)

            if fallback_nicks:
                for nick in fallback_nicks:
                    if not nick:
                        continue
                    async with db.execute(
                        "SELECT * FROM staff_stats_by_nick WHERE guild_id = ? AND lower_nick = ? LIMIT 1",
                        (guild_id, nick.strip().lower())
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            return dict(row)

            return None

    async def upsert_staff_stats(
        self,
        guild_id: int,
        user_id: int,
        minecraft_nick: Optional[str] = None,
        primary_group: Optional[str] = None,
        online_seconds: Optional[int] = None,
        norm_seconds: Optional[int] = None,
        warns_issued: Optional[int] = None,
        bans_issued: Optional[int] = None,
        reports_accepted: Optional[int] = None,
        mutes_issued: Optional[int] = None,
    ) -> bool:
        """Create or update staff stats record (for FloryModerators sync or admin tools)."""
        existing = await self.get_staff_stats(guild_id, user_id) if user_id and user_id > 0 else None
        now_str = datetime.now(timezone.utc).isoformat()

        mc_nick = minecraft_nick if minecraft_nick is not None else (existing.get("minecraft_nick") if existing else None)
        p_grp = primary_group if primary_group is not None else (existing.get("primary_group") if existing else None)
        on_sec = online_seconds if online_seconds is not None else (existing.get("online_seconds", 0) if existing else 0)
        nrm_sec = norm_seconds if norm_seconds is not None else (existing.get("norm_seconds", 0) if existing else 0)
        w_iss = warns_issued if warns_issued is not None else (existing.get("warns_issued", 0) if existing else 0)
        b_iss = bans_issued if bans_issued is not None else (existing.get("bans_issued", 0) if existing else 0)
        r_acc = reports_accepted if reports_accepted is not None else (existing.get("reports_accepted", 0) if existing else 0)
        m_iss = mutes_issued if mutes_issued is not None else (existing.get("mutes_issued", 0) if existing else 0)

        async with aiosqlite.connect(self.db_path) as db:
            if user_id and user_id > 0:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO staff_stats
                    (guild_id, user_id, minecraft_nick, primary_group, online_seconds, norm_seconds, warns_issued, bans_issued, reports_accepted, mutes_issued, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, mc_nick, p_grp, on_sec, nrm_sec, w_iss, b_iss, r_acc, m_iss, now_str)
                )

            if mc_nick:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO staff_stats_by_nick
                    (guild_id, lower_nick, minecraft_nick, user_id, primary_group, online_seconds, norm_seconds, warns_issued, bans_issued, reports_accepted, mutes_issued, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, mc_nick.strip().lower(), mc_nick.strip(), user_id or 0, p_grp, on_sec, nrm_sec, w_iss, b_iss, r_acc, m_iss, now_str)
                )

            await db.commit()
            return True

    # --- SNAPSHOTS (CHANNELS & ROLES RESTORE) ---
    async def save_snapshot(self, snapshot_id: str, guild_id: int, created_by: int, data: dict) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT OR REPLACE INTO snapshots (id, guild_id, created_at, created_by, data) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, guild_id, now, created_by, json.dumps(data))
            )
            await db.commit()
            return True

    async def get_snapshot(self, snapshot_id: str, guild_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM snapshots WHERE id = ? AND guild_id = ?",
                (snapshot_id, guild_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    item = dict(row)
                    item["data"] = json.loads(item["data"])
                    return item
                return None

    async def list_snapshots(self, guild_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, guild_id, created_at, created_by FROM snapshots WHERE guild_id = ? ORDER BY created_at DESC LIMIT 10",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # --- PUNISHMENT LOGS & STAFF STATS ADJUSTMENT ---
    async def log_punishment(
        self,
        guild_id: int,
        moderator_id: int,
        moderator_nick: str,
        target_nick: str,
        target_ip: str,
        punishment_type: str,
        reason: str,
        duration_seconds: int,
        is_valid_for_stats: int = 1,
        audit_requested: int = 0,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO punishment_logs
                (guild_id, moderator_id, moderator_nick, target_nick, target_ip, punishment_type, reason, duration_seconds, is_valid_for_stats, created_at, audit_requested)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    moderator_id,
                    moderator_nick,
                    target_nick,
                    target_ip or "",
                    punishment_type.upper(),
                    reason,
                    duration_seconds,
                    is_valid_for_stats,
                    now,
                    audit_requested,
                ),
            )
            punishment_id = cursor.lastrowid
            await db.commit()
            return punishment_id

    async def adjust_staff_stats(
        self,
        guild_id: int,
        user_id: Optional[int] = None,
        mc_nick: Optional[str] = None,
        bans_delta: int = 0,
        mutes_delta: int = 0,
        warns_delta: int = 0,
        reports_delta: int = 0,
    ) -> bool:
        """Safely adjust staff stats (positive or negative delta), never dropping below 0."""
        async with aiosqlite.connect(self.db_path) as db:
            now_str = datetime.now(timezone.utc).isoformat()
            if user_id and user_id > 0:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO staff_stats (guild_id, user_id, bans_issued, mutes_issued, warns_issued, reports_accepted, updated_at)
                    VALUES (?, ?, 0, 0, 0, 0, ?)
                    """,
                    (guild_id, user_id, now_str),
                )
                await db.execute(
                    """
                    UPDATE staff_stats
                    SET bans_issued = MAX(0, COALESCE(bans_issued, 0) + ?),
                        mutes_issued = MAX(0, COALESCE(mutes_issued, 0) + ?),
                        warns_issued = MAX(0, COALESCE(warns_issued, 0) + ?),
                        reports_accepted = MAX(0, COALESCE(reports_accepted, 0) + ?),
                        updated_at = ?
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (bans_delta, mutes_delta, warns_delta, reports_delta, now_str, guild_id, user_id),
                )
            if mc_nick:
                lower = mc_nick.strip().lower()
                await db.execute(
                    """
                    INSERT OR IGNORE INTO staff_stats_by_nick (guild_id, lower_nick, minecraft_nick, user_id, bans_issued, mutes_issued, warns_issued, reports_accepted, updated_at)
                    VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?)
                    """,
                    (guild_id, lower, mc_nick.strip(), user_id or 0, now_str),
                )
                await db.execute(
                    """
                    UPDATE staff_stats_by_nick
                    SET bans_issued = MAX(0, COALESCE(bans_issued, 0) + ?),
                        mutes_issued = MAX(0, COALESCE(mutes_issued, 0) + ?),
                        warns_issued = MAX(0, COALESCE(warns_issued, 0) + ?),
                        reports_accepted = MAX(0, COALESCE(reports_accepted, 0) + ?),
                        updated_at = ?
                    WHERE guild_id = ? AND lower_nick = ?
                    """,
                    (bans_delta, mutes_delta, warns_delta, reports_delta, now_str, guild_id, lower),
                )
            await db.commit()
            return True

    async def reset_weekly_staff_online(self, guild_id: int) -> int:
        """Resets online_seconds to 0 for all staff members on weekly digest reset."""
        async with aiosqlite.connect(self.db_path) as db:
            c1 = await db.execute("UPDATE staff_stats SET online_seconds = 0 WHERE guild_id = ?", (guild_id,))
            await db.execute("UPDATE staff_stats_by_nick SET online_seconds = 0 WHERE guild_id = ?", (guild_id,))
            await db.commit()
            return c1.rowcount

    async def get_all_staff_stats(self, guild_id: int) -> List[Dict[str, Any]]:
        """Fetch all staff stats for a guild, sorted by online time and actions."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM staff_stats
                WHERE guild_id = ?
                ORDER BY online_seconds DESC, (bans_issued + mutes_issued + reports_accepted) DESC
                """,
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_recent_punishments_by_moderator(
        self,
        guild_id: int,
        moderator_nick: Optional[str] = None,
        moderator_id: Optional[int] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            conditions = []
            params: list[Any] = [guild_id]
            if moderator_nick and moderator_nick.strip():
                conditions.append("LOWER(moderator_nick) = LOWER(?)")
                params.append(moderator_nick.strip())
            if moderator_id and moderator_id > 0:
                conditions.append("moderator_id = ?")
                params.append(moderator_id)
            if not conditions:
                return []
            clause = " OR ".join(conditions)
            params.append(limit)
            query = f"""
                SELECT * FROM punishment_logs
                WHERE guild_id = ? AND is_valid_for_stats = 1 AND ({clause})
                ORDER BY id DESC LIMIT ?
            """
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_punishment_by_id(self, punishment_id: int, guild_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM punishment_logs WHERE id = ? AND guild_id = ?",
                (punishment_id, guild_id),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def invalidate_punishment(self, punishment_id: int, guild_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE punishment_logs SET is_valid_for_stats = 0 WHERE id = ? AND guild_id = ?",
                (punishment_id, guild_id),
            )
            await db.commit()
            return True

    # --- SECURITY EVENT LOGS ---
    async def log_security_event(self, guild_id: int, user_id: int, action_type: str, details: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO security_logs (guild_id, user_id, action_type, details, timestamp) VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, action_type, details, now)
            )
            await db.commit()


# Global DB singleton
db = Database()
