from sqlalchemy import create_engine, select

from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata, provider_credentials
from app.infrastructure.provider_credentials import ProviderCredentialService

MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def make_services() -> tuple[IdentityService, ProviderCredentialService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity_metadata.create_all(engine)
    return IdentityService(engine), ProviderCredentialService(engine, MASTER_KEY)


def test_provider_secret_is_encrypted_masked_and_scoped_to_user() -> None:
    identity, credentials = make_services()
    owner = identity.bootstrap_admin(
        email="owner@example.test",
        password="correct horse battery staple",
        display_name="Owner",
    )
    secret = "sk-provider-secret-1234"

    stored = credentials.store(user_id=owner.id, provider="openai", secret=secret)

    with credentials.engine.connect() as connection:
        row = connection.execute(select(provider_credentials)).mappings().one()
    assert secret not in row["encrypted_secret"]
    assert stored.masked_secret == "*******************1234"
    assert stored.version == 1
    assert credentials.reveal(user_id=owner.id, provider="openai") == secret
    assert credentials.reveal(user_id="another-user", provider="openai") is None


def test_storing_existing_provider_rotates_secret_and_increments_version() -> None:
    identity, credentials = make_services()
    owner = identity.bootstrap_admin(
        email="owner@example.test",
        password="correct horse battery staple",
        display_name="Owner",
    )
    credentials.store(user_id=owner.id, provider="openai", secret="old-secret")

    rotated = credentials.store(user_id=owner.id, provider="openai", secret="new-secret-5678")

    assert rotated.version == 2
    assert rotated.masked_secret == "***********5678"
    assert credentials.reveal(user_id=owner.id, provider="openai") == "new-secret-5678"
    assert len(credentials.list_masked(user_id=owner.id)) == 1