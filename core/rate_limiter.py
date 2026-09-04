import time
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple, Optional


class TokenBucketRateLimiter:
    """Sliding window / Token Bucket rate limiter for chat anti-spam."""
    def __init__(self, max_tokens: int = 4, window_seconds: float = 3.0):
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds
        # Mapping: (guild_id, user_id) -> Deque[float] (timestamps of messages)
        self._history: Dict[Tuple[int, int], Deque[float]] = defaultdict(deque)

    def is_rate_limited(self, guild_id: int, user_id: int) -> bool:
        """Returns True if the user exceeds the rate limit."""
        now = time.monotonic()
        key = (guild_id, user_id)
        user_timestamps = self._history[key]

        # Purge timestamps outside the window
        while user_timestamps and (now - user_timestamps[0] > self.window_seconds):
            user_timestamps.popleft()

        if len(user_timestamps) >= self.max_tokens:
            return True

        user_timestamps.append(now)
        return False

    def reset_user(self, guild_id: int, user_id: int):
        key = (guild_id, user_id)
        if key in self._history:
            del self._history[key]


class MessageHistoryCache:
    """Stores recent message contents per user to detect duplicate spam."""
    def __init__(self, max_history: int = 5, ttl_seconds: float = 60.0):
        self.max_history = max_history
        self.ttl_seconds = ttl_seconds
        # Mapping: (guild_id, user_id) -> Deque[Tuple[float, str]] (timestamp, content)
        self._cache: Dict[Tuple[int, int], Deque[Tuple[float, str]]] = defaultdict(deque)

    def add_message(self, guild_id: int, user_id: int, content: str):
        now = time.monotonic()
        key = (guild_id, user_id)
        history = self._cache[key]

        # Purge old
        while history and (now - history[0][0] > self.ttl_seconds):
            history.popleft()

        history.append((now, content))
        while len(history) > self.max_history:
            history.popleft()

    def get_recent_messages(self, guild_id: int, user_id: int) -> list[str]:
        now = time.monotonic()
        key = (guild_id, user_id)
        history = self._cache[key]
        return [msg for ts, msg in history if (now - ts <= self.ttl_seconds)]


class JoinSpikeTracker:
    """Tracks join frequency for Anti-Raid detection."""
    def __init__(self, threshold: int = 5, window_seconds: float = 5.0):
        self.threshold = threshold
        self.window_seconds = window_seconds
        # Mapping: guild_id -> Deque[float]
        self._joins: Dict[int, Deque[float]] = defaultdict(deque)
        self._lockdown_active: Dict[int, bool] = defaultdict(bool)

    def record_join(self, guild_id: int) -> bool:
        """
        Record a member join.
        Returns True if a join spike is triggered.
        """
        now = time.monotonic()
        joins = self._joins[guild_id]

        while joins and (now - joins[0] > self.window_seconds):
            joins.popleft()

        joins.append(now)

        if len(joins) >= self.threshold:
            self._lockdown_active[guild_id] = True
            return True
        return False

    def is_in_lockdown(self, guild_id: int) -> bool:
        return self._lockdown_active[guild_id]

    def set_lockdown(self, guild_id: int, status: bool):
        self._lockdown_active[guild_id] = status


class CrossChannelSpamTracker:
    """Tracks message broadcasting across multiple channels to instantly stop nuke/raid bots."""
    def __init__(self, max_channels: int = 3, window_seconds: float = 4.0):
        self.max_channels = max_channels
        self.window_seconds = window_seconds
        # Mapping: (guild_id, user_id) -> Deque[Tuple[float, int]] (timestamp, channel_id)
        self._history: Dict[Tuple[int, int], Deque[Tuple[float, int]]] = defaultdict(deque)

    def record_channel_message(self, guild_id: int, user_id: int, channel_id: int) -> bool:
        """
        Record a message sent in channel_id.
        Returns True if the user/bot is spamming across multiple channels in short window.
        """
        now = time.monotonic()
        key = (guild_id, user_id)
        history = self._history[key]

        while history and (now - history[0][0] > self.window_seconds):
            history.popleft()

        history.append((now, channel_id))
        unique_channels = {ch_id for _, ch_id in history}

        # Trigger if 3+ different channels within 4 seconds OR 6 total messages
        if len(unique_channels) >= self.max_channels or len(history) >= 6:
            return True
        return False
