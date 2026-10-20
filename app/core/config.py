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
