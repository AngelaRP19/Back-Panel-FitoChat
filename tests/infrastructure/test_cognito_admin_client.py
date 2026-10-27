"""Cognito de administración: el simulado y el real (este contra moto, con la política de contraseñas del proyecto)."""

import time
from collections.abc import Iterator
from typing import Any

import boto3
import jwt
import pytest
from moto import mock_aws

from app.core.config import Settings
from app.infrastructure import cognito_admin_client as modulo
from app.infrastructure.cognito_admin_client import (
    CognitoReal,
    CognitoSimulado,
    ContrasenaActualIncorrecta,
    ContrasenaNoValida,
    ErrorCognito,
    UsuarioNoExiste,
    UsuarioYaExiste,
)

CORREO = "ana@anuc.org"
CELULAR = "573001112233"


def token_de(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "exp": int(time.time()) + 600}, "clave-de-prueba-de-32-bytes-o-mas!!", algorithm="HS256"
    )


# --- simulado ----------------------------------------------------------------------------------------------------


def test_simulado_crea_y_devuelve_un_sub_distinto_por_usuario() -> None:
    cognito = CognitoSimulado()
    sub1 = cognito.crear_usuario(CORREO, "Ana", CELULAR)
    sub2 = cognito.crear_usuario("beto@anuc.org", "Beto", CELULAR)
    assert sub1 != sub2 and len(sub1) == 36
    assert cognito.existe(CORREO) and cognito.existe(CORREO.upper())  # el correo no distingue mayúsculas


def test_simulado_rechaza_un_correo_repetido() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    with pytest.raises(UsuarioYaExiste):
        cognito.crear_usuario(CORREO.upper(), "Otra", CELULAR)


def test_simulado_sin_contrasena_es_una_cuenta_con_clave_temporal() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    assert cognito.obtener(CORREO).contrasena is None
    cognito.crear_usuario("luis@anuc.org", "Luis", CELULAR, "Clave1234")
    assert cognito.obtener("luis@anuc.org").contrasena == "Clave1234"


def test_simulado_habilita_y_deshabilita() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.deshabilitar(CORREO)
    assert not cognito.obtener(CORREO).habilitado
    cognito.habilitar(CORREO)
    assert cognito.obtener(CORREO).habilitado


def test_simulado_actualiza_atributos_y_cambia_el_correo() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.actualizar_atributos(CORREO, nombre="Ana María", celular_e164="573009998877", correo_nuevo="ana.m@anuc.org")
    usuario = cognito.obtener("ana.m@anuc.org")
    assert (usuario.nombre, usuario.celular) == ("Ana María", "573009998877")
    assert not cognito.existe(CORREO)  # el correo anterior ya no es un usuario


def test_simulado_no_pisa_el_correo_de_otro_usuario() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.crear_usuario("beto@anuc.org", "Beto", CELULAR)
    with pytest.raises(UsuarioYaExiste):
        cognito.actualizar_atributos(CORREO, correo_nuevo="beto@anuc.org")


def test_simulado_elimina_y_recuerda_a_quien_elimino() -> None:
    cognito = CognitoSimulado()
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.eliminar_usuario(CORREO)
    assert not cognito.existe(CORREO) and cognito.eliminados == [CORREO]
    with pytest.raises(UsuarioNoExiste):
        cognito.eliminar_usuario(CORREO)


@pytest.mark.parametrize("operacion", ["deshabilitar", "habilitar"])
def test_simulado_operar_sobre_un_usuario_que_no_existe(operacion: str) -> None:
    with pytest.raises(UsuarioNoExiste):
        getattr(CognitoSimulado(), operacion)("nadie@anuc.org")


def test_simulado_cambia_la_contrasena() -> None:
    cognito = CognitoSimulado()
    sub = cognito.crear_usuario(CORREO, "Ana", CELULAR, "Clave1234")
    cognito.cambiar_contrasena(token_de(sub), "Clave1234", "Nueva5678")
    assert cognito.obtener(CORREO).contrasena == "Nueva5678"


def test_simulado_rechaza_la_contrasena_actual_incorrecta_y_la_nueva_debil() -> None:
    cognito = CognitoSimulado()
    sub = cognito.crear_usuario(CORREO, "Ana", CELULAR, "Clave1234")
    with pytest.raises(ContrasenaActualIncorrecta):
        cognito.cambiar_contrasena(token_de(sub), "equivocada", "Nueva5678")
    with pytest.raises(ContrasenaNoValida):
        cognito.cambiar_contrasena(token_de(sub), "Clave1234", "corta")
    with pytest.raises(UsuarioNoExiste):
        cognito.cambiar_contrasena(token_de("sub-desconocido"), "Clave1234", "Nueva5678")


# --- real, contra moto -------------------------------------------------------------------------------------------


@pytest.fixture
def real() -> Iterator[tuple[CognitoReal, Any, str]]:
    with mock_aws():
        crudo = boto3.client("cognito-idp", region_name="us-east-1")
        pool = crudo.create_user_pool(
            PoolName="anuc",
            UsernameAttributes=["email"],
            AutoVerifiedAttributes=["email"],
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 8,
                    "RequireUppercase": False,
                    "RequireLowercase": False,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                }
            },
        )["UserPool"]["Id"]
        yield CognitoReal(crudo, pool), crudo, pool


def atributos(crudo: Any, pool: str, correo: str) -> dict[str, str]:
    usuario = crudo.admin_get_user(UserPoolId=pool, Username=correo)
    return {a["Name"]: a["Value"] for a in usuario["UserAttributes"]}


def test_real_crea_el_usuario_con_sus_atributos_y_devuelve_el_sub(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    sub = cognito.crear_usuario(CORREO, "Ana Torres", CELULAR)
    attrs = atributos(crudo, pool, CORREO)
    assert attrs["sub"] == sub and attrs["email"] == CORREO and attrs["email_verified"] == "true"
    assert attrs["name"] == "Ana Torres" and attrs["phone_number"] == "+573001112233"  # E.164 con «+»


def test_real_sin_contrasena_deja_que_cognito_envie_la_temporal(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    assert crudo.admin_get_user(UserPoolId=pool, Username=CORREO)["UserStatus"] == "FORCE_CHANGE_PASSWORD"


def test_real_con_contrasena_queda_lista_para_iniciar_sesion(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Luis", CELULAR, "Clave1234")
    assert crudo.admin_get_user(UserPoolId=pool, Username=CORREO)["UserStatus"] == "CONFIRMED"


def test_real_si_la_contrasena_no_cumple_la_politica_no_deja_el_usuario_a_medias(
    real: tuple[CognitoReal, Any, str],
) -> None:
    cognito, crudo, pool = real
    with pytest.raises(ContrasenaNoValida):
        cognito.crear_usuario(CORREO, "Luis", CELULAR, "sinnumeros")
    assert crudo.list_users(UserPoolId=pool)["Users"] == []  # se eliminó lo que alcanzó a crearse


def test_real_correo_repetido(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, _, _ = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    with pytest.raises(UsuarioYaExiste):
        cognito.crear_usuario(CORREO, "Otra", CELULAR)


def test_real_deshabilita_y_habilita(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR, "Clave1234")
    cognito.deshabilitar(CORREO)
    assert crudo.admin_get_user(UserPoolId=pool, Username=CORREO)["Enabled"] is False
    cognito.habilitar(CORREO)
    assert crudo.admin_get_user(UserPoolId=pool, Username=CORREO)["Enabled"] is True


def test_real_actualiza_solo_lo_que_se_le_pide(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.actualizar_atributos(CORREO, nombre="Ana María")
    assert atributos(crudo, pool, CORREO)["name"] == "Ana María"
    assert atributos(crudo, pool, CORREO)["phone_number"] == "+573001112233"  # no tocó el celular
    cognito.actualizar_atributos(CORREO, celular_e164="573009998877")
    assert atributos(crudo, pool, CORREO)["phone_number"] == "+573009998877"


def test_real_cambia_el_correo_y_lo_marca_verificado(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.actualizar_atributos(CORREO, correo_nuevo="ana.m@anuc.org")
    attrs = atributos(crudo, pool, "ana.m@anuc.org")  # ahora el usuario se identifica con el correo nuevo
    assert attrs["email"] == "ana.m@anuc.org" and attrs["email_verified"] == "true"


def test_real_sin_cambios_no_llama_a_cognito() -> None:
    from unittest.mock import MagicMock

    falso = MagicMock()
    CognitoReal(falso, "pool").actualizar_atributos(CORREO)
    falso.admin_update_user_attributes.assert_not_called()


def test_real_elimina(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cognito.crear_usuario(CORREO, "Ana", CELULAR)
    cognito.eliminar_usuario(CORREO)
    assert crudo.list_users(UserPoolId=pool)["Users"] == []
    with pytest.raises(UsuarioNoExiste):
        cognito.eliminar_usuario(CORREO)


def test_real_cambia_la_contrasena_con_el_access_token(real: tuple[CognitoReal, Any, str]) -> None:
    cognito, crudo, pool = real
    cliente = crudo.create_user_pool_client(
        UserPoolId=pool, ClientName="spa", ExplicitAuthFlows=["ALLOW_ADMIN_USER_PASSWORD_AUTH"]
    )["UserPoolClient"]["ClientId"]
    cognito.crear_usuario(CORREO, "Ana", CELULAR, "Clave1234")
    token = crudo.admin_initiate_auth(
        UserPoolId=pool,
        ClientId=cliente,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": CORREO, "PASSWORD": "Clave1234"},
    )["AuthenticationResult"]["AccessToken"]
    cognito.cambiar_contrasena(token, "Clave1234", "Nueva5678")
    with pytest.raises(ContrasenaActualIncorrecta):
        cognito.cambiar_contrasena(token, "Clave1234", "Otra12345")  # la anterior ya no sirve
    with pytest.raises(ContrasenaNoValida):
        cognito.cambiar_contrasena(token, "Nueva5678", "sinnumeros")


def test_real_traduce_los_errores_desconocidos() -> None:
    from unittest.mock import MagicMock

    from botocore.exceptions import ClientError

    falso = MagicMock()
    falso.admin_disable_user.side_effect = ClientError({"Error": {"Code": "TooManyRequestsException"}}, "Op")
    with pytest.raises(ErrorCognito, match="TooManyRequestsException"):
        CognitoReal(falso, "pool").deshabilitar(CORREO)


# --- cuál se usa -------------------------------------------------------------------------------------------------


def test_sin_user_pool_se_usa_el_simulado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo, "get_settings", lambda: Settings(_env_file=None, app_env="local"))  # type: ignore[call-arg]
    assert modulo.get_cognito_admin() is modulo._SIMULADO


def test_con_user_pool_se_usa_el_real(monkeypatch: pytest.MonkeyPatch) -> None:
    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="local", cognito_user_pool_id="us-east-1_X", cognito_app_client_id="c"
    )
    monkeypatch.setattr(modulo, "get_settings", lambda: ajustes)
    modulo._cognito_real.cache_clear()
    try:
        elegido = modulo.get_cognito_admin()
        assert isinstance(elegido, CognitoReal) and elegido._pool == "us-east-1_X"
    finally:
        modulo._cognito_real.cache_clear()
