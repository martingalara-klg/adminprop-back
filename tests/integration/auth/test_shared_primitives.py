"""Tests directos de los primitivos Redis de auth (issue #6): lockout,
refresh token store y rate limit por IP -- cubren ramas que los tests de
endpoint no ejercitan directamente (revoke_family vacio, check sin fallos
previos, etc).
"""

from uuid import uuid4

import pytest

from adminprop.config import get_settings
from adminprop.shared.auth.lockout import LoginLockout
from adminprop.shared.auth.refresh_store import RefreshTokenStore
from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.codes import UnauthorizedException
from adminprop.shared.rate_limit.token_bucket import check_rate_limit

pytestmark = pytest.mark.asyncio


class TestLoginLockout:
    async def test_check_with_no_prior_failures_is_not_locked(self):
        lockout = LoginLockout(get_redis_client(), get_settings())
        status = await lockout.check("nobody@example.com")
        assert status.locked is False
        assert status.retry_after_seconds == 0

    async def test_register_failure_below_threshold_is_not_locked(self):
        lockout = LoginLockout(get_redis_client(), get_settings())
        for _ in range(4):
            status = await lockout.register_failure("someone@example.com")
        assert status.locked is False

    async def test_reset_clears_both_counter_and_lock(self):
        lockout = LoginLockout(get_redis_client(), get_settings())
        email = "reset-me@example.com"
        for _ in range(5):
            await lockout.register_failure(email)
        locked_status = await lockout.check(email)
        assert locked_status.locked is True

        await lockout.reset(email)

        status_after_reset = await lockout.check(email)
        assert status_after_reset.locked is False


class TestRefreshTokenStore:
    async def test_issue_family_creates_a_usable_token(self):
        store = RefreshTokenStore(get_redis_client(), get_settings())
        user_id = uuid4()
        org_id = uuid4()

        issued = await store.issue_family(user_id=user_id, organization_id=org_id)
        record, _new_issued = await store.rotate(issued.raw_token)

        assert record.user_id == user_id
        assert record.organization_id == org_id

    async def test_rotate_with_unknown_token_raises_unauthorized(self):
        store = RefreshTokenStore(get_redis_client(), get_settings())
        with pytest.raises(UnauthorizedException):
            await store.rotate("not-a-real-token")

    async def test_revoke_family_on_empty_family_is_a_noop(self):
        store = RefreshTokenStore(get_redis_client(), get_settings())
        await store.revoke_family("nonexistent-family-id")

    async def test_revoke_by_raw_token_with_unknown_token_is_a_noop(self):
        store = RefreshTokenStore(get_redis_client(), get_settings())
        await store.revoke_by_raw_token("not-a-real-token")

    async def test_revoke_by_raw_token_invalidates_future_rotation(self):
        store = RefreshTokenStore(get_redis_client(), get_settings())
        issued = await store.issue_family(user_id=uuid4(), organization_id=uuid4())

        await store.revoke_by_raw_token(issued.raw_token)

        with pytest.raises(UnauthorizedException):
            await store.rotate(issued.raw_token)


class TestRateLimitByIp:
    async def test_check_rate_limit_allows_requests_under_the_limit(self):
        for _ in range(3):
            await check_rate_limit(key=f"test:{uuid4()}", max_requests=5, window_seconds=60)

    async def test_check_rate_limit_raises_after_max_requests(self):
        from adminprop.shared.errors.codes import RateLimitExceededException

        key = f"test:{uuid4()}"
        for _ in range(3):
            await check_rate_limit(key=key, max_requests=3, window_seconds=60)

        with pytest.raises(RateLimitExceededException):
            await check_rate_limit(key=key, max_requests=3, window_seconds=60)
