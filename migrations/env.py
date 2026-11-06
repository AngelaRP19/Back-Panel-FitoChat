from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import Connection, create_engine, pool

import app.infrastructure.db.models  # noqa: F401  (registra todas las tablas en Base.metadata)
from app.core.config import get_database_settings
from app.infrastructure.db.alembic_support import include_object
from app.infrastructure.db.base import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Se lee del entorno en cada ejecución: sirve igual en local, en CI o en una tarea ECS contra RDS.
    return get_database_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=alembic_helpers.render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Los tests pueden inyectar una conexión ya abierta en config.attributes["connection"].
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
