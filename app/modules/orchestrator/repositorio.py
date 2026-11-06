"""SQL del chatbot: productor, finca, cultivo, caso, respuestas y fotos, en la transacción del mensaje."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalogos_api import CATEGORIAS, ESPECIES, ESTADOS_CASO, TIPOS_CULTIVO, EstadoCaso
from app.core.evidencias import PREGUNTA_FOTO
from app.infrastructure.db import procedimientos as sp
from app.infrastructure.db.models import (
    Caso,
    Cultivo,
    CultivoEspecie,
    Diagnostico,
    Fotografia,
    Pregunta,
    Productor,
    Respuesta,
)

CREADO = ESTADOS_CASO.a_nombre(EstadoCaso.CREADO)
EN_ANALISIS = ESTADOS_CASO.a_nombre(EstadoCaso.EN_ANALISIS)
PENDIENTE = ESTADOS_CASO.a_nombre(EstadoCaso.PENDIENTE_REVISION)


@dataclass(frozen=True)
class ProductorFila:
    id: str
    nombre: str
    activo: bool
    consentimiento: datetime | None


@dataclass(frozen=True)
class CultivoFila:
    id: str
    nombre: str
    especies: list[str]  # códigos de la API


@dataclass(frozen=True)
class CasoFila:
    id: str
    id_cultivo: str
    nombre_cultivo: str
    nombre_finca: str
    especie: str  # código de la API
    zona: str
    estado: str


def productor_por_telefono(s: Session, telefono: str) -> ProductorFila | None:
    f = s.execute(
        text(
            "SELECT id_productor, nombre_productor, estado_cuenta_productor = 'ACTIVO' AS activo, fecha_consentimiento "
            "FROM productores WHERE telefono_productor = CAST(:t AS varchar)"
        ),
        {"t": telefono},
    ).first()
    return ProductorFila(str(f[0]), str(f[1]), bool(f[2]), f[3]) if f else None


def registrar_productor(s: Session, nombre: str, telefono: str) -> str:
    """Alta desde el chatbot: ya con el consentimiento (P-B08)."""
    hoy = s.scalar(text("SELECT (fn_ahora() AT TIME ZONE 'America/Bogota')::date"))
    productor = Productor(
        nombre_productor=nombre,
        telefono_productor=telefono,
        estado_cuenta_productor="ACTIVO",
        fecha_registro_productor=hoy,
        fecha_consentimiento=s.scalar(text("SELECT fn_ahora()")),
    )
    s.add(productor)
    s.flush()
    return productor.id_productor


def dar_consentimiento(s: Session, id_productor: str) -> None:
    s.execute(
        text("UPDATE productores SET fecha_consentimiento = fn_ahora() WHERE id_productor = :p"), {"p": id_productor}
    )


def fincas(s: Session, id_productor: str) -> list[tuple[str, str]]:
    filas = s.execute(
        text("SELECT id_finca, nombre_finca FROM fincas WHERE id_productor = :p ORDER BY id_finca"), {"p": id_productor}
    )
    return [(str(a), str(b)) for a, b in filas]


def nombre_de_finca(s: Session, id_finca: str) -> str:
    return str(s.scalar(text("SELECT nombre_finca FROM fincas WHERE id_finca = :f"), {"f": id_finca}) or "")


def cultivos(s: Session, id_finca: str) -> list[CultivoFila]:
    filas = s.execute(
        text(
            "SELECT cu.id_cultivo, cu.nombre_cultivo, "
            "COALESCE((SELECT array_agg(es.nombre_especie ORDER BY es.nombre_especie) FROM cultivo_especie ce "
            "JOIN especies es ON es.id_especie = ce.id_especie WHERE ce.id_cultivo = cu.id_cultivo), '{}') "
            "FROM cultivos cu WHERE cu.id_finca = :f ORDER BY cu.id_cultivo"
        ),
        {"f": id_finca},
    )
    return [CultivoFila(str(i), str(n), [ESPECIES.a_codigo(e) for e in esp]) for i, n, esp in filas]


def nombre_de_cultivo_en_uso(s: Session, id_finca: str, nombre: str) -> bool:
    return bool(
        s.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM cultivos WHERE id_finca = :f "
                "AND translate(lower(nombre_cultivo), 'áéíóúüñ', 'aeiouun') = translate(lower(CAST(:n AS varchar)), "
                "'áéíóúüñ', 'aeiouun'))"
            ),
            {"f": id_finca, "n": nombre},
        )
    )


def crear_cultivo(s: Session, id_finca: str, nombre: str, tipo: str, especies: list[str]) -> str:
    """El cultivo nace con la ubicación de su finca (la distancia de las alertas se mide con ella)."""
    lat, lon = s.execute(
        text("SELECT latitud_finca, longitud_finca FROM fincas WHERE id_finca = :f"), {"f": id_finca}
    ).one()
    cultivo = Cultivo(
        nombre_cultivo=nombre,
        latitud_cultivo=lat,
        longitud_cultivo=lon,
        id_finca=id_finca,
        tipo_cultivo=TIPOS_CULTIVO.a_nombre(tipo),
    )
    s.add(cultivo)
    s.flush()
    ids = s.scalars(
        text("SELECT id_especie FROM especies WHERE nombre_especie = ANY(:n) ORDER BY id_especie"),
        {"n": [ESPECIES.a_nombre(e) for e in especies]},
    ).all()
    for id_especie in ids:
        s.add(CultivoEspecie(id_cultivo=cultivo.id_cultivo, id_especie=id_especie))
    s.flush()
    return cultivo.id_cultivo


_CASO = (
    "SELECT c.id_caso, c.id_cultivo, cu.nombre_cultivo, f.nombre_finca, es.nombre_especie, "
    "COALESCE(c.descripcion_zona_afectada, ''), e.nombre_estado "
    "FROM casos c JOIN estados e ON e.id_estado = c.id_estado JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo "
    "JOIN fincas f ON f.id_finca = cu.id_finca JOIN especies es ON es.id_especie = c.id_especie "
)


def _caso(f: Any) -> CasoFila:
    return CasoFila(str(f[0]), str(f[1]), str(f[2]), str(f[3]), ESPECIES.a_codigo(str(f[4])), str(f[5]), str(f[6]))


def caso_abierto_del_productor(s: Session, id_productor: str) -> CasoFila | None:
    """Su consulta en curso (Caso creado o En análisis), la más reciente."""
    f = s.execute(
        text(f"{_CASO} WHERE f.id_productor = :p AND e.nombre_estado IN (:a, :b) ORDER BY c.id_caso DESC LIMIT 1"),
        {"p": id_productor, "a": CREADO, "b": EN_ANALISIS},
    ).first()
    return _caso(f) if f else None


def caso_abierto(s: Session, id_caso: str) -> CasoFila | None:
    f = s.execute(
        text(f"{_CASO} WHERE c.id_caso = :c AND e.nombre_estado IN (:a, :b)"),
        {"c": id_caso, "a": CREADO, "b": EN_ANALISIS},
    ).first()
    return _caso(f) if f else None


def crear_caso(s: Session, id_productor: str, id_cultivo: str, especie: str, zona: str) -> str:
    """Caso creado → En análisis, con su diagnóstico vacío (D-05). Los eventos los anotan los triggers."""
    s.execute(sp.ESTABLECER_ACTOR, {"tipo": "PRODUCTOR", "id": id_productor})
    ahora = s.scalar(text("SELECT fn_ahora()"))
    id_especie = s.scalar(
        text("SELECT id_especie FROM especies WHERE nombre_especie = :n"), {"n": ESPECIES.a_nombre(especie)}
    )
    caso = Caso(
        id_cultivo=id_cultivo,
        id_especie=id_especie,
        id_estado=str(s.scalar(sp.ID_ESTADO, {"nombre": CREADO})),
        descripcion_zona_afectada=zona[:300],
        fecha_creacion_caso=ahora,
    )
    s.add(caso)
    s.flush()
    s.add(Diagnostico(id_caso=caso.id_caso))
    s.execute(
        text("UPDATE casos SET id_estado = fn_id_estado(CAST(:n AS varchar)) WHERE id_caso = :c"),
        {"n": EN_ANALISIS, "c": caso.id_caso},
    )
    return caso.id_caso


def tocar(s: Session, id_caso: str) -> None:
    """Cada mensaje reinicia el conteo de inactividad (y permite una nueva advertencia de 48 h)."""
    s.execute(
        text(
            "UPDATE casos SET ultima_actividad = fn_ahora(), advertencia_inactividad_enviada = 'N' WHERE id_caso = :c"
        ),
        {"c": id_caso},
    )


def guardar_respuesta(s: Session, id_caso: str, pregunta: str, respuesta: str) -> str:
    """Guarda una respuesta; la pregunta se reutiliza si ya existe con ese texto exacto."""
    id_pregunta = s.scalar(
        text("SELECT id_pregunta FROM preguntas WHERE texto_pregunta = CAST(:t AS varchar) LIMIT 1"), {"t": pregunta}
    )
    if id_pregunta is None:
        fila = Pregunta(texto_pregunta=pregunta[:300])
        s.add(fila)
        s.flush()
        id_pregunta = fila.id_pregunta
    id_diagnostico = s.scalar(text("SELECT id_diagnostico FROM diagnosticos WHERE id_caso = :c"), {"c": id_caso})
    registro = Respuesta(
        id_pregunta=id_pregunta,
        id_diagnostico=id_diagnostico,
        texto_respuesta=respuesta[:500],
        fecha_respuesta=s.scalar(text("SELECT fn_ahora()")),
    )
    s.add(registro)
    s.flush()
    return registro.id_respuesta


def historial(s: Session, id_caso: str) -> tuple[tuple[str, str], ...]:
    """Preguntas y respuestas de síntomas, en orden (sin las que solo transportan una foto)."""
    filas = s.execute(
        text(
            "SELECT p.texto_pregunta, r.texto_respuesta FROM respuestas r "
            "JOIN preguntas p ON p.id_pregunta = r.id_pregunta "
            "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico "
            "WHERE d.id_caso = :c AND p.texto_pregunta <> :foto ORDER BY r.fecha_respuesta, r.id_respuesta"
        ),
        {"c": id_caso, "foto": PREGUNTA_FOTO},
    )
    return tuple((str(p), str(r)) for p, r in filas)


def fotos_por_tipo(s: Session, id_caso: str) -> dict[str, int]:
    filas = s.execute(
        text(
            "SELECT f.tipo_evidencia, count(*) FROM fotografias f JOIN respuestas r ON r.id_respuesta = f.id_respuesta "
            "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico WHERE d.id_caso = :c AND f.es_util = 'S' "
            "GROUP BY f.tipo_evidencia"
        ),
        {"c": id_caso},
    )
    return {str(t): int(n) for t, n in filas}


def rutas_de_fotos(s: Session, id_caso: str) -> list[str]:
    """Claves S3 de las fotos útiles del caso, en el orden en que llegaron."""
    filas = s.scalars(
        text(
            "SELECT f.ruta_fotografia FROM fotografias f JOIN respuestas r ON r.id_respuesta = f.id_respuesta "
            "JOIN diagnosticos d ON d.id_diagnostico = r.id_diagnostico WHERE d.id_caso = :c AND f.es_util = 'S' "
            "ORDER BY f.fecha_envio_fotografia, f.ruta_fotografia"
        ),
        {"c": id_caso},
    )
    return [str(x) for x in filas]


def guardar_foto(s: Session, id_caso: str, tipo: str, clave: str, descripcion: str | None) -> None:
    id_respuesta = guardar_respuesta(s, id_caso, PREGUNTA_FOTO, descripcion or "Fotografía enviada")
    s.add(
        Fotografia(
            id_respuesta=id_respuesta,
            tipo_evidencia=tipo,
            ruta_fotografia=clave,
            fecha_envio_fotografia=s.scalar(text("SELECT fn_ahora()")),
            es_util="S",
        )
    )
    s.flush()


def enviar_al_panel(s: Session, id_caso: str, id_productor: str) -> None:
    """En análisis → Pendiente de revisión: el caso aparece en la bandeja del líder."""
    s.execute(sp.ESTABLECER_ACTOR, {"tipo": "PRODUCTOR", "id": id_productor})
    s.execute(
        text(
            "UPDATE casos SET id_estado = fn_id_estado(CAST(:n AS varchar)), fecha_envio_panel = fn_ahora(), "
            "ultima_actividad = fn_ahora() WHERE id_caso = :c"
        ),
        {"n": PENDIENTE, "c": id_caso},
    )


def guardar_identificacion(
    s: Session, id_caso: str, tipo: str, explicacion: str, porcentajes: dict[str, float], detalle: Any
) -> None:
    from decimal import Decimal
    from json import dumps

    id_ident = s.scalar(
        text(
            "INSERT INTO identificacion_preliminar (id_identificacion, tipo_resultado, explicacion_resultado, "
            "fecha_analisis, detalle_explicacion) "
            "VALUES (fn_generar_id('seq_identificacion_preliminar', 'IDP', '-', 12), "
            "CAST(:t AS varchar), CAST(:e AS varchar), fn_ahora(), CAST(:d AS jsonb)) RETURNING id_identificacion"
        ),
        {"t": tipo, "e": explicacion[:2000], "d": dumps(detalle, ensure_ascii=False) if detalle else None},
    )
    for codigo, porcentaje in porcentajes.items():
        s.execute(
            text(
                "INSERT INTO coincidencias_categoria (id_identificacion, id_categoria, porcentaje_coincidencia) "
                "SELECT :i, id_categoria, :p FROM categorias_fitosanitarias WHERE nombre_categoria = :n"
            ),
            {"i": id_ident, "p": Decimal(str(porcentaje)), "n": CATEGORIAS.a_nombre(codigo)},
        )
    s.execute(
        text("UPDATE casos SET id_identificacion_preliminar = :i WHERE id_caso = :c"), {"i": id_ident, "c": id_caso}
    )


def registrar_auditoria(s: Session, accion: str, descripcion: str) -> None:
    s.execute(
        sp.REGISTRAR_AUDITORIA,
        {
            "origen": "SISTEMA",
            "id_actor": None,
            "accion": accion,
            "entidad": "productores",
            "descripcion": descripcion,
            "valor_anterior": None,
            "valor_nuevo": None,
            "resultado": "EXITO",
            "fecha": None,
        },
    )
