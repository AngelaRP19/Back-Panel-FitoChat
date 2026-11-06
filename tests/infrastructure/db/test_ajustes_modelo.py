"""Comportamiento en PostgreSQL de los ajustes aprobados el 26-oct-2026 (D-01 a D-14 y D-17)."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    AlertaCaso,
    AlertaDestinatario,
    Asignacion,
    Auditoria,
    Cultivo,
    Diagnostico,
    Evento,
    Fotografia,
    IdentificacionPreliminar,
    Pregunta,
    Respuesta,
)
from tests.infrastructure.db.factories import (
    ahora,
    crear_alerta,
    crear_caso,
    crear_categoria,
    crear_lider,
    crear_tecnico,
    escenario,
    viola,
    viola_not_null,
)

# --- D-01: asignaciones ------------------------------------------------------------------------------------------


def test_asignacion_recibe_id_con_prefijo(session: Session) -> None:
    datos = escenario(session)
    asignacion = Asignacion(
        id_caso=crear_caso(session, datos).id_caso,
        id_tecnico=crear_tecnico(session).id_tecnico,
        fecha_asignacion=ahora(),
    )
    session.add(asignacion)
    session.flush()
    assert re.fullmatch(r"ASG-\d{8}", asignacion.id_asignacion)


def test_una_sola_asignacion_vigente_por_caso(session: Session) -> None:
    datos = escenario(session)
    caso = crear_caso(session, datos)
    ana, beto = crear_tecnico(session, 1), crear_tecnico(session, 2)
    session.add(Asignacion(id_caso=caso.id_caso, id_tecnico=ana.id_tecnico, fecha_asignacion=ahora()))
    session.flush()
    viola(
        session,
        "uq_asg_caso_vigente",
        lambda: session.add(Asignacion(id_caso=caso.id_caso, id_tecnico=beto.id_tecnico, fecha_asignacion=ahora())),
    )


def test_reasignar_cierra_la_anterior_y_crea_otra(session: Session) -> None:
    datos = escenario(session)
    caso = crear_caso(session, datos)
    ana, beto = crear_tecnico(session, 1), crear_tecnico(session, 2)
    primera = Asignacion(id_caso=caso.id_caso, id_tecnico=ana.id_tecnico, fecha_asignacion=ahora())
    session.add(primera)
    session.flush()

    primera.fecha_fin = ahora()  # se cierra la vigente...
    session.flush()
    session.add(
        Asignacion(id_caso=caso.id_caso, id_tecnico=beto.id_tecnico, fecha_asignacion=ahora())
    )  # ...y se crea otra
    session.flush()

    session.refresh(caso)
    assert [a.id_tecnico for a in caso.asignaciones] == [ana.id_tecnico, beto.id_tecnico]
    vigentes = [a for a in caso.asignaciones if a.fecha_fin is None]
    assert [a.id_tecnico for a in vigentes] == [beto.id_tecnico]


def test_un_caso_puede_tener_varias_asignaciones_cerradas(session: Session) -> None:
    datos = escenario(session)
    caso = crear_caso(session, datos)
    tecnico = crear_tecnico(session)
    inicio = ahora()
    for i in range(3):
        session.add(
            Asignacion(
                id_caso=caso.id_caso,
                id_tecnico=tecnico.id_tecnico,
                fecha_asignacion=inicio + timedelta(hours=i),
                fecha_fin=inicio + timedelta(hours=i, minutes=30),
            )
        )
    session.flush()
    assert len(caso.asignaciones) == 3


def test_un_tecnico_atiende_varios_casos_a_la_vez(session: Session) -> None:
    datos = escenario(session)
    tecnico = crear_tecnico(session)
    for _ in range(2):
        caso = crear_caso(session, datos)
        session.add(Asignacion(id_caso=caso.id_caso, id_tecnico=tecnico.id_tecnico, fecha_asignacion=ahora()))
    session.flush()
    session.refresh(tecnico)
    assert len(tecnico.asignaciones) == 2


def test_la_asignacion_exige_un_tecnico_existente(session: Session) -> None:
    datos = escenario(session)
    caso = crear_caso(session, datos)
    viola(
        session,
        "fk_asg_tecnico",
        lambda: session.add(Asignacion(id_caso=caso.id_caso, id_tecnico="TEC-999999", fecha_asignacion=ahora())),
    )


def test_la_asignacion_exige_un_caso_existente(session: Session) -> None:
    tecnico = crear_tecnico(session)
    viola(
        session,
        "fk_asg_caso",
        lambda: session.add(
            Asignacion(id_caso="CAS-99999999", id_tecnico=tecnico.id_tecnico, fecha_asignacion=ahora())
        ),
    )


def test_diagnosticos_no_tiene_id_tecnico_en_la_base(session: Session) -> None:
    columnas = set(
        session.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'diagnosticos'"))
    )
    assert "id_tecnico" not in columnas
    assert {"id_caso", "resultado_tecnico", "fecha_diagnostico"} <= columnas


# --- D-03 y D-06: el caso ----------------------------------------------------------------------------------------


def test_el_caso_nace_sin_identificacion(session: Session) -> None:
    caso = crear_caso(session, escenario(session), con_identificacion=False)
    assert caso.id_identificacion_preliminar is None


def test_el_caso_se_puede_asociar_a_la_identificacion_despues(session: Session) -> None:
    datos = escenario(session)
    caso = crear_caso(session, datos, con_identificacion=False)
    caso.id_identificacion_preliminar = datos.identificacion.id_identificacion
    session.flush()
    session.refresh(datos.identificacion)
    assert datos.identificacion.casos == [caso]


def test_fechas_del_ciclo_de_vida_del_caso(session: Session) -> None:
    caso = crear_caso(session, escenario(session))
    session.refresh(caso)
    assert caso.fecha_envio_panel is None and caso.fecha_cierre is None and caso.vence_en is None
    assert caso.ultima_actividad.tzinfo is not None
    assert abs(datetime.now(UTC) - caso.ultima_actividad) < timedelta(minutes=1)  # por defecto now()

    cierre = datetime(2026, 10, 27, 15, 4, tzinfo=UTC)
    caso.fecha_envio_panel = cierre - timedelta(days=2)
    caso.fecha_cierre = cierre
    caso.vence_en = date(2027, 4, 27)
    session.flush()
    session.refresh(caso)
    assert (caso.fecha_cierre, caso.vence_en) == (cierre, date(2027, 4, 27))


def test_la_ultima_actividad_no_puede_ser_nula(session: Session) -> None:
    caso = crear_caso(session, escenario(session))
    viola_not_null(session, "ultima_actividad", lambda: setattr(caso, "ultima_actividad", None))


# --- D-04 y D-05: diagnóstico vacío y su coherencia --------------------------------------------------------------


def _diagnostico_vacio(session: Session) -> Diagnostico:
    """Diagnóstico vacío de un caso con técnico activo asignado (trg_diag_tecnico_activo lo exige para completarlo)."""
    caso = crear_caso(session, escenario(session))
    session.add(
        Asignacion(id_caso=caso.id_caso, id_tecnico=crear_tecnico(session).id_tecnico, fecha_asignacion=ahora())
    )
    diagnostico = Diagnostico(id_caso=caso.id_caso)
    session.add(diagnostico)
    session.flush()
    return diagnostico


def test_el_diagnostico_se_crea_vacio(session: Session) -> None:
    diagnostico = _diagnostico_vacio(session)
    assert re.fullmatch(r"DIA-\d{8}", diagnostico.id_diagnostico)
    assert diagnostico.resultado_tecnico is None  # "registrado" = resultado no nulo, no que exista la fila
    assert diagnostico.fecha_diagnostico is None and diagnostico.id_categoria_confirmada is None


def test_un_solo_diagnostico_por_caso(session: Session) -> None:
    diagnostico = _diagnostico_vacio(session)
    viola(session, "uq_dia_caso", lambda: session.add(Diagnostico(id_caso=diagnostico.id_caso)))


def test_el_tecnico_completa_el_diagnostico_vacio(session: Session) -> None:
    diagnostico = _diagnostico_vacio(session)
    categoria = crear_categoria(session, "Trips de prueba")
    diagnostico.resultado_tecnico = "CONFIRMADO"
    diagnostico.id_categoria_confirmada = categoria.id_categoria
    diagnostico.descripcion_diagnostico = "Daño en hojas jóvenes"
    diagnostico.fecha_diagnostico = ahora()
    session.flush()
    assert diagnostico.categoria_confirmada is categoria


@pytest.mark.parametrize("resultado", ["CONFIRMADO", "OTRA_PATOLOGIA"])
def test_confirmado_y_otra_patologia_exigen_categoria(session: Session, resultado: str) -> None:
    diagnostico = _diagnostico_vacio(session)

    def completar() -> None:
        diagnostico.resultado_tecnico = resultado
        diagnostico.fecha_diagnostico = ahora()

    viola(session, "ck_diagnosticos_categoria_requerida", completar)


@pytest.mark.parametrize("resultado", ["NO_IDENTIFICADO", "REQUIERE_VISITA"])
def test_no_identificado_y_requiere_visita_no_llevan_categoria(session: Session, resultado: str) -> None:
    diagnostico = _diagnostico_vacio(session)
    diagnostico.resultado_tecnico = resultado
    diagnostico.fecha_diagnostico = ahora()
    diagnostico.motivo_visita = "Revisar en campo"
    session.flush()
    assert diagnostico.id_categoria_confirmada is None
    assert diagnostico.descripcion_diagnostico is None  # D-04: la descripción es opcional


def test_con_resultado_la_fecha_es_obligatoria(session: Session) -> None:
    diagnostico = _diagnostico_vacio(session)
    viola(
        session,
        "ck_diagnosticos_fecha_con_resultado",
        lambda: setattr(diagnostico, "resultado_tecnico", "NO_IDENTIFICADO"),
    )


# --- D-05 y D-11: cuestionario y fotos cuelgan del diagnóstico vacío ----------------------------------------------


def _respuesta(session: Session) -> Respuesta:
    diagnostico = _diagnostico_vacio(session)
    pregunta = Pregunta(texto_pregunta="¿Pregunta de prueba?")
    session.add(pregunta)
    session.flush()
    respuesta = Respuesta(
        id_pregunta=pregunta.id_pregunta,
        id_diagnostico=diagnostico.id_diagnostico,
        texto_respuesta="Sí",
        fecha_respuesta=ahora(),
    )
    session.add(respuesta)
    session.flush()
    return respuesta


def test_respuestas_y_fotos_se_guardan_antes_del_diagnostico(session: Session) -> None:
    respuesta = _respuesta(session)
    foto = Fotografia(
        id_respuesta=respuesta.id_respuesta,
        tipo_evidencia="HOJA",
        ruta_fotografia="evidencias/2026/10/FOT-00000001.webp",
        fecha_envio_fotografia=ahora(),
    )
    session.add(foto)
    session.flush()
    assert respuesta.diagnostico.resultado_tecnico is None
    assert respuesta.fotografias == [foto]


def test_la_foto_es_util_por_defecto(session: Session) -> None:
    respuesta = _respuesta(session)
    foto = Fotografia(
        id_respuesta=respuesta.id_respuesta,
        tipo_evidencia="HOJA",
        ruta_fotografia="evidencias/2026/10/FOT-00000002.webp",
        fecha_envio_fotografia=ahora(),
    )
    session.add(foto)
    session.flush()
    session.refresh(foto)
    assert foto.es_util == "S"


def test_una_foto_se_puede_marcar_como_no_util(session: Session) -> None:
    respuesta = _respuesta(session)
    foto = Fotografia(
        id_respuesta=respuesta.id_respuesta,
        tipo_evidencia="FRUTO",
        ruta_fotografia="evidencias/2026/10/FOT-00000003.webp",
        fecha_envio_fotografia=ahora(),
        es_util="N",
    )
    session.add(foto)
    session.flush()
    assert foto.es_util == "N"


def test_es_util_solo_acepta_s_o_n(session: Session) -> None:
    respuesta = _respuesta(session)
    viola(
        session,
        "ck_fotografias_es_util_sn",
        lambda: session.add(
            Fotografia(
                id_respuesta=respuesta.id_respuesta,
                tipo_evidencia="HOJA",
                ruta_fotografia="evidencias/2026/10/FOT-00000004.webp",
                fecha_envio_fotografia=ahora(),
                es_util="X",
            )
        ),
    )


# --- D-08 y D-17: alertas ----------------------------------------------------------------------------------------


def test_la_alerta_guarda_fecha_con_hora_y_parametros_aplicados(session: Session) -> None:
    categoria = crear_categoria(session, "Trips alerta")
    detectada = datetime(2026, 10, 27, 15, 4, tzinfo=UTC)
    alerta = crear_alerta(session, categoria, fecha_generacion_alerta=detectada)
    session.refresh(alerta)
    assert re.fullmatch(r"ALR-\d{6}", alerta.id_alerta)
    assert alerta.fecha_generacion_alerta == detectada
    assert (alerta.numero_minimo_casos, alerta.distancia_km, alerta.intervalo_dias) == (3, Decimal("3.50"), 30)
    assert alerta.id_lider_gestion is None and alerta.fecha_confirmacion is None


@pytest.mark.parametrize("columna", ["numero_minimo_casos", "distancia_km", "intervalo_dias"])
def test_los_parametros_aplicados_son_obligatorios(session: Session, columna: str) -> None:
    categoria = crear_categoria(session, "Trips parámetros")
    alerta = crear_alerta(session, categoria)
    viola_not_null(session, columna, lambda: setattr(alerta, columna, None))


def test_la_alerta_conserva_sus_parametros_aunque_cambien_los_de_la_categoria(session: Session) -> None:
    from app.infrastructure.db.models import ParametroEpidemiologico

    categoria = crear_categoria(session, "Trips conserva")
    parametro = ParametroEpidemiologico(
        id_categoria=categoria.id_categoria,
        numero_minimo_casos=3,
        distancia_km=Decimal("3.50"),
        intervalo_dias=30,
        fecha_actualizacion=ahora(),
    )
    session.add(parametro)
    alerta = crear_alerta(session, categoria)
    parametro.distancia_km = Decimal("10.00")  # el líder edita los parámetros después
    session.flush()
    session.refresh(alerta)
    assert alerta.distancia_km == Decimal("3.50")


def test_confirmar_una_alerta_registra_quien_y_cuando(session: Session) -> None:
    lider = crear_lider(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips confirma"))
    alerta.estado_alerta = "CONFIRMADA"
    alerta.id_lider_gestion = lider.id_lider_tecnico
    alerta.fecha_confirmacion = ahora()
    session.flush()
    assert alerta.lider_gestion is lider
    assert lider.alertas_gestionadas == [alerta]


def test_descartar_una_alerta_guarda_la_justificacion(session: Session) -> None:
    alerta = crear_alerta(session, crear_categoria(session, "Trips descarta"))
    alerta.estado_alerta = "DESCARTADA"
    alerta.justificacion_descarte = "Casos sin relación entre sí"
    session.flush()
    assert alerta.justificacion_descarte == "Casos sin relación entre sí"


def test_la_alerta_exige_un_lider_existente(session: Session) -> None:
    alerta = crear_alerta(session, crear_categoria(session, "Trips lider"))
    viola(session, "fk_ale_lider_gestion", lambda: setattr(alerta, "id_lider_gestion", "LID-999999"))
    viola(session, "fk_ale_lider_correccion", lambda: setattr(alerta, "id_lider_correccion", "LID-999999"))


def test_corregir_una_alerta_comunicada(session: Session) -> None:
    lider = crear_lider(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips corrige"), estado_alerta="COMUNICADA")
    alerta.estado_alerta = "CORREGIDA"  # estado nuevo de D-17
    alerta.mensaje_correccion = "Se envió por error"
    alerta.fecha_correccion = ahora()
    alerta.id_lider_correccion = lider.id_lider_tecnico
    session.flush()
    assert alerta.lider_correccion is lider
    assert lider.alertas_corregidas == [alerta]
    assert lider.alertas_gestionadas == []  # gestionar y corregir son relaciones distintas


def test_alerta_casos_vincula_los_casos_que_la_originan(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips casos"))
    casos = [crear_caso(session, datos) for _ in range(4)]
    for caso in casos:
        session.add(AlertaCaso(id_alerta=alerta.id_alerta, id_caso=caso.id_caso))
    session.flush()
    session.refresh(alerta)
    assert {v.id_caso for v in alerta.casos} == {c.id_caso for c in casos}
    session.refresh(casos[0])
    assert [v.alerta for v in casos[0].alertas_vinculadas] == [alerta]


def test_alerta_casos_no_repite_el_vinculo(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips repetido"))
    caso = crear_caso(session, datos)
    session.add(AlertaCaso(id_alerta=alerta.id_alerta, id_caso=caso.id_caso))
    session.flush()
    viola(session, "pk_alerta_casos", lambda: session.add(AlertaCaso(id_alerta=alerta.id_alerta, id_caso=caso.id_caso)))


def _destinatario(
    alerta_id: str, productor_id: str, tipo: str = "ALERTA", estado: str = "SIMULADO"
) -> AlertaDestinatario:
    return AlertaDestinatario(id_alerta=alerta_id, id_productor=productor_id, tipo_envio=tipo, estado_envio=estado)


def test_destinatarios_registran_el_resultado_por_productor(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips envía"))
    ok = _destinatario(alerta.id_alerta, datos.productor.id_productor)
    session.add(ok)
    session.flush()
    assert re.fullmatch(r"ADE-\d{8}", ok.id_alerta_destinatario)
    assert ok.estado_envio == "SIMULADO"  # en modo simulado nunca se guarda como ENVIADO
    ok.fecha_envio = ahora()
    ok.estado_envio = "FALLIDO"
    ok.detalle_error = "Número no registrado en WhatsApp"
    session.flush()
    assert alerta.destinatarios == [ok]
    assert datos.productor.alertas_recibidas == [ok]


def test_un_reintento_no_duplica_el_envio(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips reintento"))
    session.add(_destinatario(alerta.id_alerta, datos.productor.id_productor))
    session.flush()
    viola(
        session,
        "uq_ade_envio",
        lambda: session.add(_destinatario(alerta.id_alerta, datos.productor.id_productor)),
    )


def test_la_correccion_se_registra_aparte_de_la_alerta(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips corrección"))
    session.add(_destinatario(alerta.id_alerta, datos.productor.id_productor, "ALERTA"))
    session.add(_destinatario(alerta.id_alerta, datos.productor.id_productor, "CORRECCION"))
    session.flush()
    assert {d.tipo_envio for d in alerta.destinatarios} == {"ALERTA", "CORRECCION"}


def test_tipo_de_envio_solo_alerta_o_correccion(session: Session) -> None:
    datos = escenario(session)
    alerta = crear_alerta(session, crear_categoria(session, "Trips tipo"))
    viola(
        session,
        "ck_alerta_destinatarios_tipo_envio_valido",
        lambda: session.add(_destinatario(alerta.id_alerta, datos.productor.id_productor, "OTRO")),
    )


# --- D-09: auditoría ---------------------------------------------------------------------------------------------


def _auditoria(origen: str, lider: str | None = None, tecnico: str | None = None) -> Auditoria:
    return Auditoria(
        id_lider_tecnico=lider,
        id_tecnico=tecnico,
        origen=origen,
        accion="CREAR",
        entidad_afectada="casos",
        descripcion="Prueba",
        resultado="EXITO",
        fecha_auditoria=ahora(),
    )


def test_auditoria_de_un_lider(session: Session) -> None:
    lider = crear_lider(session)
    registro = _auditoria("LIDER", lider=lider.id_lider_tecnico)
    session.add(registro)
    session.flush()
    assert registro.lider_tecnico is lider and registro.tecnico is None


def test_auditoria_de_un_tecnico(session: Session) -> None:
    tecnico = crear_tecnico(session)
    registro = _auditoria("TECNICO", tecnico=tecnico.id_tecnico)
    session.add(registro)
    session.flush()
    assert registro.tecnico is tecnico and tecnico.registros_auditoria == [registro]


def test_auditoria_del_sistema_no_tiene_autor(session: Session) -> None:
    registro = _auditoria("SISTEMA")
    session.add(registro)
    session.flush()
    assert registro.id_lider_tecnico is None and registro.id_tecnico is None


@pytest.mark.parametrize(
    ("origen", "con_lider", "con_tecnico"),
    [
        ("LIDER", False, False),  # falta el líder
        ("LIDER", True, True),  # sobra el técnico
        ("LIDER", False, True),  # el autor es de otro tipo
        ("TECNICO", False, False),
        ("TECNICO", True, True),
        ("TECNICO", True, False),
        ("SISTEMA", True, False),  # el sistema no tiene autor
        ("SISTEMA", False, True),
        ("SISTEMA", True, True),
        ("OTRO", False, False),  # origen inventado
    ],
)
def test_el_autor_debe_coincidir_con_el_origen(
    session: Session, origen: str, con_lider: bool, con_tecnico: bool
) -> None:
    lider = crear_lider(session).id_lider_tecnico if con_lider else None
    tecnico = crear_tecnico(session).id_tecnico if con_tecnico else None
    viola(session, "ck_auditoria_origen_coherente", lambda: session.add(_auditoria(origen, lider, tecnico)))


def test_el_origen_es_obligatorio(session: Session) -> None:
    registro = _auditoria("SISTEMA")
    registro.origen = None  # type: ignore[assignment]
    viola_not_null(session, "origen", lambda: session.add(registro))


# --- D-10 a D-14 -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["INVERNADERO", "CAMPO_ABIERTO"])
def test_tipo_de_cultivo_valido(session: Session, tipo: str) -> None:
    datos = escenario(session)
    datos.cultivo.tipo_cultivo = tipo
    session.flush()
    session.refresh(datos.cultivo)
    assert datos.cultivo.tipo_cultivo == tipo


def test_tipo_de_cultivo_invalido(session: Session) -> None:
    datos = escenario(session)
    viola(session, "ck_cultivos_tipo_cultivo_valido", lambda: setattr(datos.cultivo, "tipo_cultivo", "VIVERO"))


def test_tipo_de_cultivo_es_obligatorio(session: Session) -> None:
    datos = escenario(session)
    nuevo = Cultivo(
        nombre_cultivo="Sin tipo",
        latitud_cultivo=Decimal("5.28"),
        longitud_cultivo=Decimal("-73.17"),
        id_finca=datos.finca.id_finca,
    )
    viola_not_null(session, "tipo_cultivo", lambda: session.add(nuevo))


def test_fecha_de_consentimiento_es_opcional_y_guarda_la_hora(session: Session) -> None:
    datos = escenario(session)
    assert datos.productor.fecha_consentimiento is None
    aceptado = datetime(2026, 10, 27, 9, 30, tzinfo=UTC)
    datos.productor.fecha_consentimiento = aceptado
    session.flush()
    session.refresh(datos.productor)
    assert datos.productor.fecha_consentimiento == aceptado


def test_el_evento_guarda_su_actor(session: Session) -> None:
    caso = crear_caso(session, escenario(session))
    tecnico = crear_tecnico(session)
    evento = Evento(
        id_caso=caso.id_caso,
        tipo_evento="DIAGNOSTICO_REGISTRADO",
        descripcion_evento="El técnico registró el diagnóstico",
        fecha_evento=ahora(),
        actor_tipo="TECNICO",
        actor_id=tecnico.id_tecnico,
    )
    sistema = Evento(
        id_caso=caso.id_caso,
        tipo_evento="CASO_DESCARTADO",
        descripcion_evento="Inactividad de 72 horas",
        fecha_evento=ahora(),
    )
    session.add_all([evento, sistema])
    session.flush()
    assert (evento.actor_tipo, evento.actor_id) == ("TECNICO", tecnico.id_tecnico)
    assert sistema.actor_tipo is None and sistema.actor_id is None  # el actor es opcional


def test_detalle_de_la_explicacion_guarda_json(session: Session) -> None:
    detalle = {
        "BOTRYTIS": {"porcentaje": 62.5, "coinciden": ["moho gris"], "descartan": []},
        "TRIPS": {"porcentaje": 20, "coinciden": [], "descartan": ["sin insectos visibles"]},
    }
    identificacion = IdentificacionPreliminar(
        tipo_resultado="PRELIMINAR",
        explicacion_resultado="Resumen",
        fecha_analisis=ahora(),
        detalle_explicacion=detalle,
    )
    session.add(identificacion)
    session.flush()
    session.expire(identificacion)
    assert identificacion.detalle_explicacion == detalle
    assert (
        session.scalar(
            text(
                "SELECT detalle_explicacion -> 'TRIPS' ->> 'porcentaje' "
                "FROM identificacion_preliminar WHERE id_identificacion = :i"
            ),
            {"i": identificacion.id_identificacion},
        )
        == "20"
    )


def test_el_detalle_de_la_explicacion_es_opcional(session: Session) -> None:
    datos = escenario(session)
    assert datos.identificacion.detalle_explicacion is None
