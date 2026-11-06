"""Pipeline de imágenes (guía §11, módulo 11): descargar de WhatsApp → validar → convertir a WebP → guardar en S3.

Orden y por qué:
1. **Descarga** de la foto con la Graph API. Si Meta no la entrega, se pide reenviarla.
2. **Formato y tamaño** del original: Rekognition solo acepta JPEG y PNG de hasta 5 MB como bytes, así que el original
   se valida **antes** de convertirlo a WebP.
3. **Calidad** (brillo y nitidez), con umbrales de la configuración. Son provisionales: se validan con el técnico.
4. **Moderación y dominio** con Rekognition: sin contenido inapropiado y que sea una planta o un cultivo.
5. **WebP**: se corrige la orientación y se reescribe sin metadatos (la ubicación y el modelo del teléfono, EXIF,
   no se guardan), con el lado mayor limitado.
6. **S3** en `evidencias/<año>/<mes>/<id>.webp`.

Una foto rechazada no se guarda en ninguna parte. Si falla un servicio (Rekognition, S3) la excepción sube y el
consumidor reintenta el mensaje: no se le dice al productor que su foto no sirve por un problema nuestro.
"""

import io
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from app.core.config import Settings, get_settings
from app.infrastructure.rekognition_client import MAXIMO_BYTES, Moderador
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import ErrorDescargaMedia, WhatsAppClient
from app.modules.orchestrator.puertos import ResultadoImagen, tipo_de_evidencia

logger = logging.getLogger(__name__)

BOGOTA = ZoneInfo("America/Bogota")
LADO_MAXIMO = 1600
CALIDAD_WEBP = 80
LADO_ANALISIS = 512  # para medir nitidez y brillo no hace falta la foto entera

MOTIVO_DESCARGA = "no pude descargarla"
MOTIVO_FORMATO = "debe ser una foto normal (JPG o PNG)"
MOTIVO_TAMANO = "pesa demasiado (máximo 5 MB)"
MOTIVO_OSCURA = "está muy oscura"
MOTIVO_CLARA = "tiene demasiada luz"
MOTIVO_BORROSA = "salió borrosa"
MOTIVO_INAPROPIADA = "no es una imagen apropiada"
MOTIVO_NO_PLANTA = "no se ve una planta o un fruto de tu cultivo"


def nitidez(gris: Image.Image) -> float:
    """Varianza del laplaciano: cuanto más borde marcado, más nítida. Una foto borrosa o lisa da valores bajos."""
    laplaciano = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    return float(ImageStat.Stat(gris.filter(laplaciano)).var[0])


def brillo(gris: Image.Image) -> float:
    """Luminosidad media de 0 a 100."""
    return float(ImageStat.Stat(gris).mean[0]) / 255 * 100


def a_webp(imagen: Image.Image) -> bytes:
    """Orientación corregida, lado mayor limitado y SIN metadatos (Pillow no copia el EXIF si no se le pide)."""
    corregida = ImageOps.exif_transpose(imagen) or imagen
    corregida.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    if corregida.mode not in {"RGB", "RGBA"}:
        corregida = corregida.convert("RGB")
    salida = io.BytesIO()
    corregida.save(salida, format="WEBP", quality=CALIDAD_WEBP)
    return salida.getvalue()


class PipelineReal:
    def __init__(
        self,
        whatsapp: WhatsAppClient,
        moderador: Moderador,
        s3: S3StorageClient,
        ajustes: Settings | None = None,
        ahora: Callable[[], datetime] = lambda: datetime.now(BOGOTA),
        generar_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.whatsapp = whatsapp
        self.moderador = moderador
        self.s3 = s3
        self.ajustes = ajustes or get_settings()
        self.ahora = ahora
        self.generar_id = generar_id

    def procesar(self, id_caso: str, media_id: str, descripcion: str | None) -> ResultadoImagen:
        try:
            original = self.whatsapp.descargar_media(media_id)
        except ErrorDescargaMedia:
            logger.warning("No se pudo descargar la foto", extra={"id_caso": id_caso, "media_id": media_id})
            return _rechazo(MOTIVO_DESCARGA)
        if len(original) > MAXIMO_BYTES:
            return _rechazo(MOTIVO_TAMANO)
        try:
            imagen = Image.open(io.BytesIO(original))
            formato = imagen.format
            imagen.load()  # decodifica ahora: un archivo roto falla aquí y no más adelante
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return _rechazo(MOTIVO_FORMATO)
        if formato not in {"JPEG", "PNG"}:  # lo que Rekognition entiende como bytes
            return _rechazo(MOTIVO_FORMATO)

        gris = (ImageOps.exif_transpose(imagen) or imagen).convert("L")
        gris.thumbnail((LADO_ANALISIS, LADO_ANALISIS))
        luz = brillo(gris)
        if luz < self.ajustes.foto_brillo_minimo:
            return _rechazo(MOTIVO_OSCURA)
        if luz > self.ajustes.foto_brillo_maximo:
            return _rechazo(MOTIVO_CLARA)
        if nitidez(gris) < self.ajustes.foto_nitidez_minima:
            return _rechazo(MOTIVO_BORROSA)

        moderacion = self.moderador.analizar(original)  # el original: es lo que Rekognition acepta
        if moderacion.inapropiada:
            logger.warning("Foto rechazada por moderación", extra={"id_caso": id_caso})
            return _rechazo(MOTIVO_INAPROPIADA)
        if not moderacion.es_planta:
            return _rechazo(MOTIVO_NO_PLANTA)

        fecha = self.ahora()
        clave = f"evidencias/{fecha.year}/{fecha.month:02d}/{self.generar_id()}.webp"
        self.s3.subir(clave, a_webp(imagen), "image/webp")
        return ResultadoImagen(util=True, tipo=tipo_de_evidencia(descripcion), clave_s3=clave)

    def eliminar(self, clave_s3: str) -> None:
        self.s3.eliminar_todas_las_versiones(clave_s3)


def _rechazo(motivo: str) -> ResultadoImagen:
    return ResultadoImagen(util=False, motivo=motivo)
