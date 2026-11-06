import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from app.core.config import ISSUER_PRUEBA, DatabaseSettings, Settings

# `_env_file=None` evita que un .env local altere las pruebas; las variables obligatorias las pone conftest.
BASE = {"_env_file": None}
CONTRASENA_RDS = "p@ss:w/rd#?%&=+ ñ"  # símbolos que genera RDS y que rompen una URL armada a mano


def _db(**kwargs: object) -> DatabaseSettings:
    return DatabaseSettings(**BASE, **kwargs)  # type: ignore[arg-type]


def _settings(**kwargs: object) -> Settings:
    return Settings(**BASE, **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _sin_conexion_en_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("DATABASE_URL", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_SSLMODE", "DB_NAME", "DB_PORT"):
        monkeypatch.delenv(variable, raising=False)


# --- base de datos ---------------------------------------------------------------------------------------------


def test_url_por_piezas_escapa_la_contrasena() -> None:
    ajustes = _db(db_host="rds.example.com", db_user="anuc_admin", db_password=CONTRASENA_RDS)
    url = make_url(ajustes.sqlalchemy_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.password == CONTRASENA_RDS  # se recupera exacta: el escape es reversible
    assert url.username == "anuc_admin"
    assert (url.host, url.port, url.database) == ("rds.example.com", 5432, "anuc_db")
    assert url.query == {"sslmode": "require"}
    assert CONTRASENA_RDS not in ajustes.sqlalchemy_url  # en la cadena va codificada


def test_piezas_con_valores_propios() -> None:
    ajustes = _db(db_host="localhost", db_port=5433, db_name="otra", db_user="u", db_password="p", db_sslmode="disable")
    url = make_url(ajustes.sqlalchemy_url)
    assert (url.port, url.database, url.query) == (5433, "otra", {"sslmode": "disable"})


def test_database_url_tiene_prioridad_sobre_las_piezas() -> None:
    ajustes = _db(
        database_url="postgresql+psycopg://u:p@desde-url:5432/base_url",
        db_host="desde-piezas",
        db_user="x",
        db_password="y",
    )
    assert make_url(ajustes.sqlalchemy_url).host == "desde-url"


@pytest.mark.parametrize("esquema", ["postgresql://", "postgres://"])
def test_database_url_se_normaliza_al_driver_psycopg(esquema: str) -> None:
    ajustes = _db(database_url=f"{esquema}u:p@host/base")
    assert ajustes.sqlalchemy_url == "postgresql+psycopg://u:p@host/base"


def test_sin_conexion_falla_al_arrancar() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _db()


def test_piezas_incompletas_nombran_lo_que_falta() -> None:
    with pytest.raises(ValidationError) as exc:
        _db(db_host="h")
    mensaje = str(exc.value)
    assert "DB_USER" in mensaje and "DB_PASSWORD" in mensaje and "DB_HOST" not in mensaje


def test_la_contrasena_no_aparece_en_el_repr() -> None:
    ajustes = _db(db_host="h", db_user="u", db_password="clave-secreta")
    assert "clave-secreta" not in repr(ajustes)


def test_variable_vacia_cuenta_como_no_definida(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)  # type: ignore[call-arg]


# --- aplicación ------------------------------------------------------------------------------------------------


def test_valores_por_defecto() -> None:
    ajustes = _settings()
    assert ajustes.app_env == "test"  # lo fija conftest
    assert ajustes.integracion_whatsapp == "simulada" and ajustes.integracion_ia == "simulada"
    assert ajustes.cors_origenes == []
    assert ajustes.usa_claves_de_prueba
    assert ajustes.cognito_issuer == ISSUER_PRUEBA


def test_falta_una_variable_obligatoria(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQS_QUEUE_URL")
    with pytest.raises(ValidationError, match="sqs_queue_url"):
        _settings()


def test_cors_origins_se_separa_por_comas() -> None:
    ajustes = _settings(cors_origins=" https://a.example , https://b.example ,, ")
    assert ajustes.cors_origenes == ["https://a.example", "https://b.example"]


def test_cors_origins_no_acepta_comodin() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _settings(cors_origins="*")


def test_prod_exige_cognito_y_bucket() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(app_env="prod")
    mensaje = str(exc.value)
    for nombre in ("COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID", "S3_BUCKET_EVIDENCIAS"):
        assert nombre in mensaje


def test_prod_completa_usa_el_jwks_real() -> None:
    ajustes = _settings(
        app_env="prod",
        cognito_user_pool_id="us-east-1_AbC123",
        cognito_app_client_id="cliente-real",
        s3_bucket_evidencias="anuc-evidencias",
    )
    assert not ajustes.usa_claves_de_prueba
    assert ajustes.cognito_issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbC123"
    assert ajustes.cognito_jwks_url.endswith("/us-east-1_AbC123/.well-known/jwks.json")
    assert ajustes.cognito_client_id == "cliente-real"


def test_cognito_region_propia() -> None:
    ajustes = _settings(
        app_env="prod",
        cognito_user_pool_id="p",
        cognito_app_client_id="c",
        s3_bucket_evidencias="b",
        cognito_region="us-west-2",
    )
    assert ajustes.cognito_issuer == "https://cognito-idp.us-west-2.amazonaws.com/p"


def test_prod_rechaza_localstack() -> None:
    with pytest.raises(ValidationError, match="AWS_ENDPOINT_URL"):
        _settings(
            app_env="prod",
            cognito_user_pool_id="p",
            cognito_app_client_id="c",
            s3_bucket_evidencias="b",
            aws_endpoint_url="http://localstack:4566",
        )


def test_local_con_user_pool_valida_contra_cognito_real() -> None:
    ajustes = _settings(app_env="local", cognito_user_pool_id="us-east-1_X", cognito_app_client_id="c")
    assert not ajustes.usa_claves_de_prueba


def test_local_sin_user_pool_usa_claves_de_prueba() -> None:
    assert _settings(app_env="local").usa_claves_de_prueba


def test_en_ecs_fuera_de_prod_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Red de seguridad: con claves de prueba cualquiera podría fabricar tokens."""
    monkeypatch.setenv("AWS_EXECUTION_ENV", "AWS_ECS_FARGATE")
    with pytest.raises(ValidationError, match="APP_ENV debe ser 'prod'"):
        _settings(app_env="local")


def test_whatsapp_real_exige_credenciales() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_ACCESS_TOKEN"):
        _settings(integracion_whatsapp="real")
    ok = _settings(integracion_whatsapp="real", whatsapp_access_token="t", whatsapp_phone_number_id="123")
    assert ok.integracion_whatsapp == "real"


def test_flag_invalido_se_rechaza() -> None:
    with pytest.raises(ValidationError):
        _settings(integracion_ia="parcial")
    with pytest.raises(ValidationError):
        _settings(app_env="staging")


def test_ia_real_exige_rol_external_id_y_modelos() -> None:
    with pytest.raises(ValidationError, match="IA_ROLE_ARN|ia_role_arn"):
        _settings(integracion_ia="real")
    ok = _settings(
        integracion_ia="real",
        ia_role_arn="arn:aws:iam::111122223333:role/ia",
        ia_external_id="externo",
        bedrock_model_vision="v",
        bedrock_model_text="t",
        bedrock_model_embed="e",
    )
    assert ok.integracion_ia == "real"


def test_los_umbrales_de_identificacion_son_los_de_la_guia() -> None:
    ajustes = _settings()
    assert (ajustes.umbral_coincidencia, ajustes.umbral_ambiguo) == (50, 15)
