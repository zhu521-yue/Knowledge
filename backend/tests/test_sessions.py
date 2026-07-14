from datetime import timedelta

from sqlalchemy import create_engine

from app.infrastructure.identity import IdentityService, utc_now
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.sessions import SessionService


def make_identity_and_session_services() -> tuple[IdentityService, SessionService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity_metadata.create_all(engine)
    return IdentityService(engine), SessionService(engine)


def create_member(identity: IdentityService):
    admin = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    invitation = identity.create_invitation(actor_user_id=admin.id, code="SESSION")
    member = identity.register_with_invitation(
        email="learner@example.test",
        password="correct horse battery staple",
        display_name="Learner",
        invitation_code=invitation.code,
    )
    return admin, member


def test_session_token_is_stored_as_hash_and_resolves_user() -> None:
    identity, sessions = make_identity_and_session_services()
    _, member = create_member(identity)

    created = sessions.create_session(user_id=member.id)
    resolved = sessions.resolve_session(created.token)

    assert created.token != created.token_hash
    assert len(created.token) >= 32
    assert resolved is not None
    assert resolved.user.id == member.id
    assert resolved.session.user_id == member.id


def test_session_refresh_extends_expiry() -> None:
    identity, sessions = make_identity_and_session_services()
    _, member = create_member(identity)
    now = utc_now()
    created = sessions.create_session(
        user_id=member.id,
        now=now,
        ttl=timedelta(minutes=30),
    )

    refreshed = sessions.refresh_session(
        created.token,
        now=now + timedelta(minutes=10),
        ttl=timedelta(minutes=45),
    )

    assert refreshed is not None
    assert refreshed.expires_at == now + timedelta(minutes=55)


def test_logout_revokes_session() -> None:
    identity, sessions = make_identity_and_session_services()
    _, member = create_member(identity)
    created = sessions.create_session(user_id=member.id)

    revoked = sessions.revoke_session(created.token)
    resolved = sessions.resolve_session(created.token)

    assert revoked is True
    assert resolved is None


def test_disabled_user_invalidates_existing_session() -> None:
    identity, sessions = make_identity_and_session_services()
    admin, member = create_member(identity)
    created = sessions.create_session(user_id=member.id)

    identity.set_user_active(
        actor_user_id=admin.id,
        target_user_id=member.id,
        is_active=False,
    )
    resolved = sessions.resolve_session(created.token)

    assert resolved is None


def test_expired_session_cannot_be_resolved_or_refreshed() -> None:
    identity, sessions = make_identity_and_session_services()
    _, member = create_member(identity)
    now = utc_now()
    created = sessions.create_session(
        user_id=member.id,
        now=now,
        ttl=timedelta(seconds=1),
    )

    resolved = sessions.resolve_session(created.token, now=now + timedelta(seconds=2))
    refreshed = sessions.refresh_session(created.token, now=now + timedelta(seconds=2))

    assert resolved is None
    assert refreshed is None