"""Jobs programados: advertencia a las 48 h, descarte a las 72 h, retención a los 6 meses y barrido de alertas."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.infrastructure.db import procedimientos as sp
from app.infrastructure.s3_storage_client import ErrorBorradoS3
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.modules.scheduler import jobs
from app.modules.scheduler.jobs import (
    Contexto,
    advertencia_inactividad,
    descarte_inactividad,
    ejecutar,
    evaluar_alertas,
    retencion,
)
from tests.api.territorio import crear_alerta, crear_caso, crear_identificacion, crear_respuesta, registrar_diagnostico
from tests.infrastructure.db.factories import crear_tecnico
from tests.modules.scheduler.conftest import AHORA, AlmacenS3, Entorno, inactivo

MIN = timedelta(minutes=1)
HORA = timedelta(hours=1)


def filas(motor: Engine, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    with motor.connect() as c:
        return [dict(f) for f in c.execute(text(sql), parametros).mappings()]


def existe(motor: Engine, id_caso: str) -> bool:
    return bool(filas(motor, "SELECT 1 FROM casos WHERE id_caso = :c", c=id_caso))


def caso_inactivo(entorno: Entorno, desde: timedelta, estado: str = "En análisis") -> str:
    id_caso = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", estado)
    inactivo(entorno.motor, id_caso, desde)
    return id_caso


def advertido(motor: Engine, id_caso: str) -> bool:
    return (
        filas(motor, "SELECT advertencia_inactividad_enviada AS a FROM casos WHERE id_caso = :c", c=id_caso)[0]["a"]
        == "S"
    )


# --- advertencia de inactividad (48 h) -----------------------------------------------------------------------------


def test_avisa_a_las_48_horas_y_no_antes(entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado) -> None:
    antes = caso_inactivo(entorno, 48 * HORA - MIN)
    justo = caso_inactivo(entorno, 48 * HORA + MIN)
    r = advertencia_inactividad(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.errores) == (1, 1, 0) and r.exito
    assert not advertido(entorno.motor, antes) and advertido(entorno.motor, justo)
    [(telefono, texto)] = whatsapp.enviados
    assert telefono == entorno.celular_e164 and "24 horas" in texto and "borrarán" in texto


def test_la_advertencia_no_se_repite(entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado) -> None:
    caso_inactivo(entorno, 50 * HORA)
    advertencia_inactividad(entorno.motor, ctx)
    segunda = advertencia_inactividad(entorno.motor, ctx)
    assert segunda.procesados == 0 and len(whatsapp.enviados) == 1


@pytest.mark.parametrize("estado", ["Caso creado", "En análisis"])
def test_avisa_en_los_dos_estados_del_chatbot(
    entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado, estado: str
) -> None:
    caso_inactivo(entorno, 50 * HORA, estado)
    assert advertencia_inactividad(entorno.motor, ctx).exitosos == 1


@pytest.mark.parametrize("estado", ["Pendiente de revisión", "En revisión", "Registrado", "Cerrado"])
def test_no_avisa_casos_que_ya_no_maneja_el_chatbot(
    entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado, estado: str
) -> None:
    caso_inactivo(entorno, 60 * HORA, estado)
    assert advertencia_inactividad(entorno.motor, ctx).procesados == 0 and whatsapp.enviados == []


def test_a_las_72_horas_ya_no_se_avisa_porque_toca_descartar(
    entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado
) -> None:
    caso_inactivo(entorno, 72 * HORA + MIN)
    assert advertencia_inactividad(entorno.motor, ctx).procesados == 0 and whatsapp.enviados == []


def test_a_un_productor_deshabilitado_no_se_le_escribe_pero_se_marca(
    entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado
) -> None:
    with entorno.motor.begin() as c:
        c.execute(text("UPDATE productores SET estado_cuenta_productor = 'DESHABILITADO'"))
    id_caso = caso_inactivo(entorno, 50 * HORA)
    r = advertencia_inactividad(entorno.motor, ctx)
    assert whatsapp.enviados == [] and r.omitidos == 1 and r.errores == 0 and advertido(entorno.motor, id_caso)


def test_si_el_envio_falla_no_se_marca_y_el_siguiente_ciclo_lo_reintenta(
    entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado
) -> None:
    id_caso = caso_inactivo(entorno, 50 * HORA)
    whatsapp.fallan.add(entorno.celular_e164)
    r = advertencia_inactividad(entorno.motor, ctx)
    assert (r.errores, r.exito) == (1, False) and not advertido(entorno.motor, id_caso)
    whatsapp.fallan.clear()
    assert advertencia_inactividad(entorno.motor, ctx).exitosos == 1 and advertido(entorno.motor, id_caso)


def test_un_fallo_no_frena_a_los_demas(entorno: Entorno, ctx: Contexto, whatsapp: WhatsAppSimulado) -> None:
    otro = crear_otro_productor(entorno, "573009998877")
    uno = caso_inactivo(entorno, 50 * HORA)
    dos = crear_caso(entorno.motor, otro, "GULUPA", "En análisis")
    inactivo(entorno.motor, dos, 50 * HORA)
    whatsapp.fallan.add(entorno.celular_e164)
    r = advertencia_inactividad(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.errores) == (2, 1, 1)
    assert advertido(entorno.motor, dos) and not advertido(entorno.motor, uno)


def crear_otro_productor(entorno: Entorno, celular: str) -> str:
    """Otro productor con su finca y cultivo de gulupa; devuelve el id del cultivo."""
    with Session(entorno.motor) as s:
        s.execute(
            text(
                "INSERT INTO productores (id_productor, nombre_productor, telefono_productor, estado_cuenta_productor, "
                "fecha_registro_productor) VALUES ('PRO-900001', 'Otro Productor', :t, 'ACTIVO', '2026-01-05')"
            ),
            {"t": celular},
        )
        s.execute(
            text(
                "INSERT INTO fincas (id_finca, nombre_finca, vereda_sector_finca, latitud_finca, longitud_finca, id_productor) "
                "VALUES ('FIN-900001', 'Otra', 'Centro', 5.25, -73.17, 'PRO-900001')"
            )
        )
        s.execute(
            text(
                "INSERT INTO cultivos (id_cultivo, nombre_cultivo, latitud_cultivo, longitud_cultivo, id_finca, tipo_cultivo) "
                "VALUES ('CUL-900001', 'Lote', 5.25, -73.17, 'FIN-900001', 'CAMPO_ABIERTO')"
            )
        )
        s.execute(
            text(
                "INSERT INTO cultivo_especie (id_cultivo, id_especie) "
                "SELECT 'CUL-900001', id_especie FROM especies WHERE nombre_especie = 'Gulupa'"
            )
        )
        s.commit()
    return "CUL-900001"


# --- descarte por inactividad (72 h) -------------------------------------------------------------------------------


def caso_con_fotos(
    entorno: Entorno, s3: AlmacenS3, desde: timedelta, claves: tuple[str, ...] = ("evidencias/a.webp",)
) -> str:
    id_caso = caso_inactivo(entorno, desde)
    crear_identificacion(entorno.motor, id_caso, "PRELIMINAR", {"TRIPS": 80})
    crear_respuesta(entorno.motor, id_caso, "¿Dónde?", "En el fruto", fotos=tuple(("FRUTO", c, True) for c in claves))
    for clave in claves:
        s3.subir(clave, versiones=2)
    s3.borrar_simple(claves[0])  # además, un marcador de borrado
    return id_caso


def test_descarta_a_las_72_horas_y_no_antes(entorno: Entorno, ctx: Contexto) -> None:
    reciente = caso_inactivo(entorno, 72 * HORA - MIN)
    vencido = caso_inactivo(entorno, 72 * HORA + MIN)
    r = descarte_inactividad(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.errores) == (1, 1, 0)
    assert existe(entorno.motor, reciente) and not existe(entorno.motor, vencido)


def test_el_descarte_borra_todo_y_no_deja_fotos_huerfanas_ni_versiones(
    entorno: Entorno, ctx: Contexto, s3: AlmacenS3
) -> None:
    """Criterio de aceptación: sin fotos huérfanas ni versiones recuperables en S3."""
    claves = ("evidencias/a.webp", "evidencias/b.webp")
    id_caso = caso_con_fotos(entorno, s3, 80 * HORA, claves)
    assert s3.versiones(claves[0]) == 3 and s3.versiones(claves[1]) == 2  # versiones + marcador
    r = descarte_inactividad(entorno.motor, ctx)
    assert r.exitosos == 1 and r.detalle == {"fotos": 2, "versiones_s3": 5}
    assert s3.versiones(claves[0]) == 0 and s3.versiones(claves[1]) == 0
    assert not existe(entorno.motor, id_caso)
    for tabla in (
        "fotografias",
        "respuestas",
        "diagnosticos",
        "eventos",
        "coincidencias_categoria",
        "identificacion_preliminar",
        "asignaciones",
    ):
        assert filas(entorno.motor, f"SELECT 1 FROM {tabla}") == [], tabla


def test_el_descarte_deja_auditoria_sin_datos_personales(entorno: Entorno, ctx: Contexto) -> None:
    id_caso = caso_inactivo(entorno, 80 * HORA)
    descarte_inactividad(entorno.motor, ctx)
    [registro] = filas(entorno.motor, "SELECT * FROM auditoria WHERE accion = 'BORRAR_INACTIVO'")
    assert (registro["origen"], registro["entidad_afectada"], registro["resultado"]) == ("SISTEMA", "casos", "EXITO")
    assert id_caso in registro["descripcion"] and "72 h" in registro["descripcion"]
    texto = " ".join(map(str, registro.values()))
    assert "Ana" not in texto and "573001112233" not in texto
    assert registro["fecha_auditoria"] == AHORA


def test_el_descarte_respeta_lo_que_no_es_del_chatbot_ni_inactivo(entorno: Entorno, ctx: Contexto) -> None:
    pendiente = caso_inactivo(entorno, 200 * HORA, "Pendiente de revisión")
    cerrado = caso_inactivo(entorno, 200 * HORA, "Cerrado")
    activo = caso_inactivo(entorno, 1 * HORA)
    assert descarte_inactividad(entorno.motor, ctx).procesados == 0
    assert all(existe(entorno.motor, c) for c in (pendiente, cerrado, activo))


def test_el_descarte_es_idempotente(entorno: Entorno, ctx: Contexto) -> None:
    caso_inactivo(entorno, 80 * HORA)
    assert descarte_inactividad(entorno.motor, ctx).exitosos == 1
    segunda = descarte_inactividad(entorno.motor, ctx)
    assert (segunda.procesados, segunda.errores) == (0, 0)
    assert len(filas(entorno.motor, "SELECT 1 FROM auditoria WHERE accion = 'BORRAR_INACTIVO'")) == 1


def test_si_s3_falla_no_se_borra_la_fila_y_se_reintenta(
    entorno: Entorno, ctx: Contexto, s3: AlmacenS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    id_caso = caso_con_fotos(entorno, s3, 80 * HORA)
    otro = caso_inactivo(entorno, 80 * HORA)
    original = s3.cliente.eliminar_todas_las_versiones

    def falla(clave: str) -> int:
        raise ErrorBorradoS3("S3 no respondió")

    monkeypatch.setattr(s3.cliente, "eliminar_todas_las_versiones", falla)
    r = descarte_inactividad(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.errores, r.exito) == (2, 1, 1, False)
    assert existe(entorno.motor, id_caso) and not existe(entorno.motor, otro)  # el que no tiene fotos sí se borró
    assert filas(entorno.motor, "SELECT count(*) AS n FROM fotografias")[0]["n"] == 1
    assert s3.versiones("evidencias/a.webp") == 3  # nada se tocó

    monkeypatch.setattr(s3.cliente, "eliminar_todas_las_versiones", original)
    assert descarte_inactividad(entorno.motor, ctx).exitosos == 1
    assert not existe(entorno.motor, id_caso) and s3.versiones("evidencias/a.webp") == 0


def test_un_fallo_parcial_de_s3_entre_fotos_no_borra_filas(
    entorno: Entorno, ctx: Contexto, s3: AlmacenS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    claves = ("evidencias/1.webp", "evidencias/2.webp")
    id_caso = caso_con_fotos(entorno, s3, 80 * HORA, claves)
    original = s3.cliente.eliminar_todas_las_versiones

    def segunda_falla(clave: str) -> int:
        if clave == claves[1]:
            raise ErrorBorradoS3("falló la segunda")
        return original(clave)

    monkeypatch.setattr(s3.cliente, "eliminar_todas_las_versiones", segunda_falla)
    assert descarte_inactividad(entorno.motor, ctx).errores == 1
    assert existe(entorno.motor, id_caso) and filas(entorno.motor, "SELECT count(*) AS n FROM fotografias")[0]["n"] == 2
    monkeypatch.setattr(s3.cliente, "eliminar_todas_las_versiones", original)
    assert (
        descarte_inactividad(entorno.motor, ctx).exitosos == 1
    )  # el reintento termina lo que faltaba (borrar es idempotente)
    assert all(s3.versiones(c) == 0 for c in claves)


def test_si_el_productor_escribe_justo_antes_el_caso_no_se_borra(
    entorno: Entorno, ctx: Contexto, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entre elegir el caso y borrarlo llega un mensaje: al volver a comprobar con la fila bloqueada ya no cumple."""
    id_caso = caso_inactivo(entorno, 80 * HORA)
    original = jobs._ids

    def con_actividad_nueva(engine: Engine, sql: str, **parametros: Any) -> list[Any]:
        elegidos = original(engine, sql, **parametros)
        inactivo(engine, id_caso, 1 * HORA)
        return elegidos

    monkeypatch.setattr(jobs, "_ids", con_actividad_nueva)
    r = descarte_inactividad(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.omitidos, r.errores) == (1, 0, 1, 0) and existe(entorno.motor, id_caso)


def test_el_descarte_quita_el_caso_de_las_alertas_sin_borrar_la_alerta(entorno: Entorno, ctx: Contexto) -> None:
    id_caso = caso_inactivo(entorno, 80 * HORA)
    id_alerta = crear_alerta(entorno.motor, [id_caso])
    descarte_inactividad(entorno.motor, ctx)
    assert filas(entorno.motor, "SELECT 1 FROM alerta_casos") == []
    assert [a["id_alerta"] for a in filas(entorno.motor, "SELECT id_alerta FROM alertas")] == [id_alerta]


# --- retención (6 meses) ---------------------------------------------------------------------------------------------


def caso_cerrado(entorno: Entorno, vence: date | None, claves: tuple[str, ...] = ()) -> str:
    id_caso = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "Cerrado", vence_en=vence)
    if claves:
        crear_respuesta(
            entorno.motor, id_caso, "¿Dónde?", "En el fruto", fotos=tuple(("HOJA", c, True) for c in claves)
        )
    return id_caso


def hoy_bogota() -> date:
    return (AHORA - timedelta(hours=5)).date()


def test_borra_los_cerrados_vencidos_y_conserva_el_resto(entorno: Entorno, ctx: Contexto) -> None:
    ayer = caso_cerrado(entorno, hoy_bogota() - timedelta(days=1))
    hoy = caso_cerrado(entorno, hoy_bogota())
    manana = caso_cerrado(entorno, hoy_bogota() + timedelta(days=1))
    sin_fecha = caso_cerrado(entorno, None)
    registrado = crear_caso(
        entorno.motor, entorno.id_cultivo, "GULUPA", "Registrado", vence_en=hoy_bogota() - timedelta(days=30)
    )
    r = retencion(entorno.motor, ctx)
    assert (r.procesados, r.exitosos, r.errores) == (2, 2, 0)
    assert not existe(entorno.motor, ayer) and not existe(entorno.motor, hoy)
    assert all(existe(entorno.motor, c) for c in (manana, sin_fecha, registrado))


def test_la_retencion_borra_las_versiones_de_s3_y_audita(entorno: Entorno, ctx: Contexto, s3: AlmacenS3) -> None:
    id_caso = caso_cerrado(entorno, hoy_bogota() - timedelta(days=1), ("evidencias/r.webp",))
    s3.subir("evidencias/r.webp", versiones=3)
    s3.borrar_simple("evidencias/r.webp")
    assert s3.versiones("evidencias/r.webp") == 4
    r = retencion(entorno.motor, ctx)
    assert r.detalle == {"fotos": 1, "versiones_s3": 4} and s3.versiones("evidencias/r.webp") == 0
    assert not existe(entorno.motor, id_caso) and filas(entorno.motor, "SELECT 1 FROM fotografias") == []
    [registro] = filas(entorno.motor, "SELECT * FROM auditoria WHERE accion = 'BORRAR_RETENCION'")
    assert (
        registro["origen"] == "SISTEMA" and id_caso in registro["descripcion"] and "573001112233" not in str(registro)
    )


def test_si_s3_falla_la_retencion_no_borra_la_fila(
    entorno: Entorno, ctx: Contexto, s3: AlmacenS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    id_caso = caso_cerrado(entorno, hoy_bogota() - timedelta(days=1), ("evidencias/r.webp",))
    s3.subir("evidencias/r.webp")

    def falla(clave: str) -> int:
        raise ErrorBorradoS3("sin permiso")

    monkeypatch.setattr(s3.cliente, "eliminar_todas_las_versiones", falla)
    r = retencion(entorno.motor, ctx)
    assert (r.errores, r.exito) == (1, False) and existe(entorno.motor, id_caso)
    assert filas(entorno.motor, "SELECT 1 FROM auditoria WHERE accion = 'BORRAR_RETENCION'") == []


def test_la_retencion_es_idempotente(entorno: Entorno, ctx: Contexto) -> None:
    caso_cerrado(entorno, hoy_bogota() - timedelta(days=1))
    retencion(entorno.motor, ctx)
    assert retencion(entorno.motor, ctx).procesados == 0


def test_seis_meses_con_precision_de_un_minuto(entorno: Entorno, s3: AlmacenS3, whatsapp: WhatsAppSimulado) -> None:
    """Se cierra el 27-abr 15:00 (Bogotá): vence el 27-oct. A las 23:59 del 26-oct se conserva; a las 00:00 se borra."""
    with Session(entorno.motor) as s:
        tecnico = crear_tecnico(s)
        s.commit()
        id_tecnico = tecnico.id_tecnico
    id_caso = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "Registrado", tecnico=id_tecnico)
    cierre = datetime(2026, 4, 27, 20, 0, tzinfo=UTC)
    with Session(entorno.motor) as s, s.begin():
        s.execute(
            sp.CERRAR_CASO,
            {"id_caso": id_caso, "actor_tipo": "TECNICO", "actor_id": id_tecnico, "meses": 6, "fecha": cierre},
        )
    assert filas(entorno.motor, "SELECT vence_en AS v FROM casos")[0]["v"] == date(2026, 10, 27)

    medianoche = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)  # 00:00 en Bogotá
    antes = retencion(entorno.motor, Contexto(ahora=medianoche - MIN, s3=s3.cliente))
    assert antes.procesados == 0 and existe(entorno.motor, id_caso)
    despues = retencion(entorno.motor, Contexto(ahora=medianoche, s3=s3.cliente))
    assert despues.exitosos == 1 and not existe(entorno.motor, id_caso)


def test_la_retencion_no_toca_los_casos_de_otros_estados_aunque_esten_viejos(entorno: Entorno, ctx: Contexto) -> None:
    descartable = caso_inactivo(entorno, 10 * HORA)
    assert retencion(entorno.motor, ctx).procesados == 0 and existe(entorno.motor, descartable)


# --- barrido de alertas ------------------------------------------------------------------------------------------------


def test_el_barrido_genera_las_alertas_que_faltaban_y_no_las_repite(entorno: Entorno, ctx: Contexto) -> None:
    with Session(entorno.motor) as s:
        tecnico = crear_tecnico(s)
        s.commit()
        id_tecnico = tecnico.id_tecnico
    casos = [
        crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "Registrado", tecnico=id_tecnico) for _ in range(3)
    ]
    for id_caso in casos:
        registrar_diagnostico(entorno.motor, id_caso, "CONFIRMADO", "TRIPS")
    with entorno.motor.begin() as c:  # los diagnósticos se dejaron dentro de la ventana del reloj de la prueba
        c.execute(text("UPDATE diagnosticos SET fecha_diagnostico = :t"), {"t": AHORA - 2 * HORA})
    r = evaluar_alertas(entorno.motor, ctx)
    assert (r.exitosos, r.errores, r.detalle) == (1, 0, {"alertas_nuevas": 1})
    assert evaluar_alertas(entorno.motor, ctx).detalle == {"alertas_nuevas": 0}
    assert len(filas(entorno.motor, "SELECT 1 FROM alerta_casos")) == 3


def test_el_barrido_sin_casos_no_hace_nada(entorno: Entorno, ctx: Contexto) -> None:
    assert evaluar_alertas(entorno.motor, ctx).detalle == {"alertas_nuevas": 0}


def test_el_barrido_informa_si_falla(entorno: Entorno, ctx: Contexto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "EVALUAR_ALERTA", text("SELECT 1/0"))
    r = evaluar_alertas(entorno.motor, ctx)
    assert (r.errores, r.exito) == (1, False)


# --- bloqueo y registro ------------------------------------------------------------------------------------------------


def test_los_nombres_de_los_jobs() -> None:
    assert sorted(jobs.JOBS) == ["advertencia_inactividad", "descarte_inactividad", "evaluar_alertas", "retencion"]
    assert jobs.NECESITAN_S3 == {"descarte_inactividad", "retencion"} and jobs.NECESITAN_WHATSAPP == {
        "advertencia_inactividad"
    }


def test_dos_ejecuciones_del_mismo_job_no_corren_a_la_vez(entorno: Entorno, ctx: Contexto) -> None:
    caso_inactivo(entorno, 80 * HORA)
    with jobs.bloqueo(entorno.motor, "descarte_inactividad") as obtenido:
        assert obtenido
        segunda = ejecutar("descarte_inactividad", entorno.motor, ctx)
        assert segunda.ya_en_ejecucion and segunda.procesados == 0 and segunda.exito
        otro_job = ejecutar("evaluar_alertas", entorno.motor, ctx)  # los candados son por job
        assert not otro_job.ya_en_ejecucion
    despues = ejecutar("descarte_inactividad", entorno.motor, ctx)
    assert not despues.ya_en_ejecucion and despues.exitosos == 1


def test_el_candado_se_libera_aunque_el_job_falle(
    entorno: Entorno, ctx: Contexto, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explota(engine: Engine, contexto: Contexto) -> Any:
        raise RuntimeError("fallo inesperado")

    monkeypatch.setitem(jobs.JOBS, "retencion", explota)
    with pytest.raises(RuntimeError):
        ejecutar("retencion", entorno.motor, ctx)
    with jobs.bloqueo(entorno.motor, "retencion") as obtenido:
        assert obtenido
