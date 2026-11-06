"""Armado del reporte de alertas epidemiológicas (R3), con las mismas secciones que muestra el panel.

Es una función pura: recibe las alertas, los parámetros vigentes y las fechas, y devuelve el reporte. Así se prueba
sin base de datos. Los textos son los del panel (pantalla 24).
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.catalogos_api import CATEGORIAS, EstadoAlerta, normalizar_codigo
from app.core.codigos import codigo_alerta
from app.modules.panel_api.alertas.repository import AlertaFila, ParametroFila
from app.modules.panel_api.reportes.filtros import FiltrosAlertas
from app.modules.panel_api.reportes.schemas import (
    ColumnaTabla,
    ItemBarra,
    ItemDato,
    KpiReporte,
    ReporteDatos,
    SeccionBarras,
    SeccionDatos,
    SeccionTabla,
)

BOGOTA = ZoneInfo("America/Bogota")
E = EstadoAlerta
NOMBRE_ESTADO = {
    E.GENERADA: "Generada",
    E.EN_REVISION: "En revisión",
    E.CONFIRMADA: "Confirmada",
    E.COMUNICADA: "Comunicada",
    E.DESCARTADA: "Descartada",
    E.CORREGIDA: "Corregida",
}
MESES_LARGOS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
SEGUNDOS_DIA = 86_400


def decimal(n: float | None) -> str:
    return "—" if n is None else f"{n:.1f}".replace(".", ",")


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def fecha_corta(d: date | datetime) -> str:
    d = d.astimezone(BOGOTA).date() if isinstance(d, datetime) else d
    return d.strftime("%d/%m/%Y")


def promedio(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _decision(a: AlertaFila) -> tuple[str, datetime] | None:
    """Quién decidió (confirmó o descartó) y cuándo; la alerta aún sin decisión no tiene."""
    if a.id_lider_gestion and a.fecha_confirmacion:
        return a.nombre_lider_gestion or "—", a.fecha_confirmacion
    return None


def filtrar(alertas: Sequence[AlertaFila], f: FiltrosAlertas) -> list[AlertaFila]:
    """Aplica los filtros F2 a las alertas ya acotadas por periodo."""
    salida = []
    for a in alertas:
        if f.estados and a.estado not in f.estados:
            continue
        if f.vereda and (not a.vereda or normalizar_codigo(a.vereda) != normalizar_codigo(f.vereda)):
            continue
        decision = _decision(a)
        if f.decidida == "sin" and decision:
            continue
        if f.decidida and f.decidida != "sin" and a.id_lider_gestion != f.decidida:
            continue
        if f.casos_min is not None and a.num_casos < f.casos_min:
            continue
        if f.comunicada == "si" and a.estado != E.COMUNICADA.value:
            continue
        if f.comunicada == "sin_comunicar" and a.estado != E.CONFIRMADA.value:
            continue
        salida.append(a)
    return salida


def _datos_parametros(parametros: Sequence[ParametroFila], editores: dict[str, tuple[str, str]]) -> SeccionDatos:
    activos = [p for p in parametros if p.transmisible]
    varias = len(activos) > 1
    items = [ItemDato(termino="Categorías que participan", valor=", ".join(p.nombre_categoria for p in activos) or "—")]
    for p in activos:
        sufijo = f" ({p.nombre_categoria})" if varias else ""
        editor = editores.get(CATEGORIAS.a_codigo(p.nombre_categoria))
        modificacion = (
            f"{fecha_corta(p.actualizado_en)}{f' · {editor[1]}' if editor else ''}" if p.actualizado_en else "—"
        )
        items += [
            ItemDato(
                termino=f"Mínimo de casos{sufijo}",
                valor="—" if p.num_casos is None else f"{p.num_casos} casos confirmados",
            ),
            ItemDato(
                termino=f"Distancia máxima{sufijo}",
                valor="—" if p.distancia_km is None else f"{decimal(float(p.distancia_km))} km",
            ),
            ItemDato(
                termino=f"Intervalo{sufijo}",
                valor="—" if p.intervalo_dias is None else f"{p.intervalo_dias} días hacia atrás",
            ),
            ItemDato(termino=f"Última modificación{sufijo}", valor=modificacion),
        ]
    excluidas = [p.nombre_categoria for p in parametros if not p.transmisible]
    return SeccionDatos(
        id="parametros",
        titulo="Parámetros vigentes",
        ancho="completo",
        items=items,
        nota=f"{', '.join(excluidas)} no participan en las alertas." if excluidas else None,
    )


def construir(
    alertas: Sequence[AlertaFila],
    parametros: Sequence[ParametroFila],
    editores: dict[str, tuple[str, str]],
    veredas: dict[str, str],
    desde: date,
    hasta: date,
) -> ReporteDatos:
    """`alertas` ya viene filtrada y ordenada de la más reciente a la más antigua; `veredas` es `{código: nombre}`."""
    n = Counter(a.estado for a in alertas)
    total = len(alertas)
    decididas = [(a, d) for a in alertas if (d := _decision(a))]
    dias = [(d[1] - a.generada_en).total_seconds() / SEGUNDOS_DIA for a, d in decididas]
    mes_hasta = (hasta.year, hasta.month)
    en_el_mes = sum(
        1
        for a in alertas
        if (a.generada_en.astimezone(BOGOTA).year, a.generada_en.astimezone(BOGOTA).month) == mes_hasta
    )
    confirmadas = n[E.CONFIRMADA.value] + n[E.COMUNICADA.value]
    avisados = sum(a.notificados for a in alertas)
    categorias = ", ".join(p.nombre_categoria for p in parametros if p.transmisible)
    descartadas = [a for a in alertas if a.estado == E.DESCARTADA.value]
    sin_revisar = [a for a in alertas if a.estado == E.GENERADA.value]

    kpis = [
        KpiReporte(
            rotulo="Alertas generadas",
            valor=str(total),
            pie=f"{en_el_mes} en {MESES_LARGOS[hasta.month - 1]}",
            tono="adv",
        ),
        KpiReporte(
            rotulo="Sin decisión",
            valor=str(n[E.GENERADA.value] + n[E.EN_REVISION.value]),
            pie=f"{n[E.GENERADA.value]} generada · {n[E.EN_REVISION.value]} en revisión",
            tono="revision",
        ),
        KpiReporte(
            rotulo="Confirmadas",
            valor=str(confirmadas),
            pie=f"{n[E.COMUNICADA.value]} ya comunicadas",
            tono="peligro",
        ),
        KpiReporte(
            rotulo="Días hasta decidir",
            valor=decimal(promedio(dias)),
            pie="promedio de las alertas decididas",
            tono="neutro",
        ),
        KpiReporte(rotulo="Productores avisados", valor=str(avisados), pie="mensajes de WhatsApp enviados", tono="ok"),
    ]

    nota_embudo = " · ".join(
        parte
        for parte in (
            f"{plural(len(descartadas), 'descartada', 'descartadas')} ("
            + "; ".join(
                f"{codigo_alerta(a.id)}: {(a.justificacion_descarte or 'sin motivo').lower()}" for a in descartadas
            )
            + ")"
            if descartadas
            else "",
            f"{len(sin_revisar)} sin revisar ({', '.join(codigo_alerta(a.id) for a in sin_revisar)})"
            if sin_revisar
            else "",
        )
        if parte
    )

    nombres_vereda = sorted(veredas.items(), key=lambda par: par[1])
    por_vereda = [
        {
            "vereda": nombre,
            "alertas": len(de := [a for a in alertas if a.vereda and normalizar_codigo(a.vereda) == codigo]),
            "confirmadas": sum(1 for a in de if a.estado in (E.CONFIRMADA.value, E.COMUNICADA.value)),
            "descartadas": sum(1 for a in de if a.estado == E.DESCARTADA.value),
            "casos": sum(a.num_casos for a in de),
        }
        for codigo, nombre in nombres_vereda
    ]

    por_mes = Counter((a.generada_en.astimezone(BOGOTA).year, a.generada_en.astimezone(BOGOTA).month) for a in alertas)

    def fila(a: AlertaFila) -> dict[str, str | int]:
        decision = _decision(a)
        dd = max(1, round((decision[1] - a.generada_en).total_seconds() / SEGUNDOS_DIA)) if decision else None
        return {
            "_alerta": a.id,
            "alerta": codigo_alerta(a.id),
            "detectada": fecha_corta(a.generada_en),
            "vereda": a.vereda or "—",
            "casos": a.num_casos,
            "estado": NOMBRE_ESTADO.get(EstadoAlerta(a.estado), a.estado),
            "decidio": decision[0] if decision else "—",
            "respuesta": "—" if dd is None else plural(dd, "día", "días"),
            "avisados": "0 · falta comunicar" if a.estado == E.CONFIRMADA.value else a.notificados,
        }

    secciones = [
        SeccionBarras(
            id="estados",
            titulo="Alertas por estado",
            subtitulo=plural(total, "alerta", "alertas"),
            ancho="mitad",
            items=[ItemBarra(etiqueta=nombre, valor=n[e.value]) for e, nombre in NOMBRE_ESTADO.items()],
        ),
        SeccionBarras(
            id="embudo",
            titulo="De la detección al aviso",
            subtitulo="alertas",
            ancho="mitad",
            items=[
                ItemBarra(etiqueta="Generadas por el sistema", valor=total),
                ItemBarra(etiqueta="Revisadas por el líder", valor=total - n[E.GENERADA.value]),
                ItemBarra(etiqueta="Confirmadas", valor=confirmadas),
                ItemBarra(etiqueta="Comunicadas a productores", valor=n[E.COMUNICADA.value]),
            ],
            nota=nota_embudo or None,
        ),
        SeccionTabla(
            id="veredas",
            titulo="Alertas por vereda",
            ancho="mitad",
            columnas=[
                ColumnaTabla(clave="vereda", titulo="Vereda"),
                ColumnaTabla(clave="alertas", titulo="Alertas", alinear="derecha"),
                ColumnaTabla(clave="confirmadas", titulo="Confirm.", alinear="derecha"),
                ColumnaTabla(clave="descartadas", titulo="Descart.", alinear="derecha"),
                ColumnaTabla(clave="casos", titulo="Casos", alinear="derecha"),
            ],
            filas=list(por_vereda),
        ),
        SeccionBarras(
            id="meses",
            titulo="Alertas por mes",
            subtitulo="generadas",
            ancho="mitad",
            items=[
                ItemBarra(etiqueta=f"{MESES_LARGOS[mes - 1]} {anio}", valor=cuantas)
                for (anio, mes), cuantas in sorted(por_mes.items())
            ],
        ),
        SeccionTabla(
            id="principal",
            titulo="Detalle de alertas",
            ancho="completo",
            columnas=[
                ColumnaTabla(clave="alerta", titulo="Alerta"),
                ColumnaTabla(clave="detectada", titulo="Detectada"),
                ColumnaTabla(clave="vereda", titulo="Vereda"),
                ColumnaTabla(clave="casos", titulo="Casos", alinear="derecha"),
                ColumnaTabla(clave="estado", titulo="Estado"),
                ColumnaTabla(clave="decidio", titulo="Decidió"),
                ColumnaTabla(clave="respuesta", titulo="Respuesta"),
                ColumnaTabla(clave="avisados", titulo="Avisados", alinear="derecha"),
            ],
            filas=[fila(a) for a in alertas],
            por_pagina=10,
            sustantivo="alertas",
            vacio="Ninguna alerta coincide con los filtros.",
        ),
        _datos_parametros(parametros, editores),
    ]
    return ReporteDatos(
        tipo="alertas",
        titulo="Reporte de alertas epidemiológicas",
        subtitulo=(
            f"{fecha_corta(desde)} a {fecha_corta(hasta)}"
            + (f" · posibles concentraciones de casos confirmados de {categorias}" if categorias else "")
        ),
        total=total,
        sustantivo="alertas",
        kpis=kpis,
        secciones=secciones,
    )
