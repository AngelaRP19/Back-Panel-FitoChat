"""Límite de solicitudes por clave (por ejemplo, la IP) con ventana deslizante en memoria.

Cada tarea de ECS lleva su propia cuenta: con N tareas el límite efectivo es hasta N veces el configurado. Alcanza para
frenar la enumeración de correos de `verificar-correo` (propuesto 5 por minuto, guía §7); un límite global exigiría
WAF o Redis.
"""

import threading
import time
from collections import deque
from collections.abc import Callable


class LimitadorVentana:
    def __init__(self, maximo: int, ventana_segundos: float, reloj: Callable[[], float] = time.monotonic) -> None:
        self.maximo = maximo
        self.ventana = ventana_segundos
        self._reloj = reloj
        self._eventos: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _purgar(self, clave: str, ahora: float) -> deque[float]:
        eventos = self._eventos.setdefault(clave, deque())
        while eventos and eventos[0] <= ahora - self.ventana:
            eventos.popleft()
        return eventos

    def permitir(self, clave: str) -> bool:
        """Registra el intento y dice si cabe en el límite. Un intento rechazado no cuenta."""
        with self._lock:
            ahora = self._reloj()
            eventos = self._purgar(clave, ahora)
            if len(eventos) >= self.maximo:
                return False
            eventos.append(ahora)
            self._limpiar(ahora)
            return True

    def segundos_para_reintentar(self, clave: str) -> int:
        with self._lock:
            ahora = self._reloj()
            eventos = self._purgar(clave, ahora)
            if len(eventos) < self.maximo:
                return 0
            return max(1, int(eventos[0] + self.ventana - ahora) + 1)

    def reiniciar(self) -> None:
        with self._lock:
            self._eventos.clear()

    def _limpiar(self, ahora: float) -> None:
        """Descarta las claves sin actividad reciente para que la memoria no crezca sin límite."""
        if len(self._eventos) < 1024:
            return
        for clave in [c for c, e in self._eventos.items() if not e or e[-1] <= ahora - self.ventana]:
            del self._eventos[clave]
