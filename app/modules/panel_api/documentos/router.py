"""Documentación fitosanitaria (pantalla 20 · B-07 · D-18).

- `GET /documentos?q&tipo` → el manual principal, los documentos generales y una tarjeta por categoría con su
  participación en las alertas (categoría transmisible) y cómo se propaga.
- `GET /documentos/{id}/archivo` → el PDF (lo pide el panel con su token y lo abre en una pestaña nueva).
- `POST /documentos` (multipart: `titulo`, `tipo`, `categoria`, `archivo`) → solo el líder; PDF de hasta 20 MB.

Metadatos en DynamoDB (`documentos_store`), archivo en S3 bajo `documentos/<id>.pdf`. El manual es el documento de
tipo MANUAL general más reciente. Los textos de propagación no están en la base: son los del panel (fijos por
categoría).
"""

import logging
import unicodedata
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.errors import ApiError, ErrorRespuesta
from app.core.catalogos_api import CATEGORIAS, CategoriaFito
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual, requiere_rol
from app.infrastructure.db.session import get_session
from app.infrastructure.documentos_store import (
    ArchivosDocumentos,
    Documento,
    DocumentosStore,
    clave_s3,
    get_archivos_documentos,
    get_documentos_store,
    nuevo_id,
)
from app.modules.panel_api.auditoria import auditar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documentos", tags=["documentos"])

Usuario = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO, Rol.TECNICO))]
SoloLider = Annotated[UsuarioActual, Depends(requiere_rol(Rol.LIDER_TECNICO))]
Sesion = Annotated[AsyncSession, Depends(get_session)]
Store = Annotated[DocumentosStore, Depends(get_documentos_store)]
_RESPUESTAS: dict[int | str, dict[str, object]] = {401: {"model": ErrorRespuesta}, 403: {"model": ErrorRespuesta}}

TIPOS = ("MANUAL", "FICHA_TECNICA", "GUIA_MANEJO", "LIBRO_REFERENCIA")
MAX_BYTES = 20 * 1024 * 1024
MAX_TITULO = 150
ORDEN_CATEGORIAS = [
    CategoriaFito.TRIPS,
    CategoriaFito.BOTRYTIS,
    CategoriaFito.RONA,
    CategoriaFito.FUSARIUM,
    CategoriaFito.DEFICIENCIA_MAGNESIO,
]
PROPAGACION = {
    CategoriaFito.TRIPS: (
        "insecto que pasa de planta en planta y entre fincas vecinas con el viento, el material vegetal y la ropa."
    ),
    CategoriaFito.BOTRYTIS: (
        "hongo; sus esporas se mueven con el aire y el salpique. Avanza con humedad alta y poca ventilación."
    ),
    CategoriaFito.RONA: (
        "hongo que se dispersa con la lluvia, el salpique y las herramientas; afecta tejidos jóvenes y frutos."
    ),
    CategoriaFito.FUSARIUM: (
        "hongo del suelo; se mueve con el agua de riego, el suelo pegado a herramientas y plántulas infectadas."
    ),
    CategoriaFito.DEFICIENCIA_MAGNESIO: (
        "no se transmite entre plantas ni fincas. Es un problema nutricional ligado al suelo y al plan de "
        "fertilización."
    ),
}


class DocumentoOut(BaseModel):
    id: str = Field(examples=["DOC-3FA9C1D2E4"])
    titulo: str
    tipo: str = Field(description="MANUAL, FICHA_TECNICA, GUIA_MANEJO o LIBRO_REFERENCIA")
    categoria: CategoriaFito | None
    subido_por: str = Field(description="Nombre del líder que lo subió")
    subido_en: datetime


class CategoriaDocumentos(BaseModel):
    categoria: CategoriaFito
    participa: bool = Field(description="Participa en las alertas (categoría transmisible)")
    propagacion: str
    documentos: list[DocumentoOut]


class Documentacion(BaseModel):
    manual: DocumentoOut | None
    generales: list[DocumentoOut]
    categorias: list[CategoriaDocumentos]


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _salida(d: Documento) -> DocumentoOut:
    categoria = CategoriaFito(d.categoria) if d.categoria and d.categoria in CategoriaFito.__members__ else None
    return DocumentoOut(
        id=d.id,
        titulo=d.titulo,
        tipo=d.tipo,
        categoria=categoria,
        subido_por=d.subido_por_nombre,
        subido_en=d.subido_en,
    )


async def _transmisibles(session: AsyncSession) -> set[str]:
    nombres = await session.scalars(
        text("SELECT nombre_categoria FROM categorias_fitosanitarias WHERE es_transmisible = 'S'")
    )
    return {CATEGORIAS.a_codigo(n) for n in nombres}


@router.get("", response_model=Documentacion, responses=_RESPUESTAS)
async def listar_documentos(
    _: Usuario,
    session: Sesion,
    store: Store,
    q: Annotated[str | None, Query(max_length=100, description="Busca en títulos, categorías y propagación")] = None,
    tipo: Annotated[str | None, Query(description="MANUAL, FICHA_TECNICA, GUIA_MANEJO o LIBRO_REFERENCIA")] = None,
) -> Documentacion:
    """El manual principal, los documentos generales y una tarjeta por categoría (más recientes primero)."""
    if tipo and tipo not in TIPOS:
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", {"tipo": "Tipo de documento no válido."})
    buscado = _normalizar(q or "").strip()
    documentos = sorted(await run_in_threadpool(store.listar), key=lambda d: d.subido_en, reverse=True)
    nombres = {c: CATEGORIAS.a_nombre(c) for c in ORDEN_CATEGORIAS}

    def pasa(d: Documento) -> bool:
        if tipo and d.tipo != tipo:
            return False
        if not buscado or buscado in _normalizar(d.titulo):
            return True
        if d.categoria in nombres:
            categoria = CategoriaFito(d.categoria)
            return buscado in _normalizar(f"{nombres[categoria]} {PROPAGACION[categoria]}")
        return False

    manual = next((d for d in documentos if d.tipo == "MANUAL" and d.categoria is None), None)
    generales = [d for d in documentos if d.categoria is None and d is not manual and pasa(d)]
    transmisibles = await _transmisibles(session)
    categorias = [
        CategoriaDocumentos(
            categoria=c,
            participa=c.value in transmisibles,
            propagacion=PROPAGACION[c],
            documentos=[_salida(d) for d in documentos if d.categoria == c.value and pasa(d)],
        )
        for c in ORDEN_CATEGORIAS
    ]
    categorias = [
        c
        for c in categorias
        if (not buscado or c.documentos or buscado in _normalizar(f"{nombres[c.categoria]} {c.propagacion}"))
        and (not tipo or c.documentos)
    ]
    return Documentacion(
        manual=_salida(manual) if manual and (not tipo or tipo == "MANUAL") else None,
        generales=[_salida(d) for d in generales],
        categorias=categorias,
    )


@router.get(
    "/{id_documento}/archivo",
    responses={**_RESPUESTAS, 200: {"content": {"application/pdf": {}}}, 404: {"model": ErrorRespuesta}},
)
async def archivo_del_documento(
    _: Usuario,
    id_documento: str,
    store: Store,
    archivos: Annotated[ArchivosDocumentos, Depends(get_archivos_documentos)],
) -> Response:
    """El PDF, para abrirlo en una pestaña nueva."""
    documento = await run_in_threadpool(store.obtener, id_documento)
    if documento is None:
        raise ApiError(404, "NO_ENCONTRADO", "El documento no existe.")
    try:
        contenido = await run_in_threadpool(archivos.descargar, documento.clave_s3)
    except FileNotFoundError:
        raise ApiError(404, "NO_ENCONTRADO", "El archivo del documento no existe.") from None
    except Exception:
        logger.exception("No se pudo leer el PDF %s", documento.clave_s3)
        raise ApiError(503, "ERROR_INTERNO", "No pudimos abrir el documento. Intenta de nuevo.") from None
    return Response(
        contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{documento.id}.pdf"'},
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentoOut,
    responses={**_RESPUESTAS, 413: {"model": ErrorRespuesta}, 422: {"model": ErrorRespuesta}},
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["titulo", "tipo", "archivo"],
                        "properties": {
                            "titulo": {"type": "string", "maxLength": MAX_TITULO},
                            "tipo": {"type": "string", "enum": list(TIPOS)},
                            "categoria": {"type": "string", "description": "Código o vacío si es general"},
                            "archivo": {"type": "string", "format": "binary"},
                        },
                    }
                }
            },
            "required": True,
        }
    },
)
async def subir_documento(
    actor: SoloLider,
    request: Request,
    session: Sesion,
    store: Store,
    archivos: Annotated[ArchivosDocumentos, Depends(get_archivos_documentos)],
) -> DocumentoOut:
    """PDF de hasta 20 MB con título, tipo y categoría opcional (vacía = documento general)."""
    try:
        form = await request.form(max_files=1, max_fields=10)
    except Exception:
        raise ApiError(400, "ARCHIVO_ILEGIBLE", "No se pudo leer el archivo. Intenta de nuevo.") from None
    titulo = str(form.get("titulo") or "").strip()
    tipo = str(form.get("tipo") or "")
    categoria = str(form.get("categoria") or "")
    archivo = form.get("archivo")
    campos: dict[str, str] = {}
    if not titulo:
        campos["titulo"] = "Escribe el título del documento."
    elif len(titulo) > MAX_TITULO:
        campos["titulo"] = f"Máximo {MAX_TITULO} caracteres."
    if tipo not in TIPOS:
        campos["tipo"] = "Elige el tipo de documento."
    if categoria and categoria not in CategoriaFito.__members__:
        campos["categoria"] = "Categoría no válida."
    contenido = b""
    if not isinstance(archivo, UploadFile):
        campos["archivo"] = "Elige el archivo PDF."
    else:
        contenido = await archivo.read(MAX_BYTES + 1)
        if len(contenido) > MAX_BYTES:
            campos["archivo"] = "El archivo supera los 20 MB."
        elif not contenido.startswith(b"%PDF-"):
            campos["archivo"] = "El archivo debe ser un PDF."
    if campos:
        raise ApiError(422, "VALIDACION", "Revisa los campos marcados.", campos)

    documento = Documento(
        id=(id_documento := nuevo_id()),
        titulo=titulo,
        tipo=tipo,
        categoria=categoria or None,
        clave_s3=clave_s3(id_documento),
        tamano_bytes=len(contenido),
        subido_por=actor.id,
        subido_por_nombre=actor.nombre,
        subido_en=datetime.now(UTC),
    )
    try:
        await run_in_threadpool(archivos.subir, documento.clave_s3, contenido, "application/pdf")
        await run_in_threadpool(store.guardar, documento)
    except Exception:
        logger.exception("No se pudo guardar el documento %s", documento.id)
        raise ApiError(503, "ERROR_INTERNO", "La documentación no está disponible por ahora.") from None
    await auditar(session, actor, "SUBIR_DOCUMENTO", "documentos", f"Documento {documento.id} subido ({tipo})")
    await session.commit()
    return _salida(documento)
