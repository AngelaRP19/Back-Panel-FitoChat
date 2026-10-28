"""Reportes R1 (casos), R2 (identificación), R4 (productores) y R5 (técnicos) y el resumen (B-08, pantallas 23 y 24).

Funciones puras sobre la foto de `datos.py`, con las mismas secciones y textos que dibuja el panel (son el contrato de
`src/mocks/handlers/reportes.ts`). Diferencias con la simulación del panel, a propósito:

- Recepción → asignación usa la fecha real de la primera asignación (la simulación usaba 16 minutos fijos).
- «Uso del chatbot» (R4) solo muestra datos reales: casos creados, enviados al panel y descartados. Las conversaciones
  abandonadas antes de crear un caso no se guardan, así que no se inventan.
- R5 no tiene la columna ni el filtro de extensiones de conservación (D-07 rechazado).

Los filtros llegan como en la URL (`FiltrosReporte`: texto, listas separadas por coma). Fechas en hora de Colombia.
"""

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.modules.panel_api.reportes.datos import CasoR, CuentaR, Datos, ProductorR
from app.modules.panel_api.reportes.schemas import (
    ColumnaTabla,
    ItemBarra,
    KpiReporte,
    ReporteDatos,
    ResumenReportes,
    Seccion,
    SeccionBarras,
    SeccionTabla,
    TarjetaReporte,
)

FiltrosReporte = dict[str, str]
BOGOTA = ZoneInfo("America/Bogota")
DIA = 86_400
PERIODO_PREDETERMINADO = "anio"
MAXIMO_DIAS = 366

VEREDAS = ["JURACAMBITA", "CENTRO", "SAN_LUIS"]
ESPECIES = ["GULUPA", "GRANADILLA", "MARACUYA"]
ESPECIE = {"GULUPA": "Gulupa", "GRANADILLA": "Granadilla", "MARACUYA": "Maracuyá"}
CATEGORIAS = ["TRIPS", "BOTRYTIS", "RONA", "FUSARIUM", "DEFICIENCIA_MAGNESIO"]
CATEGORIA = {
    "TRIPS": "Trips",
    "BOTRYTIS": "Botrytis",
    "RONA": "Roña",
    "FUSARIUM": "Fusarium",
    "DEFICIENCIA_MAGNESIO": "Def. magnesio",
}
ESTADOS = [
    ("CREADO", "Caso creado"),
    ("EN_ANALISIS", "En análisis"),
    ("PENDIENTE_REVISION", "Pendiente de revisión"),
    ("EN_REVISION", "En revisión"),
    ("REGISTRADO", "Registrado"),
    ("CERRADO", "Cerrado"),
    ("DESCARTADO", "Descartado"),
]
ESTADO = dict(ESTADOS)
RESULTADO = {
    "CONFIRMADO": "Confirmado",
    "OTRA_PATOLOGIA": "Otra patología",
    "NO_IDENTIFICADO": "No identificado",
    "REQUIERE_VISITA": "Requiere visita",
    "SIN_DIAGNOSTICO": "Sin diagnóstico aún",
}
ACTIVOS = ("PENDIENTE_REVISION", "EN_REVISION", "REGISTRADO")
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
TITULOS = {
    "casos": "Casos y estados",
    "identificacion": "Identificación preliminar y diagnóstico",
    "alertas": "Alertas epidemiológicas",
    "productores": "Productores, fincas y chatbot",
    "tecnicos": "Técnicos y carga de trabajo",
}
DESCRIPCIONES = {
    "casos": "Cuántos casos llegan, en qué estado están, por vereda y especie, y cuánto tarda cada paso.",
    "identificacion": (
        "Compara la categoría sugerida por el sistema con el diagnóstico técnico y la coincidencia de síntomas."
    ),
    "alertas": "Alertas generadas, decisiones del líder técnico, tiempos de respuesta y productores avisados.",
    "productores": "Productores activos, fincas y cultivos por especie, productores con más casos y uso del chatbot.",
    "tecnicos": "Casos asignados, pendientes y cerrados por técnico, y tiempos de revisión.",
}


# --- utilidades -------------------------------------------------------------------------------------------------------


def lista(valor: str | None) -> list[str]:
    return [v for v in (valor or "").split(",") if v]


def dia(momento: datetime) -> date:
    return momento.astimezone(BOGOTA).date()


def fecha_corta(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def decimal(n: float | None) -> str:
    return "—" if n is None else f"{n:.1f}".replace(".", ",")


def pct(a: int, b: int) -> int:
    return round(a / b * 100) if b else 0


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def promedio(xs: Iterable[float]) -> float | None:
    valores = list(xs)
    return sum(valores) / len(valores) if valores else None


def dias_entre(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / DIA


def fecha(texto: str | None, campo: str) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ApiError(
            422, "VALIDACION", "Revisa los campos marcados.", {campo: "Usa el formato aaaa-mm-dd."}
        ) from None


def rango(f: FiltrosReporte, hoy: date) -> tuple[date, date]:
    """Mismo cálculo del panel (`rangoPeriodo`). Valida el orden y el máximo de 12 meses (422)."""
    desde_f, hasta_f = fecha(f.get("desde"), "desde"), fecha(f.get("hasta"), "hasta")
    periodo = f.get("periodo") or ("personalizado" if desde_f or hasta_f else PERIODO_PREDETERMINADO)
    if periodo == "7d":
        desde, hasta = hoy - timedelta(days=6), hoy
    elif periodo == "30d":
        desde, hasta = hoy - timedelta(days=29), hoy
    elif periodo == "mes":
        desde, hasta = hoy.replace(day=1), hoy
    elif periodo == "anio":
        desde, hasta = date(hoy.year, 1, 1), hoy
    elif periodo == "personalizado":
        desde, hasta = desde_f or date(hoy.year, 1, 1), hasta_f or hoy
    else:
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"periodo": "Periodo desconocido."})
    if desde > hasta:
        raise ApiError(
            422, "VALIDACION", "La fecha «Desde» no puede ser posterior a «Hasta».", {"desde": "Posterior a «hasta»"}
        )
    if (hasta - desde).days > MAXIMO_DIAS:
        raise ApiError(422, "VALIDACION", "El rango máximo es de 12 meses.", {"hasta": "Máximo 12 meses."})
    return desde, hasta


def _numero(f: FiltrosReporte, clave: str) -> float | None:
    texto = f.get(clave)
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {clave: "Escribe un número."}) from None


def resultado_de(c: CasoR) -> str:
    return c.resultado or "SIN_DIAGNOSTICO"


def categoria_preliminar(c: CasoR) -> str | None:
    if c.preliminar == "NO_IDENTIFICADO":
        return "NO_IDENTIFICADA"
    return c.categoria_preliminar


def con_categoria(c: CasoR) -> bool:
    return c.preliminar is not None and c.preliminar != "NO_IDENTIFICADO" and c.categoria_preliminar is not None


def dias_revision(casos: Iterable[CasoR]) -> list[float]:
    """Días desde el envío al panel hasta el diagnóstico técnico."""
    return [dias_entre(c.enviado_en, c.diagnosticado_en) for c in casos if c.enviado_en and c.diagnosticado_en]


def kpi(rotulo: str, valor: str, pie: str, tono: str) -> KpiReporte:
    return KpiReporte(rotulo=rotulo, valor=valor, pie=pie, tono=tono)


def barra(etiqueta: str, valor: float, texto: str | None = None, categoria: str | None = None) -> ItemBarra:
    return ItemBarra(etiqueta=etiqueta, valor=valor, texto=texto, categoria=categoria)


def columnas(*definicion: tuple[str, str] | tuple[str, str, bool]) -> list[ColumnaTabla]:
    return [
        ColumnaTabla(clave=d[0], titulo=d[1], alinear="derecha" if len(d) > 2 and d[2] else None) for d in definicion
    ]


def nombre_vereda(codigo: str, casos: Iterable[CasoR] = (), productores: Iterable[ProductorR] = ()) -> str:
    for c in casos:
        if c.vereda == codigo:
            return c.vereda_nombre
    for p in productores:
        for f in p.fincas:
            if f.vereda == codigo:
                return f.vereda_nombre
    return codigo.replace("_", " ").title()


# --- F1 · casos -------------------------------------------------------------------------------------------------------


def casos_filtrados(datos: Datos, f: FiltrosReporte, ahora: datetime) -> list[CasoR]:
    desde, hasta = rango(f, dia(ahora))
    especies, tipos, estados = lista(f.get("especie")), lista(f.get("tipo_cultivo")), lista(f.get("estado"))
    categorias, elegidos = lista(f.get("categoria")), lista(f.get("productor"))
    minimo, maximo, espera = _numero(f, "coincidencia_min"), _numero(f, "coincidencia_max"), _numero(f, "espera")
    tecnico, resultado = f.get("tecnico"), f.get("resultado")

    def entra(c: CasoR) -> bool:
        if not desde <= dia(c.fecha) <= hasta:
            return False
        if f.get("vereda") and c.vereda != f["vereda"]:
            return False
        if f.get("finca") and c.finca_id != f["finca"]:
            return False
        if elegidos and c.productor_id not in elegidos:
            return False
        if especies and c.especie not in especies:
            return False
        if tipos and c.tipo_cultivo not in tipos:
            return False
        if estados and c.estado not in estados:
            return False
        if categorias and (categoria_preliminar(c) or "") not in categorias:
            return False
        if minimo is not None and (c.porcentaje is None or c.porcentaje < minimo):
            return False
        if maximo is not None and (c.porcentaje is None or c.porcentaje > maximo):
            return False
        if resultado and resultado_de(c) != resultado:
            return False
        if tecnico == "sin" and c.tecnico_id:
            return False
        if tecnico and tecnico != "sin" and c.tecnico_id != tecnico:
            return False
        if espera is not None:
            if c.estado not in ("PENDIENTE_REVISION", "EN_REVISION") or not c.enviado_en:
                return False
            if dias_entre(c.enviado_en, ahora) <= espera:
                return False
        return True

    return [c for c in datos.casos if entra(c)]


def texto_rango(f: FiltrosReporte, casos: list[CasoR], ahora: datetime) -> str:
    desde, hasta = rango(f, dia(ahora))
    codigos = sorted(c.codigo for c in casos)
    detalle = f" · {plural(len(casos), 'caso', 'casos')} ({codigos[0]} a {codigos[-1]})" if codigos else " · sin casos"
    return f"{fecha_corta(desde)} a {fecha_corta(hasta)}{detalle}"


def por_semana(casos: list[CasoR]) -> list[ItemBarra]:
    cuenta: dict[date, int] = {}
    for c in casos:
        if c.enviado_en:
            d = dia(c.enviado_en)
            lunes = d - timedelta(days=d.weekday())
            cuenta[lunes] = cuenta.get(lunes, 0) + 1
    return [barra(f"Sem. del {s.day} {MESES[s.month - 1]}", n) for s, n in sorted(cuenta.items())]


def fila_caso(c: CasoR) -> dict[str, str | int | float]:
    if not c.preliminar:
        preliminar = "—"
    elif c.preliminar == "NO_IDENTIFICADO" or not c.categoria_preliminar:
        preliminar = "No identificada"
    else:
        preliminar = f"{CATEGORIA[c.categoria_preliminar]} · {round(c.porcentaje or 0)} %"
    return {
        "_caso": c.id,
        "caso": c.codigo,
        "recibido": fecha_corta(dia(c.fecha)),
        "productor": c.productor_nombre,
        "vereda": c.vereda_nombre,
        "preliminar": preliminar,
        "estado": ESTADO.get(c.estado, c.estado),
        "tecnico": c.tecnico_nombre or "Sin técnico",
    }


# --- R1 · casos y estados ---------------------------------------------------------------------------------------------


def reporte_casos(datos: Datos, f: FiltrosReporte, ahora: datetime) -> ReporteDatos:
    lc = casos_filtrados(datos, f, ahora)
    enviados = [c for c in lc if c.enviado_en]

    def n(estado: str) -> int:
        return sum(c.estado == estado for c in lc)

    cerrados = [c for c in lc if c.cerrado_en and c.enviado_en]
    dias_cierre = [dias_entre(c.enviado_en, c.cerrado_en) for c in cerrados if c.enviado_en and c.cerrado_en]
    activos = [c for c in lc if c.estado in ACTIVOS and c.enviado_en]

    def antiguedad(c: CasoR) -> float:
        return dias_entre(c.enviado_en, ahora) if c.enviado_en else 0.0

    tramos: list[tuple[str, Callable[[float], bool]]] = [
        ("0 a 1 día", lambda d: d < 2),
        ("2 a 3 días", lambda d: 2 <= d < 4),
        ("4 a 7 días", lambda d: 4 <= d <= 7),
        ("Más de 7 días", lambda d: d > 7),
    ]
    viejos = [c for c in activos if antiguedad(c) > 7]
    asignacion = [dias_entre(c.enviado_en, c.asignado_en) for c in enviados if c.asignado_en and c.enviado_en]
    diagnostico = dias_revision(lc)
    a_cierre = [dias_entre(c.diagnosticado_en, c.cerrado_en) for c in lc if c.cerrado_en and c.diagnosticado_en]
    veredas = sorted(
        {c.vereda for c in lc} | set(VEREDAS),
        key=lambda v: (v not in VEREDAS, VEREDAS.index(v) if v in VEREDAS else 99, v),
    )
    secciones: list[Seccion] = [
        SeccionBarras(
            id="estados",
            titulo="Casos por estado",
            subtitulo=f"{plural(len(lc), 'caso', 'casos')} · cada estado con su nombre y cifra",
            ancho="mitad",
            items=[barra(nombre, n(e)) for e, nombre in ESTADOS],
        ),
        SeccionBarras(
            id="semanas",
            titulo="Casos enviados al panel por semana",
            subtitulo=f"{plural(len(enviados), 'caso', 'casos')} con fecha",
            ancho="mitad",
            items=por_semana(enviados),
        ),
        SeccionBarras(
            id="tiempos",
            titulo="Tiempos del proceso",
            subtitulo="promedios en días",
            ancho="mitad",
            items=[
                barra("Recepción → asignación", promedio(asignacion) or 0, f"{decimal(promedio(asignacion))} días"),
                barra(
                    "Asignación → diagnóstico técnico",
                    promedio(diagnostico) or 0,
                    f"{decimal(promedio(diagnostico))} días",
                ),
                barra("Diagnóstico → cierre", promedio(a_cierre) or 0, f"{decimal(promedio(a_cierre))} días"),
            ],
            nota=f"Tiempo medio desde que llega al panel hasta el cierre: {decimal(promedio(dias_cierre))} días.",
        ),
        SeccionBarras(
            id="antiguedad",
            titulo="Casos activos por antigüedad",
            subtitulo="días desde que llegó al panel",
            ancho="mitad",
            items=[
                barra(etiqueta, v, plural(v, "caso", "casos"))
                for etiqueta, en in tramos
                for v in [sum(en(antiguedad(c)) for c in activos)]
            ],
            nota=f"{plural(len(activos), 'caso activo', 'casos activos')}."
            + (f" Los de más de 7 días: {' y '.join(c.codigo for c in viejos)}." if viejos else ""),
        ),
        SeccionTabla(
            id="vereda-especie",
            titulo="Casos por vereda y especie",
            ancho="completo",
            columnas=columnas(
                ("vereda", "Vereda"), *[(e, ESPECIE[e], True) for e in ESPECIES], ("total", "Total", True)
            ),
            filas=[
                *[
                    {
                        "vereda": nombre_vereda(v, datos.casos, datos.productores),
                        **{e: sum(c.vereda == v and c.especie == e for c in lc) for e in ESPECIES},
                        "total": sum(c.vereda == v for c in lc),
                    }
                    for v in veredas
                ],
                {"vereda": "Total", **{e: sum(c.especie == e for c in lc) for e in ESPECIES}, "total": len(lc)},
            ],
        ),
        SeccionTabla(
            id="principal",
            titulo="Casos del periodo",
            subtitulo="orden: más recientes",
            ancho="completo",
            columnas=columnas(
                ("caso", "Caso"),
                ("recibido", "Recibido"),
                ("productor", "Productor"),
                ("vereda", "Vereda"),
                ("preliminar", "Preliminar"),
                ("estado", "Estado"),
                ("tecnico", "Técnico"),
            ),
            filas=[fila_caso(c) for c in sorted(lc, key=lambda c: c.id, reverse=True)],
            por_pagina=10,
            sustantivo="casos",
            vacio="Ningún caso coincide con los filtros.",
        ),
    ]
    return ReporteDatos(
        tipo="casos",
        titulo="Reporte de casos",
        subtitulo=f"{texto_rango(f, lc, ahora)} · datos del panel y del chatbot",
        total=len(lc),
        sustantivo="casos",
        kpis=[
            kpi("Recibidos", str(len(lc)), f"{len(enviados)} enviados al panel", "info"),
            kpi("Pendientes de revisión", str(n("PENDIENTE_REVISION")), "sin técnico asignado", "adv"),
            kpi(
                "En revisión",
                str(n("EN_REVISION")),
                f"{plural(n('REGISTRADO'), 'registrado', 'registrados')} sin cerrar",
                "revision",
            ),
            kpi(
                "Cerrados",
                str(n("CERRADO")),
                f"{plural(n('DESCARTADO'), 'descartado', 'descartados')} por el chatbot",
                "ok",
            ),
            kpi("Días hasta el cierre", decimal(promedio(dias_cierre)), "promedio de los casos cerrados", "neutro"),
        ],
        secciones=secciones,
    )


# --- R2 · identificación preliminar y diagnóstico ---------------------------------------------------------------------


def reporte_identificacion(datos: Datos, f: FiltrosReporte, ahora: datetime) -> ReporteDatos:
    lc = casos_filtrados(datos, f, ahora)
    con_pre = [c for c in lc if c.preliminar]
    con_cat = [c for c in con_pre if con_categoria(c)]
    no_ident = len(con_pre) - len(con_cat)
    porcentajes = [c.porcentaje or 0 for c in con_cat]
    diag_cat = [c for c in con_cat if resultado_de(c) in ("CONFIRMADO", "OTRA_PATOLOGIA")]
    confirmados = [c for c in diag_cat if resultado_de(c) == "CONFIRMADO"]
    otra = [c for c in lc if resultado_de(c) == "OTRA_PATOLOGIA"]
    sin_diag = [c for c in lc if resultado_de(c) == "SIN_DIAGNOSTICO" and c.enviado_en]
    filas_pre = [(CATEGORIA[cat], cat) for cat in CATEGORIAS] + [("No identificada", "NO_IDENTIFICADA")]
    columnas_res = ["CONFIRMADO", "OTRA_PATOLOGIA", "NO_IDENTIFICADO", "REQUIERE_VISITA", "SIN_DIAGNOSTICO"]
    rangos: list[tuple[str, Callable[[float], bool]]] = [
        ("Menos de 60 %", lambda p: p < 60),
        ("60 a 69 %", lambda p: 60 <= p < 70),
        ("70 a 79 %", lambda p: 70 <= p < 80),
        ("80 % o más", lambda p: p >= 80),
    ]
    bajos = sorted((c for c in con_cat if (c.porcentaje or 0) < 70), key=lambda c: c.porcentaje or 0)

    def media_categoria(cat: str) -> ItemBarra:
        ps = [c.porcentaje or 0 for c in con_cat if c.categoria_preliminar == cat]
        m = promedio(ps)
        return barra(CATEGORIA[cat], round(m) if m is not None else 0, f"{round(m)} %" if m is not None else "—", cat)

    def por_categoria(cat: str) -> ItemBarra:
        de = [c for c in lc if categoria_preliminar(c) == cat]
        conf = sum(resultado_de(c) == "CONFIRMADO" for c in de)
        return barra(CATEGORIA[cat], len(de), f"{len(de)} · {conf} conf.", cat)

    return ReporteDatos(
        tipo="identificacion",
        titulo="Identificación preliminar y diagnóstico técnico",
        subtitulo=(f"{texto_rango(f, lc, ahora)} · la identificación preliminar no reemplaza el diagnóstico técnico"),
        total=len(lc),
        sustantivo="casos",
        kpis=[
            kpi(
                "Con identificación preliminar",
                str(len(con_pre)),
                f"{len(con_cat)} con categoría · {plural(no_ident, 'no identificada', 'no identificadas')}",
                "info",
            ),
            kpi(
                "Coincidencia media de síntomas",
                f"{round(promedio(porcentajes) or 0)} %" if porcentajes else "—",
                "de la categoría sugerida",
                "revision",
            ),
            kpi(
                "El técnico confirmó la categoría",
                f"{pct(len(confirmados), len(diag_cat))} %" if diag_cat else "—",
                f"{len(confirmados)} de {len(diag_cat)} diagnósticos con categoría",
                "ok",
            ),
            kpi("Sin diagnóstico técnico aún", str(len(sin_diag)), "pendientes, en revisión o sin cerrar", "adv"),
            kpi(
                "Otra patología",
                str(len(otra)),
                ", ".join(c.codigo for c in otra) if otra else "ningún caso",
                "neutro",
            ),
        ],
        secciones=[
            SeccionTabla(
                id="matriz",
                titulo="Preliminar frente a resultado técnico",
                subtitulo="filas: preliminar · columnas: técnico",
                ancho="completo",
                columnas=columnas(("preliminar", "Preliminar"), *[(r, RESULTADO[r], True) for r in columnas_res]),
                filas=[
                    {
                        "preliminar": nombre,
                        **{
                            r: (v or "—")
                            for r in columnas_res
                            for v in [sum(categoria_preliminar(c) == cat and resultado_de(c) == r for c in lc)]
                        },
                    }
                    for nombre, cat in filas_pre
                ],
            ),
            SeccionBarras(
                id="rangos",
                titulo="Coincidencia de síntomas",
                subtitulo="casos por rango",
                ancho="mitad",
                items=[
                    barra(etiqueta, v, plural(v, "caso", "casos"))
                    for etiqueta, en in rangos
                    for v in [sum(en(p) for p in porcentajes)]
                ],
                nota=(
                    "Porcentaje de coincidencia de síntomas de la categoría sugerida · "
                    f"{plural(len(con_cat), 'caso', 'casos')} con categoría."
                ),
            ),
            SeccionBarras(
                id="categorias",
                titulo="Casos por categoría preliminar",
                subtitulo="total · confirmados",
                ancho="mitad",
                items=[por_categoria(cat) for cat in CATEGORIAS],
            ),
            SeccionBarras(
                id="media-categoria",
                titulo="Coincidencia media por categoría",
                subtitulo="porcentaje",
                ancho="mitad",
                items=[media_categoria(cat) for cat in CATEGORIAS],
                nota=f"Las {plural(no_ident, 'no identificada', 'no identificadas')} no tienen porcentaje."
                if no_ident
                else None,
            ),
            SeccionTabla(
                id="principal",
                titulo="Casos con coincidencia de síntomas menor a 70 %",
                subtitulo="revisar el cuestionario",
                ancho="completo",
                columnas=columnas(
                    ("caso", "Caso"),
                    ("recibido", "Recibido"),
                    ("productor", "Productor"),
                    ("preliminar", "Preliminar"),
                    ("coincidencia", "Coincid.", True),
                    ("estado", "Estado"),
                    ("resultado", "Resultado técnico"),
                ),
                filas=[
                    {
                        **fila_caso(c),
                        "preliminar": CATEGORIA.get(c.categoria_preliminar or "", "—"),
                        "coincidencia": f"{round(c.porcentaje or 0)} %",
                        "resultado": RESULTADO[resultado_de(c)],
                    }
                    for c in bajos
                ],
                por_pagina=10,
                sustantivo="casos",
                vacio="Ningún caso con coincidencia menor a 70 % en el periodo.",
            ),
        ],
    )


# --- R4 · productores, fincas y chatbot -------------------------------------------------------------------------------


def reporte_productores(datos: Datos, f: FiltrosReporte, ahora: datetime) -> ReporteDatos:
    desde, hasta = rango(f, dia(ahora))
    estados, especies, tipos = lista(f.get("estado_productor")), lista(f.get("especie")), lista(f.get("tipo_cultivo"))
    reg_desde, reg_hasta = (
        fecha(f.get("registrado_desde"), "registrado_desde"),
        fecha(f.get("registrado_hasta"), "registrado_hasta"),
    )
    casos_desde, casos_hasta = _numero(f, "casos_desde"), _numero(f, "casos_hasta")
    casos_periodo = [c for c in datos.casos if desde <= dia(c.fecha) <= hasta]

    def casos_de(p: ProductorR) -> list[CasoR]:
        return [c for c in casos_periodo if c.productor_id == p.id]

    def entra(p: ProductorR) -> bool:
        if estados and ("activo" if p.activo else "deshabilitado") not in estados:
            return False
        if reg_desde and p.registrado < reg_desde:
            return False
        if reg_hasta and p.registrado > reg_hasta:
            return False
        if f.get("vereda") and not any(x.vereda == f["vereda"] for x in p.fincas):
            return False
        cultivos = [cu for x in p.fincas for cu in x.cultivos]
        if tipos and not any(t in tipos for t, _ in cultivos):
            return False
        if especies and not any(e in especies for _, es in cultivos for e in es):
            return False
        n = len(casos_de(p))
        if casos_desde is not None and n < casos_desde:
            return False
        if casos_hasta is not None and n > casos_hasta:
            return False
        if f.get("con_activos") and not any(c.productor_id == p.id and c.estado in ACTIVOS for c in datos.casos):
            return False
        dias_consulta = dias_entre(p.ultima_consulta, ahora) if p.ultima_consulta else float("inf")
        chatbot = f.get("chatbot")
        if chatbot == "30" and dias_consulta > 30:
            return False
        if chatbot == "90" and dias_consulta <= 90:
            return False
        if chatbot == "abandono" and not any(c.estado == "DESCARTADO" for c in casos_de(p)):
            return False  # consultas que el chatbot descartó por inactividad o con /salir
        return True

    lp = [p for p in datos.productores if entra(p)]
    sus_fincas = [x for p in lp for x in p.fincas]
    cultivos = [cu for x in sus_fincas for cu in x.cultivos]
    ids = {p.id for p in lp}
    sus_casos = [c for c in casos_periodo if c.productor_id in ids]
    con_casos = [p for p in lp if casos_de(p)]
    sin_casos = [p for p in lp if not casos_de(p)]
    creados = len(sus_casos)
    enviados = sum(c.enviado_en is not None for c in sus_casos)
    descartados = sum(c.estado == "DESCARTADO" for c in sus_casos)
    veredas = sorted({x.vereda for x in sus_fincas} | set(VEREDAS), key=lambda v: (v not in VEREDAS, v))
    ranking = sorted(((p, casos_de(p)) for p in lp), key=lambda par: (-len(par[1]), par[0].nombre.lower()))
    return ReporteDatos(
        tipo="productores",
        titulo="Reporte de productores, fincas y chatbot",
        subtitulo=(
            f"{fecha_corta(desde)} a {fecha_corta(hasta)} · {plural(len(lp), 'productor', 'productores')} · "
            f"{plural(len(sus_fincas), 'finca', 'fincas')} · {plural(len(cultivos), 'cultivo', 'cultivos')}"
        ),
        total=len(lp),
        sustantivo="productores",
        kpis=[
            kpi(
                "Productores",
                str(len(lp)),
                f"{sum(p.activo for p in lp)} activos · {sum(not p.activo for p in lp)} deshabilitados",
                "info",
            ),
            kpi(
                "Con casos en el periodo",
                str(len(con_casos)),
                f"de {plural(len(lp), 'productor', 'productores')}",
                "adv",
            ),
            kpi(
                "Fincas",
                str(len(sus_fincas)),
                f"en {plural(len({x.vereda for x in sus_fincas}), 'vereda', 'veredas')}",
                "ok",
            ),
            kpi("Cultivos", str(len(cultivos)), "gulupa, granadilla y maracuyá", "revision"),
            kpi("Conversaciones", str(creados), "consultas con el chatbot que crearon un caso", "neutro"),
        ],
        secciones=[
            SeccionBarras(
                id="chatbot",
                titulo="Uso del chatbot de WhatsApp",
                subtitulo="consultas del periodo",
                ancho="mitad",
                items=[
                    barra("Casos creados", creados),
                    barra("Enviados al panel", enviados),
                    barra("Descartados por el chatbot", descartados),
                ],
                nota="Las conversaciones que terminan antes de crear un caso no se guardan.",
            ),
            SeccionBarras(
                id="veredas",
                titulo="Fincas y casos por vereda",
                ancho="mitad",
                items=[
                    barra(
                        nombre_vereda(v, datos.casos, datos.productores),
                        nc,
                        f"{plural(nf, 'finca', 'fincas')} · {plural(nc, 'caso', 'casos')}",
                    )
                    for v in veredas
                    for nf, nc in [(sum(x.vereda == v for x in sus_fincas), sum(c.vereda == v for c in sus_casos))]
                ],
            ),
            SeccionBarras(
                id="especies",
                titulo="Cultivos por especie",
                ancho="mitad",
                items=[
                    barra(ESPECIE[e], v, plural(v, "cultivo", "cultivos"))
                    for e in ESPECIES
                    for v in [sum(e in es for _, es in cultivos)]
                ],
                nota=(
                    f"{plural(len(cultivos), 'cultivo', 'cultivos')} · "
                    f"{sum(t == 'CAMPO_ABIERTO' for t, _ in cultivos)} campo abierto · "
                    f"{sum(t == 'INVERNADERO' for t, _ in cultivos)} invernadero. "
                    "Un cultivo puede tener más de una especie."
                ),
            ),
            SeccionBarras(
                id="estados",
                titulo="Estado de los productores",
                ancho="mitad",
                items=[
                    barra("Activos", sum(p.activo for p in lp)),
                    barra("Deshabilitados", sum(not p.activo for p in lp)),
                    barra("Sin casos en el periodo", len(sin_casos)),
                ],
                nota=f"Sin casos en el periodo: {', '.join(p.nombre for p in sin_casos)}." if sin_casos else None,
            ),
            SeccionTabla(
                id="principal",
                titulo="Productores con más casos",
                subtitulo="orden: más casos",
                ancho="completo",
                columnas=columnas(
                    ("productor", "Productor"),
                    ("vereda", "Vereda"),
                    ("finca", "Finca"),
                    ("casos", "Casos", True),
                    ("ultimo", "Último caso"),
                    ("estado", "Estado"),
                ),
                filas=[
                    {
                        "productor": p.nombre,
                        "vereda": p.fincas[0].vereda_nombre if p.fincas else "—",
                        "finca": ", ".join(x.nombre for x in p.fincas) or "—",
                        "casos": len(cs),
                        "ultimo": fecha_corta(dia(max(c.fecha for c in cs))) if cs else "—",
                        "estado": "Activo" if p.activo else "Deshabilitado",
                    }
                    for p, cs in ranking
                ],
                por_pagina=8,
                sustantivo="productores",
                vacio="Ningún productor coincide con los filtros.",
            ),
        ],
    )


# --- R5 · técnicos y carga de trabajo ---------------------------------------------------------------------------------


def reporte_tecnicos(datos: Datos, f: FiltrosReporte, ahora: datetime) -> ReporteDatos:
    desde, hasta = rango(f, dia(ahora))
    elegidos = lista(f.get("tecnicos"))
    dias_desde, dias_hasta = _numero(f, "dias_desde"), _numero(f, "dias_hasta")
    casos_periodo = [c for c in datos.casos if desde <= dia(c.fecha) <= hasta]

    def de(u: CuentaR) -> list[CasoR]:
        return [c for c in casos_periodo if c.tecnico_id == u.id]

    def dias_de(u: CuentaR) -> float | None:
        return promedio(dias_revision(de(u)))

    def entra(u: CuentaR) -> bool:
        if elegidos and u.id not in elegidos:
            return False
        if f.get("rol") and u.rol != f["rol"]:
            return False
        if f.get("estado_cuenta") == "activa" and not u.activo:
            return False
        if f.get("estado_cuenta") == "deshabilitada" and u.activo:
            return False
        d = dias_de(u)
        if dias_desde is not None and (d is None or d < dias_desde):
            return False
        if dias_hasta is not None and (d is None or d > dias_hasta):
            return False
        return not (f.get("con_pendientes") and not any(c.estado == "EN_REVISION" for c in de(u)))

    lu = [u for u in datos.cuentas if entra(u)]
    enviados = [c for c in casos_periodo if c.enviado_en]
    asignados = [c for c in enviados if c.tecnico_id]
    sin_tecnico = [c for c in casos_periodo if c.estado == "PENDIENTE_REVISION"]
    registrados = [c for c in casos_periodo if c.estado == "REGISTRADO"]
    ids = {u.id for u in lu}
    sus_casos = [c for c in casos_periodo if c.tecnico_id in ids]
    con_casos = [u for u in lu if de(u)]

    def rol(r: str) -> str:
        return "Líder técnico" if r == "LIDER_TECNICO" else "Técnico"

    def desglose(u: CuentaR) -> str:
        partes = [
            (ESTADO[e].lower(), sum(c.estado == e for c in de(u))) for e in ("EN_REVISION", "REGISTRADO", "CERRADO")
        ]
        return " · ".join(f"{e} {v}" for e, v in partes if v > 0)

    equipo = promedio(dias_revision(sus_casos))
    return ReporteDatos(
        tipo="tecnicos",
        titulo="Reporte de técnicos y carga de trabajo",
        subtitulo=(
            f"{texto_rango(f, casos_periodo, ahora)} · {plural(len(asignados), 'caso asignado', 'casos asignados')}"
        ),
        total=len(lu),
        sustantivo="cuentas",
        kpis=[
            kpi(
                "Técnicos con casos",
                str(len(con_casos)),
                f"{sum(u.activo for u in lu)} cuentas activas · {sum(not u.activo for u in lu)} deshabilitadas",
                "info",
            ),
            kpi("Casos asignados", str(len(asignados)), f"de {len(enviados)} enviados al panel", "ok"),
            kpi(
                "Pendientes sin técnico",
                str(len(sin_tecnico)),
                " y ".join(c.codigo for c in sin_tecnico) or "ninguno",
                "adv",
            ),
            kpi("Días de revisión", decimal(equipo), "promedio del equipo", "info"),
            kpi(
                "Registrados sin cerrar",
                str(len(registrados)),
                ", ".join(f"{c.codigo} · {c.tecnico_nombre or '—'}" for c in registrados) or "ninguno",
                "revision",
            ),
        ],
        secciones=[
            SeccionBarras(
                id="carga",
                titulo="Carga por técnico",
                subtitulo="casos del periodo por estado",
                ancho="completo",
                items=[
                    barra(
                        f"{u.nombre} · {rol(u.rol)}",
                        len(de(u)),
                        f"{plural(len(de(u)), 'caso', 'casos')} · {desglose(u)}" if de(u) else "0 casos",
                    )
                    for u in lu
                ],
                nota=(
                    f"{plural(len(sin_tecnico), 'caso pendiente', 'casos pendientes')} sin técnico "
                    f"({', '.join(c.codigo for c in sin_tecnico)})."
                )
                if sin_tecnico
                else None,
            ),
            SeccionBarras(
                id="tiempos",
                titulo="Tiempo medio de revisión",
                subtitulo="días",
                ancho="mitad",
                items=[
                    barra(u.nombre, dias_de(u) or 0, f"{decimal(dias_de(u))} días")
                    for u in lu
                    if any(c.diagnosticado_en for c in de(u))
                ],
                nota=(
                    "Desde la llegada al panel hasta el diagnóstico técnico. "
                    f"Promedio del equipo: {decimal(equipo)} días."
                ),
            ),
            SeccionBarras(
                id="actividad",
                titulo="Actividad del equipo",
                ancho="mitad",
                items=[
                    barra("Diagnósticos registrados", sum(c.diagnosticado_en is not None for c in sus_casos)),
                    barra("Casos cerrados", sum(c.estado == "CERRADO" for c in sus_casos)),
                ],
            ),
            SeccionTabla(
                id="principal",
                titulo="Resumen por cuenta",
                ancho="completo",
                columnas=columnas(
                    ("tecnico", "Técnico"),
                    ("cuenta", "Cuenta"),
                    ("asignados", "Asignados", True),
                    ("revision", "En revisión", True),
                    ("registrados", "Registrados", True),
                    ("cerrados", "Cerrados", True),
                ),
                filas=[
                    {
                        "tecnico": u.nombre,
                        "cuenta": "Activa" if u.activo else "Deshabilitada",
                        "asignados": len(de(u)),
                        "revision": sum(c.estado == "EN_REVISION" for c in de(u)),
                        "registrados": sum(c.estado == "REGISTRADO" for c in de(u)),
                        "cerrados": sum(c.estado == "CERRADO" for c in de(u)),
                    }
                    for u in lu
                ],
                por_pagina=10,
                sustantivo="cuentas",
                vacio="Ninguna cuenta coincide con los filtros.",
            ),
        ],
    )


GENERADORES: dict[str, Callable[[Datos, FiltrosReporte, datetime], ReporteDatos]] = {
    "casos": reporte_casos,
    "identificacion": reporte_identificacion,
    "productores": reporte_productores,
    "tecnicos": reporte_tecnicos,
}


# --- Resumen (pantalla 23) --------------------------------------------------------------------------------------------


def resumen(datos: Datos, f: FiltrosReporte, ahora: datetime, alertas: ReporteDatos) -> ResumenReportes:
    """`alertas` es el R3 del periodo (lo arma el servicio de alertas con sus propios datos)."""
    lc = casos_filtrados(datos, f, ahora)
    periodo = {k: v for k, v in f.items() if k in ("periodo", "desde", "hasta")}
    r1 = reporte_casos(datos, f, ahora)
    r2 = reporte_identificacion(datos, f, ahora)
    r4 = reporte_productores(datos, periodo, ahora)
    r5 = reporte_tecnicos(datos, periodo, ahora)

    def valor(r: ReporteDatos, rotulo: str) -> str:
        return next((k.valor for k in r.kpis if k.rotulo == rotulo), "—")

    confirmados = sum(resultado_de(c) == "CONFIRMADO" for c in lc)
    revision = promedio(dias_revision(lc))
    estados_alerta = next((s for s in alertas.secciones if s.id == "estados"), None)
    pie_alertas = (
        " · ".join(f"{int(i.valor)} {i.etiqueta.lower()}" for i in estados_alerta.items if i.valor > 0)
        if isinstance(estados_alerta, SeccionBarras)
        else ""
    )
    kpis = [
        kpi(
            "Casos recibidos",
            str(len(lc)),
            f"{len({c.vereda for c in lc})} veredas · {sum(c.enviado_en is not None for c in lc)} enviados al panel",
            "info",
        ),
        kpi("Confirmados", str(confirmados), f"{pct(confirmados, len(lc))} % de los casos recibidos", "ok"),
        kpi(
            "Tiempo medio de revisión",
            f"{decimal(revision)} días",
            "de la recepción al diagnóstico técnico",
            "revision",
        ),
        kpi(
            "Pendientes de revisión",
            str(sum(c.estado == "PENDIENTE_REVISION" for c in lc)),
            "sin técnico asignado",
            "adv",
        ),
        kpi("Alertas del periodo", str(alertas.total), pie_alertas or "ninguna", "peligro"),
    ]
    veredas = sorted({c.vereda for c in lc} | set(VEREDAS), key=lambda v: (v not in VEREDAS, v))
    tecnicos = [u for u in datos.cuentas if any(c.tecnico_id == u.id for c in lc)]

    def casos_tec(id_: str | None) -> list[CasoR]:
        return [c for c in lc if (c.tecnico_id == id_ if id_ else (not c.tecnico_id and c.enviado_en))]

    def principal(de: list[CasoR]) -> str:
        cuenta = {cat: sum(c.categoria_preliminar == cat for c in de) for cat in CATEGORIAS}
        cat, n = max(cuenta.items(), key=lambda kv: kv[1])
        return CATEGORIA[cat] if n > 0 else "—"

    semanas = next(s for s in r1.secciones if s.id == "semanas")
    assert isinstance(semanas, SeccionBarras)
    no_ident = sum(categoria_preliminar(c) == "NO_IDENTIFICADA" for c in lc)
    secciones: list[Seccion] = [
        SeccionBarras(
            id="categorias",
            titulo="Casos por categoría fitosanitaria",
            subtitulo="categoría de la identificación preliminar · «conf.» = confirmados por el técnico",
            ancho="mitad",
            items=[
                *[
                    barra(CATEGORIA[cat], len(de), f"{len(de)} · {conf} conf.", cat)
                    for cat in CATEGORIAS
                    for de in [[c for c in lc if categoria_preliminar(c) == cat]]
                    for conf in [sum(resultado_de(c) == "CONFIRMADO" for c in de)]
                ],
                barra("No identificada", no_ident, f"{no_ident} · 0 conf."),
            ],
            nota="Cada categoría tiene su forma: triángulo, círculo, rombo, cruz y cuadrado.",
        ),
        semanas.model_copy(update={"titulo": "Casos recibidos por semana"}),
        SeccionTabla(
            id="veredas",
            titulo="Casos por vereda",
            ancho="mitad",
            columnas=columnas(
                ("vereda", "Vereda"),
                ("casos", "Casos", True),
                ("confirmados", "Confirmados", True),
                ("porcentaje", "% confirm.", True),
                ("principal", "Categoría principal"),
            ),
            filas=[
                {
                    "vereda": nombre_vereda(v, datos.casos, datos.productores),
                    "casos": len(de),
                    "confirmados": conf,
                    "porcentaje": f"{pct(conf, len(de))} %",
                    "principal": principal(de),
                }
                for v in veredas
                for de in [[c for c in lc if c.vereda == v]]
                for conf in [sum(resultado_de(c) == "CONFIRMADO" for c in de)]
            ],
        ),
        SeccionTabla(
            id="carga",
            titulo="Carga por técnico",
            ancho="mitad",
            columnas=columnas(
                ("tecnico", "Técnico"),
                ("asignados", "Asignados", True),
                ("pendientes", "Pendientes", True),
                ("cerrados", "Cerrados", True),
                ("tiempo", "Tiempo medio", True),
            ),
            filas=[
                *[
                    {
                        "tecnico": u.nombre,
                        "asignados": len(casos_tec(u.id)),
                        "pendientes": sum(c.estado == "EN_REVISION" for c in casos_tec(u.id)),
                        "cerrados": sum(c.estado == "CERRADO" for c in casos_tec(u.id)),
                        "tiempo": f"{decimal(promedio(dias_revision(casos_tec(u.id))))} días",
                    }
                    for u in tecnicos
                ],
                {
                    "tecnico": "Sin asignar",
                    "asignados": len(casos_tec(None)),
                    "pendientes": len(casos_tec(None)),
                    "cerrados": "—",
                    "tiempo": "—",
                },
            ],
        ),
    ]
    con_cat = valor(r2, "Con identificación preliminar")
    pie_r2 = next((k.pie for k in r2.kpis if k.rotulo == "Con identificación preliminar"), "")
    destacados = {
        "casos": [
            f"{valor(r1, 'Recibidos')} recibidos",
            f"{sum(c.estado in ACTIVOS for c in lc)} activos",
            f"{kpis[2].valor} de revisión media",
        ],
        "identificacion": [
            f"{valor(r2, 'El técnico confirmó la categoría')} coincide con el técnico",
            f"{valor(r2, 'Coincidencia media de síntomas')} coincidencia media",
            pie_r2.split(" · ")[1] if " · " in pie_r2 else f"{con_cat} con identificación",
        ],
        "alertas": [
            f"{alertas.total} en el periodo",
            f"{valor(alertas, 'Sin decisión')} por revisar",
            f"{valor(alertas, 'Productores avisados')} productores avisados",
        ],
        "productores": [
            f"{sum(p.activo for p in datos.productores)} de {len(datos.productores)} productores activos",
            f"{valor(r4, 'Cultivos')} cultivos",
            f"{valor(r4, 'Conversaciones')} conversaciones",
        ],
        "tecnicos": [
            f"{valor(r5, 'Técnicos con casos')} técnicos con casos",
            f"{valor(r5, 'Pendientes sin técnico')} pendientes sin técnico",
            f"{valor(r5, 'Registrados sin cerrar')} registrados sin cerrar",
        ],
    }
    return ResumenReportes(
        subtitulo=f"Resumen de casos, categorías fitosanitarias y carga de trabajo · {texto_rango(f, lc, ahora)}",
        total=len(lc),
        sustantivo="casos",
        kpis=kpis,
        reportes=[
            TarjetaReporte(tipo=t, titulo=TITULOS[t], descripcion=DESCRIPCIONES[t], destacados=destacados[t])
            for t in ("casos", "identificacion", "alertas", "productores", "tecnicos")
        ],
        secciones=secciones,
    )
