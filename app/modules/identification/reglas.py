"""Reglas de la identificación preliminar (guía §2). Los umbrales viven en la configuración, no aquí.

- **Identificado**: una categoría tiene al menos el umbral de coincidencia (50 %).
- **Ambiguo**: dos o más categorías llegan al umbral y la diferencia entre las dos primeras es MENOR que el margen
  (15 puntos). Con 15 puntos exactos o más ya hay una ganadora clara.
- **No identificado**: ninguna categoría llega al umbral.

El resultado nunca es un diagnóstico: el único diagnóstico es el del técnico.
"""

from app.core.catalogos_api import ResultadoAnalisis


def resultado_de(porcentajes: dict[str, float], umbral: int, margen: int) -> ResultadoAnalisis:
    llegan = sorted((p for p in porcentajes.values() if p >= umbral), reverse=True)
    if not llegan:
        return ResultadoAnalisis.NO_IDENTIFICADO
    if len(llegan) >= 2 and llegan[0] - llegan[1] < margen:
        return ResultadoAnalisis.AMBIGUO
    return ResultadoAnalisis.PRELIMINAR


def ordenar(porcentajes: dict[str, float]) -> list[tuple[str, float]]:
    """De mayor a menor coincidencia; a igual porcentaje, por código (orden estable y predecible)."""
    return sorted(porcentajes.items(), key=lambda par: (-par[1], par[0]))
