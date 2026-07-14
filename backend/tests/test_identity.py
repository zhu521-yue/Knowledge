from datetime import timedelta

from sqlalchemy import create_engine

from app.infrastructure.identity import IdentityService, utc_now
from app.infrastructure.identity_tables import identity_metadata


def make_identity_service() -> IdentityService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity_metadata.create_all(engine)
    return IdentityService(engine)


def test_bootstrap_admin_is_one_time_only() -> None:
    service = make_identity_service()

    admin = service.bootstrap_admin(
        email="Admin@Example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )

    assert admin.email == "admin@example.test"
    assert admin.role == "admin"
    assert admin.is_active is True

    repeated = service.bootstrap_admin(
        email="other@example.test",
        password="correct horse battery staple",
        display_name="Other",
    )

    assert repeated.error == "admin_already_initialized"


def test_invitation_registers_one_member_once() -> None:
    service = make_identity_service()
    admin = service.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    invitation = service.create_invitation(
        actor_user_id=admin.id,
        code="INVITE-ONE",
        max_uses=1,
    )

    member = service.register_with_invitation(
        email="learner@example.test",
        password="correct horse battery staple",
        display_name="Learner",
        invitation_code=invitation.code,
    )
    reused = service.register_with_invitation(
        email="other@example.test",
        password="correct horse battery staple",
        display_name="Other",
        invitation_code=invitation.code,
    )

    assert member.email == "learner@example.test"
    assert member.role == "member"
    assert reused.error == "invitation_exhausted"


def test_invalid_and_expired_invitations_are_rejected() -> None:
    service = make_identity_service()
    admin = service.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    expired = service.create_invitation(
        actor_user_id=admin.id,
        code="EXPIRED",
        expires_at=utc_now() - timedelta(seconds=1),
    )

    invalid_result = service.register_with_invitation(
        email="learner@example.test",
        password="correct horse battery staple",
        display_name="Learner",
        invitation_code="MISSING",
    )
    expired_result = service.register_with_invitation(
        email="learner@example.test",
        password="correct horse battery staple",
        display_name="Learner",
        invitation_code=expired.code,
    )

    assert invalid_result.error == "invitation_not_found"
    assert expired_result.error == "invitation_expired"


def test_login_verifies_password_and_active_status() -> None:
    service = make_identity_service()
    admin = service.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    invitation = service.create_invitation(actor_user_id=admin.id, code="LOGIN")
    member = service.register_with_invitation(
        email="learner@example.test",
        password="correct horse battery staple",
        display_name="Learner",
        invitation_code=invitation.code,
    )

    login = service.authenticate(
        email="learner@example.test",
        password="correct horse battery staple",
    )
    wrong_password = service.authenticate(
        email="learner@example.test",
        password="wrong password",
    )
    disabled = service.set_user_active(
        actor_user_id=admin.id,
        target_user_id=member.id,
        is_active=False,
    )
    disabled_login = service.authenticate(
        email="learner@example.test",
        password="correct horse battery staple",
    )

    assert login.id == member.id
    assert wrong_password.error == "invalid_credentials"
    assert disabled.is_active is False
    assert disabled_login.error == "user_disabled"