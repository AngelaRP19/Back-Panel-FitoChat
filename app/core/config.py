import os
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

AppEnv = Literal["local", "test", "prod"]
Integracion = Literal["simulada", "real"]
SslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]

# Valores que solo existen fuera de prod, para firmar y validar tokens de prueba.
ISSUER_PRUEBA = "https://cognito-idp.local.test/pool-de-prueba"
CLIENT_ID_PRUEBA = "cliente-de-prueba"


def _falta(**valores: object) -> list[str]:
    return [nombre.upper() for nombre, valor in valores.items() if not valor]


class Settings(BaseSettings):
    """Configuración leída de variables de entorno (en local, también de .env).

    Las variables sin valor por defecto son obligatorias: si faltan, la app falla al arrancar.
    Una variable vacía se trata como no definida.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    app_env: AppEnv = "local"
    integracion_whatsapp: Integracion = "simulada"
    integracion_ia: Integracion = "simulada"
    log_level: str = "INFO"

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None  # solo para LocalStack
    sqs_queue_url: str
    s3_bucket_evidencias: str | None = None

    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None
    cognito_region: str | None = None
    # Archivo donde se guarda la clave de pruebas en `local`, para que la comparta cada proceso.
    jwt_prueba_key_path: str = ".local/jwt_prueba.pem"

    cors_origins: str = ""
    max_body_bytes: int = 10 * 1024 * 1024  # propuesto en la guía (§7)

    # Identificación preliminar (guía §2): una categoría con menos de este porcentaje no se sugiere.
    umbral_identificacion_pct: int = 50
    # Resultado ambiguo: dos categorías sobre el umbral con menos de estos puntos de diferencia.
    margen_ambiguedad_pts: int = 15

    # Conservación de un caso cerrado, con borrado físico al vencer (guía §2). No se amplía (D-07 rechazado).
    meses_conservacion: int = 6

    # Envío masivo de alertas por WhatsApp: tamaño del lote y pausa entre lotes (límites de la API de Meta).
    alertas_lote_envio: int = 50
    alertas_pausa_lote_segundos: float = 1.0

    whatsapp_verify_token: SecretStr
    whatsapp_app_secret: SecretStr
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None

    @model_validator(mode="after")
    def _validar_coherencia(self) -> Self:
        problemas: list[str] = []
        if self.app_env == "prod":
            faltan = _falta(
                cognito_user_pool_id=self.cognito_user_pool_id,
                cognito_app_client_id=self.cognito_app_client_id,
                s3_bucket_evidencias=self.s3_bucket_evidencias,
            )
            if faltan:
                problemas.append(f"En prod son obligatorias: {', '.join(faltan)}")
            if self.aws_endpoint_url:
                problemas.append("AWS_ENDPOINT_URL (LocalStack) no puede usarse en prod")
        elif os.environ.get("AWS_EXECUTION_ENV", "").startswith("AWS_ECS"):
            # Red de seguridad: fuera de prod se aceptan tokens firmados con una clave de pruebas.
            problemas.append("En ECS, APP_ENV debe ser 'prod'")
        if self.integracion_whatsapp == "real":
            faltan = _falta(
                whatsapp_access_token=self.whatsapp_access_token,
                whatsapp_phone_number_id=self.whatsapp_phone_number_id,
            )
            if faltan:
                problemas.append(f"Con INTEGRACION_WHATSAPP=real son obligatorias: {', '.join(faltan)}")
        if "*" in self.cors_origenes:
            problemas.append("CORS_ORIGINS debe ser una lista explícita de orígenes, no '*'")
        if problemas:
            raise ValueError("; ".join(problemas))
        return self

    @property
    def cors_origenes(self) -> list[str]:
        return [origen.strip() for origen in self.cors_origins.split(",") if origen.strip()]

    @property
    def en_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def cognito_configurado(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_app_client_id)

    @property
    def usa_claves_de_prueba(self) -> bool:
        """`test` siempre; `local` solo si no se apuntó a un User Pool real. Nunca en `prod`."""
        return self.app_env == "test" or (self.app_env == "local" and not self.cognito_configurado)

    @property
    def cognito_issuer(self) -> str:
        if self.usa_claves_de_prueba:
            return ISSUER_PRUEBA
        region = self.cognito_region or self.aws_region
        return f"https://cognito-idp.{region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def cognito_client_id(self) -> str:
        if self.usa_claves_de_prueba:
            return self.cognito_app_client_id or CLIENT_ID_PRUEBA
        return self.cognito_app_client_id or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


class DatabaseSettings(BaseSettings):
    """Conexión a la base, separada para que Alembic no exija las variables de SQS ni WhatsApp.

    Dos formas, con `DATABASE_URL` como prioritaria (útil en local):
    - `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/anuc_db`
    - piezas `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE` (en ECS, el usuario y la
      contraseña llegan desde Secrets Manager).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    database_url: SecretStr | None = None

    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "anuc_db"
    db_user: str | None = None
    db_password: SecretStr | None = None
    db_sslmode: SslMode = "require"

    # Dimensión de los embeddings (pgvector). Cambiarla obliga a recalcular TODOS los vectores.
    embedding_dim: int = 1024

    @model_validator(mode="after")
    def _exigir_conexion(self) -> Self:
        if not self.database_url:
            faltan = _falta(db_host=self.db_host, db_user=self.db_user, db_password=self.db_password)
            if faltan:
                raise ValueError(f"Falta la conexión a la base: define DATABASE_URL o {', '.join(faltan)}")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """URL con el driver psycopg 3 (sirve para el engine síncrono de Alembic y el asíncrono de la app)."""
        if self.database_url:
            url = self.database_url.get_secret_value()
            for prefijo in ("postgresql://", "postgres://"):
                if url.startswith(prefijo):
                    return "postgresql+psycopg://" + url.removeprefix(prefijo)
            return url
        # URL.create escapa los caracteres especiales de la contraseña (RDS genera claves con símbolos).
        assert self.db_password is not None
        url_piezas = URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"sslmode": self.db_sslmode},
        )
        return url_piezas.render_as_string(hide_password=False)


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
