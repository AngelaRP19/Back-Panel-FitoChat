"""Middlewares ASGI de seguridad: cabeceras y límite del tamaño del cuerpo."""

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CABECERAS_SEGURIDAD = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Las respuestas llevan datos de personas: ni el navegador ni CloudFront deben guardarlas.
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_con_cabeceras(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for nombre, valor in CABECERAS_SEGURIDAD.items():
                    headers.setdefault(nombre, valor)
            await send(message)

        await self.app(scope, receive, send_con_cabeceras)


class _CuerpoDemasiadoGrande(Exception):
    pass


class BodySizeLimitMiddleware:
    """Responde 413 si el cuerpo supera `max_bytes`, ya sea por `Content-Length` o contando lo recibido."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declarado = dict(scope["headers"]).get(b"content-length")
        if declarado is not None and declarado.isdigit() and int(declarado) > self.max_bytes:
            await self._rechazar(scope, receive, send)
            return

        recibidos = 0
        respuesta_iniciada = False

        async def receive_limitado() -> Message:
            nonlocal recibidos
            message = await receive()
            if message["type"] == "http.request":
                recibidos += len(message.get("body", b""))
                if recibidos > self.max_bytes:
                    raise _CuerpoDemasiadoGrande
            return message

        async def send_vigilado(message: Message) -> None:
            nonlocal respuesta_iniciada
            if message["type"] == "http.response.start":
                respuesta_iniciada = True
            await send(message)

        try:
            await self.app(scope, receive_limitado, send_vigilado)
        except _CuerpoDemasiadoGrande:
            if respuesta_iniciada:
                raise
            await self._rechazar(scope, receive, send)

    async def _rechazar(self, scope: Scope, receive: Receive, send: Send) -> None:
        respuesta = JSONResponse(
            status_code=413,
            content={"codigo": "SOLICITUD_INVALIDA", "mensaje": "El cuerpo de la solicitud es demasiado grande."},
        )
        await respuesta(scope, receive, send)
