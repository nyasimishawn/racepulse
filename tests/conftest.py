from collections.abc import Generator
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.db.database import get_db
from app.main import app


def pytest_addoption(parser):
    parser.addoption(
        "--postgresql",
        action="store_true",
        help="Run against a new temporary PostgreSQL database using Alembic.",
    )


@pytest.fixture(scope="session")
def postgres_engine(request):
    if not request.config.getoption("--postgresql"):
        return None
    database_name = "racepulse_test_" + uuid4().hex
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    test_url = admin.url.set(database=database_name)
    engine = create_engine(test_url)
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        env = dict(
            os.environ,
            DATABASE_URL=test_url.render_as_string(hide_password=False),
        )
        # Validate a fresh install and reversibility of the two new revisions.
        for operation, revision in [
            ("upgrade", "head"),
            ("downgrade", "a1b2c3d4e5f6"),
            ("upgrade", "head"),
        ]:
            subprocess.run(
                [sys.executable, "-m", "alembic", operation, revision],
                env=env,
                check=True,
            )
        yield engine
    finally:
        engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(
                    text(f'DROP DATABASE "{database_name}" WITH (FORCE)')
                )
        admin.dispose()


@pytest.fixture()
def db_session(request) -> Generator[Session, None, None]:
    postgres = request.config.getoption("--postgresql")
    if postgres:
        engine = request.getfixturevalue("postgres_engine")
    else:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        if postgres:
            # Only the generated database above is ever cleaned.
            assert engine.url.database.startswith("racepulse_test_")
            quote = engine.dialect.identifier_preparer.quote
            tables = ", ".join(
                quote(table.name) for table in Base.metadata.tables.values()
            )
            with engine.begin() as connection:
                connection.execute(text(f"TRUNCATE {tables} CASCADE"))
        else:
            Base.metadata.drop_all(bind=engine)
            engine.dispose()


@pytest.fixture()
def api_client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
