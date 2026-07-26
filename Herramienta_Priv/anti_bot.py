"""
Detección de bloqueo por sistemas anti-bot.

La herramienta no podía distinguir "el sitio no tiene banner de cookies" de
"el sitio nos ha bloqueado", de modo que un bloqueo podía acabar registrado como
FAILED (incumplimiento del sitio) en lugar de NO_EVALUABLE (limitación de
medición). Este módulo identifica la situación a partir de las señales que dejan
los principales sistemas de protección, para que el veredicto sea honesto y para
poder cuantificar cuántos sitios de la muestra quedan fuera de alcance y por qué.

Uso típico desde un módulo que ya tiene una página de Playwright abierta:

    from anti_bot import detectar_bloqueo_pagina
    bloqueo = detectar_bloqueo_pagina(page, status=resp.status if resp else None)
    if bloqueo:
        log.warning("Bloqueado por %s (%s)", bloqueo["sistema"], bloqueo["senal"])

O sobre HTML ya descargado:

    from anti_bot import detectar_bloqueo
    bloqueo = detectar_bloqueo(html=texto, titulo=t, status=403)
"""

from __future__ import annotations

import re

# ── Firmas por sistema ────────────────────────────────────────────────────────
#
# Cada entrada define patrones sobre el título, el HTML y las cabeceras. Se
# comprueban en orden: el primero que casa determina el sistema atribuido.
# Los patrones buscan artefactos de la *página de desafío*, no del sitio real.

_FIRMAS: list[dict] = [
    {
        "sistema": "Cloudflare",
        # "access denied" se deja fuera a propósito: lo usan varios sistemas y
        # provocaría atribuciones erróneas. Estos títulos sí son distintivos.
        "titulo": re.compile(
            r"just a moment|attention required|un momento", re.I),
        "html": re.compile(
            r"challenges\.cloudflare\.com"      # iframe del challenge
            r"|cf-browser-verification"          # div clásico de verificación
            r"|__cf_chl_"                        # scripts del managed challenge
            r"|cf_chl_opt"
            r"|cdn-cgi/challenge-platform"
            r"|turnstile", re.I),
        "cabeceras": ("cf-mitigated", "cf-chl-bypass"),
    },
    {
        "sistema": "DataDome",
        "titulo": re.compile(r"blocked|bloqueado", re.I),
        "html": re.compile(
            r"captcha-delivery\.com"             # dominio del captcha de DataDome
            r"|geo\.captcha-delivery\.com"
            r"|datadome"
            r"|dd_?cookie", re.I),
        "cabeceras": ("x-datadome", "x-dd-b"),
    },
    {
        "sistema": "Akamai",
        "titulo": re.compile(r"access denied|acceso denegado", re.I),
        "html": re.compile(
            r"reference\s*#\d|akamai|_abck|bm_sz", re.I),
        "cabeceras": ("akamai-grn",),
    },
    {
        "sistema": "PerimeterX/HUMAN",
        "titulo": re.compile(r"access to this page has been denied", re.I),
        "html": re.compile(r"perimeterx|px-captcha|_pxhd|captcha\.px", re.I),
        "cabeceras": ("x-px",),
    },
    {
        "sistema": "Imperva/Incapsula",
        "titulo": re.compile(r"request unsuccessful", re.I),
        "html": re.compile(r"incapsula|_incap_|visid_incap", re.I),
        "cabeceras": ("x-iinfo",),
    },
]

# Códigos HTTP que, por sí solos, indican bloqueo probable
_STATUS_BLOQUEO = {401, 403, 405, 406, 429, 503}

# Un DOM de desafío es diminuto comparado con una portada real
_MIN_LONGITUD_HTML = 2000
_MIN_ENLACES       = 3

# Umbrales para dar por buena una página: por encima de esto es contenido real,
# no un desafío. Una página de challenge no llega a 15 enlaces ni a 150 KB.
_ENLACES_PAGINA_REAL = 15
_TAM_PAGINA_REAL     = 150_000


def _parece_pagina_real(html: str, status: int | None,
                        n_enlaces: int | None) -> bool:
    """
    True si la respuesta es inequívocamente la página real del sitio.

    Necesario porque muchos sitios legítimos protegidos por Cloudflare incluyen
    en su propio HTML referencias a Turnstile o a `challenges.cloudflare.com`
    para sus formularios. Sin esta comprobación, la portada real de decathlon.es
    —1,2 MB y 335 enlaces— se marcaba como bloqueada por contener esa cadena en
    un blob de configuración, invirtiendo por completo el resultado.
    """
    if status is not None and status in _STATUS_BLOQUEO:
        return False
    if n_enlaces is not None and n_enlaces >= _ENLACES_PAGINA_REAL:
        return True
    return len(html) > _TAM_PAGINA_REAL


def _buscar_firma(html: str, titulo: str, cabeceras: dict | None) -> dict | None:
    """
    Devuelve la firma que case, o None.

    Se comprueban todas las firmas por tipo de señal, de más específica a menos:
    primero el HTML (artefactos propios de cada sistema), luego las cabeceras y
    por último el título. Si se recorriera firma por firma, un título ambiguo de
    un sistema podría casar antes que el HTML inequívoco de otro y atribuir el
    bloqueo al sistema equivocado.
    """
    cab_norm = {k.lower(): str(v) for k, v in (cabeceras or {}).items()}

    for firma in _FIRMAS:
        if html and firma["html"].search(html):
            return {"sistema": firma["sistema"], "senal": "html"}

    for firma in _FIRMAS:
        for cab in firma["cabeceras"]:
            if any(cab in k for k in cab_norm):
                return {"sistema": firma["sistema"], "senal": f"cabecera:{cab}"}

    for firma in _FIRMAS:
        if titulo and firma["titulo"].search(titulo):
            return {"sistema": firma["sistema"], "senal": "titulo"}

    return None


def detectar_bloqueo(html: str = "", titulo: str = "",
                     status: int | None = None,
                     cabeceras: dict | None = None,
                     n_enlaces: int | None = None) -> dict | None:
    """
    Determina si la respuesta corresponde a una página de bloqueo anti-bot.

    Args:
        html:      Contenido HTML de la página.
        titulo:    Título del documento.
        status:    Código de respuesta HTTP, si se conoce.
        cabeceras: Cabeceras de respuesta, si se conocen.
        n_enlaces: Número de enlaces <a href> del DOM, si se conoce.

    Returns:
        None si no hay indicios de bloqueo, o un dict:
            {"sistema": "Cloudflare", "senal": "html", "status": 403,
             "confianza": "alta"}
    """
    html   = html or ""
    titulo = titulo or ""

    # Si la página cargó de verdad, no hay bloqueo por mucho que su HTML
    # mencione al proveedor de protección. Esta comprobación va antes que las
    # firmas justamente para evitar ese falso positivo.
    if _parece_pagina_real(html, status, n_enlaces):
        return None

    firma = _buscar_firma(html, titulo, cabeceras)
    if firma:
        return {**firma, "status": status, "confianza": "alta"}

    # Sin firma reconocible: sospechar por código de estado o por un DOM
    # anormalmente pobre (página de desafío genérica). Confianza menor porque
    # un 403 puede deberse a geobloqueo y un DOM corto a un sitio muy simple.
    motivos = []
    if status in _STATUS_BLOQUEO:
        motivos.append(f"HTTP {status}")
    if html and len(html) < _MIN_LONGITUD_HTML:
        motivos.append(f"DOM muy corto ({len(html)} bytes)")
    if n_enlaces is not None and n_enlaces < _MIN_ENLACES:
        motivos.append(f"solo {n_enlaces} enlaces")

    # Un único indicio débil no basta: se exige o bien un status de bloqueo,
    # o bien dos señales estructurales coincidentes.
    if status in _STATUS_BLOQUEO or len(motivos) >= 2:
        return {
            "sistema":   "Desconocido",
            "senal":     " + ".join(motivos),
            "status":    status,
            "confianza": "media",
        }
    return None


def detectar_bloqueo_pagina(page, status: int | None = None,
                            cabeceras: dict | None = None) -> dict | None:
    """
    Variante para una página de Playwright ya cargada.

    Extrae título, HTML y número de enlaces de la página y delega en
    `detectar_bloqueo`. Nunca lanza: si la página no es accesible devuelve None.
    """
    try:
        titulo = page.title() or ""
    except Exception:
        titulo = ""
    try:
        html = page.content() or ""
    except Exception:
        html = ""
    try:
        n_enlaces = page.evaluate("() => document.querySelectorAll('a[href]').length")
    except Exception:
        n_enlaces = None

    return detectar_bloqueo(html=html, titulo=titulo, status=status,
                            cabeceras=cabeceras, n_enlaces=n_enlaces)


def describir(bloqueo: dict | None) -> str:
    """Mensaje legible para logs e informes."""
    if not bloqueo:
        return "sin bloqueo detectado"
    partes = [f"bloqueado por {bloqueo['sistema']}"]
    if bloqueo.get("senal"):
        partes.append(f"señal: {bloqueo['senal']}")
    if bloqueo.get("status"):
        partes.append(f"HTTP {bloqueo['status']}")
    if bloqueo.get("confianza") == "media":
        partes.append("confianza media")
    return " · ".join(partes)
