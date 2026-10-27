from pathlib import Path

import pytest

from app.core import tokens_prueba
from app.core.config import CLIENT_ID_PRUEBA, ISSUER_PRUEBA, Settings
from app.core.security_cognito import ClaveFija, ValidadorToken
from app.core.tokens_prueba import emitir_token_de_prueba, obtener_claves_prueba


def _local(ruta: Path, **extra: object) -> Settings:
    return Settings(_env_file=None, app_env="local", jwt_prueba_key_path=str(ruta), **extra)  # type: ignore[arg-type]


def test_local_guarda_la_clave_y_la_reutiliza(tmp_path: Path) -> None:
    ruta = tmp_path / "carpeta" / "jwt.pem"
    primera = obtener_claves_prueba(_local(ruta))
    assert ruta.exists()
    segunda = obtener_claves_prueba(_local(ruta))  # otro proceso: la lee del archivo
    assert primera.publica.public_numbers() == segunda.publica.public_numbers()


def test_un_token_emitido_en_un_proceso_lo_valida_otro(tmp_path: Path) -> None:
    ajustes = _local(tmp_path / "jwt.pem")
    token = emitir_token_de_prueba("sub-1", settings=ajustes)
    validador = ValidadorToken(
        issuer=ISSUER_PRUEBA, client_id=CLIENT_ID_PRUEBA, resolver=ClaveFija(obtener_claves_prueba(ajustes).publica)
    )
    assert validador.validar(token).sub == "sub-1"


def test_archivo_dañado_se_regenera(tmp_path: Path) -> None:
    ruta = tmp_path / "jwt.pem"
    ruta.write_text("esto no es un PEM")
    obtener_claves_prueba(_local(ruta))
    assert ruta.read_text().startswith("-----BEGIN PRIVATE KEY-----")


def test_sin_permiso_de_escritura_usa_clave_en_memoria(tmp_path: Path) -> None:
    archivo = tmp_path / "es-un-archivo"
    archivo.write_text("x")
    claves = obtener_claves_prueba(_local(archivo / "jwt.pem"))  # no se puede crear una carpeta dentro de un archivo
    assert claves.publica is not None


def test_en_test_la_clave_es_la_misma_en_todo_el_proceso() -> None:
    ajustes = Settings(_env_file=None)  # type: ignore[call-arg]
    assert ajustes.app_env == "test"
    assert obtener_claves_prueba(ajustes) is obtener_claves_prueba(ajustes)


def test_comando_emite_un_token_valido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("JWT_PRUEBA_KEY_PATH", str(tmp_path / "jwt.pem"))
    monkeypatch.setattr("sys.argv", ["tokens_prueba", "sub-cli", "--horas", "1"])
    tokens_prueba.get_settings.cache_clear()
    try:
        tokens_prueba.main()
        token = capsys.readouterr().out.strip()
        ajustes = tokens_prueba.get_settings()
        validador = ValidadorToken(
            issuer=ajustes.cognito_issuer,
            client_id=ajustes.cognito_client_id,
            resolver=ClaveFija(obtener_claves_prueba(ajustes).publica),
        )
        assert validador.validar(token).sub == "sub-cli"
    finally:
        tokens_prueba.get_settings.cache_clear()


def test_comando_se_niega_si_hay_user_pool_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_X")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "cliente")
    monkeypatch.setattr("sys.argv", ["tokens_prueba", "sub-cli"])
    tokens_prueba.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="Cognito real"):
            tokens_prueba.main()
    finally:
        tokens_prueba.get_settings.cache_clear()
