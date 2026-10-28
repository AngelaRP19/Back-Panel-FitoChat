"""Validadores de los esquemas de cuentas: qué se acepta, qué se normaliza y qué mensaje ve la persona."""

import pytest
from pydantic import ValidationError

from app.api.paginacion import PAGE_SIZE_MAXIMO, ParametrosPagina
from app.core.roles import Rol
from app.modules.panel_api.cuentas.schemas import (
    ActualizarMe,
    CambiarContrasenaIn,
    CuentaEditar,
    CuentaIn,
    PrimerLiderIn,
    VerificarCorreoIn,
)


def mensajes(error: ValidationError) -> dict[str, str]:
    return {".".join(map(str, e["loc"])): str(e["msg"]).removeprefix("Value error, ") for e in error.errors()}


VALIDO = {"nombre": "Ana Torres", "correo": "ana@anuc.org", "celular": "3001234567", "rol": "TECNICO"}


def test_una_cuenta_valida() -> None:
    cuenta = CuentaIn(**VALIDO)  # type: ignore[arg-type]
    assert (cuenta.nombre, cuenta.correo, cuenta.celular, cuenta.rol) == (
        "Ana Torres",
        "ana@anuc.org",
        "3001234567",
        Rol.TECNICO,
    )


def test_el_nombre_se_recorta_y_el_correo_va_en_minusculas() -> None:
    cuenta = CuentaIn(**{**VALIDO, "nombre": "  Ana Torres  ", "correo": "  Ana.Torres@ANUC.org "})  # type: ignore[arg-type]
    assert cuenta.nombre == "Ana Torres" and cuenta.correo == "ana.torres@anuc.org"


@pytest.mark.parametrize("nombre", ["", "   ", "x" * 101])
def test_nombre_invalido(nombre: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaIn(**{**VALIDO, "nombre": nombre})  # type: ignore[arg-type]
    assert "nombre" in mensajes(exc.value)


@pytest.mark.parametrize(
    "correo", ["", "ana", "ana@", "@anuc.org", "ana@anuc", "ana @anuc.org", "a@b@c.co", "x" * 96 + "@a.co"]
)
def test_correo_invalido(correo: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaIn(**{**VALIDO, "correo": correo})  # type: ignore[arg-type]
    assert mensajes(exc.value)["correo"].startswith("Escribe un correo válido")


@pytest.mark.parametrize(
    "celular",
    ["", "300123456", "30012345678", "2001234567", "+583001234567", "3001234abc"],
)
def test_celular_invalido(celular: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaIn(**{**VALIDO, "celular": celular})  # type: ignore[arg-type]
    assert mensajes(exc.value)["celular"] == "Escribe un celular de 10 dígitos que empiece por 3"


@pytest.mark.parametrize("celular", ["+573001234567", "573001234567", "300 123 4567", "+57 300-123-4567"])
def test_celular_en_e164_o_con_espacios_se_normaliza(celular: str) -> None:
    """El panel envía E.164 (+573001234567); la API trabaja con 10 dígitos."""
    assert CuentaIn(**{**VALIDO, "celular": celular}).celular == "3001234567"  # type: ignore[arg-type]


def test_rol_invalido() -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaIn(**{**VALIDO, "rol": "ADMIN"})  # type: ignore[arg-type]
    assert "rol" in mensajes(exc.value)


def test_todos_los_errores_juntos() -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaIn(nombre="", correo="x", celular="1", rol="X")  # type: ignore[arg-type]
    assert set(mensajes(exc.value)) == {"nombre", "correo", "celular", "rol"}


# --- contraseña --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password", ["Clave123", "abcdefg1", "12345678a", "Una contraseña larga 2026", "a" * 127 + "1"]
)
def test_contrasenas_validas(password: str) -> None:
    assert PrimerLiderIn(**{**VALIDO, "password": password, "rol": None}).password == password  # type: ignore[arg-type]


@pytest.mark.parametrize("password", ["", "corta1", "sinnumeros", "12345678", "a" * 129 + "1"])
def test_contrasenas_invalidas(password: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PrimerLiderIn(nombre="Luis", correo="l@anuc.org", celular="3001234567", password=password)
    assert "password" in mensajes(exc.value)


def test_el_mensaje_de_la_politica() -> None:
    with pytest.raises(ValidationError) as exc:
        CambiarContrasenaIn(actual="x", nueva="corta")
    assert mensajes(exc.value)["nueva"] == "Mínimo 8 caracteres, con letras y números."


def test_la_contrasena_actual_se_envia_tal_cual() -> None:
    """La actual no se valida contra la política (puede ser una contraseña vieja); la verifica Cognito."""
    assert CambiarContrasenaIn(actual="x", nueva="Nueva5678").actual == "x"
    with pytest.raises(ValidationError):
        CambiarContrasenaIn(actual="", nueva="Nueva5678")


# --- mi cuenta ---------------------------------------------------------------------------------------------------


def test_actualizar_me_acepta_nombre_y_celular_opcionales() -> None:
    assert ActualizarMe().nombre is None
    assert ActualizarMe(nombre=" Ana ", celular="3001234567").nombre == "Ana"


def test_actualizar_me_acepta_el_correo() -> None:
    """B-04: el panel siempre envía el correo; si cambia, se actualiza en Cognito y en la base."""
    assert ActualizarMe(correo="otro@anuc.org").correo == "otro@anuc.org"


def test_actualizar_me_valida_el_correo() -> None:
    with pytest.raises(ValidationError) as exc:
        ActualizarMe(correo="no-es-correo")
    assert mensajes(exc.value)["correo"].startswith("Escribe un correo válido")


# --- edición -----------------------------------------------------------------------------------------------------


def test_editar_cuenta_todo_es_opcional() -> None:
    assert CuentaEditar().model_dump(exclude_none=True) == {}
    assert CuentaEditar(celular="3109998877").celular == "3109998877"


def test_editar_cuenta_ignora_campos_que_no_conoce() -> None:
    assert CuentaEditar(**{"nombre": "Ana", "casos_activos": 3}).model_dump(exclude_none=True) == {"nombre": "Ana"}  # type: ignore[arg-type]


def test_editar_cuenta_valida_lo_que_recibe() -> None:
    with pytest.raises(ValidationError) as exc:
        CuentaEditar(correo="mal", celular="1")
    assert set(mensajes(exc.value)) == {"correo", "celular"}


def test_verificar_correo_normaliza_y_valida() -> None:
    assert VerificarCorreoIn(correo=" ANA@anuc.ORG ").correo == "ana@anuc.org"
    with pytest.raises(ValidationError):
        VerificarCorreoIn(correo="no-es-un-correo")


# --- paginación --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("page", "size", "offset"), [(1, 20, 0), (2, 20, 20), (3, 10, 20), (1, 100, 0)])
def test_offset(page: int, size: int, offset: int) -> None:
    assert ParametrosPagina(page=page, page_size=size).offset == offset


def test_el_tamano_maximo_de_pagina_es_100() -> None:
    assert PAGE_SIZE_MAXIMO == 100
