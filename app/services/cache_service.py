"""
Redis Caching Service

Comprehensive caching implementation for session states, user data, and frequent queries.
"""

import base64
import json
from typing import Any, Dict, Optional

import redis.asyncio as redis
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings


class CacheService:
    """
    Redis-based caching service for the APGI API.

    Provides caching for:
    - Session states and metadata
    - User data and authentication tokens
    - Frequent database queries
    - API response caching
    """

    def __init__(self, redis_client: redis.Redis, default_ttl: int = 3600):
        """
        Initialize cache service.

        Args:
            redis_client: Redis client instance
            default_ttl: Default TTL for cached items in seconds (default 1 hour)
        """
        self.redis = redis_client
        self.default_ttl = default_ttl

        # Cache key prefixes for different data types
        self.prefixes = {
            "session": "session:",
            "user": "user:",
            "task": "task:",
            "query": "query:",
            "response": "response:",
            "auth": "auth:",
        }

        # Encryption key for sensitive data (derived using HKDF)
        if settings.jwt_secret_key is None:
            raise ValueError("JWT secret key is required for cache encryption")
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"cache_encryption_key")
        key_bytes = hkdf.derive(settings.jwt_secret_key.encode())
        key = base64.urlsafe_b64encode(key_bytes)
        self.fernet = Fernet(key)

    async def set_session_state(
        self, session_id: str, state: Dict[str, Any], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache session state.

        Args:
            session_id: Unique session identifier
            state: Session state data
            ttl: Time to live in seconds (uses default if None)

        Returns:
            True if successful, False otherwise
        """
        key = f"{self.prefixes['session']}{session_id}:state"
        return await self._set_json(key, state, ttl or self.default_ttl)

    async def get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached session state.

        Args:
            session_id: Unique session identifier

        Returns:
            Session state data or None if not found
        """
        key = f"{self.prefixes['session']}{session_id}:state"
        return await self._get_json(key)

    async def set_session_metadata(
        self, session_id: str, metadata: Dict[str, Any], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache session metadata.

        Args:
            session_id: Unique session identifier
            metadata: Session metadata
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        from app.config import settings

        key = f"{self.prefixes['session']}{session_id}:metadata"
        return await self._set_json(
            key, metadata, ttl or settings.cache_default_ttl * settings.cache_session_ttl_multiplier
        )

    async def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached session metadata.

        Args:
            session_id: Unique session identifier

        Returns:
            Session metadata or None if not found
        """
        key = f"{self.prefixes['session']}{session_id}:metadata"
        return await self._get_json(key)

    async def delete_session_state(self, session_id: str) -> bool:
        """
        Delete cached session state.

        Args:
            session_id: Unique session identifier

        Returns:
            True if successful, False otherwise
        """
        key = f"{self.prefixes['session']}{session_id}:state"
        try:
            result = await self.redis.delete(key)
            return result > 0
        except Exception:
            return False

    async def set_user_data(
        self, user_id: str, user_data: Dict[str, Any], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache user data.

        Args:
            user_id: Unique user identifier
            user_data: User data dictionary
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        from app.config import settings

        key = f"{self.prefixes['user']}{user_id}:data"
        return await self._set_encrypted_json(
            key, user_data, ttl or settings.cache_default_ttl * settings.cache_user_ttl_multiplier
        )

    async def get_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached user data.

        Args:
            user_id: Unique user identifier

        Returns:
            User data or None if not found
        """
        key = f"{self.prefixes['user']}{user_id}:data"
        return await self._get_encrypted_json(key)

    async def set_auth_token(self, token_hash: str, token_data: Dict[str, Any], ttl: int) -> bool:
        """
        Cache authentication token data.

        Args:
            token_hash: Hashed token identifier
            token_data: Token data (user_id, expires_at, etc.)
            ttl: Time to live in seconds (should match token expiration)

        Returns:
            True if successful, False otherwise
        """
        key = f"{self.prefixes['auth']}{token_hash}"
        return await self._set_json(key, token_data, ttl)

    async def get_auth_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached authentication token data.

        Args:
            token_hash: Hashed token identifier

        Returns:
            Token data or None if not found or expired
        """
        key = f"{self.prefixes['auth']}{token_hash}"
        return await self._get_json(key)

    async def set_task_result(self, task_id: str, result: Any, ttl: Optional[int] = None) -> bool:
        """
        Cache task result.

        Args:
            task_id: Unique task identifier
            result: Task result data
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        from app.config import settings

        key = f"{self.prefixes['task']}{task_id}:result"
        return await self._set_json(
            key, result, ttl or settings.cache_default_ttl * settings.cache_task_ttl_multiplier
        )

    async def get_task_result(self, task_id: str) -> Optional[Any]:
        """
        Retrieve cached task result.

        Args:
            task_id: Unique task identifier

        Returns:
            Task result or None if not found
        """
        key = f"{self.prefixes['task']}{task_id}:result"
        return await self._get_json(key)

    async def set_query_result(
        self, query_hash: str, result: Any, ttl: Optional[int] = None
    ) -> bool:
        """
        Cache database query result.

        Args:
            query_hash: Hash of the query and parameters
            result: Query result data
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        key = f"{self.prefixes['query']}{query_hash}"
        return await self._set_json(key, result, ttl or self.default_ttl // 4)  # 15 minutes

    async def get_query_result(self, query_hash: str) -> Optional[Any]:
        """
        Retrieve cached query result.

        Args:
            query_hash: Hash of the query and parameters

        Returns:
            Query result or None if not found
        """
        key = f"{self.prefixes['query']}{query_hash}"
        return await self._get_json(key)

    async def set_api_response(
        self, endpoint: str, params_hash: str, response: Dict[str, Any], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache API response.

        Args:
            endpoint: API endpoint path
            params_hash: Hash of request parameters
            response: API response data
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        key = f"{self.prefixes['response']}{endpoint}:{params_hash}"
        return await self._set_json(key, response, ttl or self.default_ttl // 2)  # 30 minutes

    async def get_api_response(self, endpoint: str, params_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached API response.

        Args:
            endpoint: API endpoint path
            params_hash: Hash of request parameters

        Returns:
            Cached response or None if not found
        """
        key = f"{self.prefixes['response']}{endpoint}:{params_hash}"
        return await self._get_json(key)

    async def invalidate_session_cache(self, session_id: str) -> int:
        """
        Invalidate all cached data for a session.

        Args:
            session_id: Unique session identifier

        Returns:
            Number of keys deleted
        """
        pattern = f"{self.prefixes['session']}{session_id}:*"
        keys = [key async for key in self.redis.scan_iter(pattern)]
        if keys:
            return await self.redis.delete(*keys)
        return 0

    async def invalidate_user_cache(self, user_id: str) -> int:
        """
        Invalidate all cached data for a user.

        Args:
            user_id: Unique user identifier

        Returns:
            Number of keys deleted
        """
        pattern = f"{self.prefixes['user']}{user_id}:*"
        keys = [key async for key in self.redis.scan_iter(pattern)]
        if keys:
            return await self.redis.delete(*keys)
        return 0

    async def clear_expired_cache(self) -> int:
        """
        Clear all expired cache entries.

        Note: Redis automatically expires keys, but this method can be used
        for manual cleanup or to get a count of expired keys.

        Returns:
            Number of keys that were expired
        """
        # Redis handles expiration automatically, so this is mainly for monitoring
        # In a production system, you might want to use Redis SCAN with TTL checks
        return 0

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        stats: Dict[str, Any] = {}
        for prefix_name, prefix in self.prefixes.items():
            pattern = f"{prefix}*"
            keys = [key async for key in self.redis.scan_iter(pattern)]
            stats[f"{prefix_name}_keys"] = len(keys)

        # Get Redis memory info
        info = await self.redis.info("memory")
        stats["redis_used_memory"] = info.get("used_memory")
        stats["redis_max_memory"] = info.get("maxmemory")

        return stats

    async def _set_encrypted_json(self, key: str, data: Any, ttl: int) -> bool:
        """Set encrypted JSON data in cache for sensitive information."""
        try:
            json_data = json.dumps(data, default=str)
            encrypted = self.fernet.encrypt(json_data.encode())
            return await self.redis.setex(key, ttl, encrypted)
        except Exception:
            return False

    async def _get_encrypted_json(self, key: str) -> Optional[Any]:
        """Get encrypted JSON data from cache."""
        try:
            data = await self.redis.get(key)
            if data:
                decrypted = self.fernet.decrypt(data)
                return json.loads(decrypted)
        except Exception:  # nosec: B110 - cache decryption failure, returning None is expected
            pass
        return None

    async def _set_json(self, key: str, data: Any, ttl: int) -> bool:
        """Set JSON data in cache."""
        try:
            json_data = json.dumps(data, default=str)
            return await self.redis.setex(key, ttl, json_data)
        except Exception:
            return False

    async def _get_json(self, key: str) -> Optional[Any]:
        """Get JSON data from cache."""
        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception:  # nosec: B110 - cache parse failure, returning None is expected
            pass
        return None


# Global cache service instance
cache_service: Optional[CacheService] = None


def init_cache_service(redis_client: redis.Redis, default_ttl: int = 3600) -> CacheService:
    """
    Initialize the global cache service instance.

    Args:
        redis_client: Redis client instance
        default_ttl: Default TTL for cached items

    Returns:
        CacheService instance
    """
    global cache_service
    cache_service = CacheService(redis_client, default_ttl)
    return cache_service


def get_cache_service() -> Optional[CacheService]:
    """
    Get the global cache service instance.

    Returns:
        CacheService instance or None if not initialized
    """
    return cache_service
