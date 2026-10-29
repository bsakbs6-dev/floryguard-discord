import aiosqlite
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

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
                    audit_requested INTEGER DEFAULT 0,
                    proof_url TEXT DEFAULT '',
                    proof_comment TEXT DEFAULT '',
                    proof_submitted_at TIMESTAMP,
                    proof_requested_by INTEGER DEFAULT 0,
                    proof_requested_at TIMESTAMP,
                    proof_status TEXT DEFAULT 'NONE',
                    proof_materials TEXT DEFAULT '[]',
                    proof_curator_msg_id INTEGER DEFAULT 0,
                    proof_curator_channel_id INTEGER DEFAULT 0,
                    proof_reviewed_by INTEGER DEFAULT 0,
                    proof_reviewed_at TIMESTAMP
                );
            """)

            # Safe migration for existing punishment_logs tables
            for col_sql in (
                "proof_url TEXT DEFAULT ''",
                "proof_comment TEXT DEFAULT ''",
                "proof_submitted_at TIMESTAMP",
                "proof_requested_by INTEGER DEFAULT 0",
                "proof_requested_at TIMESTAMP",
                "proof_status TEXT DEFAULT 'NONE'",
                "proof_materials TEXT DEFAULT '[]'",
                "proof_curator_msg_id INTEGER DEFAULT 0",
                "proof_curator_channel_id INTEGER DEFAULT 0",
                "proof_reviewed_by INTEGER DEFAULT 0",
                "proof_reviewed_at TIMESTAMP",
            ):
                try:
                    await db.execute(f"ALTER TABLE punishment_logs ADD COLUMN {col_sql}")
                except Exception:
                    pass

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

            # Table for Candidate & Staff Attestations
            await db.execute("""
                CREATE TABLE IF NOT EXISTS attestations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    candidate_id INTEGER NOT NULL,
                    candidate_nick TEXT NOT NULL,
                    curator_id INTEGER NOT NULL,
                    curator_nick TEXT NOT NULL,
                    target_role_id INTEGER NOT NULL,
                    target_role_name TEXT NOT NULL,
                    voice_channel_id INTEGER NOT NULL,
                    scheduled_at TIMESTAMP NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    result_reason TEXT DEFAULT '',
                    result_at TIMESTAMP,
                    reminded_15m INTEGER DEFAULT 0,
                    reminded_5m INTEGER DEFAULT 0,
                    created_at TIMESTAMP NOT NULL
                );
            """)

            await db.execute("CREATE INDEX IF NOT EXISTS idx_attestations_candidate ON attestations(guild_id, candidate_id, status);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_attestations_reminders ON attestations(status, scheduled_at);")

            # Table for Candidate Attestation History & 3-Strike Blacklist
            await db.execute("""
                CREATE TABLE IF NOT EXISTS attestation_candidates (
                    guild_id INTEGER NOT NULL,
                    candidate_id INTEGER NOT NULL,
                    consecutive_fails INTEGER DEFAULT 0,
                    total_attempts INTEGER DEFAULT 0,
                    is_blacklisted INTEGER DEFAULT 0,
                    blacklisted_until TIMESTAMP,
                    blacklisted_reason TEXT DEFAULT '',
                    unblocked_by INTEGER DEFAULT 0,
                    updated_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (guild_id, candidate_id)
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

    async def get_punishment_by_id(self, punishment_id: int, guild_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if guild_id and guild_id > 0:
                query = "SELECT * FROM punishment_logs WHERE id = ? AND guild_id = ?"
                params = (punishment_id, guild_id)
            else:
                query = "SELECT * FROM punishment_logs WHERE id = ?"
                params = (punishment_id,)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def invalidate_punishment(self, punishment_id: int, guild_id: Optional[int] = None) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            if guild_id and guild_id > 0:
                await db.execute(
                    "UPDATE punishment_logs SET is_valid_for_stats = 0 WHERE id = ? AND guild_id = ?",
                    (punishment_id, guild_id),
                )
            else:
                await db.execute(
                    "UPDATE punishment_logs SET is_valid_for_stats = 0 WHERE id = ?",
                    (punishment_id,),
                )
            await db.commit()
            return True

    async def request_punishment_proof(self, punishment_id: int, guild_id: int, requested_by: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE punishment_logs
                SET audit_requested = 1,
                    proof_requested_by = ?,
                    proof_requested_at = ?,
                    proof_status = 'PENDING_MOD',
                    proof_materials = '[]'
                WHERE id = ? AND (? = 0 OR guild_id = ?)
                """,
                (requested_by, now, punishment_id, guild_id, guild_id),
            )
            await db.commit()
            return True

    async def append_punishment_proof_material(
        self, punishment_id: int, guild_id: int, text: str = "", attachments: Optional[List[str]] = None
    ) -> bool:
        """Appends a new message/attachment entry to the punishment's proof_materials JSON array."""
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "text": text.strip(),
            "attachments": attachments or [],
            "timestamp": now,
        }
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT proof_materials FROM punishment_logs WHERE id = ? AND (? = 0 OR guild_id = ?)",
                (punishment_id, guild_id, guild_id),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False
                try:
                    materials = json.loads(row["proof_materials"] or "[]")
                    if not isinstance(materials, list):
                        materials = []
                except Exception:
                    materials = []
            materials.append(entry)
            await db.execute(
                "UPDATE punishment_logs SET proof_materials = ? WHERE id = ? AND (? = 0 OR guild_id = ?)",
                (json.dumps(materials, ensure_ascii=False), punishment_id, guild_id, guild_id),
            )
            await db.commit()
            return True

    async def submit_punishment_proof(
        self,
        punishment_id: int,
        guild_id: int,
        proof_url: str = "",
        comment: str = "",
        curator_msg_id: int = 0,
        curator_channel_id: int = 0,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE punishment_logs
                SET proof_url = CASE WHEN ? != '' THEN ? ELSE proof_url END,
                    proof_comment = CASE WHEN ? != '' THEN ? ELSE proof_comment END,
                    proof_submitted_at = ?,
                    proof_status = 'PENDING_CURATOR',
                    proof_curator_msg_id = CASE WHEN ? > 0 THEN ? ELSE proof_curator_msg_id END,
                    proof_curator_channel_id = CASE WHEN ? > 0 THEN ? ELSE proof_curator_channel_id END
                WHERE id = ? AND (? = 0 OR guild_id = ?)
                """,
                (
                    proof_url, proof_url,
                    comment, comment,
                    now,
                    curator_msg_id, curator_msg_id,
                    curator_channel_id, curator_channel_id,
                    punishment_id, guild_id, guild_id,
                ),
            )
            await db.commit()
            return True

    async def resolve_punishment_proof(
        self, punishment_id: int, guild_id: int, status: str, reviewed_by: int = 0
    ) -> bool:
        """Resolves the proof request with a verdict (APPROVED, REJECTED, EXPIRED_NO_PROOF, EXPIRED_AUTO_APPROVED)."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE punishment_logs
                SET proof_status = ?, proof_reviewed_by = ?, proof_reviewed_at = ?
                WHERE id = ? AND (? = 0 OR guild_id = ?)
                """,
                (status, reviewed_by, now, punishment_id, guild_id, guild_id),
            )
            await db.commit()
            return True

    async def get_active_mod_proof_request(
        self, user_id: int, mc_nick: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find the latest active punishment proof request waiting for materials from this moderator."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if user_id:
                async with db.execute(
                    """
                    SELECT * FROM punishment_logs
                    WHERE proof_status = 'PENDING_MOD' AND moderator_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (user_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)

            if mc_nick:
                async with db.execute(
                    """
                    SELECT * FROM punishment_logs
                    WHERE proof_status = 'PENDING_MOD' AND LOWER(moderator_nick) = LOWER(?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (mc_nick.strip(),),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)

            # Try linked minecraft_nick from staff_stats
            if user_id:
                async with db.execute(
                    "SELECT minecraft_nick FROM staff_stats WHERE user_id = ? LIMIT 1",
                    (user_id,),
                ) as cursor:
                    s_row = await cursor.fetchone()
                    if s_row and s_row["minecraft_nick"]:
                        linked_nick = s_row["minecraft_nick"]
                        async with db.execute(
                            """
                            SELECT * FROM punishment_logs
                            WHERE proof_status = 'PENDING_MOD' AND LOWER(moderator_nick) = LOWER(?)
                            ORDER BY id DESC LIMIT 1
                            """,
                            (linked_nick.strip(),),
                        ) as p_cursor:
                            row = await p_cursor.fetchone()
                            if row:
                                return dict(row)
            return None

    async def get_expired_pending_mod_proofs(self, timeout_seconds: int = 86400) -> List[Dict[str, Any]]:
        """Punishments where moderator failed to submit proof within timeout (24 hours)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM punishment_logs
                WHERE proof_status = 'PENDING_MOD' AND proof_requested_at IS NOT NULL AND proof_requested_at <= ?
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_expired_pending_curator_proofs(self, timeout_seconds: int = 86400) -> List[Dict[str, Any]]:
        """Punishments where curator failed to review submitted proof within timeout (24 hours)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM punishment_logs
                WHERE proof_status = 'PENDING_CURATOR' AND proof_submitted_at IS NOT NULL AND proof_submitted_at <= ?
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # --- ATTESTATIONS & INTERVIEWS ---
    async def create_attestation(
        self,
        guild_id: int,
        candidate_id: int,
        candidate_nick: str,
        curator_id: int,
        curator_nick: str,
        target_role_id: int,
        target_role_name: str,
        voice_channel_id: int,
        scheduled_at_iso: str,
    ) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO attestations (
                    guild_id, candidate_id, candidate_nick, curator_id, curator_nick,
                    target_role_id, target_role_name, voice_channel_id, scheduled_at,
                    status, result_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', '', ?)
                """,
                (
                    guild_id,
                    candidate_id,
                    candidate_nick,
                    curator_id,
                    curator_nick,
                    target_role_id,
                    target_role_name,
                    voice_channel_id,
                    scheduled_at_iso,
                    now_iso,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_active_attestation(
        self, guild_id: int, candidate_id: int
    ) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM attestations 
                WHERE guild_id = ? AND candidate_id = ? AND status = 'PENDING'
                ORDER BY id DESC LIMIT 1
                """,
                (guild_id, candidate_id),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_attestation_by_id(self, attestation_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM attestations WHERE id = ?", (attestation_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_candidate_history(
        self, guild_id: int, candidate_id: int
    ) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM attestation_candidates WHERE guild_id = ? AND candidate_id = ?",
                (guild_id, candidate_id),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def set_attestation_result(
        self, attestation_id: int, status: str, result_reason: str = ""
    ) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE attestations 
                SET status = ?, result_reason = ?, result_at = ?
                WHERE id = ?
                """,
                (status, result_reason, now_iso, attestation_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def record_candidate_fail(
        self,
        guild_id: int,
        candidate_id: int,
        reason: str = "",
        max_fails: int = 3,
        cooldown_days: int = 30,
    ) -> Tuple[int, bool, Optional[str]]:
        """
        Records a failed or skipped attestation for a candidate.
        Increments consecutive_fails and total_attempts.
        If consecutive_fails >= max_fails:
          Activates 1-month blacklist.
        Returns:
          (consecutive_fails: int, is_blacklisted: bool, blacklisted_until_iso: Optional[str])
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM attestation_candidates WHERE guild_id = ? AND candidate_id = ?",
                (guild_id, candidate_id),
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                fails = int(row["consecutive_fails"]) + 1
                total = int(row["total_attempts"]) + 1
            else:
                fails = 1
                total = 1

            is_blacklisted = 0
            blacklisted_until_iso = None
            if fails >= max_fails:
                is_blacklisted = 1
                blacklisted_until_dt = now + timedelta(days=cooldown_days)
                blacklisted_until_iso = blacklisted_until_dt.isoformat()

            await db.execute(
                """
                INSERT OR REPLACE INTO attestation_candidates (
                    guild_id, candidate_id, consecutive_fails, total_attempts,
                    is_blacklisted, blacklisted_until, blacklisted_reason,
                    unblocked_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    guild_id,
                    candidate_id,
                    fails,
                    total,
                    is_blacklisted,
                    blacklisted_until_iso,
                    reason,
                    now_iso,
                ),
            )
            await db.commit()
            return fails, bool(is_blacklisted), blacklisted_until_iso

    async def reset_candidate_fails(self, guild_id: int, candidate_id: int) -> bool:
        """Resets consecutive fails counter upon passing an attestation."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE attestation_candidates 
                SET consecutive_fails = 0, is_blacklisted = 0, blacklisted_until = NULL, updated_at = ?
                WHERE guild_id = ? AND candidate_id = ?
                """,
                (now_iso, guild_id, candidate_id),
            )
            await db.commit()
            return True

    async def unblock_candidate(self, guild_id: int, candidate_id: int, admin_id: int) -> bool:
        """Prematurely unblocks a candidate from the 1-month attestation blacklist."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE attestation_candidates 
                SET is_blacklisted = 0, blacklisted_until = NULL, consecutive_fails = 0, unblocked_by = ?, updated_at = ?
                WHERE guild_id = ? AND candidate_id = ?
                """,
                (admin_id, now_iso, guild_id, candidate_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cancel_attestation(self, attestation_id: int, reason: str = "") -> bool:
        return await self.set_attestation_result(attestation_id, "CANCELLED", reason)

    async def get_pending_reminders(self) -> List[Dict[str, Any]]:
        """Retrieves all pending attestations that may need 15m or 5m reminders."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM attestations 
                WHERE status = 'PENDING' AND (reminded_15m = 0 OR reminded_5m = 0)
                ORDER BY scheduled_at ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def mark_reminder_sent(self, attestation_id: int, reminder_type: str) -> bool:
        field = "reminded_15m" if reminder_type == "15m" else "reminded_5m"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE attestations SET {field} = 1 WHERE id = ?",
                (attestation_id,),
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
