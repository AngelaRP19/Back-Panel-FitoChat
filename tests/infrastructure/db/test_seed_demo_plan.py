"""El plan de los datos demo, sin base de datos: que sea coherente y cumpla lo que pide la guía."""

import io
import math
import re
from datetime import UTC, datetime

import pytest
from PIL import Image

from app.core import catalogos_api as api
from app.core.config import Settings
from app.infrastructure.db import seed_demo as demo
from app.infrastructure.db.seed_demo import (
    CASOS,
    CASOS_TRIPS_CENTRO,
    CASOS_TRIPS_SAN_LUIS,
    FINCAS,
    MARCA,
    PREFIJO_TELEFONO,
    CasoDemo,
)

TODOS = [*CASOS, *CASOS_TRIPS_CENTRO, *CASOS_TRIPS_SAN_LUIS]
CE = api.EstadoCaso


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine: suficiente para comprobar radios de unos pocos km."""
    (la1, lo1), (la2, lo2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


# --- marcas de demo ----------------------------------------------------------------------------------------------


def test_todo_dato_demo_es_reconocible() -> None:
    assert MARCA == "(demo)"
    assert PREFIJO_TELEFONO == "573000000"
    assert demo.telefono(7) == "573000000007"
    assert all(p.endswith(MARCA) for p in demo.PREGUNTAS)


def test_los_telefonos_demo_son_validos_y_unicos() -> None:
    cuentas = [demo.LIDER, demo.TECNICO_DEL_LIDER, *demo.TECNICOS.values()]
    numeros = [f.n for f in FINCAS] + [c[2] for c in cuentas]
    assert len(numeros) == len(set(numeros))
    for numero in numeros:
        assert re.fullmatch(r"573000000\d{3}", demo.telefono(numero))


def test_los_correos_demo_son_unicos() -> None:
    usuarios = [demo.LIDER[1], demo.TECNICO_DEL_LIDER[1], *(t[1] for t in demo.TECNICOS.values())]
    assert len(usuarios) == len(set(usuarios))
    assert demo.DOMINIO_CORREO == "demo.anuc.test"


def test_el_lider_tiene_otra_cuenta_de_tecnico_con_otro_correo() -> None:
    """D-02: el líder no diagnostica con su rol; atiende casos con una cuenta de técnico aparte."""
    assert demo.LIDER[1] != demo.TECNICO_DEL_LIDER[1]
    assert demo.LIDER[2] != demo.TECNICO_DEL_LIDER[2]


# --- territorio --------------------------------------------------------------------------------------------------


def test_las_tres_veredas_de_la_guia() -> None:
    assert {f.vereda for f in FINCAS} == {"Juracambita", "Centro", "San Luis"}


def test_las_coordenadas_son_de_zetaquira() -> None:
    for f in FINCAS:
        assert 5.20 <= float(f.lat) <= 5.30 and float(f.lon) < 0, f


def test_hay_invernaderos_y_campo_abierto() -> None:
    assert {f.tipo_cultivo for f in FINCAS} == set(api.TIPOS_CULTIVO.codigos)


def test_las_tres_especies_y_un_cultivo_con_dos() -> None:
    usadas = {e for f in FINCAS for e in f.especies}
    assert usadas == set(api.EspecieCultivo)
    assert any(len(f.especies) > 1 for f in FINCAS)


def test_los_numeros_de_finca_son_consecutivos() -> None:
    assert [f.n for f in FINCAS] == list(range(1, 10))


# --- casos -------------------------------------------------------------------------------------------------------


def test_hay_casos_en_los_siete_estados() -> None:
    assert {c.estado for c in TODOS} == set(CE)


def test_cada_caso_apunta_a_una_finca_que_existe() -> None:
    assert {c.finca for c in TODOS} <= {f.n for f in FINCAS}


def test_los_resultados_del_analisis_cubren_los_tres_tipos() -> None:
    assert {c.analisis[0] for c in TODOS if c.analisis} == set(api.RESULTADOS_ANALISIS.codigos)


def test_los_porcentajes_respetan_las_reglas_de_la_guia() -> None:
    """Identificado: una categoría ≥ 50. Ambiguo: dos ≥ 50 a menos de 15 puntos. No identificado: ninguna."""
    from decimal import Decimal

    for caso in (c for c in TODOS if c.analisis):
        resultado, porcentajes = caso.analisis  # type: ignore[misc]
        valores = sorted((Decimal(p) for p in porcentajes.values()), reverse=True)
        assert all(0 <= v <= 100 for v in valores)
        if resultado == "PRELIMINAR":
            assert valores[0] >= 50
        elif resultado == "AMBIGUO":
            assert valores[0] >= 50 and valores[1] >= 50 and valores[0] - valores[1] < 15
        else:
            assert valores[0] < 50


def test_un_caso_en_el_panel_tiene_analisis() -> None:
    en_panel = {CE.PENDIENTE_REVISION, CE.EN_REVISION, CE.REGISTRADO, CE.CERRADO}
    assert all(c.analisis for c in TODOS if c.estado in en_panel)
    assert not any(c.analisis for c in TODOS if c.estado in {CE.CREADO, CE.EN_ANALISIS, CE.DESCARTADO})  # D-03


@pytest.mark.parametrize("estado", [CE.EN_REVISION, CE.REGISTRADO, CE.CERRADO])
def test_un_caso_asignado_tiene_tecnico(estado: CE) -> None:
    casos = [c for c in TODOS if c.estado is estado]
    assert casos and all(c.tecnico in {"ana", "beto", "lider"} for c in casos)


def test_los_casos_sin_asignar_no_tienen_tecnico() -> None:
    sin_asignar = {CE.CREADO, CE.EN_ANALISIS, CE.PENDIENTE_REVISION, CE.DESCARTADO}
    assert not any(c.tecnico for c in TODOS if c.estado in sin_asignar)


def test_un_caso_lo_atiende_la_cuenta_de_tecnico_del_lider() -> None:
    assert any(c.tecnico == "lider" for c in CASOS)


def test_los_resultados_tecnicos_son_coherentes() -> None:
    for caso in (c for c in TODOS if c.estado in {CE.REGISTRADO, CE.CERRADO}):
        assert caso.resultado is not None
        if caso.resultado in {api.ResultadoTecnico.CONFIRMADO, api.ResultadoTecnico.OTRA_PATOLOGIA}:
            assert caso.categoria is not None
        else:
            assert caso.categoria is None
    assert not any(c.resultado for c in TODOS if c.estado not in {CE.REGISTRADO, CE.CERRADO})


def test_se_ven_los_cuatro_resultados_tecnicos() -> None:
    assert {c.resultado for c in TODOS if c.resultado} == set(api.ResultadoTecnico)


def test_hay_cerrados_proximos_a_vencer_y_lejanos() -> None:
    vencimientos = sorted(c.vence_en_dias for c in CASOS if c.estado is CE.CERRADO and c.vence_en_dias is not None)
    assert vencimientos[0] <= 30 < vencimientos[-1]


def test_casos_del_chatbot_cerca_de_las_reglas_de_48_y_72_horas() -> None:
    horas = {c.hace.total_seconds() / 3600 for c in CASOS if c.estado is CE.EN_ANALISIS}
    assert any(48 <= h < 72 for h in horas)  # ya merece la advertencia
    assert any(h < 48 for h in horas)  # todavía no
    assert any(c.advertido for c in CASOS)


def test_un_caso_tiene_una_foto_marcada_como_no_util() -> None:
    assert any(len(c.fotos) == 3 for c in TODOS)  # la tercera se marca es_util = 'N'


def test_las_fotos_respetan_los_limites() -> None:
    for caso in TODOS:
        assert len(caso.fotos) <= 12
        assert all(caso.fotos.count(tipo) <= 3 for tipo in set(caso.fotos))


# --- las alertas -------------------------------------------------------------------------------------------------


def test_cuatro_casos_de_trips_en_el_centro_y_tres_en_san_luis() -> None:
    assert len(CASOS_TRIPS_CENTRO) == 4 and len(CASOS_TRIPS_SAN_LUIS) == 3
    for plan in (*CASOS_TRIPS_CENTRO, *CASOS_TRIPS_SAN_LUIS):
        assert plan.resultado is api.ResultadoTecnico.CONFIRMADO and plan.categoria is api.CategoriaFito.TRIPS


def test_los_grupos_estan_dentro_del_radio_de_trips() -> None:
    por_n = {f.n: (float(f.lat), float(f.lon)) for f in FINCAS}
    for grupo in (CASOS_TRIPS_CENTRO, CASOS_TRIPS_SAN_LUIS):
        puntos = [por_n[c.finca] for c in grupo]
        assert max(km(a, b) for a in puntos for b in puntos) < 3.5


def test_los_dos_grupos_estan_lejos_entre_si() -> None:
    por_n = {f.n: (float(f.lat), float(f.lon)) for f in FINCAS}
    centro = [por_n[c.finca] for c in CASOS_TRIPS_CENTRO]
    san_luis = [por_n[c.finca] for c in CASOS_TRIPS_SAN_LUIS]
    assert min(km(a, b) for a in centro for b in san_luis) > 3.5


def test_la_alerta_comunicada_es_de_hace_casi_un_mes_y_dentro_del_intervalo() -> None:
    dias = [c.hace.days for c in CASOS_TRIPS_SAN_LUIS]
    assert all(23 <= d <= 30 for d in dias)


# --- fotos sintéticas --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", api.TIPOS_EVIDENCIA.codigos)
def test_la_foto_demo_es_un_webp_valido_y_pequeno(tipo: str) -> None:
    contenido = demo.foto_demo(tipo)
    imagen = Image.open(io.BytesIO(contenido))
    assert imagen.format == "WEBP" and imagen.size == (640, 480)
    assert len(contenido) < 60_000


def test_cada_tipo_de_foto_se_ve_distinto() -> None:
    assert len({demo.foto_demo(t) for t in api.TIPOS_EVIDENCIA.codigos}) == 4


def test_la_clave_de_s3_sigue_el_formato_del_pipeline() -> None:
    cuando = datetime(2026, 10, 5, tzinfo=UTC)
    assert demo.clave_s3("FOT-00000007", cuando) == "evidencias/2026/10/FOT-00000007.webp"
    assert re.fullmatch(r"evidencias/\d{4}/\d{2}/FOT-\d{8}\.webp", demo.clave_s3("FOT-00000007", cuando))


# --- el comando --------------------------------------------------------------------------------------------------


def _prod() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="prod",
        cognito_user_pool_id="p",
        cognito_app_client_id="c",
        s3_bucket_evidencias="b",
    )


def test_en_prod_exige_permitir_demo(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(demo, "get_settings", _prod)
    monkeypatch.setattr(demo, "configure_logging", lambda *_: None)
    assert demo.main([]) == 2
    assert "--permitir-demo" in capsys.readouterr().err
    assert demo.main(["--limpiar"]) == 2


def test_sin_bucket_no_siembra(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(demo, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(demo, "configure_logging", lambda *_: None)
    assert demo.main([]) == 2
    assert "S3_BUCKET_EVIDENCIAS" in capsys.readouterr().err


def test_los_planes_son_instancias_de_caso_demo() -> None:
    assert all(isinstance(c, CasoDemo) for c in TODOS)
    assert len(TODOS) == 22 == len(CASOS) + 4 + 3
