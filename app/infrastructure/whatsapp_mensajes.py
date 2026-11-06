"""Mensajes que el chatbot envía por WhatsApp: texto, botones, listas, pedido de ubicación y ubicación.

Cada mensaje sabe armar su objeto para la Cloud API (`cuerpo`) y su versión en texto plano (`texto_plano`), que se usa
si Meta rechaza el interactivo y en las pruebas. Los `id` de las opciones son lo que el productor podría escribir a
mano («1», «si», «listo»): así el orquestador entiende igual un botón pulsado que una respuesta escrita.

Límites de Meta que se respetan aquí (se recortan, no se rechazan): hasta 3 botones con títulos de 20 caracteres;
listas de hasta 10 filas con títulos de 24 y descripciones de 72; texto del botón de la lista de 20.
"""

from dataclasses import dataclass
from typing import Any

MAXIMO_BOTONES = 3
MAXIMO_FILAS = 10
LARGO_TITULO_BOTON = 20
LARGO_TITULO_FILA = 24
LARGO_DESCRIPCION_FILA = 72
LARGO_TEXTO_LISTA = 20
LARGO_CUERPO = 1024


def recortar(texto: str, largo: int) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


@dataclass(frozen=True)
class Opcion:
    id: str
    titulo: str
    descripcion: str | None = None


@dataclass(frozen=True)
class Botones:
    """Hasta 3 botones de respuesta debajo del texto."""

    texto: str
    opciones: tuple[Opcion, ...]
    pie: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.opciones) <= MAXIMO_BOTONES:
            raise ValueError(f"Un mensaje con botones lleva de 1 a {MAXIMO_BOTONES} opciones")

    def cuerpo(self) -> dict[str, Any]:
        interactivo: dict[str, Any] = {
            "type": "button",
            "body": {"text": recortar(self.texto, LARGO_CUERPO)},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": o.id, "title": recortar(o.titulo, LARGO_TITULO_BOTON)}}
                    for o in self.opciones
                ]
            },
        }
        if self.pie:
            interactivo["footer"] = {"text": recortar(self.pie, 60)}
        return {"type": "interactive", "interactive": interactivo}

    def texto_plano(self) -> str:
        opciones = " / ".join(o.titulo for o in self.opciones)
        return f"{self.texto}\n({opciones})"


@dataclass(frozen=True)
class Lista:
    """Un botón que abre una lista de hasta 10 opciones."""

    texto: str
    boton: str
    opciones: tuple[Opcion, ...]
    titulo_seccion: str = "Opciones"
    pie: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.opciones) <= MAXIMO_FILAS:
            raise ValueError(f"Una lista lleva de 1 a {MAXIMO_FILAS} opciones")

    def cuerpo(self) -> dict[str, Any]:
        filas: list[dict[str, str]] = []
        for o in self.opciones:
            fila = {"id": o.id, "title": recortar(o.titulo, LARGO_TITULO_FILA)}
            if o.descripcion:
                fila["description"] = recortar(o.descripcion, LARGO_DESCRIPCION_FILA)
            filas.append(fila)
        interactivo: dict[str, Any] = {
            "type": "list",
            "body": {"text": recortar(self.texto, LARGO_CUERPO)},
            "action": {
                "button": recortar(self.boton, LARGO_TEXTO_LISTA),
                "sections": [{"title": recortar(self.titulo_seccion, LARGO_TITULO_FILA), "rows": filas}],
            },
        }
        if self.pie:
            interactivo["footer"] = {"text": recortar(self.pie, 60)}
        return {"type": "interactive", "interactive": interactivo}

    def texto_plano(self) -> str:
        filas = "\n".join(f"{i}. {o.titulo}" for i, o in enumerate(self.opciones, start=1))
        return f"{self.texto}\n{filas}"


@dataclass(frozen=True)
class PedirUbicacion:
    """Mensaje con el botón nativo «Enviar ubicación»: el productor comparte dónde está en ese momento."""

    texto: str

    def cuerpo(self) -> dict[str, Any]:
        return {
            "type": "interactive",
            "interactive": {
                "type": "location_request_message",
                "body": {"text": recortar(self.texto, LARGO_CUERPO)},
                "action": {"name": "send_location"},
            },
        }

    def texto_plano(self) -> str:
        return f"{self.texto}\n(Toca el clip 📎 → Ubicación → Enviar tu ubicación actual)"


@dataclass(frozen=True)
class Ubicacion:
    """Un punto en el mapa (WhatsApp lo muestra como un mapa que se puede abrir)."""

    latitud: float
    longitud: float
    nombre: str = ""
    direccion: str = ""

    def cuerpo(self) -> dict[str, Any]:
        ubicacion: dict[str, Any] = {"latitude": self.latitud, "longitude": self.longitud}
        if self.nombre:
            ubicacion["name"] = recortar(self.nombre, 100)
        if self.direccion:
            ubicacion["address"] = recortar(self.direccion, 100)
        return {"type": "location", "location": ubicacion}

    def texto_plano(self) -> str:
        return f"📍 {self.nombre or 'Ubicación'}: {self.latitud:.6f}, {self.longitud:.6f}"


Interactivo = Botones | Lista | PedirUbicacion | Ubicacion
Mensaje = str | Interactivo


def texto_de(mensaje: Mensaje) -> str:
    """El mensaje en texto plano, con sus opciones numeradas: el respaldo si Meta rechaza el interactivo."""
    return mensaje if isinstance(mensaje, str) else mensaje.texto_plano()


def texto_visible(mensaje: Mensaje) -> str:
    """El texto principal que lee el productor (sin las opciones): lo que anota el cliente simulado."""
    if isinstance(mensaje, str):
        return mensaje
    return mensaje.texto_plano() if isinstance(mensaje, Ubicacion) else mensaje.texto
