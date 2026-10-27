"""Exportación del reporte a CSV y PDF (guía §11, módulo 12), con los mismos datos y el mismo orden que el panel.

- **CSV**: separado por punto y coma (Excel en español lo abre en columnas) y con BOM para que se lean las tildes.
- **PDF**: A4 horizontal con encabezado, filtros aplicados, indicadores y una tabla por sección.
"""

import calendar
import io
import unicodedata
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from app.modules.panel_api.reportes.alertas import MESES_LARGOS, fecha_corta
from app.modules.panel_api.reportes.schemas import Documento, ReporteDatos, SeccionBarras, SeccionDatos, SeccionTabla

SEPARADOR = ";"
VERDE = colors.HexColor("#2f5d3a")
GRIS = colors.HexColor("#f1f3f0")


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def mes_completo(desde: date, hasta: date) -> bool:
    """El periodo es un mes casi entero (empieza el día 1 y llega al menos al 28 o al fin de mes)."""
    ultimo = calendar.monthrange(hasta.year, hasta.month)[1]
    return (desde.year, desde.month) == (hasta.year, hasta.month) and desde.day == 1 and hasta.day >= min(ultimo, 28)


def documento_de(reporte: ReporteDatos, desde: date, hasta: date, chips: list[str], generado: str) -> Documento:
    rango = f"{fecha_corta(desde)} – {fecha_corta(hasta)}"
    completo = mes_completo(desde, hasta)
    archivo = (
        f"{MESES_LARGOS[desde.month - 1]}_{desde.year}" if completo else f"{desde.isoformat()}_a_{hasta.isoformat()}"
    )
    return Documento(
        reporte=reporte,
        periodo_largo=f"Periodo del {fecha_corta(desde)} al {fecha_corta(hasta)}",
        periodo_corto=f"{MESES_LARGOS[desde.month - 1]} de {desde.year}" if completo else rango,
        chips=[f"Periodo: {rango}", *chips],
        generado=generado,
        nombre_archivo=sin_tildes(f"Reporte_alertas_{archivo}"),
    )


# --- CSV ------------------------------------------------------------


def _celda(valor: object) -> str:
    texto = "" if valor is None else str(valor)
    if any(c in texto for c in (SEPARADOR, '"', "\r", "\n")):
        return '"' + texto.replace('"', '""') + '"'
    # Una celda que empieza con = + - @ se interpretaría como fórmula al abrirla en Excel.
    if texto[:1] in ("=", "+", "@") or (texto[:1] == "-" and len(texto) > 1 and not texto[1:2].isdigit()):
        return "'" + texto
    return texto


def _fila(celdas: list[object]) -> str:
    return SEPARADOR.join(_celda(c) for c in celdas)


def a_csv(d: Documento) -> bytes:
    r = d.reporte
    bloques: list[list[str]] = [
        [
            _fila([r.titulo]),
            _fila([f"{d.periodo_largo} · {r.total} {r.sustantivo}"]),
            *[_fila([c.split(":", 1)[0], c.split(":", 1)[1].strip()]) for c in d.chips if ":" in c],
            _fila(["Generado", d.generado]),
        ],
        [_fila(["Indicador", "Valor", "Detalle"]), *[_fila([k.rotulo, k.valor, k.pie]) for k in r.kpis]],
    ]
    for s in r.secciones:
        titulo = f"{s.titulo} ({s.subtitulo})" if s.subtitulo else s.titulo
        if isinstance(s, SeccionTabla):
            filas = (
                [_fila([f.get(c.clave, "") for c in s.columnas]) for f in s.filas]
                if s.filas
                else [_fila([s.vacio or "Sin datos en el periodo."])]
            )
            bloque = [_fila([titulo]), _fila([c.titulo for c in s.columnas]), *filas]
        elif isinstance(s, SeccionBarras):
            bloque = [
                _fila([titulo]),
                _fila([s.titulo, "Valor"]),
                *[_fila([i.etiqueta, i.texto or i.valor]) for i in s.items],
            ]
        else:
            bloque = [_fila([titulo]), _fila(["Dato", "Valor"]), *[_fila([i.termino, i.valor]) for i in s.items]]
        if s.nota:
            bloque.append(_fila([s.nota]))
        bloques.append(bloque)
    return ("﻿" + "\r\n\r\n".join("\r\n".join(b) for b in bloques) + "\r\n").encode("utf-8")


# --- PDF ------------------------------------------------------------


def _estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("titulo", parent=base["Title"], fontSize=17, textColor=VERDE, alignment=0),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=9, textColor=colors.HexColor("#555555")),
        "seccion": ParagraphStyle("seccion", parent=base["Heading3"], fontSize=11, textColor=VERDE, spaceBefore=10),
        "celda": ParagraphStyle("celda", parent=base["Normal"], fontSize=8, leading=10),
        "celda_der": ParagraphStyle("celda_der", parent=base["Normal"], fontSize=8, leading=10, alignment=2),
        "cab": ParagraphStyle("cab", parent=base["Normal"], fontSize=8, leading=10, textColor=colors.white),
        "nota": ParagraphStyle("nota", parent=base["Italic"], fontSize=8, textColor=colors.HexColor("#555555")),
    }


def _p(texto: object, estilo: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(texto)), estilo)


def _tabla(filas: list[list[Paragraph]], anchos: list[float] | None = None) -> Table:
    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), VERDE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9cfc7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tabla


def a_pdf(d: Documento) -> bytes:
    e = _estilos()
    r = d.reporte
    historia: list[Flowable] = [
        _p(r.titulo, e["titulo"]),
        _p(f"{d.periodo_largo} · {r.total} {r.sustantivo}", e["sub"]),
        _p(" · ".join(d.chips), e["sub"]),
        Spacer(1, 8),
        _tabla(
            [[_p("Indicador", e["cab"]), _p("Valor", e["cab"]), _p("Detalle", e["cab"])]]
            + [[_p(k.rotulo, e["celda"]), _p(k.valor, e["celda_der"]), _p(k.pie, e["celda"])] for k in r.kpis],
            [7 * cm, 3 * cm, 14 * cm],
        ),
    ]
    for s in r.secciones:
        historia.append(_p(f"{s.titulo}{f' ({s.subtitulo})' if s.subtitulo else ''}", e["seccion"]))
        if isinstance(s, SeccionTabla):
            cuerpo = (
                [
                    [_p(f.get(c.clave, ""), e["celda_der" if c.alinear else "celda"]) for c in s.columnas]
                    for f in s.filas
                ]
                if s.filas
                else [
                    [_p(s.vacio or "Sin datos en el periodo.", e["celda"])]
                    + [_p("", e["celda"])] * (len(s.columnas) - 1)
                ]
            )
            historia.append(_tabla([[_p(c.titulo, e["cab"]) for c in s.columnas], *cuerpo]))
        elif isinstance(s, SeccionBarras):
            historia.append(
                _tabla(
                    [[_p(s.titulo, e["cab"]), _p("Valor", e["cab"])]]
                    + [[_p(i.etiqueta, e["celda"]), _p(i.texto or i.valor, e["celda_der"])] for i in s.items],
                    [14 * cm, 6 * cm],
                )
            )
        elif isinstance(s, SeccionDatos):
            historia.append(
                _tabla(
                    [[_p("Dato", e["cab"]), _p("Valor", e["cab"])]]
                    + [[_p(i.termino, e["celda"]), _p(i.valor, e["celda"])] for i in s.items],
                    [9 * cm, 15 * cm],
                )
            )
        if s.nota:
            historia.append(_p(s.nota, e["nota"]))

    def pie(canvas, doc) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(1.5 * cm, 1 * cm, f"ANUC Zetaquirá · {r.titulo} · {d.periodo_corto}")
        canvas.drawRightString(landscape(A4)[0] - 1.5 * cm, 1 * cm, f"Generado el {d.generado} · página {doc.page}")
        canvas.restoreState()

    salida = io.BytesIO()
    SimpleDocTemplate(
        salida,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
        title=r.titulo,
        author="ANUC Zetaquirá",
    ).build(historia, onFirstPage=pie, onLaterPages=pie)
    return salida.getvalue()
