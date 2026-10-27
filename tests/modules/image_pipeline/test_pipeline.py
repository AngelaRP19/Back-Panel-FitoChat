"""Pipeline de imágenes: descarga → validación → WebP sin metadatos → S3. Con S3 de moto y servicios externos simulados."""

import io
import random
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws
from PIL import Image

from app.core.config import Settings, get_settings
from app.infrastructure.rekognition_client import ErrorModeracion, ModeradorSimulado, ResultadoModeracion
from app.infrastructure.s3_storage_client import S3StorageClient
from app.infrastructure.whatsapp_client import WhatsAppSimulado
from app.modules.image_pipeline import pipeline as pl
from app.modules.image_pipeline.pipeline import BOGOTA, LADO_MAXIMO, PipelineReal

BUCKET = "anuc-evidencias-prueba"


# --- Imágenes sintéticas -----------------------------------------------------------------------------------------


def _ruido(valor_minimo: int, valor_maximo: int, lado: int = 192) -> Image.Image:
    azar = random.Random(7)
    datos = bytes(azar.randint(valor_minimo, valor_maximo) for _ in range(lado * lado * 3))
    return Image.frombytes("RGB", (lado, lado), datos)


def _bytes(imagen: Image.Image, formato: str = "JPEG", **opciones: Any) -> bytes:
    salida = io.BytesIO()
    imagen.save(salida, format=formato, **opciones)
    return salida.getvalue()


def nitida() -> bytes:
    return _bytes(_ruido(0, 255), quality=95)


def tablero(ancho: int, alto: int) -> Image.Image:
    """Bloques blancos y negros: nítida, de brillo medio y muy liviana aunque sea grande."""
    pequena = Image.new("L", (ancho // 40, alto // 40))
    pequena.putdata([255 if (i + i // (ancho // 40)) % 2 else 0 for i in range(len(pequena.getdata()))])
    return pequena.resize((ancho, alto), Image.Resampling.NEAREST).convert("RGB")


# --- Entorno -----------------------------------------------------------------------------------------------------


class ModeradorEspia(ModeradorSimulado):
    def __init__(self) -> None:
        super().__init__()
        self.recibidas: list[bytes] = []

    def analizar(self, imagen: bytes) -> ResultadoModeracion:
        self.recibidas.append(imagen)
        return super().analizar(imagen)


class Entorno:
    def __init__(self, crudo: Any, ajustes: Settings | None = None) -> None:
        self.crudo = crudo
        self.whatsapp = WhatsAppSimulado()
        self.moderador = ModeradorEspia()
        self.s3 = S3StorageClient(crudo, BUCKET)
        self.ids = iter(f"id{n}" for n in range(1, 100))
        self.pipeline = PipelineReal(
            self.whatsapp,
            self.moderador,
            self.s3,
            ajustes or get_settings(),
            ahora=lambda: datetime(2026, 10, 27, 9, 30, tzinfo=BOGOTA),
            generar_id=lambda: next(self.ids),
        )

    def claves(self) -> list[str]:
        return [o["Key"] for o in self.crudo.list_objects_v2(Bucket=BUCKET).get("Contents", [])]

    def procesar(self, contenido: bytes, descripcion: str | None = None) -> Any:
        self.whatsapp.medios["m1"] = contenido
        return self.pipeline.procesar("CAS-000001", "m1", descripcion)


@pytest.fixture
def entorno() -> Iterator[Entorno]:
    with mock_aws():
        crudo = boto3.client("s3", region_name="us-east-1")
        crudo.create_bucket(Bucket=BUCKET)
        crudo.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield Entorno(crudo)


# --- Camino feliz ------------------------------------------------------------------------------------------------


def test_una_foto_valida_se_convierte_a_webp_y_se_guarda_en_s3(entorno: Entorno) -> None:
    resultado = entorno.procesar(nitida(), "una hoja con manchas")
    assert resultado.util and resultado.tipo == "HOJA"
    assert resultado.clave_s3 == "evidencias/2026/10/id1.webp"
    objeto = entorno.crudo.get_object(Bucket=BUCKET, Key=resultado.clave_s3)
    contenido = objeto["Body"].read()
    assert objeto["ContentType"] == "image/webp" and contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP"
    assert Image.open(io.BytesIO(contenido)).format == "WEBP"


def test_el_tipo_sale_de_la_descripcion(entorno: Entorno) -> None:
    assert entorno.procesar(nitida(), "el fruto").tipo == "FRUTO"
    assert entorno.procesar(nitida(), "mira el tallo").tipo == "TALLO"
    assert entorno.procesar(nitida(), "el envés").tipo == "ENVES"
    assert entorno.procesar(nitida(), None).tipo == "HOJA"


def test_cada_foto_tiene_su_propia_clave(entorno: Entorno) -> None:
    claves = {entorno.procesar(nitida()).clave_s3 for _ in range(3)}
    assert claves == {f"evidencias/2026/10/id{n}.webp" for n in (1, 2, 3)}


def test_acepta_png_con_transparencia(entorno: Entorno) -> None:
    png = _bytes(_ruido(0, 255).convert("RGBA"), "PNG")
    assert entorno.procesar(png).util and len(entorno.claves()) == 1


def test_la_moderacion_recibe_el_original_no_el_webp(entorno: Entorno) -> None:
    original = nitida()
    entorno.procesar(original)
    assert entorno.moderador.recibidas == [original]


def test_se_quitan_los_metadatos_exif(entorno: Entorno) -> None:
    exif = Image.Exif()
    exif[0x010F] = "MarcaDelTelefono"  # fabricante
    exif[0x0110] = "ModeloSecreto"
    resultado = entorno.procesar(_bytes(_ruido(0, 255), exif=exif.tobytes(), quality=95))
    guardado = entorno.crudo.get_object(Bucket=BUCKET, Key=resultado.clave_s3)["Body"].read()
    assert b"MarcaDelTelefono" not in guardado and b"ModeloSecreto" not in guardado
    assert len(Image.open(io.BytesIO(guardado)).getexif()) == 0


def test_se_corrige_la_orientacion_antes_de_quitar_el_exif(entorno: Entorno) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotada 90°: el ancho y el alto se intercambian al enderezarla
    ancha = _ruido(0, 255, 160).resize((200, 100))
    resultado = entorno.procesar(_bytes(ancha, exif=exif.tobytes(), quality=95))
    guardado = Image.open(io.BytesIO(entorno.crudo.get_object(Bucket=BUCKET, Key=resultado.clave_s3)["Body"].read()))
    assert guardado.size == (100, 200)


def test_las_fotos_grandes_se_reducen(entorno: Entorno) -> None:
    resultado = entorno.procesar(_bytes(tablero(3200, 2000), quality=90))
    guardado = Image.open(io.BytesIO(entorno.crudo.get_object(Bucket=BUCKET, Key=resultado.clave_s3)["Body"].read()))
    assert resultado.util and max(guardado.size) == LADO_MAXIMO


# --- Rechazos: ninguna foto rechazada llega a S3 -------------------------------------------------------------------


def _rechazada(entorno: Entorno, contenido: bytes, motivo: str) -> None:
    resultado = entorno.procesar(contenido)
    assert not resultado.util and resultado.motivo == motivo and resultado.clave_s3 is None
    assert entorno.claves() == []


def test_si_meta_no_entrega_la_foto_se_pide_reenviarla(entorno: Entorno) -> None:
    resultado = entorno.pipeline.procesar("CAS-000001", "no-existe", None)
    assert not resultado.util and resultado.motivo == pl.MOTIVO_DESCARGA and entorno.moderador.recibidas == []


def test_mas_de_5_mb_se_rechaza_sin_decodificar(entorno: Entorno) -> None:
    _rechazada(entorno, b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024), pl.MOTIVO_TAMANO)


@pytest.mark.parametrize("formato", ["GIF", "BMP", "WEBP"])
def test_solo_se_aceptan_jpeg_y_png(entorno: Entorno, formato: str) -> None:
    _rechazada(entorno, _bytes(_ruido(0, 255), formato), pl.MOTIVO_FORMATO)


def test_un_archivo_que_no_es_imagen_o_esta_roto_se_rechaza(entorno: Entorno) -> None:
    _rechazada(entorno, b"esto no es una foto", pl.MOTIVO_FORMATO)
    _rechazada(entorno, nitida()[:2000], pl.MOTIVO_FORMATO)  # cortada a la mitad


def test_una_foto_muy_oscura(entorno: Entorno) -> None:
    _rechazada(entorno, _bytes(_ruido(0, 12)), pl.MOTIVO_OSCURA)


def test_una_foto_con_demasiada_luz(entorno: Entorno) -> None:
    _rechazada(entorno, _bytes(_ruido(245, 255)), pl.MOTIVO_CLARA)


def test_una_foto_borrosa_o_lisa(entorno: Entorno) -> None:
    _rechazada(entorno, _bytes(Image.new("RGB", (192, 192), (120, 160, 90))), pl.MOTIVO_BORROSA)


def test_contenido_inapropiado(entorno: Entorno) -> None:
    mala = nitida()
    entorno.moderador.inapropiadas.add(mala)
    _rechazada(entorno, mala, pl.MOTIVO_INAPROPIADA)


def test_lo_que_no_es_una_planta(entorno: Entorno) -> None:
    auto = nitida()
    entorno.moderador.no_plantas.add(auto)
    _rechazada(entorno, auto, pl.MOTIVO_NO_PLANTA)


def test_los_umbrales_de_calidad_vienen_de_la_configuracion(entorno: Entorno) -> None:
    lisa = _bytes(Image.new("RGB", (192, 192), (120, 160, 90)))
    permisivo = get_settings().model_copy(update={"foto_nitidez_minima": 0.0})
    otro = Entorno(entorno.crudo, permisivo)
    assert otro.procesar(lisa).util  # con nitidez mínima 0 ya no es «borrosa»
    estricto = Entorno(entorno.crudo, get_settings().model_copy(update={"foto_brillo_minimo": 90.0}))
    assert estricto.procesar(nitida()).motivo == pl.MOTIVO_OSCURA


# --- Fallas de servicios y borrado -----------------------------------------------------------------------------------


def test_si_rekognition_falla_sube_el_error_y_no_se_guarda_nada(entorno: Entorno) -> None:
    class Caido:
        def analizar(self, imagen: bytes) -> ResultadoModeracion:
            raise ErrorModeracion("Falló Rekognition")

    entorno.pipeline.moderador = Caido()
    entorno.whatsapp.medios["m1"] = nitida()
    with pytest.raises(ErrorModeracion):
        entorno.pipeline.procesar("CAS-000001", "m1", None)
    assert entorno.claves() == []


def test_eliminar_borra_todas_las_versiones(entorno: Entorno) -> None:
    clave = entorno.procesar(nitida()).clave_s3
    entorno.s3.subir(clave, b"otra version")  # el bucket tiene versionado: ahora hay dos versiones
    assert len(entorno.s3.listar_versiones(clave)) == 2
    entorno.pipeline.eliminar(clave)
    assert entorno.s3.listar_versiones(clave) == [] and entorno.claves() == []
