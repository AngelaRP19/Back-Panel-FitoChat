"""/productores: alta, edición, estado y listado con filtros combinados (solo el líder técnico)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.roles import Rol
from app.infrastructure.db.models import MensajeWhatsapp
from tests.api.conftest import Cuenta, NuevaCuenta, auditoria_de, consultar
from tests.api.territorio import PRODUCTORES as URL
from tests.api.territorio import crear_caso, crear_cultivo, crear_finca, crear_productor

NUEVO = {"nombre": "Beatriz Núñez", "celular": "3109998877"}


@pytest.fixture
def lider(nueva_cuenta: NuevaCuenta) -> Cuenta:
    return nueva_cuenta(Rol.LIDER_TECNICO)


def mensaje(motor: Engine, id_productor: str, celular_e164: str, hace: timedelta, n: int = 1) -> None:
    with Session(motor) as sesion:
        sesion.add(
            MensajeWhatsapp(
                wamid=f"wamid.{id_productor}.{n}",
                telefono=celular_e164,
                tipo="text",
                contenido={"texto": "hola"},
                estado_procesamiento="PROCESADO",
                fecha_recepcion=datetime.now(UTC) - hace,
                id_productor=id_productor,
            )
        )
        sesion.commit()


# --- permisos ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", URL, None),
        ("POST", URL, NUEVO),
        ("GET", f"{URL}/PRO-000001", None),
        ("PATCH", f"{URL}/PRO-000001", {"nombre": "X"}),
        ("PATCH", f"{URL}/PRO-000001/estado", {"activo": False}),
    ],
)
def test_sin_sesion_401(api: TestClient, metodo: str, ruta: str, cuerpo: dict[str, Any] | None) -> None:
    assert api.request(metodo, ruta, json=cuerpo).status_code == 401


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", URL, None),
        ("POST", URL, NUEVO),
        ("GET", f"{URL}/PRO-000001", None),
        ("PATCH", f"{URL}/PRO-000001", {"nombre": "X"}),
        ("PATCH", f"{URL}/PRO-000001/estado", {"activo": False}),
    ],
)
def test_el_tecnico_no_administra_productores_403(
    api: TestClient, nueva_cuenta: NuevaCuenta, metodo: str, ruta: str, cuerpo: dict[str, Any] | None
) -> None:
    ana = nueva_cuenta(Rol.TECNICO)
    r = api.request(metodo, ruta, json=cuerpo, headers=ana.headers)
    assert r.status_code == 403 and r.json()["codigo"] == "SIN_PERMISO"


# --- POST --------------------------------------------------------------------------------------------------------


def test_crea_un_productor(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    r = api.post(URL, json=NUEVO, headers=lider.headers)
    assert r.status_code == 201
    p = r.json()
    assert p["id"].startswith("PRO-") and (p["nombre"], p["celular"], p["activo"]) == (
        "Beatriz Núñez",
        "3109998877",
        True,
    )
    assert (p["vereda"], p["fincas"], p["casos_activos"], p["casos_ultimo_anio"]) == (None, [], 0, 0)
    assert p["ultima_consulta_en"] is None and datetime.fromisoformat(p["registrado_en"]).tzinfo is not None
    fila = consultar(migrated, "SELECT * FROM productores")[0]
    assert fila["telefono_productor"] == "573109998877" and fila["estado_cuenta_productor"] == "ACTIVO"
    assert fila["fecha_consentimiento"] is None  # el consentimiento lo pide el chatbot al primer contacto (P-B08)


def test_la_fecha_de_registro_es_el_dia_de_hoy_en_colombia(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    api.post(URL, json=NUEVO, headers=lider.headers)
    hoy_bogota = consultar(migrated, "SELECT (now() AT TIME ZONE 'America/Bogota')::date AS hoy")[0]["hoy"]
    assert consultar(migrated, "SELECT fecha_registro_productor AS f FROM productores")[0]["f"] == hoy_bogota


def test_el_registro_se_entrega_como_inicio_del_dia_en_utc(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    id_productor = crear_productor(api, lider)["id"]
    with migrated.begin() as c:
        c.execute(text("UPDATE productores SET fecha_registro_productor = '2026-10-05'"))
    registrado = api.get(f"{URL}/{id_productor}", headers=lider.headers).json()["registrado_en"]
    assert registrado.startswith("2026-10-05T05:00:00")  # 00:00 en Bogotá (UTC-5)


def test_se_puede_registrar_deshabilitado(api: TestClient, lider: Cuenta) -> None:
    r = api.post(URL, json={**NUEVO, "activo": False}, headers=lider.headers)
    assert r.status_code == 201 and r.json()["activo"] is False


def test_el_nombre_se_recorta(api: TestClient, lider: Cuenta) -> None:
    assert api.post(URL, json={**NUEVO, "nombre": "  Beatriz  "}, headers=lider.headers).json()["nombre"] == "Beatriz"


def test_audita_el_alta_sin_el_celular(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    id_productor = api.post(URL, json=NUEVO, headers=lider.headers).json()["id"]
    [registro] = auditoria_de(migrated)
    assert (registro["origen"], registro["id_lider_tecnico"], registro["accion"]) == (
        "LIDER",
        lider.id,
        "CREAR_PRODUCTOR",
    )
    assert (registro["entidad_afectada"], registro["resultado"]) == ("productores", "EXITO")
    assert id_productor in registro["descripcion"]
    assert "3109998877" not in " ".join(map(str, registro.values()))


def test_celular_repetido_409(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    api.post(URL, json=NUEVO, headers=lider.headers)
    r = api.post(URL, json={**NUEVO, "nombre": "Otra Persona"}, headers=lider.headers)
    assert r.status_code == 409 and r.json()["codigo"] == "DUPLICADO"
    assert r.json()["campos"] == {"celular": "Este número de celular ya está registrado"}
    assert len(consultar(migrated, "SELECT 1 FROM productores")) == 1 and len(auditoria_de(migrated)) == 1


@pytest.mark.parametrize(
    ("cambio", "campo"),
    [
        ({"nombre": ""}, "nombre"),
        ({"nombre": "x" * 101}, "nombre"),
        ({"celular": "+583109998877"}, "celular"),
        ({"celular": "31099988"}, "celular"),
        ({"celular": "1234567890"}, "celular"),
    ],
)
def test_datos_invalidos_422(api: TestClient, lider: Cuenta, cambio: dict[str, str], campo: str) -> None:
    r = api.post(URL, json={**NUEVO, **cambio}, headers=lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION" and campo in r.json()["campos"]


def test_faltan_campos(api: TestClient, lider: Cuenta) -> None:
    assert set(api.post(URL, json={}, headers=lider.headers).json()["campos"]) == {"nombre", "celular"}


def test_dos_altas_simultaneas_con_el_mismo_celular_dejan_una(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    import asyncio

    import httpx

    from app.main import app

    async def disparar() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cliente:
            return await asyncio.gather(*(cliente.post(URL, json=NUEVO, headers=lider.headers) for _ in range(3)))

    codigos = sorted(r.status_code for r in asyncio.run(disparar()))
    assert codigos == [201, 409, 409]
    assert len(consultar(migrated, "SELECT 1 FROM productores")) == 1


# --- GET /{id} ---------------------------------------------------------------------------------------------------


def test_obtiene_un_productor_con_sus_fincas(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"], "El Roble", "San Luis")
    crear_cultivo(api, lider, finca["id"], "Lote 1", especies=("MARACUYA", "GULUPA"))
    r = api.get(f"{URL}/{p['id']}", headers=lider.headers).json()
    assert r["vereda"] == "SAN_LUIS"
    assert r["fincas"] == [
        {"id": finca["id"], "nombre": "El Roble", "vereda": "SAN_LUIS", "especies": ["GULUPA", "MARACUYA"]}
    ]


def test_la_vereda_es_la_de_su_primera_finca(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    p = crear_productor(api, lider)
    crear_finca(api, lider, p["id"], "Primera", "Centro")
    crear_finca(api, lider, p["id"], "Segunda", "San Luis")
    r = api.get(f"{URL}/{p['id']}", headers=lider.headers).json()
    assert r["vereda"] == "CENTRO" and [f["nombre"] for f in r["fincas"]] == ["Primera", "Segunda"]


@pytest.mark.parametrize("id_productor", ["PRO-999999", "TEC-000001", "x", "%20"])
def test_productor_inexistente_404(api: TestClient, lider: Cuenta, id_productor: str) -> None:
    r = api.get(f"{URL}/{id_productor}", headers=lider.headers)
    assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADO"


def test_no_expone_el_celular_en_e164(api: TestClient, lider: Cuenta) -> None:
    crear_productor(api, lider)
    assert "573001112" not in api.get(URL, headers=lider.headers).text


# --- PATCH /{id} -------------------------------------------------------------------------------------------------


def test_edita_nombre_y_celular(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}", json={"nombre": "Nuevo Nombre", "celular": "3201112233"}, headers=lider.headers)
    assert r.status_code == 200 and (r.json()["nombre"], r.json()["celular"]) == ("Nuevo Nombre", "3201112233")
    assert consultar(migrated, "SELECT telefono_productor AS t FROM productores")[0]["t"] == "573201112233"


def test_edita_un_solo_campo(api: TestClient, lider: Cuenta) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}", json={"nombre": "Solo Nombre"}, headers=lider.headers)
    assert (r.json()["nombre"], r.json()["celular"]) == ("Solo Nombre", p["celular"])


def test_el_estado_no_se_cambia_con_el_patch_del_productor(api: TestClient, lider: Cuenta) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}", json={"nombre": "Otro", "activo": False}, headers=lider.headers)
    assert r.json()["activo"] is True  # para eso existe PATCH /{id}/estado


def test_celular_de_otro_productor_409(api: TestClient, lider: Cuenta) -> None:
    uno, dos = crear_productor(api, lider, 1), crear_productor(api, lider, 2)
    r = api.patch(f"{URL}/{uno['id']}", json={"celular": dos["celular"]}, headers=lider.headers)
    assert r.status_code == 409 and r.json()["campos"] == {"celular": "Este número de celular ya está registrado"}


def test_conservar_su_propio_celular_no_es_duplicado(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}", json={"celular": p["celular"], "nombre": p["nombre"]}, headers=lider.headers)
    assert r.status_code == 200 and len(auditoria_de(migrated)) == 1  # solo la del alta: sin cambios no audita


def test_edicion_invalida_422(api: TestClient, lider: Cuenta) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}", json={"nombre": "", "celular": "1"}, headers=lider.headers)
    assert r.status_code == 422 and set(r.json()["campos"]) == {"nombre", "celular"}


def test_editar_un_productor_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert api.patch(f"{URL}/PRO-999999", json={"nombre": "X"}, headers=lider.headers).status_code == 404


def test_audita_la_edicion_sin_celulares(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    api.patch(f"{URL}/{p['id']}", json={"nombre": "Nombre Nuevo", "celular": "3201112233"}, headers=lider.headers)
    registro = auditoria_de(migrated)[-1]
    assert (registro["accion"], registro["origen"], registro["id_lider_tecnico"]) == (
        "EDITAR_PRODUCTOR",
        "LIDER",
        lider.id,
    )
    assert p["nombre"] in registro["valor_anterior"] and "Nombre Nuevo" in registro["valor_nuevo"]
    assert "3201112233" not in " ".join(map(str, registro.values())) and p["celular"] not in " ".join(
        map(str, registro.values())
    )


# --- PATCH /{id}/estado ------------------------------------------------------------------------------------------


def test_deshabilita_y_habilita(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    r = api.patch(f"{URL}/{p['id']}/estado", json={"activo": False}, headers=lider.headers)
    assert r.status_code == 200 and r.json()["activo"] is False and r.json()["id"] == p["id"]
    assert consultar(migrated, "SELECT estado_cuenta_productor AS e FROM productores")[0]["e"] == "DESHABILITADO"
    assert api.patch(f"{URL}/{p['id']}/estado", json={"activo": True}, headers=lider.headers).json()["activo"] is True


def test_cambiar_el_estado_es_idempotente_y_no_audita_dos_veces(
    api: TestClient, lider: Cuenta, migrated: Engine
) -> None:
    p = crear_productor(api, lider)
    api.patch(f"{URL}/{p['id']}/estado", json={"activo": True}, headers=lider.headers)  # ya estaba activo
    api.patch(f"{URL}/{p['id']}/estado", json={"activo": False}, headers=lider.headers)
    api.patch(f"{URL}/{p['id']}/estado", json={"activo": False}, headers=lider.headers)
    acciones = [a["accion"] for a in auditoria_de(migrated)]
    assert acciones == ["CREAR_PRODUCTOR", "ESTADO_PRODUCTOR"]


def test_audita_el_cambio_de_estado(api: TestClient, lider: Cuenta, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    api.patch(f"{URL}/{p['id']}/estado", json={"activo": False}, headers=lider.headers)
    registro = auditoria_de(migrated)[-1]
    assert "deshabilitado" in registro["descripcion"]
    assert '"activo": true' in registro["valor_anterior"] and '"activo": false' in registro["valor_nuevo"]


def test_deshabilitar_no_borra_sus_casos(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> None:
    p = crear_productor(api, lider)
    finca = crear_finca(api, lider, p["id"])
    cultivo = crear_cultivo(api, lider, finca["id"])["cultivos"][0]
    id_caso = crear_caso(migrated, cultivo["id"])
    r = api.patch(f"{URL}/{p['id']}/estado", json={"activo": False}, headers=lider.headers).json()
    assert r["activo"] is False and r["casos_activos"] == 1
    assert consultar(migrated, "SELECT id_caso FROM casos")[0]["id_caso"] == id_caso


def test_el_cuerpo_del_estado_exige_un_booleano(api: TestClient, lider: Cuenta) -> None:
    p = crear_productor(api, lider)
    for cuerpo in ({}, {"activo": "tal vez"}, {"activo": None}):
        assert api.patch(f"{URL}/{p['id']}/estado", json=cuerpo, headers=lider.headers).status_code == 422


def test_estado_de_un_productor_inexistente_404(api: TestClient, lider: Cuenta) -> None:
    assert api.patch(f"{URL}/PRO-999999/estado", json={"activo": False}, headers=lider.headers).status_code == 404


# --- GET / (listado y filtros) -----------------------------------------------------------------------------------


def listar(api: TestClient, lider: Cuenta, **filtros: Any) -> dict[str, Any]:
    r = api.get(URL, params=filtros, headers=lider.headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def nombres(api: TestClient, lider: Cuenta, **filtros: Any) -> list[str]:
    return [p["nombre"] for p in listar(api, lider, **filtros)["items"]]


@pytest.fixture
def zona(api: TestClient, lider: Cuenta, catalogos: None, migrated: Engine) -> dict[str, Any]:
    """Cuatro productores: Ana (Centro, gulupa, 2 casos), Álvaro (San Luis, maracuyá), Bea (sin fincas), Carlos (deshabilitado)."""
    ana = crear_productor(api, lider, 1, nombre="Ana Pérez")
    alvaro = crear_productor(api, lider, 2, nombre="Álvaro Díaz")
    bea = crear_productor(api, lider, 3, nombre="Bea Núñez")
    carlos = crear_productor(api, lider, 4, nombre="Carlos Ruiz")
    f_ana = crear_finca(api, lider, ana["id"], "La Esperanza", "Centro")
    f_alvaro = crear_finca(api, lider, alvaro["id"], "El Roble", "San Luis")
    c_ana = crear_cultivo(api, lider, f_ana["id"], "Lote 1", especies=("GULUPA",))["cultivos"][0]
    c_alvaro = crear_cultivo(api, lider, f_alvaro["id"], "Lote 1", "INVERNADERO", ("MARACUYA",))["cultivos"][0]
    api.patch(f"{URL}/{carlos['id']}/estado", json={"activo": False}, headers=lider.headers)
    crear_caso(migrated, c_ana["id"], "GULUPA", "En revisión")
    crear_caso(migrated, c_ana["id"], "GULUPA", "Cerrado")
    return {"ana": ana, "alvaro": alvaro, "bea": bea, "carlos": carlos, "c_ana": c_ana, "c_alvaro": c_alvaro}


def test_ordena_por_nombre_sin_importar_las_tildes(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider) == ["Álvaro Díaz", "Ana Pérez", "Bea Núñez", "Carlos Ruiz"]


def test_sin_productores_la_lista_viene_vacia(api: TestClient, lider: Cuenta) -> None:
    assert listar(api, lider) == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_cuenta_casos_activos_y_del_ultimo_anio(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    ana = next(p for p in listar(api, lider)["items"] if p["nombre"] == "Ana Pérez")
    assert (ana["casos_activos"], ana["casos_ultimo_anio"]) == (1, 2)  # el cerrado ya no está activo


def test_un_caso_de_hace_mas_de_un_anio_no_cuenta_en_el_ultimo_anio(
    api: TestClient, lider: Cuenta, zona: dict[str, Any], migrated: Engine
) -> None:
    crear_caso(migrated, zona["c_alvaro"]["id"], "MARACUYA", "Cerrado", dias_atras=400)
    alvaro = next(p for p in listar(api, lider)["items"] if p["nombre"] == "Álvaro Díaz")
    assert (alvaro["casos_activos"], alvaro["casos_ultimo_anio"]) == (0, 0)


@pytest.mark.parametrize(
    ("estado", "esperados"),
    [("activo", {"Ana Pérez", "Álvaro Díaz", "Bea Núñez"}), ("deshabilitado", {"Carlos Ruiz"})],
)
def test_filtra_por_estado(
    api: TestClient, lider: Cuenta, zona: dict[str, Any], estado: str, esperados: set[str]
) -> None:
    assert set(nombres(api, lider, estado=estado)) == esperados


def test_busca_por_nombre_sin_tildes_ni_mayusculas(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, q="alvaro") == ["Álvaro Díaz"]
    assert nombres(api, lider, q="PÉREZ") == ["Ana Pérez"]
    assert nombres(api, lider, q="nunez") == ["Bea Núñez"]
    assert nombres(api, lider, q="zzz") == []


def test_busca_por_celular_desde_tres_digitos(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, q="3001112002") == ["Álvaro Díaz"]
    assert nombres(api, lider, q="112002") == ["Álvaro Díaz"]
    assert nombres(api, lider, q="+57 300 111 2002") == ["Álvaro Díaz"]
    assert len(nombres(api, lider, q="300")) == 4  # tres dígitos ya cuentan como número


def test_menos_de_tres_digitos_no_buscan_en_el_celular(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, q="30") == []


def test_la_busqueda_no_es_inyectable(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    for q in ("'; DROP TABLE productores; --", "%", "_", "\\"):
        assert api.get(URL, params={"q": q}, headers=lider.headers).status_code == 200
    assert len(nombres(api, lider)) == 4


def test_filtra_por_vereda(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, vereda="CENTRO") == ["Ana Pérez"]
    assert nombres(api, lider, vereda="SAN_LUIS") == ["Álvaro Díaz"]
    assert nombres(api, lider, vereda="san luis") == ["Álvaro Díaz"]  # también acepta el nombre
    assert nombres(api, lider, vereda="NO_EXISTE") == []


def test_filtra_por_especie(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, especie="GULUPA") == ["Ana Pérez"]
    assert nombres(api, lider, especie="MARACUYA") == ["Álvaro Díaz"]
    assert nombres(api, lider, especie="GRANADILLA") == []


def test_filtra_por_casos_activos(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, con_casos="con") == ["Ana Pérez"]
    assert set(nombres(api, lider, con_casos="sin")) == {"Álvaro Díaz", "Bea Núñez", "Carlos Ruiz"}


def test_filtra_por_nombre_de_finca(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, finca="esperanza") == ["Ana Pérez"]
    assert nombres(api, lider, finca="ROBLE") == ["Álvaro Díaz"]
    assert nombres(api, lider, finca="nada") == []


def test_filtra_por_fecha_de_registro(api: TestClient, lider: Cuenta, zona: dict[str, Any], migrated: Engine) -> None:
    with migrated.begin() as c:
        c.execute(
            text("UPDATE productores SET fecha_registro_productor = '2026-08-10' WHERE nombre_productor LIKE 'Ana%'")
        )
        c.execute(
            text("UPDATE productores SET fecha_registro_productor = '2026-09-10' WHERE nombre_productor LIKE 'Bea%'")
        )
        c.execute(
            text("UPDATE productores SET fecha_registro_productor = '2026-10-10' WHERE nombre_productor LIKE 'Carlos%'")
        )
        c.execute(
            text("UPDATE productores SET fecha_registro_productor = '2026-10-20' WHERE nombre_productor LIKE 'Álvaro%'")
        )
    assert nombres(api, lider, desde="2026-09-10") == ["Álvaro Díaz", "Bea Núñez", "Carlos Ruiz"]  # inclusivo
    assert nombres(api, lider, hasta="2026-09-10") == ["Ana Pérez", "Bea Núñez"]
    assert nombres(api, lider, desde="2026-09-01", hasta="2026-10-10") == ["Bea Núñez", "Carlos Ruiz"]


def test_filtra_por_ultima_consulta(api: TestClient, lider: Cuenta, zona: dict[str, Any], migrated: Engine) -> None:
    mensaje(migrated, zona["ana"]["id"], "573001112001", timedelta(days=5))
    mensaje(migrated, zona["alvaro"]["id"], "573001112002", timedelta(days=60))
    mensaje(migrated, zona["bea"]["id"], "573001112003", timedelta(days=120))
    assert nombres(api, lider, consulta="30") == ["Ana Pérez"]
    # «sin consultas en 90 días» incluye a quien nunca escribió (Carlos)
    assert nombres(api, lider, consulta="90") == ["Bea Núñez", "Carlos Ruiz"]


def test_la_ultima_consulta_es_el_mensaje_mas_reciente(
    api: TestClient, lider: Cuenta, zona: dict[str, Any], migrated: Engine
) -> None:
    mensaje(migrated, zona["ana"]["id"], "573001112001", timedelta(days=20), 1)
    mensaje(migrated, zona["ana"]["id"], "573001112001", timedelta(days=2), 2)
    ana = next(p for p in listar(api, lider)["items"] if p["nombre"] == "Ana Pérez")
    esperado = datetime.now(UTC) - timedelta(days=2)
    assert abs(datetime.fromisoformat(ana["ultima_consulta_en"]) - esperado) < timedelta(minutes=5)
    bea = next(p for p in listar(api, lider)["items"] if p["nombre"] == "Bea Núñez")
    assert bea["ultima_consulta_en"] is None


def test_un_mensaje_anterior_al_registro_se_asocia_por_el_telefono(
    api: TestClient, lider: Cuenta, zona: dict[str, Any], migrated: Engine
) -> None:
    with Session(migrated) as sesion:
        sesion.add(
            MensajeWhatsapp(
                wamid="wamid.suelto",
                telefono="573001112003",
                tipo="text",
                contenido={},
                estado_procesamiento="PROCESADO",
                fecha_recepcion=datetime.now(UTC) - timedelta(days=1),
                id_productor=None,
            )
        )
        sesion.commit()
    assert nombres(api, lider, consulta="30") == ["Bea Núñez"]


def test_combina_varios_filtros(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    assert nombres(api, lider, vereda="CENTRO", especie="GULUPA", con_casos="con", estado="activo") == ["Ana Pérez"]
    assert nombres(api, lider, vereda="CENTRO", especie="MARACUYA") == []
    assert nombres(api, lider, estado="deshabilitado", con_casos="con") == []
    assert nombres(api, lider, q="ana", finca="esperanza", especie="GULUPA") == ["Ana Pérez"]


def test_pagina_y_cuenta_el_total(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    for n in range(1, 8):
        crear_productor(api, lider, n, nombre=f"Productor {n}")
    paginas = [listar(api, lider, page=p, page_size=3) for p in (1, 2, 3)]
    assert [pag["total"] for pag in paginas] == [7, 7, 7]
    assert [len(pag["items"]) for pag in paginas] == [3, 3, 1]
    ids = [p["id"] for pag in paginas for p in pag["items"]]
    assert len(ids) == len(set(ids)) == 7


def test_pagina_fuera_de_rango_informa_el_total(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    crear_productor(api, lider)
    assert listar(api, lider, page=9, page_size=5) == {"items": [], "total": 1, "page": 9, "page_size": 5}


def test_admite_1000_filas_para_el_selector_de_los_reportes(api: TestClient, lider: Cuenta, catalogos: None) -> None:
    crear_productor(api, lider)
    assert listar(api, lider, page_size=1000)["page_size"] == 1000


@pytest.mark.parametrize(
    "parametros",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 1001},
        {"estado": "borrado"},
        {"con_casos": "algunos"},
        {"consulta": "15"},
        {"especie": "CURUBA"},
        {"desde": "ayer"},
        {"hasta": "2026-13-45"},
    ],
)
def test_filtros_invalidos_422(api: TestClient, lider: Cuenta, parametros: dict[str, object]) -> None:
    r = api.get(URL, params=parametros, headers=lider.headers)
    assert r.status_code == 422 and r.json()["codigo"] == "VALIDACION"


def test_el_listado_trae_los_campos_del_contrato(api: TestClient, lider: Cuenta, zona: dict[str, Any]) -> None:
    ana = next(p for p in listar(api, lider)["items"] if p["nombre"] == "Ana Pérez")
    assert set(ana) == {
        "id", "nombre", "celular", "activo", "vereda", "fincas", "registrado_en", "ultima_consulta_en",
        "casos_ultimo_anio", "casos_activos",
    }  # fmt: skip
    assert ana["celular"] == "3001112001" and ana["vereda"] == "CENTRO" and ana["fincas"][0]["especies"] == ["GULUPA"]
