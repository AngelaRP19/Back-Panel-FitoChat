"""CORS, cabeceras de seguridad, límite de cuerpo y documentación OpenAPI (no necesitan base de datos)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ORIGEN_PANEL = "https://panel.example.com"


def _cliente(**ajustes: object) -> TestClient:
    settings = Settings(_env_file=None, **ajustes)  # type: ignore[call-arg, arg-type]
    return TestClient(create_app(settings), raise_server_exceptions=False)


@pytest.fixture
def con_cors() -> Iterator[TestClient]:
    with _cliente(cors_origins=f"{ORIGEN_PANEL},https://otro.example.com") as c:
        yield c


# --- CORS --------------------------------------------------------------------------------------------------------


def test_preflight_de_origen_permitido(con_cors: TestClient) -> None:
    r = con_cors.options(
        "/api/v1/me",
        headers={
            "Origin": ORIGEN_PANEL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGEN_PANEL
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in r.headers


def test_preflight_de_origen_no_permitido(con_cors: TestClient) -> None:
    r = con_cors.options(
        "/api/v1/me", headers={"Origin": "https://malo.example.com", "Access-Control-Request-Method": "GET"}
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_la_respuesta_de_error_tambien_lleva_cors(con_cors: TestClient) -> None:
    r = con_cors.get("/api/v1/me", headers={"Origin": ORIGEN_PANEL})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == ORIGEN_PANEL
    assert r.headers["access-control-expose-headers"] == "X-Request-Id"


def test_sin_cors_origins_no_hay_cabeceras_cors() -> None:
    with _cliente() as c:
        r = c.get("/salud", headers={"Origin": ORIGEN_PANEL})
        assert "access-control-allow-origin" not in r.headers


# --- cabeceras de seguridad ------------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", ["/salud", "/api/v1/me", "/no-existe"])
def test_cabeceras_de_seguridad_en_todas_las_respuestas(ruta: str) -> None:
    with _cliente() as c:
        r = c.get(ruta)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-request-id"]


# --- límite del cuerpo -------------------------------------------------------------------------------------------


def test_cuerpo_mayor_al_limite_413_por_content_length() -> None:
    with _cliente(max_body_bytes=100) as c:
        r = c.post("/webhook/whatsapp", content=b"x" * 101)
    assert r.status_code == 413
    assert r.json()["codigo"] == "SOLICITUD_INVALIDA"
    assert r.headers["x-request-id"]


def test_cuerpo_sin_content_length_se_corta_al_pasar_el_limite() -> None:
    def en_trozos() -> Iterator[bytes]:
        for _ in range(5):
            yield b"x" * 40

    with _cliente(max_body_bytes=100) as c:
        r = c.post("/webhook/whatsapp", content=en_trozos())  # Transfer-Encoding: chunked
    assert r.status_code == 413


def test_cuerpo_dentro_del_limite_llega_al_endpoint() -> None:
    with _cliente(max_body_bytes=100) as c:
        r = c.post("/webhook/whatsapp", content=b"x" * 100)
    assert r.status_code == 401  # llegó al webhook, que rechaza la firma ausente


# --- documentación -----------------------------------------------------------------------------------------------


def test_docs_disponibles_fuera_de_prod() -> None:
    with _cliente() as c:
        assert c.get("/docs").status_code == 200
        assert c.get("/openapi.json").status_code == 200


def test_docs_y_openapi_apagados_en_prod() -> None:
    with _cliente(
        app_env="prod",
        cognito_user_pool_id="us-east-1_AbC123",
        cognito_app_client_id="cliente",
        s3_bucket_evidencias="bucket",
    ) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/salud").json() == {"status": "ok"}


def test_las_rutas_del_panel_viven_bajo_api_v1_y_las_demas_fuera() -> None:
    with _cliente() as c:
        rutas = set(c.get("/openapi.json").json()["paths"])
    assert "/api/v1/me" in rutas
    assert {"/salud", "/webhook/whatsapp"} <= rutas
    assert "/me" not in rutas


# --- JWKS caído --------------------------------------------------------------------------------------------------


def test_jwks_caido_responde_503_sin_detalles_tecnicos() -> None:
    from app.core.config import CLIENT_ID_PRUEBA, ISSUER_PRUEBA
    from app.core.security_cognito import JwksNoDisponible, ValidadorToken, get_validador

    class SinJwks:
        def clave_para(self, token: str) -> object:
            raise JwksNoDisponible

    aplicacion = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    aplicacion.dependency_overrides[get_validador] = lambda: ValidadorToken(
        issuer=ISSUER_PRUEBA, client_id=CLIENT_ID_PRUEBA, resolver=SinJwks()
    )
    with TestClient(aplicacion, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/me", headers={"Authorization": "Bearer a.b.c"})
    assert r.status_code == 503
    assert r.json()["codigo"] == "ERROR_INTERNO"
    assert "jwks" not in r.text.lower() and "cognito" not in r.text.lower()
