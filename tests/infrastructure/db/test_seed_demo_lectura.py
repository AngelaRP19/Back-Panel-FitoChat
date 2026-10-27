"""Datos demo ya sembrados: pruebas de solo lectura que comparten UNA siembra (se revierte al terminar el archivo).

Van en su propio archivo a propósito: la transacción compartida usa las secuencias y bloquearía a cualquier otra
prueba que las reinicie mientras siga abierta.
"""

import io
import re
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws
from PIL import Image
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core import catalogos_api as api
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db import seed_demo as demo
from app.infrastructure.db.seed_demo import MARCA
from app.infrastructure.s3_storage_client import S3StorageClient
from tests.db_fixtures import reiniciar_secuencias_cortas
from tests.infrastructure.db.demo_helpers import AHORA, BUCKET, TODOS, escalar, filas, versiones


@pytest.fixture(scope="module")
def compartido(migrated: Engine) -> Iterator[tuple[Session, S3StorageClient, Any, dict[str, Any]]]:
    """Una siembra por archivo para las pruebas de solo lectura: se revierte al terminar."""
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        crudo.create_bucket(Bucket=BUCKET)
        crudo.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        s3 = S3StorageClient(crudo, BUCKET)
        with migrated.connect() as conexion:
            transaccion = conexion.begin()
            reiniciar_secuencias_cortas(conexion)
            with Session(bind=conexion) as session:
                resultado = demo.sembrar(session, s3, AHORA)
                session.execute(sp.ESTABLECER_AHORA, {"ahora": AHORA.isoformat()})
                yield session, s3, crudo, resultado
            transaccion.rollback()


@pytest.fixture
def demo_db(compartido: Any) -> Session:
    return compartido[0]  # type: ignore[no-any-return]


# --- qué se siembra ----------------------------------------------------------------------------------------------


def test_el_resultado_resume_la_siembra(compartido: Any) -> None:
    _, _, _, resultado = compartido
    assert resultado["ya_estaba"] is False and resultado["casos"] == 22
    assert resultado["fotos"] == sum(len(c.fotos) for c in TODOS)
    assert re.fullmatch(r"ALR-\d{6}", resultado["alerta_generada"]) and resultado["alerta_comunicada"]


def test_hay_casos_en_cada_estado_y_los_conteos_coinciden_con_el_listado(demo_db: Session) -> None:
    """Aceptación del módulo: la bandeja tiene casos en cada estado y los conteos de las pestañas cuadran."""
    esperado = Counter(api.ESTADOS_CASO.a_nombre(c.estado) for c in TODOS)
    conteo = {
        f["nombre_estado"]: f["n"]
        for f in filas(demo_db, "SELECT nombre_estado, count(*) AS n FROM vw_casos_detalle GROUP BY 1")
    }
    assert conteo == dict(esperado) and set(conteo) == set(api.ESTADOS_CASO.nombres)
    for nombre in api.ESTADOS_CASO.nombres:
        listado = escalar(demo_db, "SELECT count(*) FROM vw_casos_detalle WHERE nombre_estado = :n", n=nombre)
        assert listado == conteo[nombre]
    assert sum(conteo.values()) == escalar(demo_db, "SELECT count(*) FROM casos") == 22


def test_la_bandeja_de_pendientes_tiene_los_tres_resultados_del_analisis(demo_db: Session) -> None:
    resultados = {
        f["resultado_analisis"] for f in filas(demo_db, "SELECT resultado_analisis FROM vw_casos_pendientes_revision")
    }
    assert resultados == set(api.RESULTADOS_ANALISIS.codigos)


def test_los_porcentajes_sembrados_dan_el_resultado_que_dice_la_guia(demo_db: Session) -> None:
    ambiguo = filas(
        demo_db,
        "SELECT c.porcentaje_coincidencia FROM vw_casos_pendientes_revision v "
        "JOIN coincidencias_categoria c ON c.id_identificacion = v.id_identificacion_preliminar "
        "WHERE v.resultado_analisis = 'AMBIGUO' ORDER BY 1 DESC",
    )
    primero, segundo = (float(f["porcentaje_coincidencia"]) for f in ambiguo[:2])
    assert primero >= 50 and segundo >= 50 and primero - segundo < 15


# --- cuentas y marcas ---------------------------------------------------------------------------------------------


def test_cuentas_del_lider_y_tecnicos(demo_db: Session) -> None:
    lideres = filas(demo_db, "SELECT * FROM lideres_tecnicos")
    tecnicos = filas(demo_db, "SELECT * FROM tecnicos ORDER BY correo_tecnico")
    assert len(lideres) == 1 and len(tecnicos) == 3
    assert lideres[0]["nombre_lider_tecnico"] == f"Luis Pérez {MARCA}"
    assert {t["nombre_tecnico"] for t in tecnicos} == {
        f"Luis Pérez (técnico) {MARCA}",
        f"Ana Torres {MARCA}",
        f"Beto Quintero {MARCA}",
    }
    correos = {lideres[0]["correo_lider_tecnico"], *(t["correo_tecnico"] for t in tecnicos)}
    assert len(correos) == 4 and all(c.endswith("@demo.anuc.test") for c in correos)  # el líder tiene un 2.º correo


def test_las_cuentas_demo_no_guardan_contrasenas_utilizables(demo_db: Session) -> None:
    from app.core.passwords import verify_password

    hashes = [f["contrasena_tecnico"] for f in filas(demo_db, "SELECT contrasena_tecnico FROM tecnicos")]
    assert all(h.startswith("$argon2id$") for h in hashes)
    assert not any(verify_password(p, h) for h in hashes for p in ("", "demo", "password", "Demo1234"))


def test_las_cuentas_quedan_enlazadas_a_un_sub_de_prueba(demo_db: Session) -> None:
    """En local y test el sub permite emitir tokens de prueba; en prod quedaría vacío para que lo enlace Cognito."""
    subs = [f["cognito_sub"] for f in filas(demo_db, "SELECT cognito_sub FROM tecnicos")]
    assert all(s and s.startswith("demo-TEC-") for s in subs)
    assert escalar(demo_db, "SELECT cognito_sub FROM lideres_tecnicos").startswith("demo-LID-")


def test_todo_lo_sembrado_lleva_la_marca_o_el_rango_demo(demo_db: Session) -> None:
    for tabla, columna in (
        ("productores", "nombre_productor"),
        ("fincas", "nombre_finca"),
        ("cultivos", "nombre_cultivo"),
    ):
        assert escalar(demo_db, f"SELECT count(*) FROM {tabla} WHERE {columna} NOT LIKE '%{MARCA}'") == 0
    assert escalar(demo_db, "SELECT count(*) FROM productores WHERE telefono_productor NOT LIKE '573000000%'") == 0
    assert escalar(demo_db, "SELECT count(*) FROM tecnicos WHERE celular_tecnico NOT LIKE '573000000%'") == 0
    assert escalar(demo_db, "SELECT count(*) FROM preguntas WHERE texto_pregunta NOT LIKE '%(demo)'") == 0


def test_los_productores_dieron_su_consentimiento(demo_db: Session) -> None:
    assert escalar(demo_db, "SELECT count(*) FROM productores WHERE fecha_consentimiento IS NULL") == 0


def test_las_tres_veredas_y_la_ubicacion_calculada(demo_db: Session) -> None:
    veredas = {f["vereda_sector_finca"] for f in filas(demo_db, "SELECT vereda_sector_finca FROM fincas")}
    assert veredas == {"Juracambita", "Centro", "San Luis"}
    assert escalar(demo_db, "SELECT count(*) FROM fincas WHERE ubicacion IS NULL") == 0


# --- cada caso ---------------------------------------------------------------------------------------------------


def test_cada_caso_tiene_su_fila_de_diagnostico(demo_db: Session) -> None:
    assert (
        escalar(
            demo_db,
            "SELECT count(*) FROM casos c WHERE NOT EXISTS (SELECT 1 FROM diagnosticos d WHERE d.id_caso = c.id_caso)",
        )
        == 0
    )
    assert escalar(demo_db, "SELECT count(*) FROM diagnosticos") == 22
    vacios = escalar(demo_db, "SELECT count(*) FROM diagnosticos WHERE resultado_tecnico IS NULL")
    assert vacios == sum(1 for c in TODOS if c.estado not in {api.EstadoCaso.REGISTRADO, api.EstadoCaso.CERRADO})


def test_las_asignaciones_siguen_el_estado(demo_db: Session) -> None:
    por_estado = {
        f["nombre_estado"]: f["n"]
        for f in filas(
            demo_db,
            "SELECT nombre_estado, count(id_tecnico) AS n FROM vw_casos_detalle GROUP BY 1",
        )
    }
    con_tecnico = {
        api.ESTADOS_CASO.a_nombre(e)
        for e in (api.EstadoCaso.EN_REVISION, api.EstadoCaso.REGISTRADO, api.EstadoCaso.CERRADO)
    }
    for nombre, n in por_estado.items():
        assert (n > 0) == (nombre in con_tecnico), nombre
    assert escalar(demo_db, "SELECT count(*) FROM asignaciones WHERE fecha_fin IS NOT NULL") == 0
    assert (
        escalar(demo_db, "SELECT count(*) FROM (SELECT id_caso FROM asignaciones GROUP BY 1 HAVING count(*) > 1) x")
        == 0
    )


def test_un_caso_esta_con_la_cuenta_de_tecnico_del_lider(demo_db: Session) -> None:
    assert (
        escalar(demo_db, "SELECT count(*) FROM vw_casos_detalle WHERE nombre_tecnico LIKE 'Luis Pérez (técnico)%'") == 1
    )


def test_los_casos_registrados_y_cerrados_tienen_su_resultado(demo_db: Session) -> None:
    resultados = Counter(
        f["resultado_tecnico"] for f in filas(demo_db, "SELECT resultado_tecnico FROM vw_diagnosticos_detalle")
    )
    assert set(resultados) == set(api.RESULTADOS_TECNICOS.codigos)
    assert sum(resultados.values()) == sum(1 for c in TODOS if c.resultado)


def test_la_lista_de_vencimientos(demo_db: Session) -> None:
    hoy = AHORA.date()
    esperados = sorted(
        c.vence_en_dias for c in TODOS if c.estado is api.EstadoCaso.CERRADO and c.vence_en_dias is not None
    )
    reales = sorted(
        (f["vence_en"] - hoy).days for f in filas(demo_db, "SELECT vence_en FROM casos WHERE vence_en IS NOT NULL")
    )
    assert len(reales) == len(esperados)
    for real, previsto in zip(reales, esperados, strict=True):
        assert abs(real - previsto) <= 2  # cierre + 6 meses con meses de distinto largo
    assert reales[0] <= 30  # hay uno próximo a vencer


def test_la_linea_de_tiempo_de_un_caso_cerrado(demo_db: Session) -> None:
    id_caso = escalar(
        demo_db,
        "SELECT id_caso FROM vw_casos_detalle WHERE nombre_tecnico LIKE 'Ana Torres%' "
        "AND nombre_estado = 'Cerrado' ORDER BY id_caso LIMIT 1",
    )
    eventos = filas(demo_db, "SELECT * FROM eventos WHERE id_caso = :c ORDER BY fecha_evento, id_evento", c=id_caso)
    assert [e["tipo_evento"] for e in eventos] == [
        "CASO_CREADO",
        "ANALISIS_CHATBOT",
        "ENVIADO_A_REVISION",
        "TECNICO_ASIGNADO",
        "DIAGNOSTICO_REGISTRADO",
        "CASO_CERRADO",
    ]
    actores = [e["actor_tipo"] for e in eventos]
    assert actores == ["PRODUCTOR", "SISTEMA", "SISTEMA", "LIDER", "TECNICO", "TECNICO"]
    fechas = [e["fecha_evento"] for e in eventos]
    assert fechas == sorted(fechas) and fechas[-1] <= AHORA + timedelta(days=200)


def test_el_caso_descartado_y_el_del_chatbot_tienen_su_historia(demo_db: Session) -> None:
    descartado = escalar(demo_db, "SELECT id_caso FROM vw_casos_detalle WHERE nombre_estado = 'Descartado'")
    tipos = [
        e["tipo_evento"]
        for e in filas(
            demo_db, "SELECT tipo_evento FROM eventos WHERE id_caso = :c ORDER BY fecha_evento", c=descartado
        )
    ]
    assert tipos == ["CASO_CREADO", "ANALISIS_CHATBOT", "CASO_DESCARTADO"]


def test_los_casos_del_chatbot_para_los_jobs_de_inactividad(demo_db: Session) -> None:
    # La vista mide contra now() real y la siembra parte de AHORA fijo: se descuenta lo transcurrido desde AHORA.
    transcurridas = (datetime.now(UTC) - AHORA).total_seconds() / 3600
    horas = sorted(
        round(float(f["horas_inactivo"]) - transcurridas)
        for f in filas(demo_db, "SELECT horas_inactivo FROM vw_ultima_actividad_caso")
    )
    assert len(horas) == 4
    assert 1 <= horas[0] <= 4 and 4 <= horas[1] <= 6 and 48 <= horas[2] <= 52 and 58 <= horas[3] <= 62
    assert (
        escalar(demo_db, "SELECT count(*) FROM vw_ultima_actividad_caso WHERE advertencia_inactividad_enviada = 'S'")
        == 1
    )


# --- alertas -----------------------------------------------------------------------------------------------------


def test_una_alerta_generada_de_trips_con_cuatro_casos_confirmados(demo_db: Session, compartido: Any) -> None:
    id_alerta = compartido[3]["alerta_generada"]
    alerta = filas(demo_db, "SELECT * FROM alertas WHERE id_alerta = :a", a=id_alerta)[0]
    assert alerta["estado_alerta"] == "GENERADA"
    assert (
        escalar(
            demo_db,
            "SELECT nombre_categoria FROM categorias_fitosanitarias WHERE id_categoria = :c",
            c=alerta["id_categoria"],
        )
        == "Trips"
    )
    casos = filas(
        demo_db,
        "SELECT v.* FROM alerta_casos ac JOIN vw_diagnosticos_detalle v USING (id_caso) WHERE ac.id_alerta = :a",
        a=id_alerta,
    )
    assert len(casos) == 4 and {c["resultado_tecnico"] for c in casos} == {"CONFIRMADO"}
    assert (alerta["numero_minimo_casos"], float(alerta["distancia_km"]), alerta["intervalo_dias"]) == (3, 3.5, 30)
    assert escalar(demo_db, "SELECT count(*) FROM alerta_destinatarios WHERE id_alerta = :a", a=id_alerta) == 0


def test_una_alerta_comunicada_lista_para_probar_la_correccion(demo_db: Session, compartido: Any) -> None:
    id_alerta = compartido[3]["alerta_comunicada"]
    alerta = filas(demo_db, "SELECT * FROM alertas WHERE id_alerta = :a", a=id_alerta)[0]
    assert alerta["estado_alerta"] == "COMUNICADA" and alerta["id_lider_gestion"]
    assert alerta["mensaje_enviado"] and alerta["fecha_comunicacion_alerta"] and alerta["fecha_confirmacion"]
    assert escalar(demo_db, "SELECT count(*) FROM alerta_casos WHERE id_alerta = :a", a=id_alerta) == 3
    assert alerta["mensaje_correccion"] is None and alerta["id_lider_correccion"] is None  # aún se puede corregir
    destinatarios = filas(demo_db, "SELECT * FROM alerta_destinatarios WHERE id_alerta = :a", a=id_alerta)
    assert len(destinatarios) >= 3
    assert {d["tipo_envio"] for d in destinatarios} == {"ALERTA"} and {d["estado_envio"] for d in destinatarios} == {
        "SIMULADO"
    }


def test_solo_hay_dos_alertas_y_ninguna_se_envio_de_verdad(demo_db: Session) -> None:
    assert escalar(demo_db, "SELECT count(*) FROM alertas") == 2
    assert escalar(demo_db, "SELECT count(*) FROM alerta_destinatarios WHERE estado_envio = 'ENVIADO'") == 0


def test_los_destinatarios_estan_dentro_del_radio(demo_db: Session, compartido: Any) -> None:
    id_alerta = compartido[3]["alerta_comunicada"]
    fuera = escalar(
        demo_db,
        "SELECT count(*) FROM alerta_destinatarios d JOIN fincas f ON f.id_productor = d.id_productor "
        "WHERE d.id_alerta = :a AND NOT EXISTS (SELECT 1 FROM alerta_casos ac JOIN casos c USING (id_caso) "
        "JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo JOIN fincas f2 ON f2.id_finca = cu.id_finca "
        "WHERE ac.id_alerta = d.id_alerta AND fn_distancia_km(f.ubicacion, f2.ubicacion) <= 3.5)",
        a=id_alerta,
    )
    assert fuera == 0


def test_la_evaluacion_de_alertas_no_crea_nada_nuevo(demo_db: Session) -> None:
    antes = escalar(demo_db, "SELECT count(*) FROM alertas")
    demo_db.execute(sp.EVALUAR_ALERTA, {"id_categoria": None, "fecha": AHORA})
    assert escalar(demo_db, "SELECT count(*) FROM alertas") == antes


# --- fotos -------------------------------------------------------------------------------------------------------


def test_cada_foto_de_la_base_esta_en_s3_con_su_clave(compartido: Any) -> None:
    session, s3, crudo, _ = compartido
    claves = [f["ruta_fotografia"] for f in filas(session, "SELECT ruta_fotografia FROM fotografias")]
    assert len(claves) == sum(len(c.fotos) for c in TODOS) == len(set(claves))
    assert all(re.fullmatch(r"evidencias/\d{4}/\d{2}/FOT-\d{8}\.webp", c) for c in claves)
    assert all(s3.existe(c) for c in claves)
    assert {v["Key"] for v in versiones(crudo)} == set(claves)  # y no hay nada más en el bucket


def test_las_fotos_son_webp_y_se_firman(compartido: Any) -> None:
    session, s3, crudo, _ = compartido
    fila = filas(session, "SELECT ruta_fotografia, tipo_evidencia FROM fotografias ORDER BY id_fotografia LIMIT 1")[0]
    cuerpo = crudo.get_object(Bucket=BUCKET, Key=fila["ruta_fotografia"])["Body"].read()
    assert Image.open(io.BytesIO(cuerpo)).format == "WEBP"
    assert fila["ruta_fotografia"] in s3.url_prefirmada(fila["ruta_fotografia"])


def test_las_fotos_cuelgan_de_las_respuestas_del_diagnostico_del_caso(demo_db: Session) -> None:
    huerfanas = escalar(
        demo_db,
        "SELECT count(*) FROM fotografias f WHERE NOT EXISTS (SELECT 1 FROM respuestas r "
        "JOIN diagnosticos d USING (id_diagnostico) WHERE r.id_respuesta = f.id_respuesta)",
    )
    assert huerfanas == 0


def test_los_tipos_de_evidencia_y_una_foto_no_util(demo_db: Session) -> None:
    tipos = {f["tipo_evidencia"] for f in filas(demo_db, "SELECT DISTINCT tipo_evidencia FROM fotografias")}
    assert tipos == set(api.TIPOS_EVIDENCIA.codigos)
    assert escalar(demo_db, "SELECT count(*) FROM fotografias WHERE es_util = 'N'") == 1


def test_ningun_caso_pasa_de_los_limites_de_fotos(demo_db: Session) -> None:
    por_caso = filas(
        demo_db,
        "SELECT d.id_caso, count(*) FILTER (WHERE f.es_util = 'S') AS utiles FROM fotografias f "
        "JOIN respuestas r USING (id_respuesta) JOIN diagnosticos d USING (id_diagnostico) GROUP BY 1",
    )
    assert max(f["utiles"] for f in por_caso) <= 12
    por_tipo = filas(
        demo_db,
        "SELECT d.id_caso, f.tipo_evidencia, count(*) AS n FROM fotografias f JOIN respuestas r USING (id_respuesta) "
        "JOIN diagnosticos d USING (id_diagnostico) GROUP BY 1, 2",
    )
    assert max(f["n"] for f in por_tipo) <= 3
