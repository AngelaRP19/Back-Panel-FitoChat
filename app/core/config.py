from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída de variables de entorno (en local, también de .env).

    Las variables sin valor por defecto son obligatorias: si faltan, la app falla al arrancar.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None  # solo para LocalStack
    sqs_queue_url: str
    whatsapp_verify_token: SecretStr
    whatsapp_app_secret: SecretStr
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class DatabaseSettings(BaseSettings):
    """Configuración de la base de datos, separada para que Alembic no exija las variables de SQS/WhatsApp."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ej.: postgresql+psycopg://usuario:clave@host:5432/anuc_db (en RDS: añadir ?sslmode=require).
    database_url: SecretStr
    # Dimensión de los embeddings (pgvector). Cambiarla obliga a recalcular TODOS los vectores.
    embedding_dim: int = 1024

    @property
    def sqlalchemy_url(self) -> str:
        """URL con el driver psycopg 3 (sirve para el engine síncrono de Alembic y el asíncrono de la app)."""
        url = self.database_url.get_secret_value()
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()  # type: ignore[call-arg]
