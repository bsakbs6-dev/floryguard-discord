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
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

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

            # Table for Warnings (Anti-Nuke / Mod warnings with expiration)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    is_active INTEGER DEFAULT 1
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
    async def add_admin(self, guild_id: int, user_id: int, added_by: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT OR REPLACE INTO admins (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                (guild_id, user_id, added_by, now)
            )
            await db.commit()
            return True

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

    # --- WARNINGS SYSTEM ---
    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
        expiry_days: int = WARN_EXPIRY_DAYS
    ) -> (int, int):
        """Add a warning. Returns (new_warn_id, active_warns_count)."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=expiry_days)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (guild_id, user_id, moderator_id, reason, now.isoformat(), expires_at.isoformat())
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
                "UPDATE warnings SET is_active = 0 WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            await db.commit()
            return cursor.rowcount

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
