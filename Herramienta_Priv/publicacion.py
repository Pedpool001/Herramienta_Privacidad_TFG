"""
Numeración de publicación: fusión de R2+R3 y renumeración a R1–R18.

En pruebas sobre la muestra de 107 sitios, R2 (cookies no exentas en PRE) y R3
(web beacons en PRE) dieron resultados idénticos: comparten script, metodología
y veredicto, y el web beacon no es más que el tipo «trackingPixel» dentro del
mismo conjunto de rastreadores no exentos. Por eso se fusionan en un único
requisito, y el resto se renumera de forma consecutiva para el estudio a publicar.

**Decisión de diseño — por qué la renumeración vive en la capa de presentación:**
Toda la maquinaria de medición (scripts de análisis, módulos de herramienta,
`_loader`, claves del dict de resultados) sigue usando la numeración interna
original R1…R19. Renombrar esos 17 scripts y sus claves habría tocado el código
que costó decenas de bugs depurar, con el riesgo de corromper los datos del
estudio en silencio. En su lugar, la fusión y la renumeración se aplican solo al
generar los informes y la interfaz: es donde el lector las ve, y un fallo aquí es
inmediatamente visible en el HTML en lugar de alterar los veredictos.

Mapa interno → etiqueta de publicación:

    R1→R1   R2→R2 (absorbe R3)   R4→R3   R5→R4   R6→R5   R7→R6   R8→R7
    R9→R8   R10→R9   R11→R10   R12→R11   R13→R12   R14→R13   R15→R14
    R16→R15   R17→R16   R18→R17   R19→R18
"""

from __future__ import annotations

# Clave interna que absorbe a otra por fusión (R2 absorbe R3)
FUSIONADO_EN = {"R3": "R2"}

# Claves internas que NO se muestran por separado (quedan fusionadas)
OCULTOS = set(FUSIONADO_EN)

# Orden interno de los requisitos que sí se muestran (R3 fuera)
_INTERNOS_VISIBLES = [f"R{i}" for i in range(1, 20) if f"R{i}" not in OCULTOS]

# Etiqueta de publicación por requisito interno (consecutiva, saltando el hueco)
ETIQUETA_PUB: dict[str, str] = {}
_n = 0
for _r in _INTERNOS_VISIBLES:
    _n += 1
    ETIQUETA_PUB[_r] = f"R{_n}"

# Nombres de publicación (el de R2 refleja la fusión)
NOMBRE_PUB: dict[str, str] = {
    "R2": "Bloqueo de cookies y web beacons no exentos",
}


def etiqueta(req_interno: str) -> str:
    """Etiqueta de publicación (R1–R18) de un requisito interno (R1–R19)."""
    return ETIQUETA_PUB.get(req_interno, req_interno)


def internos_visibles() -> list[str]:
    """Requisitos internos a mostrar, en orden, con R3 ya fusionado en R2."""
    return list(_INTERNOS_VISIBLES)


def es_oculto(req_interno: str) -> bool:
    """True si el requisito no debe mostrarse por separado (p. ej. R3)."""
    return req_interno in OCULTOS
