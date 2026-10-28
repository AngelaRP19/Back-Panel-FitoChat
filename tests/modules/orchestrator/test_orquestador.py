"""Orquestador conversacional: recorridos completos y alternativos, entradas inválidas, /salir y reanudación."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.infrastructure.ai_client import Turno
from app.infrastructure.db.models import LiderTecnico
from app.infrastructure.notificaciones_store import _EN_MEMORIA as NOTIFICACIONES
from app.modules.orchestrator import textos as t
from app.modules.orchestrator.puertos import Identificacion
from tests.modules.orchestrator.conftest import (
    TELEFONO,
    Sistema,
    avanzar_inactividad,
    productor_con_finca,
    programar,
)

PREGUNTA_1 = "¿Cuándo notaste las manchas?"
PREGUNTA_2 = "¿Las manchas están en las hojas nuevas?"


def junto(respuestas: list[str]) -> str:
    return "\n".join(respuestas)


def hasta_afectacion(s: Sistema, **kwargs: Any) -> None:
    productor_con_finca(s.motor, **kwargs)
    s.escribir("hola")
    assert s.paso() == "AFECTACION"


def hasta_cuestionario(s: Sistema, *turnos: Turno) -> list[str]:
    """Productor con una finca, un cultivo y una especie; el caso queda abierto y la IA hizo su primera pregunta."""
    programar(s, *turnos)
    hasta_afectacion(s)
    return s.escribir("1")


# --- registro y consentimiento -----------------------------------------------------------------------------------


def test_un_numero_desconocido_recibe_el_saludo_y_el_aviso_de_privacidad(sistema: Sistema) -> None:
    respuestas = sistema.escribir("hola")
    assert respuestas == [t.SALUDO, t.CONSENTIMIENTO]
    assert sistema.paso() == "CONSENTIMIENTO"
    assert sistema.consulta("SELECT 1 FROM productores") == []  # nada se guarda sin consentimiento


def test_si_no_acepta_no_se_guarda_nada(sistema: Sistema) -> None:
    sistema.escribir("hola")
    assert sistema.escribir("No") == [t.CONSENTIMIENTO_NEGADO]
    assert sistema.consulta("SELECT 1 FROM productores") == [] and sistema.paso() == "INICIO"


@pytest.mark.parametrize("texto", ["tal vez", "???", "42"])
def test_una_respuesta_que_no_es_si_ni_no_se_repite(sistema: Sistema, texto: str) -> None:
    sistema.escribir("hola")
    assert sistema.escribir(texto) == [t.SI_O_NO] and sistema.paso() == "CONSENTIMIENTO"


@pytest.mark.parametrize("afirmacion", ["Sí", "si", "SÍ.", "claro", "¡Sí!"])
def test_acepta_de_varias_formas(sistema: Sistema, afirmacion: str) -> None:
    sistema.escribir("hola")
    assert sistema.escribir(afirmacion) == [t.PEDIR_NOMBRE]


def test_un_numero_desconocido_se_registra_con_consentimiento(sistema: Sistema) -> None:
    sistema.escribir("hola")
    sistema.escribir("sí")
    for malo in ("1", "?", "A"):
        assert sistema.escribir(malo) == [t.NOMBRE_INVALIDO] and sistema.paso() == "NOMBRE"
    respuestas = sistema.escribir("  Juan   Pérez ")
    assert respuestas[0] == t.BIENVENIDA_NUEVO.format(nombre="Juan") and respuestas[-1] == t.SIN_FINCAS
    [p] = sistema.consulta("SELECT * FROM productores")
    assert (p["nombre_productor"], p["telefono_productor"], p["estado_cuenta_productor"]) == (
        "Juan Pérez",
        TELEFONO,
        "ACTIVO",
    )
    assert p["fecha_consentimiento"] is not None and p["id_productor"].startswith("PRO-")
    [auditoria] = sistema.consulta("SELECT * FROM auditoria WHERE accion = 'REGISTRO_PRODUCTOR'")
    assert auditoria["origen"] == "SISTEMA" and TELEFONO not in str(auditoria) and "3001112233" not in str(auditoria)
    assert sistema.paso() == "INICIO"  # sin fincas no hay consulta: la ANUC debe registrarlas


def test_un_productor_del_panel_sin_consentimiento_debe_aceptar(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, consentimiento=False)
    assert sistema.escribir("hola") == [t.SALUDO_CON_NOMBRE.format(nombre="Ana"), t.CONSENTIMIENTO]
    respuestas = sistema.escribir("sí")
    assert sistema.consulta("SELECT fecha_consentimiento AS f FROM productores")[0]["f"] is not None
    assert respuestas[-1] == t.PEDIR_AFECTACION and sistema.paso() == "AFECTACION"


def test_un_productor_deshabilitado_no_abre_consultas(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, activo=False)
    assert sistema.escribir("hola") == [t.CUENTA_DESHABILITADA]
    assert sistema.consulta("SELECT 1 FROM casos") == []


def test_un_productor_sin_fincas_es_remitido_a_la_anuc(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, fincas=0)
    assert sistema.escribir("hola")[-1] == t.SIN_FINCAS and sistema.paso() == "INICIO"


# --- finca, cultivo, especie y zona --------------------------------------------------------------------------------


def test_con_una_sola_finca_cultivo_y_especie_se_eligen_solos(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor)
    respuestas = junto(sistema.escribir("hola"))
    assert "La Esperanza" in respuestas and "Lote 1" in respuestas and t.PEDIR_AFECTACION in respuestas


def test_varias_fincas_se_eligen_por_numero_o_por_nombre(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, fincas=2)
    assert "1. Finca 1\n2. Finca 2" in junto(sistema.escribir("hola"))
    assert sistema.escribir("7")[0].startswith("No te entendí") and sistema.paso() == "ELEGIR_FINCA"
    assert sistema.escribir("la que no existe")[0].startswith("No te entendí")
    sistema.escribir("finca 1")  # por nombre
    assert sistema.paso() == "AFECTACION"


def test_varios_cultivos_y_varias_especies(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, cultivos=(("Lote 1", ("Gulupa",)), ("Lote 2", ("Gulupa", "Maracuyá"))))
    assert "2. Lote 2" in junto(sistema.escribir("hola")) and sistema.paso() == "ELEGIR_CULTIVO"
    respuestas = sistema.escribir("2")
    assert "Gulupa" in junto(respuestas) and "Maracuyá" in junto(respuestas) and sistema.paso() == "ELEGIR_ESPECIE"
    assert sistema.escribir("9")[0].startswith("No te entendí")
    assert sistema.escribir("Maracuyá") == [t.PEDIR_AFECTACION]
    assert sistema.store.obtener(TELEFONO).contexto["especie"] == "MARACUYA"  # type: ignore[union-attr]


def test_una_finca_sin_cultivos_se_registra_desde_whatsapp(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, cultivos=())
    assert sistema.escribir("hola")[-1] == t.SIN_CULTIVOS and sistema.paso() == "CULTIVO_NOMBRE"
    assert sistema.escribir("Lote Norte") == [t.PEDIR_TIPO_CULTIVO]
    assert sistema.escribir("a ver")[0].startswith("No te entendí")
    sistema.escribir("1")
    assert sistema.escribir("ninguna") == [t.ESPECIES_INVALIDAS] and sistema.paso() == "CULTIVO_ESPECIES"
    respuestas = sistema.escribir("1, 3")
    [cultivo] = sistema.consulta("SELECT * FROM cultivos")
    assert (cultivo["nombre_cultivo"], cultivo["tipo_cultivo"], cultivo["id_finca"]) == (
        "Lote Norte",
        "INVERNADERO",
        "FIN-000001",
    )
    assert (float(cultivo["latitud_cultivo"]), float(cultivo["longitud_cultivo"])) == (
        5.25,
        -73.17,
    )  # la ubicación de su finca
    especies = sistema.consulta(
        "SELECT es.nombre_especie AS n FROM cultivo_especie ce JOIN especies es USING (id_especie) ORDER BY 1"
    )
    assert [e["n"] for e in especies] == ["Gulupa", "Maracuyá"]
    assert t.CULTIVO_CREADO.format(nombre="Lote Norte") in respuestas and sistema.paso() == "ELEGIR_ESPECIE"


def test_un_nombre_de_cultivo_vacio_o_demasiado_largo_se_rechaza(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor, cultivos=())
    sistema.escribir("hola")
    for malo in ("   ", "x" * 101):
        assert sistema.escribir(malo) == [t.NOMBRE_CULTIVO_INVALIDO] and sistema.paso() == "CULTIVO_NOMBRE"
    assert sistema.consulta("SELECT 1 FROM cultivos") == []


def test_la_afectacion_total_no_se_asume_y_abre_el_caso(sistema: Sistema) -> None:
    hasta_afectacion(sistema)
    assert sistema.escribir("quizás")[0].startswith("No te entendí")
    assert sistema.consulta("SELECT 1 FROM casos") == []  # sin responder, no hay caso
    sistema.escribir("1")
    [caso] = sistema.consulta("SELECT * FROM casos")
    assert caso["descripcion_zona_afectada"] == t.ZONA_TOTAL and sistema.estado_caso() == "En análisis"


def test_la_afectacion_parcial_pide_la_zona(sistema: Sistema) -> None:
    hasta_afectacion(sistema)
    assert sistema.escribir("2") == [t.PEDIR_ZONA] and sistema.paso() == "ZONA"
    assert sistema.escribir("x" * 301) == [t.ZONA_INVALIDA]
    sistema.escribir("junto a la quebrada")
    assert sistema.consulta("SELECT descripcion_zona_afectada AS z FROM casos")[0]["z"] == "junto a la quebrada"


def test_el_caso_nace_con_su_evento_y_su_diagnostico_vacio(sistema: Sistema) -> None:
    hasta_afectacion(sistema)
    sistema.escribir("1")
    tipos = [
        e["tipo_evento"] for e in sistema.consulta("SELECT tipo_evento FROM eventos ORDER BY fecha_evento, id_evento")
    ]
    assert "CASO_CREADO" in tipos and "ANALISIS_CHATBOT" in tipos
    creado = sistema.consulta("SELECT * FROM eventos WHERE tipo_evento = 'CASO_CREADO'")[0]
    assert (creado["actor_tipo"], creado["actor_id"]) == ("PRODUCTOR", "PRO-000001")
    [diag] = sistema.consulta("SELECT * FROM diagnosticos")
    assert diag["resultado_tecnico"] is None  # D-05: la fila nace vacía


# --- el cuestionario lo conduce la IA --------------------------------------------------------------------------------------


def test_la_ia_hace_las_preguntas_y_la_app_solo_las_envia(sistema: Sistema) -> None:
    respuestas = hasta_cuestionario(
        sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2), Turno(completo=True)
    )
    assert respuestas == [t.CUESTIONARIO_INICIO, PREGUNTA_1] and sistema.paso() == "CUESTIONARIO"
    assert sistema.escribir("hace tres días") == [PREGUNTA_2]
    assert t.PEDIR_FOTOS.format(por_tipo=3, total=12) in sistema.escribir("sí, en las nuevas")[0]
    assert sistema.paso() == "FOTOS"
    historial = [
        (r["texto_pregunta"], r["texto_respuesta"])
        for r in sistema.consulta(
            "SELECT p.texto_pregunta, r.texto_respuesta FROM respuestas r JOIN preguntas p USING (id_pregunta) ORDER BY r.id_respuesta"
        )
    ]
    assert historial == [(PREGUNTA_1, "hace tres días"), (PREGUNTA_2, "sí, en las nuevas")]


def test_la_ia_recibe_el_contexto_del_caso_y_el_historial(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2))
    sistema.escribir("hace tres días")
    primera, segunda = sistema.ai.llamadas
    assert primera[1] is None and primera[0].pregunta_pendiente is None  # arranque del cuestionario
    assert (primera[0].especie, primera[0].cultivo, primera[0].zona_afectada) == ("GULUPA", "Lote 1", t.ZONA_TOTAL)
    assert segunda[1] == "hace tres días" and segunda[0].pregunta_pendiente == PREGUNTA_1


def test_lo_irrelevante_no_se_guarda_en_el_caso(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2, relevante=False))
    sistema.escribir("mi vecino tiene perro")
    assert sistema.consulta("SELECT 1 FROM respuestas") == []


def test_lo_que_no_es_del_dominio_se_rechaza_y_se_repite_la_pregunta(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(fuera_de_dominio=True, relevante=False))
    assert sistema.escribir("¿quién ganó el partido?") == [t.FUERA_DE_DOMINIO, PREGUNTA_1]
    assert sistema.paso() == "CUESTIONARIO" and sistema.consulta("SELECT 1 FROM respuestas") == []


def test_si_la_ia_propone_un_tratamiento_se_bloquea(sistema: Sistema) -> None:
    """El chatbot nunca receta: se corta el cuestionario y el caso sigue su curso hacia el técnico."""
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta="Aplique 2 ml de insecticida y cuénteme"))
    respuestas = sistema.escribir("hace tres días")
    assert t.PREGUNTA_BLOQUEADA in respuestas and "insecticida" not in junto(respuestas)
    assert sistema.paso() == "FOTOS"


def test_el_cuestionario_tiene_un_tope_de_preguntas(sistema: Sistema) -> None:
    from collections import deque

    programar(sistema)
    sistema.ai.turnos = deque(Turno(pregunta=f"Pregunta {n}") for n in range(40))
    hasta_afectacion(sistema)
    sistema.escribir("1")
    for n in range(40):
        if sistema.paso() != "CUESTIONARIO":
            break
        sistema.escribir(f"respuesta {n}")
    assert sistema.paso() == "FOTOS" and len(sistema.consulta("SELECT 1 FROM respuestas")) == 15


def test_una_foto_durante_el_cuestionario_repite_la_pregunta(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    assert sistema.foto() == [PREGUNTA_1] and sistema.paso() == "CUESTIONARIO"


def test_sin_ia_programada_el_cuestionario_se_da_por_completo(sistema: Sistema) -> None:
    """Con la IA simulada (sin turnos) no se inventan preguntas del dominio: se pasa directo a las fotos."""
    respuestas = hasta_cuestionario(sistema)
    assert respuestas[-1].startswith("Ahora envíame fotos") and sistema.paso() == "FOTOS"


# --- fotos y envío -------------------------------------------------------------------------------------------------------


def a_fotos(sistema: Sistema) -> None:
    hasta_cuestionario(sistema)


def test_las_fotos_se_aceptan_y_se_guardan(sistema: Sistema) -> None:
    a_fotos(sistema)
    assert sistema.foto("m1", "foto del fruto") == [t.FOTO_ACEPTADA.format(n=1, total=12)]
    assert sistema.foto("m2", "el tallo") == [t.FOTO_ACEPTADA.format(n=2, total=12)]
    fotos = sistema.consulta(
        "SELECT tipo_evidencia AS t, es_util AS u, ruta_fotografia AS r FROM fotografias ORDER BY id_fotografia"
    )
    assert [(f["t"], f["u"]) for f in fotos] == [("FRUTO", "S"), ("TALLO", "S")] and fotos[0]["r"].endswith("m1.webp")


def test_una_foto_rechazada_no_se_guarda_y_se_pide_otra(sistema: Sistema) -> None:
    a_fotos(sistema)
    sistema.pipeline.rechazar["mala"] = "está desenfocada"
    assert sistema.foto("mala") == [t.FOTO_RECHAZADA.format(motivo="está desenfocada")]
    assert sistema.consulta("SELECT 1 FROM fotografias") == []


def test_solo_tres_fotos_por_tipo(sistema: Sistema) -> None:
    a_fotos(sistema)
    for n in range(3):
        assert "Recibí la foto" in sistema.foto(f"h{n}", "hoja")[0]
    cuarta = sistema.foto("h4", "hoja")
    assert cuarta == [t.LIMITE_POR_TIPO.format(por_tipo=3)] and len(sistema.consulta("SELECT 1 FROM fotografias")) == 3
    [eliminada] = sistema.pipeline.eliminadas  # la foto de más ya estaba guardada: no debe quedar huérfana
    assert eliminada.endswith("/h4.webp")
    assert "Recibí la foto" in sistema.foto("f1", "fruto")[0]  # otro tipo sí


def test_con_doce_fotos_utiles_se_pasa_solo_al_resumen(sistema: Sistema) -> None:
    a_fotos(sistema)
    for tipo in ("hoja", "fruto", "tallo", "envés"):
        for n in range(3):
            respuestas = sistema.foto(f"{tipo}{n}", tipo)
    assert t.LIMITE_TOTAL.format(total=12) in respuestas and "Resumen de tu consulta" in junto(respuestas)
    assert sistema.paso() == "CONFIRMAR_ENVIO" and len(sistema.consulta("SELECT 1 FROM fotografias")) == 12


def test_sin_fotos_no_se_puede_continuar(sistema: Sistema) -> None:
    a_fotos(sistema)
    assert sistema.escribir("listo") == [t.FALTA_FOTO] and sistema.paso() == "FOTOS"
    assert sistema.escribir("hola otra vez") == [t.SOLO_FOTOS]


def test_el_resumen_y_la_confirmacion(sistema: Sistema) -> None:
    a_fotos(sistema)
    sistema.foto("m1")
    resumen = junto(sistema.escribir("LISTO"))
    for esperado in ("La Esperanza", "Lote 1", "Gulupa", t.ZONA_TOTAL, "Fotos: 1"):
        assert esperado in resumen
    assert sistema.escribir("tal vez") == [t.SI_O_NO]
    assert sistema.escribir("no") == [t.NO_ENVIADO] and sistema.paso() == "FOTOS"


def test_recorrido_completo_hasta_pendiente_de_revision(sistema: Sistema) -> None:
    programar(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2), Turno(completo=True))
    productor_con_finca(sistema.motor)
    sistema.escribir("hola")
    sistema.escribir("2")
    sistema.escribir("esquina de abajo")
    sistema.escribir("hace una semana")
    sistema.escribir("sí")
    sistema.foto("m1", "fruto")
    sistema.escribir("listo")
    respuestas = sistema.escribir("sí")
    assert respuestas[0] == t.ENVIADO and "preliminar" in respuestas[0]
    assert sistema.estado_caso() == "Pendiente de revisión"
    [caso] = sistema.consulta("SELECT * FROM casos")
    assert (
        caso["fecha_envio_panel"] is not None and caso["id_identificacion_preliminar"] is None
    )  # D-03: puede ir sin análisis
    assert sistema.store.obtener(TELEFONO) is None  # la conversación terminó
    tipos = {e["tipo_evento"] for e in sistema.consulta("SELECT tipo_evento FROM eventos")}
    assert {"CASO_CREADO", "ANALISIS_CHATBOT", "ENVIADO_A_REVISION"} <= tipos


def test_el_caso_nuevo_avisa_a_los_lideres_activos_en_la_campana(sistema: Sistema) -> None:
    """B-10: llega al panel sin técnico → aviso CASO_SIN_TECNICO a cada líder activo, no a los deshabilitados."""
    NOTIFICACIONES.vaciar()
    with Session(sistema.motor) as sesion:
        lideres = [
            LiderTecnico(
                nombre_lider_tecnico=nombre,
                correo_lider_tecnico=f"{nombre.lower()}@anuc.test",
                celular_lider_tecnico=f"57300000000{i}",
                contrasena_lider_tecnico="hash-inutilizable",
                estado_cuenta_lider_tecnico=estado,
            )
            for i, (nombre, estado) in enumerate([("Laura", "ACTIVO"), ("Dora", "DESHABILITADO")])
        ]
        sesion.add_all(lideres)
        sesion.commit()
        activo, deshabilitado = (x.id_lider_tecnico for x in lideres)
    programar(sistema, Turno(completo=True))
    productor_con_finca(sistema.motor)
    for texto in ("hola", "2", "esquina de abajo", "hace una semana", "sí"):
        sistema.escribir(texto)
    sistema.foto("m1", "fruto")
    sistema.escribir("listo")
    sistema.escribir("sí")
    [caso] = sistema.consulta("SELECT id_caso FROM casos")
    desde = datetime.now(UTC) - timedelta(minutes=5)
    [aviso] = NOTIFICACIONES.listar(activo, desde)
    assert aviso.tipo == "CASO_SIN_TECNICO" and aviso.enlace == f"/casos/{caso['id_caso']}"
    assert NOTIFICACIONES.listar(deshabilitado, desde) == []


def test_un_caso_no_llega_al_panel_sin_foto_valida(sistema: Sistema) -> None:
    """Aunque el estado de la conversación se corrompa, la regla se vuelve a comprobar al enviar."""
    a_fotos(sistema)
    conv = sistema.store.obtener(TELEFONO)
    assert conv is not None
    conv.paso = "CONFIRMAR_ENVIO"
    sistema.store.guardar(conv)
    assert sistema.escribir("sí") == [t.FALTA_FOTO]
    assert sistema.estado_caso() == "En análisis" and sistema.paso() == "FOTOS"


def test_un_caso_no_llega_al_panel_con_el_cuestionario_incompleto(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    sistema.foto("m1")  # una foto en pleno cuestionario no avanza ni cuenta
    conv = sistema.store.obtener(TELEFONO)
    assert conv is not None
    conv.paso = "CONFIRMAR_ENVIO"
    sistema.store.guardar(conv)
    assert sistema.escribir("sí") == [t.FALTA_FOTO] and sistema.estado_caso() == "En análisis"


def test_si_hay_identificacion_se_guarda_y_se_cuenta_al_productor(sistema: Sistema) -> None:
    sistema.identificador.resultado = Identificacion(
        "PRELIMINAR", "Coinciden los síntomas", {"TRIPS": 80.0, "BOTRYTIS": 20.0}
    )
    a_fotos(sistema)
    sistema.foto("m1")
    sistema.escribir("listo")
    respuestas = sistema.escribir("sí")
    assert t.RESULTADO_PRELIMINAR.format(resultado="Trips") in respuestas
    [ident] = sistema.consulta("SELECT * FROM identificacion_preliminar")
    assert ident["tipo_resultado"] == "PRELIMINAR"
    assert len(sistema.consulta("SELECT 1 FROM coincidencias_categoria")) == 2
    assert sistema.consulta("SELECT id_identificacion_preliminar AS i FROM casos")[0]["i"] == ident["id_identificacion"]


# --- /salir -----------------------------------------------------------------------------------------------------------------


def test_salir_sin_consulta_abierta(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor)
    assert sistema.escribir("/salir") == [t.NO_HAY_CONSULTA]


def test_salir_pide_confirmacion_y_se_puede_cancelar(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    assert sistema.escribir("/salir") == [t.CONFIRMAR_SALIR] and sistema.paso() == "CONFIRMAR_SALIR"
    assert sistema.escribir("a ver") == [t.SI_O_NO]
    assert sistema.escribir("No") == [t.SALIR_CANCELADO, PREGUNTA_1]  # sigue donde estaba, con su pregunta
    assert sistema.paso() == "CUESTIONARIO" and sistema.estado_caso() == "En análisis"


def test_salir_confirmado_borra_todo_el_caso(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(completo=True))
    sistema.escribir("hace tres días")
    sistema.foto("m1", "fruto")
    id_caso = sistema.consulta("SELECT id_caso FROM casos")[0]["id_caso"]
    sistema.escribir("/SALIR")
    assert sistema.escribir("sí") == [t.SALIR_HECHO]
    for tabla in ("casos", "respuestas", "fotografias", "diagnosticos", "eventos", "asignaciones"):
        assert sistema.consulta(f"SELECT 1 FROM {tabla}") == [], tabla
    assert sistema.paso() is None and sistema.s3.borradas == [f"simulado/{id_caso}/m1.webp"]
    [auditoria] = sistema.consulta("SELECT * FROM auditoria WHERE accion = 'BORRAR_SALIR'")
    assert auditoria["origen"] == "SISTEMA" and TELEFONO not in str(auditoria)
    assert len(sistema.consulta("SELECT 1 FROM productores")) == 1  # el productor se conserva


def test_salir_funciona_en_cualquier_paso_del_caso(sistema: Sistema) -> None:
    hasta_cuestionario(sistema)
    sistema.foto("m1")
    sistema.escribir("listo")  # ya en el resumen
    assert sistema.paso() == "CONFIRMAR_ENVIO"
    sistema.escribir("/salir")
    sistema.escribir("no")
    assert sistema.paso() == "CONFIRMAR_ENVIO"  # vuelve y vuelve a mostrar el resumen
    sistema.escribir("/salir")
    sistema.escribir("sí")
    assert sistema.consulta("SELECT 1 FROM casos") == []


# --- una consulta ya abierta y reanudación ------------------------------------------------------------------------------------


def test_con_una_consulta_abierta_se_pregunta_si_se_abandona(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    sistema.store.borrar(TELEFONO)  # se perdió el estado de la conversación; la consulta sigue en la base
    assert sistema.escribir("hola")[-1] == t.CONFIRMAR_ABANDONO and sistema.paso() == "CONFIRMAR_ABANDONO"
    assert sistema.escribir("quizá") == [t.SI_O_NO]


def test_abandonar_descarta_la_anterior_y_empieza_otra(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    sistema.store.borrar(TELEFONO)
    sistema.escribir("hola")
    respuestas = sistema.escribir("sí")
    assert t.ABANDONO_HECHO in respuestas and sistema.consulta("SELECT 1 FROM casos") == []
    assert sistema.consulta("SELECT 1 FROM auditoria WHERE accion = 'BORRAR_ABANDONO'")
    assert sistema.paso() == "AFECTACION"


def test_no_abandonar_continua_la_consulta_con_la_ia(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2))
    sistema.store.borrar(TELEFONO)
    sistema.escribir("hola")
    respuestas = sistema.escribir("no")
    assert respuestas == [t.CONTINUAR_EXISTENTE, PREGUNTA_2] and sistema.paso() == "CUESTIONARIO"
    assert len(sistema.consulta("SELECT 1 FROM casos")) == 1


def test_cada_mensaje_reinicia_el_conteo_de_inactividad(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2))
    avanzar_inactividad(sistema.motor, 30)
    antes = sistema.consulta("SELECT ultima_actividad AS u FROM casos")[0]["u"]
    sistema.escribir("hace tres días")
    despues = sistema.consulta("SELECT ultima_actividad AS u FROM casos")[0]["u"]
    assert (
        despues > antes
        and sistema.consulta("SELECT now() - ultima_actividad < interval '1 minute' AS ok FROM casos")[0]["ok"]
    )


def test_responder_tras_la_advertencia_de_48_horas_retoma_donde_quedo(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1), Turno(pregunta=PREGUNTA_2))
    avanzar_inactividad(sistema.motor, 50)
    with sistema.motor.begin() as c:
        c.execute(sql("UPDATE casos SET advertencia_inactividad_enviada = 'S'"))
    assert sistema.escribir("hace tres días") == [PREGUNTA_2]  # la conversación sigue en el mismo paso
    [caso] = sistema.consulta("SELECT advertencia_inactividad_enviada AS a FROM casos")
    assert caso["a"] == "N"  # la advertencia se reinicia: si vuelve a callar, se avisará de nuevo


def test_si_el_job_descarto_la_consulta_se_empieza_de_nuevo(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    with sistema.motor.begin() as c:  # lo que hace el job de 72 h
        for tabla in ("respuestas", "diagnosticos", "eventos"):
            c.execute(sql(f"DELETE FROM {tabla}"))
        c.execute(sql("DELETE FROM casos"))
    respuestas = sistema.escribir("hace tres días")
    assert respuestas[0] == t.CONSULTA_VENCIDA and sistema.paso() == "AFECTACION"


# --- estado, efectos y duplicados ------------------------------------------------------------------------------------------------


def test_el_estado_de_la_conversacion_persiste_entre_mensajes(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    conv = sistema.store.obtener(TELEFONO)
    assert conv is not None and conv.paso == "CUESTIONARIO"
    assert conv.caso_id == sistema.consulta("SELECT id_caso FROM casos")[0]["id_caso"]
    assert conv.contexto["pregunta_actual"] == PREGUNTA_1 and conv.contexto["finca_nombre"] == "La Esperanza"


def test_si_algo_falla_no_sale_ninguna_respuesta_ni_cambia_el_estado(sistema: Sistema) -> None:
    hasta_cuestionario(sistema, Turno(pregunta=PREGUNTA_1))
    antes = sistema.store.obtener(TELEFONO)
    enviados = len(sistema.whatsapp.enviados)

    def explota(contexto: Any, mensaje: Any) -> Turno:
        raise RuntimeError("la IA no responde")

    sistema.ai.conducir = explota  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        sistema.escribir("hace tres días")
    assert len(sistema.whatsapp.enviados) == enviados and sistema.store.obtener(TELEFONO) == antes
    assert (
        sistema.consulta("SELECT 1 FROM respuestas") == []
        and sistema.consulta("SELECT 1 FROM mensajes_whatsapp WHERE wamid = 'wamid.4'") == []
    )


def test_un_mensaje_repetido_no_genera_dos_respuestas(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor)
    cuerpo = json.dumps(
        {"wamid": "wamid.X", "from": TELEFONO, "type": "text", "timestamp": "1", "content": {"text": "hola"}}
    )
    sistema.procesador.procesar(cuerpo)
    enviados = len(sistema.whatsapp.enviados)
    sistema.procesador.procesar(cuerpo)
    assert len(sistema.whatsapp.enviados) == enviados and enviados > 0


def test_otro_telefono_tiene_su_propia_conversacion(sistema: Sistema) -> None:
    productor_con_finca(sistema.motor)
    sistema.escribir("hola")
    assert sistema.escribir("hola", telefono="573009998877") == [t.SALUDO, t.CONSENTIMIENTO]
    assert sistema.paso() == "AFECTACION" and sistema.paso("573009998877") == "CONSENTIMIENTO"


def test_un_estado_guardado_de_una_version_anterior_no_rompe(sistema: Sistema) -> None:
    from app.infrastructure.conversaciones_store import Conversacion

    productor_con_finca(sistema.motor)
    sistema.store.guardar(Conversacion(TELEFONO, "PASO_QUE_YA_NO_EXISTE"))
    assert sistema.escribir("hola")[-1] == t.PEDIR_AFECTACION


def test_si_whatsapp_falla_al_responder_el_mensaje_ya_quedo_procesado(
    sistema: Sistema, caplog: pytest.LogCaptureFixture
) -> None:
    productor_con_finca(sistema.motor)
    sistema.whatsapp.fallan.add(TELEFONO)
    sistema.escribir("hola")
    assert sistema.paso() == "AFECTACION"  # el estado sí avanzó: el mensaje se procesó
    assert "No se pudo enviar la respuesta" in caplog.text and TELEFONO not in caplog.text
