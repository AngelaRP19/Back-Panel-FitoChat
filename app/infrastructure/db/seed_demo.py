"""Datos de demostración (guía, módulo 1d): que el frontend pueda probarse con la API real sin WhatsApp.

    python -m app.infrastructure.db.seed_demo                  siembra (idempotente: si ya hay datos demo, no hace nada)
    python -m app.infrastructure.db.seed_demo --limpiar        borrado físico de todo lo demo, fotos y versiones en S3
    python -m app.infrastructure.db.seed_demo --permitir-demo  obligatorio con APP_ENV=prod

Todo dato demo es reconocible: los nombres llevan el sufijo «(demo)», los celulares están en el rango 3000000xxx, los
correos son @demo.anuc.test y no hay datos personales reales. Cada caso recorre los estados de verdad (reloj y actor
simulados) para que los triggers escriban su línea de tiempo. No usa los `sp_*` que escriben auditoría: esa tabla es
inmutable y impediría borrar las cuentas demo después.

Las preguntas del cuestionario son de demostración (sufijo «(demo)»): el texto real del chatbot aún no está definido
(P-B10) y se borra con --limpiar.
"""

import argparse
import io
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core import catalogos_api as api
from app.core.config import get_database_settings, get_settings
from app.core.logging import configure_logging
from app.core.passwords import unusable_password_hash
from app.core.telefonos import PREFIJO_DEMO
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.ids import siguiente_id
from app.infrastructure.db.models import (
    Alerta,
    AlertaDestinatario,
    Asignacion,
    Caso,
    CategoriaFitosanitaria,
    CoincidenciaCategoria,
    Cultivo,
    CultivoEspecie,
    Diagnostico,
    Especie,
    Finca,
    Fotografia,
    IdentificacionPreliminar,
    LiderTecnico,
    Pregunta,
    Productor,
    Respuesta,
    Tecnico,
)
from app.infrastructure.db.seed import seed
from app.infrastructure.s3_storage_client import S3StorageClient, build_boto3_s3_client

logger = logging.getLogger(__name__)

MARCA = "(demo)"
DOMINIO_CORREO = "demo.anuc.test"
PREFIJO_TELEFONO = PREFIJO_DEMO  # celulares 3000000xxx en E.164
MESES_CONSERVACION = 6

CE = api.EstadoCaso
CAT = api.CategoriaFito
ESP = api.EspecieCultivo


def telefono(n: int) -> str:
    return f"{PREFIJO_TELEFONO}{n:03d}"


# --- Cuentas -------------------------------------------------------------------------------------------------------

LIDER = ("Luis Pérez", "luis.perez", 100)
TECNICO_DEL_LIDER = ("Luis Pérez (técnico)", "luis.perez.tecnico", 101)  # el líder atiende casos con otra cuenta (D-02)
TECNICOS = {"ana": ("Ana Torres", "ana.torres", 102), "beto": ("Beto Quintero", "beto.quintero", 103)}

# --- Territorio ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FincaDemo:
    n: int  # 1..9: sufijo del celular del productor
    productor: str
    finca: str
    vereda: str
    lat: str
    lon: str
    tipo_cultivo: str
    especies: tuple[ESP, ...]


# Centro (4 fincas a ~0,4 km entre sí), Juracambita (2) y San Luis (3, a más de 7 km del Centro).
FINCAS = [
    FincaDemo(1, "Carlos Mora", "La Esperanza", "Centro", "5.2800", "-73.1700", "CAMPO_ABIERTO", (ESP.GULUPA,)),
    FincaDemo(2, "Marta Rojas", "El Mirador", "Centro", "5.2835", "-73.1700", "INVERNADERO", (ESP.MARACUYA, ESP.GRANADILLA)),
    FincaDemo(3, "Jorge Pineda", "Buenavista", "Centro", "5.2870", "-73.1700", "CAMPO_ABIERTO", (ESP.GRANADILLA,)),
    FincaDemo(4, "Luz Acosta", "San Rafael", "Centro", "5.2905", "-73.1700", "INVERNADERO", (ESP.GULUPA,)),
    FincaDemo(5, "Pedro Salinas", "Juracambita Alta", "Juracambita", "5.2600", "-73.1300", "CAMPO_ABIERTO", (ESP.GULUPA,)),
    FincaDemo(6, "Ana Duarte", "Los Naranjos", "Juracambita", "5.2640", "-73.1330", "CAMPO_ABIERTO", (ESP.MARACUYA,)),
    FincaDemo(7, "Rosa Camargo", "El Recuerdo", "San Luis", "5.2300", "-73.2200", "CAMPO_ABIERTO", (ESP.MARACUYA,)),
    FincaDemo(8, "Luis Cárdenas", "La Palma", "San Luis", "5.2330", "-73.2200", "INVERNADERO", (ESP.GULUPA,)),
    FincaDemo(9, "Nelly Vargas", "Santa Ana", "San Luis", "5.2360", "-73.2200", "CAMPO_ABIERTO", (ESP.GRANADILLA,)),
]  # fmt: skip

# --- Casos ---------------------------------------------------------------------------------------------------------

RUTA = [CE.CREADO, CE.EN_ANALISIS, CE.PENDIENTE_REVISION, CE.EN_REVISION, CE.REGISTRADO, CE.CERRADO]
# Tiempo entre un estado de la ruta y el siguiente.
DURACION = {
    CE.CREADO: timedelta(minutes=30),
    CE.EN_ANALISIS: timedelta(minutes=90),
    CE.PENDIENTE_REVISION: timedelta(hours=3),
    CE.EN_REVISION: timedelta(hours=20),
    CE.REGISTRADO: timedelta(hours=4),
}
DESCARTE_TRAS = timedelta(hours=72)

Analisis = tuple[str, dict[CAT, str]]  # (resultado del análisis, % de coincidencia por categoría)


@dataclass(frozen=True)
class CasoDemo:
    finca: int
    estado: CE
    hace: timedelta  # cuánto hace que el caso llegó a su estado actual
    analisis: Analisis | None = None
    tecnico: str | None = None  # 'ana', 'beto' o 'lider' (la cuenta de técnico del líder)
    resultado: api.ResultadoTecnico | None = None
    categoria: CAT | None = None
    vence_en_dias: int | None = None  # solo casos Cerrados: días que faltan para el vencimiento
    fotos: tuple[api.TipoEvidencia, ...] = ()
    advertido: bool = False


def H(n: float) -> timedelta:
    return timedelta(hours=n)


def D(n: float) -> timedelta:
    return timedelta(days=n)


HOJA, ENVES, FRUTO, TALLO = api.TipoEvidencia
CONF = api.ResultadoTecnico.CONFIRMADO
OTRA = api.ResultadoTecnico.OTRA_PATOLOGIA
NOID = api.ResultadoTecnico.NO_IDENTIFICADO
VISITA = api.ResultadoTecnico.REQUIERE_VISITA
PRELIMINAR = "PRELIMINAR"

CASOS: list[CasoDemo] = [
    # Antes de llegar al panel: los que atiende el chatbot (los jobs de 48 h y 72 h miran estos).
    CasoDemo(5, CE.CREADO, H(2)),
    CasoDemo(6, CE.EN_ANALISIS, H(50), fotos=(HOJA,)),  # a punto de recibir la advertencia
    CasoDemo(5, CE.EN_ANALISIS, H(5), fotos=(HOJA, ENVES)),
    CasoDemo(6, CE.EN_ANALISIS, H(60), fotos=(HOJA,), advertido=True),  # ya advertido, cerca del descarte
    # Pendientes de revisión: identificada, ambigua y no identificada.
    CasoDemo(1, CE.PENDIENTE_REVISION, H(6), (PRELIMINAR, {CAT.BOTRYTIS: "78.00", CAT.RONA: "22.00"}), fotos=(HOJA, ENVES, FRUTO)),
    CasoDemo(2, CE.PENDIENTE_REVISION, H(30), ("AMBIGUO", {CAT.BOTRYTIS: "58.00", CAT.RONA: "52.00"}), fotos=(HOJA, ENVES)),
    CasoDemo(6, CE.PENDIENTE_REVISION, D(3), ("NO_IDENTIFICADO", {CAT.FUSARIUM: "32.00", CAT.BOTRYTIS: "20.00"}), fotos=(TALLO,)),
    # En revisión: uno con un técnico y otro con la cuenta de técnico del líder.
    CasoDemo(3, CE.EN_REVISION, H(10), (PRELIMINAR, {CAT.RONA: "81.00", CAT.BOTRYTIS: "19.00"}), "ana", fotos=(HOJA, FRUTO)),
    CasoDemo(5, CE.EN_REVISION, D(2), (PRELIMINAR, {CAT.FUSARIUM: "66.00", CAT.RONA: "34.00"}), "lider", fotos=(TALLO, HOJA)),
    # Registrados: confirmado, y que requiere visita.
    CasoDemo(4, CE.REGISTRADO, D(1), (PRELIMINAR, {CAT.BOTRYTIS: "74.00", CAT.RONA: "26.00"}), "beto", CONF, CAT.BOTRYTIS, fotos=(HOJA, ENVES)),
    CasoDemo(6, CE.REGISTRADO, D(4), ("AMBIGUO", {CAT.FUSARIUM: "55.00", CAT.BOTRYTIS: "51.00"}), "ana", VISITA, None, fotos=(TALLO, HOJA)),
    # Cerrados con distinto vencimiento (la lista de próximos a vencer).
    CasoDemo(5, CE.CERRADO, D(170), (PRELIMINAR, {CAT.DEFICIENCIA_MAGNESIO: "70.00", CAT.RONA: "30.00"}), "beto", CONF, CAT.DEFICIENCIA_MAGNESIO, 15, (HOJA, ENVES)),
    CasoDemo(6, CE.CERRADO, D(110), ("AMBIGUO", {CAT.FUSARIUM: "57.00", CAT.RONA: "53.00"}), "ana", OTRA, CAT.FUSARIUM, 70, (TALLO, HOJA)),
    CasoDemo(2, CE.CERRADO, D(30), ("NO_IDENTIFICADO", {CAT.BOTRYTIS: "28.00"}), "beto", NOID, None, 150, (HOJA,)),
    # Descartado por inactividad.
    CasoDemo(3, CE.DESCARTADO, D(2), fotos=(HOJA,)),
]  # fmt: skip

TRIPS = (PRELIMINAR, {CAT.TRIPS: "85.00", CAT.BOTRYTIS: "15.00"})
# Cuatro casos de Trips confirmados en el Centro (alerta GENERADA) y tres en San Luis, hace casi un mes (alerta COMUNICADA).
CASOS_TRIPS_CENTRO = [
    CasoDemo(1, CE.CERRADO, D(9), TRIPS, "ana", CONF, CAT.TRIPS, 175, (HOJA, ENVES)),
    CasoDemo(2, CE.CERRADO, D(6), TRIPS, "beto", CONF, CAT.TRIPS, 178, (HOJA, FRUTO)),
    CasoDemo(3, CE.REGISTRADO, D(3), TRIPS, "ana", CONF, CAT.TRIPS, None, (ENVES, HOJA)),
    CasoDemo(4, CE.REGISTRADO, D(1), TRIPS, "beto", CONF, CAT.TRIPS, None, (HOJA,)),
]  # fmt: skip
CASOS_TRIPS_SAN_LUIS = [
    CasoDemo(7, CE.CERRADO, D(26), TRIPS, "ana", CONF, CAT.TRIPS, 154, (HOJA, ENVES)),
    CasoDemo(8, CE.CERRADO, D(25), TRIPS, "beto", CONF, CAT.TRIPS, 155, (HOJA,)),
    CasoDemo(9, CE.CERRADO, D(24), TRIPS, "ana", CONF, CAT.TRIPS, 156, (ENVES, FRUTO)),
]  # fmt: skip

PREGUNTAS = [f"Pregunta de demostración {i} {MARCA}" for i in (1, 2, 3)]


# --- Fotos sintéticas ------------------------------------------------------------------------------------------------

_COLORES = {"HOJA": (76, 140, 60), "ENVES": (120, 160, 90), "FRUTO": (150, 90, 150), "TALLO": (140, 110, 70)}


def foto_demo(tipo: str) -> bytes:
    """Imagen sintética (degradado con rótulo) convertida a WebP: no es una foto real de ningún cultivo."""
    base = _COLORES[tipo]
    imagen = Image.new("RGB", (640, 480))
    dibujo = ImageDraw.Draw(imagen)
    for y in range(480):
        factor = 0.55 + 0.45 * y / 480
        dibujo.line([(0, y), (640, y)], fill=tuple(int(c * factor) for c in base))
    dibujo.text((24, 24), f"DEMO · {tipo}", fill=(255, 255, 255))
    salida = io.BytesIO()
    imagen.save(salida, format="WEBP", quality=75)
    return salida.getvalue()


def clave_s3(id_fotografia: str, cuando: datetime) -> str:
    """Misma forma que usará el pipeline real: evidencias/<año>/<mes>/<id>.webp."""
    return f"evidencias/{cuando.year}/{cuando.month:02d}/{id_fotografia}.webp"


# --- Siembra ---------------------------------------------------------------------------------------------------------


def ya_sembrado(session: Session) -> bool:
    return bool(
        session.scalar(
            text("SELECT EXISTS (SELECT 1 FROM productores WHERE telefono_productor LIKE :p)"),
            {"p": f"{PREFIJO_TELEFONO}%"},
        )
    )


def _fijar(session: Session, cuando: datetime, actor: tuple[str, str] = ("", "")) -> None:
    """Reloj y actor de la transacción: los triggers anotan los eventos con esta hora y este autor."""
    session.execute(sp.ESTABLECER_AHORA, {"ahora": cuando.isoformat()})
    session.execute(sp.ESTABLECER_ACTOR, {"tipo": actor[0], "id": actor[1]})


def _estado(session: Session, caso: Caso, nombre: CE, **columnas: Any) -> None:
    asignaciones = "".join(f", {c} = :{c}" for c in columnas)
    session.execute(
        text(f"UPDATE casos SET id_estado = fn_id_estado(:nombre){asignaciones} WHERE id_caso = :id"),
        {"nombre": api.ESTADOS_CASO.a_nombre(nombre), "id": caso.id_caso, **columnas},
    )


class _Siembra:
    def __init__(self, session: Session, s3: S3StorageClient, ahora: datetime) -> None:
        self.session, self.s3, self.ahora = session, s3, ahora
        self.cuentas: dict[str, Any] = {}
        self.cultivos: dict[int, Cultivo] = {}
        self.especies: dict[int, list[Especie]] = {}
        self.preguntas: list[Pregunta] = []
        self.subidas: list[str] = []
        self.imagenes = {t.value: foto_demo(t.value) for t in api.TipoEvidencia}
        self.fotos = 0
        self.categorias = {
            c: session.scalars(
                select(CategoriaFitosanitaria).where(
                    CategoriaFitosanitaria.nombre_categoria == api.CATEGORIAS.a_nombre(c)
                )
            ).one()
            for c in CAT
        }

    # cuentas ------------------------------------------------------------------------------------------------------
    def _sub(self, id_cuenta: str) -> str | None:
        """Con claves de prueba (local y test) la cuenta queda enlazada a un sub fijo; en prod, a Cognito lo enlaza el usuario."""
        return None if get_settings().en_prod else f"demo-{id_cuenta}"

    def cuentas_demo(self) -> None:
        s = self.session
        nombre, usuario, n = LIDER
        lider = LiderTecnico(
            nombre_lider_tecnico=f"{nombre} {MARCA}",
            correo_lider_tecnico=f"{usuario}@{DOMINIO_CORREO}",
            celular_lider_tecnico=telefono(n),
            contrasena_lider_tecnico=unusable_password_hash(),
            estado_cuenta_lider_tecnico="ACTIVO",
        )
        s.add(lider)
        s.flush()
        lider.cognito_sub = self._sub(lider.id_lider_tecnico)
        self.cuentas["lider_cuenta"] = lider
        for clave, (nombre, usuario, n) in {"lider": TECNICO_DEL_LIDER, **TECNICOS}.items():
            tecnico = Tecnico(
                nombre_tecnico=f"{nombre} {MARCA}",
                correo_tecnico=f"{usuario}@{DOMINIO_CORREO}",
                celular_tecnico=telefono(n),
                contrasena_tecnico=unusable_password_hash(),
                estado_cuenta_tecnico="ACTIVO",
                fecha_registro_tecnico=self.ahora.date() - timedelta(days=90),
            )
            s.add(tecnico)
            s.flush()
            tecnico.cognito_sub = self._sub(tecnico.id_tecnico)
            self.cuentas[clave] = tecnico
        s.flush()

    # territorio ---------------------------------------------------------------------------------------------------
    def territorio(self) -> None:
        s = self.session
        for f in FINCAS:
            productor = Productor(
                nombre_productor=f"{f.productor} {MARCA}",
                telefono_productor=telefono(f.n),
                estado_cuenta_productor="ACTIVO",
                fecha_registro_productor=self.ahora.date() - timedelta(days=60),
                fecha_consentimiento=self.ahora - timedelta(days=60),
            )
            finca = Finca(
                nombre_finca=f"{f.finca} {MARCA}",
                vereda_sector_finca=f.vereda,
                latitud_finca=Decimal(f.lat),
                longitud_finca=Decimal(f.lon),
                productor=productor,
            )
            cultivo = Cultivo(
                nombre_cultivo=f"Lote principal {MARCA}",
                latitud_cultivo=Decimal(f.lat),
                longitud_cultivo=Decimal(f.lon),
                finca=finca,
                tipo_cultivo=f.tipo_cultivo,
            )
            s.add_all([productor, finca, cultivo])
            s.flush()
            especies = [
                s.scalars(select(Especie).where(Especie.nombre_especie == api.ESPECIES.a_nombre(e))).one()
                for e in f.especies
            ]
            for especie in especies:
                s.add(CultivoEspecie(id_cultivo=cultivo.id_cultivo, id_especie=especie.id_especie))
            self.cultivos[f.n], self.especies[f.n] = cultivo, especies
        for texto in PREGUNTAS:
            pregunta = Pregunta(texto_pregunta=texto)
            s.add(pregunta)
            self.preguntas.append(pregunta)
        s.flush()

    # casos --------------------------------------------------------------------------------------------------------
    def _tiempos(self, plan: CasoDemo) -> dict[CE, datetime]:
        """Hora en que el caso llegó a cada estado de su ruta, hacia atrás desde el estado actual."""
        final = self.ahora - plan.hace
        if plan.estado is CE.DESCARTADO:
            return {
                CE.CREADO: final - DESCARTE_TRAS,
                CE.EN_ANALISIS: final - DESCARTE_TRAS + DURACION[CE.CREADO],
                CE.DESCARTADO: final,
            }
        if plan.estado is CE.CERRADO and plan.vence_en_dias is not None:
            final = self.ahora + timedelta(days=plan.vence_en_dias) - timedelta(days=MESES_CONSERVACION * 30.5)
        tiempos: dict[CE, datetime] = {plan.estado: final}
        for estado in reversed(RUTA[: RUTA.index(plan.estado)]):
            tiempos[estado] = tiempos[RUTA[RUTA.index(estado) + 1]] - DURACION[estado]
        return tiempos

    def caso(self, plan: CasoDemo) -> Caso:
        s = self.session
        t = self._tiempos(plan)
        cultivo, especie = self.cultivos[plan.finca], self.especies[plan.finca][0]
        inicio = t[CE.CREADO]
        _fijar(s, inicio)
        caso = Caso(
            id_cultivo=cultivo.id_cultivo,
            id_especie=especie.id_especie,
            id_estado=s.scalar(sp.ID_ESTADO, {"nombre": api.ESTADOS_CASO.a_nombre(CE.CREADO)}),
            descripcion_zona_afectada=f"Hojas y frutos del lote principal {MARCA}",
            fecha_creacion_caso=inicio,
            ultima_actividad=inicio,
            advertencia_inactividad_enviada="S" if plan.advertido else "N",
        )
        s.add(caso)
        s.flush()
        diagnostico = Diagnostico(id_caso=caso.id_caso)  # la fila nace vacía junto con el caso (D-05)
        s.add(diagnostico)
        s.flush()

        if plan.estado is CE.CREADO:
            return caso
        if CE.EN_ANALISIS in t:
            _fijar(s, t[CE.EN_ANALISIS])
            _estado(s, caso, CE.EN_ANALISIS, ultima_actividad=t[CE.EN_ANALISIS])
            self._respuestas_y_fotos(diagnostico, plan, t[CE.EN_ANALISIS])
        if plan.estado is CE.DESCARTADO:
            _fijar(s, t[CE.DESCARTADO])
            _estado(s, caso, CE.DESCARTADO, ultima_actividad=t[CE.EN_ANALISIS])
            return caso
        if plan.estado is CE.EN_ANALISIS:
            return caso

        _fijar(s, t[CE.PENDIENTE_REVISION])
        identificacion = self._identificacion(plan.analisis, t[CE.PENDIENTE_REVISION])
        _estado(
            s,
            caso,
            CE.PENDIENTE_REVISION,
            id_identificacion_preliminar=identificacion.id_identificacion,
            fecha_envio_panel=t[CE.PENDIENTE_REVISION],
            ultima_actividad=t[CE.PENDIENTE_REVISION],
        )
        if plan.estado is CE.PENDIENTE_REVISION:
            return caso

        tecnico = self.cuentas[plan.tecnico or "ana"]
        s.add(Asignacion(id_caso=caso.id_caso, id_tecnico=tecnico.id_tecnico, fecha_asignacion=t[CE.EN_REVISION]))
        s.flush()
        _fijar(s, t[CE.EN_REVISION], ("LIDER", self.cuentas["lider_cuenta"].id_lider_tecnico))
        _estado(s, caso, CE.EN_REVISION)
        if plan.estado is CE.EN_REVISION:
            return caso

        _fijar(s, t[CE.REGISTRADO], ("TECNICO", tecnico.id_tecnico))
        categoria = self.categorias[plan.categoria].id_categoria if plan.categoria else None
        s.execute(
            text(
                "UPDATE diagnosticos SET resultado_tecnico = :r, id_categoria_confirmada = :c, motivo_visita = :m, "
                "descripcion_diagnostico = :d, fecha_diagnostico = :f WHERE id_caso = :id"
            ),
            {
                "r": plan.resultado.value if plan.resultado else None,
                "c": categoria,
                "m": f"Revisar el lote en campo {MARCA}" if plan.resultado is VISITA else None,
                "d": f"Diagnóstico de demostración {MARCA}",
                "f": t[CE.REGISTRADO],
                "id": caso.id_caso,
            },
        )
        _estado(s, caso, CE.REGISTRADO)
        if plan.estado is CE.REGISTRADO:
            return caso

        actor = (
            ("LIDER", self.cuentas["lider_cuenta"].id_lider_tecnico)
            if plan.tecnico == "lider"
            else ("TECNICO", tecnico.id_tecnico)
        )
        _fijar(s, t[CE.CERRADO], actor)
        s.execute(
            text(
                "UPDATE casos SET id_estado = fn_id_estado(:n), fecha_cierre = :f, "
                "vence_en = (timezone('America/Bogota', CAST(:f AS timestamptz))::date + make_interval(months => :m))::date "
                "WHERE id_caso = :id"
            ),
            {
                "n": api.ESTADOS_CASO.a_nombre(CE.CERRADO),
                "f": t[CE.CERRADO],
                "m": MESES_CONSERVACION,
                "id": caso.id_caso,
            },
        )
        return caso

    def _identificacion(self, analisis: Analisis | None, cuando: datetime) -> IdentificacionPreliminar:
        assert analisis is not None, "un caso en el panel necesita su identificación preliminar"
        resultado, porcentajes = analisis
        mejor = max(porcentajes, key=lambda c: Decimal(porcentajes[c]))
        identificacion = IdentificacionPreliminar(
            tipo_resultado=resultado,
            explicacion_resultado=f"Resultado preliminar de demostración {MARCA}: coincidencia de síntomas con {api.CATEGORIAS.a_nombre(mejor)}.",
            fecha_analisis=cuando,
            detalle_explicacion={
                api.CATEGORIAS.a_codigo(api.CATEGORIAS.a_nombre(c)): {
                    "porcentaje": float(p),
                    "coinciden": ["síntoma de demostración"],
                    "descartan": [],
                }
                for c, p in porcentajes.items()
            },
        )
        self.session.add(identificacion)
        self.session.flush()
        for categoria, porcentaje in porcentajes.items():
            self.session.add(
                CoincidenciaCategoria(
                    id_identificacion=identificacion.id_identificacion,
                    id_categoria=self.categorias[categoria].id_categoria,
                    porcentaje_coincidencia=Decimal(porcentaje),
                )
            )
        self.session.flush()
        return identificacion

    def _respuestas_y_fotos(self, diagnostico: Diagnostico, plan: CasoDemo, cuando: datetime) -> None:
        s = self.session
        respuestas = []
        for i, pregunta in enumerate(self.preguntas[:2]):
            respuesta = Respuesta(
                id_pregunta=pregunta.id_pregunta,
                id_diagnostico=diagnostico.id_diagnostico,
                texto_respuesta=f"Respuesta de demostración {i + 1} {MARCA}",
                fecha_respuesta=cuando + timedelta(minutes=2 * i),
            )
            s.add(respuesta)
            respuestas.append(respuesta)
        s.flush()
        for i, tipo in enumerate(plan.fotos):
            id_foto = siguiente_id(s.connection(), "fotografias")
            enviada = cuando + timedelta(minutes=5 + i)
            clave = clave_s3(id_foto, enviada)
            s.add(
                Fotografia(
                    id_fotografia=id_foto,
                    id_respuesta=respuestas[i % len(respuestas)].id_respuesta,
                    tipo_evidencia=tipo.value,
                    ruta_fotografia=clave,
                    fecha_envio_fotografia=enviada,
                    es_util="N" if (i == 2 and len(plan.fotos) == 3) else "S",  # una foto descartada
                )
            )
            s.flush()
            self.s3.subir(clave, self.imagenes[tipo.value])
            self.subidas.append(clave)
            self.fotos += 1

    # alertas ------------------------------------------------------------------------------------------------------
    def alerta_comunicada(self) -> str:
        """Tres casos de Trips en San Luis hace casi un mes: se detecta la alerta y el líder la comunica (simulado)."""
        s = self.session
        for plan in CASOS_TRIPS_SAN_LUIS:
            self.caso(plan)
        trips = self.categorias[CAT.TRIPS].id_categoria
        detectada = self.ahora - D(23)
        s.execute(sp.EVALUAR_ALERTA, {"id_categoria": trips, "fecha": detectada})
        alerta = s.scalars(select(Alerta).where(Alerta.id_categoria == trips).order_by(Alerta.id_alerta.desc())).first()
        assert alerta is not None, "la evaluación debía generar la alerta de San Luis"
        lider = self.cuentas["lider_cuenta"]
        enviada = detectada + timedelta(hours=5)
        alerta.estado_alerta = "COMUNICADA"
        alerta.id_lider_gestion = lider.id_lider_tecnico
        alerta.fecha_confirmacion = detectada + timedelta(hours=2)
        alerta.recomendacion = f"Revise el envés de las hojas y avise al técnico si ve insectos {MARCA}"
        alerta.mensaje_enviado = f"Aviso fitosanitario de demostración {MARCA}: {alerta.recomendacion}"
        alerta.fecha_comunicacion_alerta = enviada.date()
        s.flush()
        destinatarios = s.scalars(
            text(
                "SELECT DISTINCT f2.id_productor FROM alerta_casos ac "
                "JOIN casos c ON c.id_caso = ac.id_caso JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo "
                "JOIN fincas f1 ON f1.id_finca = cu.id_finca "
                "JOIN fincas f2 ON fn_distancia_km(f1.ubicacion, f2.ubicacion) <= :km "
                "JOIN productores p ON p.id_productor = f2.id_productor "
                "WHERE ac.id_alerta = :a AND p.estado_cuenta_productor = 'ACTIVO' AND p.telefono_productor LIKE :p "
                "ORDER BY 1"
            ),
            {"km": alerta.distancia_km, "a": alerta.id_alerta, "p": f"{PREFIJO_TELEFONO}%"},
        ).all()
        for id_productor in destinatarios:
            s.add(
                AlertaDestinatario(
                    id_alerta=alerta.id_alerta,
                    id_productor=id_productor,
                    tipo_envio=api.TipoEnvio.ALERTA.value,
                    estado_envio="SIMULADO",  # nunca ENVIADO: en demo no sale ningún mensaje real
                    fecha_envio=enviada,
                )
            )
        s.flush()
        return str(alerta.id_alerta)

    def alerta_generada(self) -> str:
        """Cuatro casos de Trips en el Centro, confirmados esta semana: la evaluación genera la alerta GENERADA."""
        s = self.session
        for plan in CASOS_TRIPS_CENTRO:
            self.caso(plan)
        trips = self.categorias[CAT.TRIPS].id_categoria
        _fijar(s, self.ahora)
        s.execute(sp.EVALUAR_ALERTA, {"id_categoria": trips, "fecha": self.ahora})
        alerta = s.scalars(select(Alerta).where(Alerta.id_categoria == trips, Alerta.estado_alerta == "GENERADA")).one()
        return str(alerta.id_alerta)


def sembrar(session: Session, s3: S3StorageClient, ahora: datetime | None = None) -> dict[str, Any]:
    """Siembra los datos demo en la transacción de la sesión (el que llama confirma). Idempotente."""
    if ya_sembrado(session):
        return {"ya_estaba": True}
    ahora = ahora or datetime.now(UTC)
    seed(session.connection())  # catálogos reales primero
    siembra = _Siembra(session, s3, ahora)
    try:
        siembra.cuentas_demo()
        siembra.territorio()
        for plan in CASOS:
            siembra.caso(plan)
        comunicada = siembra.alerta_comunicada()
        generada = siembra.alerta_generada()
    except Exception:
        for clave in siembra.subidas:  # no dejar fotos huérfanas si la siembra se revierte
            s3.eliminar_todas_las_versiones(clave)
        raise
    _fijar(session, ahora)
    session.execute(sp.ESTABLECER_ACTOR, {"tipo": "", "id": ""})
    session.flush()
    return {
        "ya_estaba": False,
        "casos": len(CASOS) + len(CASOS_TRIPS_CENTRO) + len(CASOS_TRIPS_SAN_LUIS),
        "fotos": siembra.fotos,
        "alerta_generada": generada,
        "alerta_comunicada": comunicada,
    }


# --- Limpieza --------------------------------------------------------------------------------------------------------


def limpiar(session: Session, s3: S3StorageClient) -> dict[str, int]:
    """Borrado físico de todo lo demo. Primero S3 (todas las versiones): si falla, no se borra ninguna fila."""
    patron = {"p": f"{PREFIJO_TELEFONO}%"}
    ids_caso = list(
        session.scalars(
            text(
                "SELECT c.id_caso FROM casos c JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo "
                "JOIN fincas f ON f.id_finca = cu.id_finca JOIN productores p ON p.id_productor = f.id_productor "
                "WHERE p.telefono_productor LIKE :p"
            ),
            patron,
        )
    )
    claves = list(
        session.scalars(
            text(
                "SELECT ft.ruta_fotografia FROM fotografias ft JOIN respuestas r ON r.id_respuesta = ft.id_respuesta "
                "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico WHERE d.id_caso = ANY(:ids)"
            ),
            {"ids": ids_caso},
        )
    )
    versiones = sum(s3.eliminar_todas_las_versiones(clave) for clave in claves)

    ids_alerta = list(
        session.scalars(
            text("SELECT DISTINCT id_alerta FROM alerta_casos WHERE id_caso = ANY(:ids)"), {"ids": ids_caso}
        )
    )
    ids_identificacion = list(
        session.scalars(
            text(
                "SELECT DISTINCT id_identificacion_preliminar FROM casos WHERE id_caso = ANY(:ids) AND id_identificacion_preliminar IS NOT NULL"
            ),
            {"ids": ids_caso},
        )
    )
    a, c, ident = {"a": ids_alerta}, {"ids": ids_caso}, {"i": ids_identificacion}
    borrados: dict[str, int] = {}

    def borrar(nombre: str, sql: str, parametros: dict[str, Any]) -> None:
        borrados[nombre] = session.execute(text(sql), parametros).rowcount  # type: ignore[attr-defined]

    borrar("alerta_destinatarios", "DELETE FROM alerta_destinatarios WHERE id_alerta = ANY(:a)", a)
    borrar("alerta_casos", "DELETE FROM alerta_casos WHERE id_alerta = ANY(:a)", a)
    borrar("alertas", "DELETE FROM alertas WHERE id_alerta = ANY(:a)", a)
    borrar("fotografias", "DELETE FROM fotografias WHERE ruta_fotografia = ANY(:k)", {"k": claves})
    borrar(
        "respuestas",
        "DELETE FROM respuestas WHERE id_diagnostico IN (SELECT id_diagnostico FROM diagnosticos WHERE id_caso = ANY(:ids))",
        c,
    )
    borrar("diagnosticos", "DELETE FROM diagnosticos WHERE id_caso = ANY(:ids)", c)
    borrar("eventos", "DELETE FROM eventos WHERE id_caso = ANY(:ids)", c)
    borrar("asignaciones", "DELETE FROM asignaciones WHERE id_caso = ANY(:ids)", c)
    borrar("casos", "DELETE FROM casos WHERE id_caso = ANY(:ids)", c)
    borrar("coincidencias_categoria", "DELETE FROM coincidencias_categoria WHERE id_identificacion = ANY(:i)", ident)
    borrar(
        "identificacion_preliminar", "DELETE FROM identificacion_preliminar WHERE id_identificacion = ANY(:i)", ident
    )
    borrar("preguntas", "DELETE FROM preguntas WHERE texto_pregunta LIKE :m", {"m": f"%{MARCA}"})
    cultivos = "(SELECT cu.id_cultivo FROM cultivos cu JOIN fincas f ON f.id_finca = cu.id_finca JOIN productores p ON p.id_productor = f.id_productor WHERE p.telefono_productor LIKE :p)"
    borrar("cultivo_especie", f"DELETE FROM cultivo_especie WHERE id_cultivo IN {cultivos}", patron)
    borrar("cultivos", f"DELETE FROM cultivos WHERE id_cultivo IN {cultivos}", patron)
    borrar(
        "fincas",
        "DELETE FROM fincas WHERE id_productor IN (SELECT id_productor FROM productores WHERE telefono_productor LIKE :p)",
        patron,
    )
    borrar("productores", "DELETE FROM productores WHERE telefono_productor LIKE :p", patron)
    # Una cuenta que ya tiene auditoría (alguien la usó en el panel) no se puede borrar: la auditoría es inmutable.
    correo = {"c": f"%@{DOMINIO_CORREO}"}
    borrar(
        "tecnicos",
        "DELETE FROM tecnicos WHERE correo_tecnico LIKE :c AND id_tecnico NOT IN (SELECT id_tecnico FROM auditoria WHERE id_tecnico IS NOT NULL)",
        correo,
    )
    borrar(
        "lideres_tecnicos",
        "DELETE FROM lideres_tecnicos WHERE correo_lider_tecnico LIKE :c AND id_lider_tecnico NOT IN (SELECT id_lider_tecnico FROM auditoria WHERE id_lider_tecnico IS NOT NULL)",
        correo,
    )
    borrados["objetos_s3"] = len(claves)
    borrados["versiones_s3"] = versiones
    return borrados


# --- Comando ---------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Datos de demostración del panel")
    parser.add_argument("--limpiar", action="store_true", help="elimina todo lo demo (base y S3, con sus versiones)")
    parser.add_argument("--permitir-demo", action="store_true", help="obligatorio con APP_ENV=prod")
    args = parser.parse_args(argv)

    configure_logging("INFO")
    settings = get_settings()
    if settings.en_prod and not args.permitir_demo:
        print("En prod los datos de demostración exigen --permitir-demo", file=sys.stderr)
        return 2
    if not settings.s3_bucket_evidencias:
        print("Falta S3_BUCKET_EVIDENCIAS", file=sys.stderr)
        return 2

    s3 = S3StorageClient(build_boto3_s3_client(), settings.s3_bucket_evidencias)
    engine = create_engine(get_database_settings().sqlalchemy_url)
    with Session(engine) as session, session.begin():
        resultado = limpiar(session, s3) if args.limpiar else sembrar(session, s3)
    logger.info(
        "Datos de demostración", extra={"resultado": resultado, "accion": "limpiar" if args.limpiar else "sembrar"}
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
