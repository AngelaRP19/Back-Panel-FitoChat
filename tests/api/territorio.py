"""Ayudas de las pruebas del módulo 3: crear productores, fincas, cultivos y casos reales con datos confirmados."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.core.catalogos_api import CATEGORIAS, ESPECIES
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import (
    Alerta,
    AlertaCaso,
    Caso,
    CoincidenciaCategoria,
    Diagnostico,
    Fotografia,
    IdentificacionPreliminar,
    Pregunta,
    Respuesta,
    Tecnico,
)
from tests.api.conftest import Cuenta
from tests.infrastructure.db.factories import asignar

PRODUCTORES = "/api/v1/productores"
FINCAS = "/api/v1/fincas"


def crear_productor(api: TestClient, lider: Cuenta, n: int = 1, **extra: Any) -> dict[str, Any]:
    cuerpo = {"nombre": f"Productor {n}", "celular": f"3001112{n:03d}", **extra}
    r = api.post(PRODUCTORES, json=cuerpo, headers=lider.headers)
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_finca(
    api: TestClient,
    lider: Cuenta,
    id_productor: str,
    nombre: str = "La Esperanza",
    vereda: str = "Centro",
    latitud: float = 5.25,
    longitud: float = -73.17,
) -> dict[str, Any]:
    cuerpo = {
        "nombre": nombre,
        "vereda": vereda,
        "latitud": latitud,
        "longitud": longitud,
        "productor_id": id_productor,
    }
    r = api.post(FINCAS, json=cuerpo, headers=lider.headers)
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_cultivo(
    api: TestClient,
    lider: Cuenta,
    id_finca: str,
    nombre: str = "Lote 1",
    tipo: str = "CAMPO_ABIERTO",
    especies: tuple[str, ...] = ("GULUPA",),
) -> dict[str, Any]:
    """Devuelve la finca actualizada (así responde `POST /fincas/{id}/cultivos`)."""
    r = api.post(
        f"{FINCAS}/{id_finca}/cultivos",
        json={"nombre": nombre, "tipo": tipo, "especies": list(especies)},
        headers=lider.headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def crear_caso(
    motor: Engine,
    id_cultivo: str,
    especie: str = "GULUPA",
    estado: str = "En revisión",
    *,
    tecnico: str | None = None,
    vigente: bool = True,
    dias_atras: int = 0,
    enviado_hace: timedelta | None = None,
    vence_en: date | None = None,
    zona: str | None = None,
) -> str:
    """Caso real del cultivo y la especie (el par debe existir); con `tecnico`, asignado a esa cuenta."""
    with Session(motor) as sesion:
        id_especie = sesion.scalar(
            text("SELECT id_especie FROM especies WHERE nombre_especie = :n"), {"n": ESPECIES.a_nombre(especie)}
        )
        caso = Caso(
            id_cultivo=id_cultivo,
            id_especie=id_especie,
            id_estado=str(sesion.scalar(sp.ID_ESTADO, {"nombre": estado})),
            fecha_creacion_caso=datetime.now(UTC) - timedelta(days=dias_atras),
            fecha_envio_panel=None if enviado_hace is None else datetime.now(UTC) - enviado_hace,
            vence_en=vence_en,
            descripcion_zona_afectada=zona,
        )
        sesion.add(caso)
        sesion.flush()
        if tecnico is not None:
            asignar(sesion, caso, sesion.get(Tecnico, tecnico), vigente=vigente)  # type: ignore[arg-type]
        sesion.commit()
        return caso.id_caso


def crear_identificacion(
    motor: Engine,
    id_caso: str,
    resultado: str = "PRELIMINAR",
    porcentajes: dict[str, float] | None = None,
    detalle: dict[str, Any] | None = None,
    texto: str = "Resultado preliminar de prueba",
) -> str:
    """Identificación preliminar del caso, con el porcentaje de coincidencia de cada categoría (códigos de la API)."""
    with Session(motor) as sesion:
        identificacion = IdentificacionPreliminar(
            tipo_resultado=resultado,
            explicacion_resultado=texto,
            fecha_analisis=datetime.now(UTC),
            detalle_explicacion=detalle,
        )
        sesion.add(identificacion)
        sesion.flush()
        for codigo, porcentaje in (porcentajes or {}).items():
            id_categoria = sesion.scalar(
                text("SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = :n"),
                {"n": CATEGORIAS.a_nombre(codigo)},
            )
            sesion.add(
                CoincidenciaCategoria(
                    id_identificacion=identificacion.id_identificacion,
                    id_categoria=id_categoria,
                    porcentaje_coincidencia=Decimal(str(porcentaje)),
                )
            )
        sesion.flush()
        sesion.execute(
            text("UPDATE casos SET id_identificacion_preliminar = :i WHERE id_caso = :c"),
            {"i": identificacion.id_identificacion, "c": id_caso},
        )
        sesion.commit()
        return identificacion.id_identificacion


def crear_respuesta(
    motor: Engine,
    id_caso: str,
    pregunta: str,
    respuesta: str,
    fotos: tuple[tuple[str, str, bool], ...] = (),
    hace: timedelta = timedelta(hours=1),
) -> None:
    """Una respuesta del cuestionario con sus fotos `(tipo, clave en S3, útil)`; crea el diagnóstico vacío si falta."""
    with Session(motor) as sesion:
        diagnostico = sesion.scalar(select(Diagnostico).where(Diagnostico.id_caso == id_caso))
        if diagnostico is None:
            diagnostico = Diagnostico(id_caso=id_caso)
            sesion.add(diagnostico)
            sesion.flush()
        pregunta_fila = Pregunta(texto_pregunta=pregunta)
        sesion.add(pregunta_fila)
        sesion.flush()
        cuando = datetime.now(UTC) - hace
        fila = Respuesta(
            id_pregunta=pregunta_fila.id_pregunta,
            id_diagnostico=diagnostico.id_diagnostico,
            texto_respuesta=respuesta,
            fecha_respuesta=cuando,
        )
        sesion.add(fila)
        sesion.flush()
        for orden, (tipo, clave, util) in enumerate(fotos):
            sesion.add(
                Fotografia(
                    id_respuesta=fila.id_respuesta,
                    tipo_evidencia=tipo,
                    ruta_fotografia=clave,
                    fecha_envio_fotografia=cuando + timedelta(seconds=orden),
                    es_util="S" if util else "N",
                )
            )
        sesion.commit()


def registrar_diagnostico(
    motor: Engine, id_caso: str, resultado: str = "CONFIRMADO", categoria: str | None = "TRIPS", descripcion: str = ""
) -> None:
    """Completa el diagnóstico vacío (el caso debe tener técnico activo asignado)."""
    with Session(motor) as sesion:
        if sesion.scalar(select(Diagnostico.id_diagnostico).where(Diagnostico.id_caso == id_caso)) is None:
            sesion.add(Diagnostico(id_caso=id_caso))
            sesion.flush()
        sesion.execute(
            text(
                "UPDATE diagnosticos SET resultado_tecnico = :r, fecha_diagnostico = now(), "
                "id_categoria_confirmada = (SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = :c), "
                "descripcion_diagnostico = :d WHERE id_caso = :caso"
            ),
            {
                "r": resultado,
                "c": CATEGORIAS.a_nombre(categoria) if categoria else None,
                "d": descripcion,
                "caso": id_caso,
            },
        )
        sesion.commit()


def crear_alerta(
    motor: Engine,
    casos: list[str],
    estado: str = "GENERADA",
    categoria: str = "TRIPS",
    km: float = 3.5,
    minimo: int = 3,
    dias: int = 30,
    **extra: Any,
) -> str:
    """Alerta con sus casos de origen y los parámetros con que se generó (sin pasar por `sp_evaluar_alerta`)."""
    with Session(motor) as sesion:
        id_categoria = sesion.scalar(
            text("SELECT id_categoria FROM categorias_fitosanitarias WHERE nombre_categoria = :n"),
            {"n": CATEGORIAS.a_nombre(categoria)},
        )
        alerta = Alerta(
            id_categoria=id_categoria,
            estado_alerta=estado,
            fecha_generacion_alerta=datetime.now(UTC),
            numero_minimo_casos=minimo,
            distancia_km=Decimal(str(km)),
            intervalo_dias=dias,
            **extra,
        )
        sesion.add(alerta)
        sesion.flush()
        for id_caso in casos:
            sesion.add(AlertaCaso(id_alerta=alerta.id_alerta, id_caso=id_caso))
        sesion.commit()
        return alerta.id_alerta
