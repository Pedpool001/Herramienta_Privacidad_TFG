"""
Localiza la URL de la política de privacidad de un sitio web.
Prefiere la versión en inglés cuando está disponible, ya que PoliGraph
funciona mejor sobre textos en inglés (su modelo NLP fue entrenado en inglés).

Estrategias de detección (en orden de prioridad):
  1. hreflang="en" en el <head> — estándar W3C, la más fiable
  2. Candidatos cuya URL contiene indicadores de idioma inglés
  3. Candidatos cuyo texto de enlace está en inglés
  4. Mejor candidato por puntuación de relevancia (sin importar idioma)
  5. Búsqueda en DuckDuckGo — resuelve sitios con Cloudflare/DataDome
  6. Sondeo de rutas comunes con Playwright (último recurso)
"""

import re
import logging
import requests as _requests
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
try:
    from ddgs import DDGS as _DDGS
    _DDGS_DISPONIBLE = True
except ImportError:
    _DDGS_DISPONIBLE = False

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_STEALTH = Stealth()


def _nuevo_contexto(browser):
    """Contexto Playwright con stealth y headers de navegador real."""
    ctx = browser.new_context(
        user_agent=_UA,
        viewport={"width": 1366, "height": 768},
        locale="es-ES",
        timezone_id="Europe/Madrid",
        extra_http_headers={
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "sec-ch-ua": (
                '"Chromium";v="124", "Google Chrome";v="124", '
                '"Not-A.Brand";v="99"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    _STEALTH.apply_stealth_sync(ctx)
    return ctx

# Palabras clave que identifican una página de política de privacidad
_PALABRAS_CLAVE = [
    "privacidad", "privacy", "legal", "cookies",
    "aviso", "notice", "datenschutz", "politique",
]

# Patrones de URL que indican versión en inglés
_URL_EN_PATTERNS = re.compile(
    r"(/en[-_/])|(/en$)|([-_.]en[.-/])|(lang=en)|(\?locale=en)"
    r"|(en[-_]us)|(en[-_]gb)|(#english)"
    r"|/privacy[-_]policy|/privacy[-_]notice|/data[-_]protection",
    re.IGNORECASE,
)

# Palabras del texto del enlace que confirman que está en inglés
_TEXTO_EN_KEYWORDS = {"privacy policy", "privacy notice", "data protection"}

# Palabras clave de mayor relevancia (política de privacidad propiamente dicha)
_RELEVANCIA_ALTA = re.compile(r"privacidad|privacy|datenschutz", re.IGNORECASE)
_RELEVANCIA_MEDIA = re.compile(r"cookies|legal|aviso|notice", re.IGNORECASE)


_CONTENT_TYPES_HTML = ("text/html", "text/plain", "application/xhtml")
_CONTENT_TYPES_DESCARGA = (
    "application/pdf", "application/msword", "application/vnd",
    "application/octet-stream", "application/zip",
)


def _es_url_html(url: str, timeout: int = 8) -> bool:
    """
    Comprueba que la URL sirve HTML (no PDF ni Word) haciendo una petición HEAD.
    Devuelve True si el Content-Type indica HTML o si no se puede determinar
    (asumir HTML para no bloquear URLs válidas).
    """
    try:
        r = _requests.head(url, timeout=timeout, allow_redirects=True,
                           headers={"User-Agent": _UA})
        ct = r.headers.get("Content-Type", "").lower()
        if any(t in ct for t in _CONTENT_TYPES_HTML):
            return True
        if any(t in ct for t in _CONTENT_TYPES_DESCARGA):
            logger.warning("URL devuelve Content-Type de descarga (%s): %s", ct, url)
            return False
        # Content-Type desconocido o vacío → asumir HTML
        return True
    except Exception:
        return True  # si no se puede verificar, dejar que lo intente PoliGraph


def _es_candidato(href: str, texto: str) -> bool:
    """Devuelve True si el enlace parece apuntar a una página legal/privacidad."""
    href_l = href.lower()
    texto_l = texto.lower()
    return any(p in href_l or p in texto_l for p in _PALABRAS_CLAVE)


_SEGMENTO_PRIVACIDAD = re.compile(r"privacidad|privacy|datenschutz", re.IGNORECASE)
_TEXTO_PRIVACIDAD = re.compile(
    r"política de privacidad|privacy policy|data protection policy|"
    r"aviso de privacidad|privacy notice",
    re.IGNORECASE,
)


_SOLO_COOKIES = re.compile(r"cooki", re.IGNORECASE)
_AVISO_LEGAL  = re.compile(r"aviso.legal|condiciones|terminos|terms", re.IGNORECASE)


def _puntuacion(href: str, texto: str, dominio_sitio: str = "") -> int:
    """Puntuación de relevancia de un candidato (mayor = más relevante)."""
    score = 0
    href_l = href.lower()
    texto_l = texto.lower()

    # Fuerte penalización por patrones de artículo/noticia — una URL con fecha en
    # la ruta o con /noticias/ no es una política de privacidad aunque su texto
    # de enlace contenga "legal" o "privacidad".
    if _RE_NOTICIA.search(href_l):
        score -= 10

    if _RELEVANCIA_ALTA.search(href_l) or _RELEVANCIA_ALTA.search(texto_l):
        score += 3
    elif _RELEVANCIA_MEDIA.search(href_l) or _RELEVANCIA_MEDIA.search(texto_l):
        score += 1

    # Penalizar páginas de política de cookies sin mención de privacidad:
    # queremos la política de privacidad, no la de cookies.
    if _SOLO_COOKIES.search(href_l) and not _RELEVANCIA_ALTA.search(href_l):
        score -= 4
    # Penalizar avisos legales y condiciones de uso (no son política de privacidad)
    if _AVISO_LEGAL.search(href_l) and not _RELEVANCIA_ALTA.search(href_l):
        score -= 3

    # Preferir URLs que no sean la raíz del sitio
    if len(urlparse(href).path) > 5:
        score += 1
    # Bonus cuando el segmento final de la ruta o el texto hablan explícitamente
    # de privacidad (desempate frente a páginas de cookies o condiciones)
    path_final = urlparse(href).path.split("/")[-1]
    if _SEGMENTO_PRIVACIDAD.search(path_final):
        score += 2
    if _TEXTO_PRIVACIDAD.search(texto):
        score += 2
    # Fuerte penalización por dominio externo — la política del sitio debe estar
    # en el propio dominio (o en un dominio corporativo relacionado), no en
    # proveedores técnicos como Cloudflare, Google, etc.
    if dominio_sitio:
        parsed = urlparse(href)
        if dominio_sitio not in parsed.netloc:
            score -= 5
    return score


def _es_ingles_url(href: str) -> bool:
    return bool(_URL_EN_PATTERNS.search(href))


def _es_ingles_texto(texto: str) -> bool:
    return any(k in texto.lower() for k in _TEXTO_EN_KEYWORDS)


# Rutas comunes de política de privacidad, ordenadas por probabilidad
# Inglés primero para que el fallback también prefiera inglés
_RUTAS_COMUNES = [
    "/en/privacy-policy",
    "/en/privacy",
    "/privacy-policy",
    "/privacy",
    "/politica-privacidad",
    "/politica-de-privacidad",
    "/legal/privacy-policy",
    "/legal/privacy",
    "/legal/privacidad",
    "/legal",
    "/aviso-legal",
    "/avisolegal",
    "/terms/privacy",
]


_RE_NOTICIA = re.compile(
    # Fechas en la ruta: /2026/06/10/, /2026-06-10/, o compactas /20260610/
    r"/\d{4}/\d{2}/\d{2}/"
    r"|/\d{4}-\d{2}-\d{2}/"
    r"|/\d{8}/"
    # Sufijo de ID de artículo con fecha compacta: _18_20260610abc
    r"|_\d{1,4}_\d{6,8}[0-9a-f]"
    # Secciones de noticias/entretenimiento habituales en medios españoles
    r"|/noticias/|/news/|/opinion/|/articulo/"
    r"|/famosos?/|/gente/|/celebrid|/entretenimiento/"
    r"|/actualidad/|/sociedad/|/cultura/|/deportes?/"
    r"|/television/|/cine/|/musica/|/motor/",
    re.IGNORECASE,
)


def _buscar_en_ddg(dominio: str) -> dict | None:
    """
    Consulta DuckDuckGo para encontrar la URL de la política de privacidad.
    Prueba primero la versión en inglés (ideal para PoliGraph) y luego en español.
    Filtra resultados que son noticias o artículos (no la política propiamente dicha).
    """
    if not _DDGS_DISPONIBLE:
        logger.warning("ddgs no está instalado; saltando búsqueda DDG.")
        return None

    # Normalizar dominio: quitar www. para que site: funcione con subdominios
    dominio_base = re.sub(r"^www\.", "", dominio)

    queries = [
        (f'site:{dominio_base} "privacy policy"', True),
        (f'site:{dominio_base} "política de privacidad"', False),
        (f'site:{dominio_base} privacidad', False),
    ]

    _PRIVACIDAD_PATH = re.compile(r"privac|privacy|datenschutz", re.IGNORECASE)
    _PRIVACIDAD_TITULO = re.compile(
        r"privac|privacy|datenschutz|politique de confidentialité|"
        r"datos personales|personal data|protection des données",
        re.IGNORECASE,
    )

    _PRIVACIDAD_PATH = re.compile(r"privac|privacy|datenschutz", re.IGNORECASE)
    _PRIVACIDAD_TITULO = re.compile(
        r"privac|privacy|datenschutz|politique de confidentialité|"
        r"datos personales|personal data|protection des données",
        re.IGNORECASE,
    )

    def _puntua_resultado(href: str, titulo: str) -> int:
        """
        Puntuación de un resultado DDG: mayor = más relevante.
        Devuelve -1 si el resultado no es una política de privacidad válida.
        Requiere al menos una señal de privacidad (path o título) para ser válido.
        """
        if not href or _RE_NOTICIA.search(href):
            return -1
        parsed = urlparse(href)
        # Ignorar dominios externos
        if dominio_base not in parsed.netloc:
            return -1

        tiene_path = bool(_PRIVACIDAD_PATH.search(parsed.path))
        tiene_titulo = bool(_PRIVACIDAD_TITULO.search(titulo))

        # Necesario: al menos una señal de privacidad
        if not tiene_path and not tiene_titulo:
            return -1

        score = 0
        if tiene_path:
            score += 3
        if tiene_titulo:
            score += 2
        # Preferir dominio exacto sobre subdominios
        if parsed.netloc.lstrip("www.") == dominio_base:
            score += 2
        else:
            score += 1
        return score

    try:
        with _DDGS() as ddgs:
            for query, es_en in queries:
                logger.info(f"DDG: {query}")
                resultados = list(ddgs.text(query, max_results=8))
                candidatos = []
                for r in resultados:
                    href = r.get("href", "")
                    titulo = r.get("title", "")
                    score = _puntua_resultado(href, titulo)
                    if score > 0:
                        candidatos.append((score, href, es_en))
                if candidatos:
                    candidatos.sort(key=lambda x: -x[0])
                    mejor_score, mejor_href, _ = candidatos[0]
                    # Determinar idioma por la URL real, no por el idioma de la query
                    es_en_real = _es_ingles_url(mejor_href)
                    logger.info(f"DDG encontró (score={mejor_score}): {mejor_href}")
                    return {"url": mejor_href, "es_ingles": es_en_real}
    except Exception as e:
        logger.warning(f"Error en búsqueda DDG: {e}")

    return None


def _sondear_rutas(base_url: str, timeout_ms: int = 12_000) -> dict | None:
    """
    Intenta acceder a rutas típicas de política de privacidad usando Playwright
    (necesario para sitios con protección JS como Cloudflare).
    Devuelve {url, es_ingles} para la primera ruta que responda con contenido
    de política real (detectado por palabras clave en el título/body), o None.
    """
    parsed = urlparse(base_url)
    scheme_host = f"{parsed.scheme}://{parsed.netloc}"

    _KEYWORDS_POLITICA = re.compile(
        r"privacidad|privacy|datenschutz|politique de confidentialité",
        re.IGNORECASE,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _nuevo_contexto(browser)
        page = context.new_page()

        for ruta in _RUTAS_COMUNES:
            url = scheme_host + ruta
            try:
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout_ms)
                if resp and resp.status == 200:
                    # Verificar que la página realmente habla de privacidad
                    title = page.title()
                    body_preview = page.inner_text("body")[:500]
                    if _KEYWORDS_POLITICA.search(title + body_preview):
                        es_en = "/en/" in ruta or ruta.endswith("/en")
                        logger.info(f"Sondeo encontró: {url}")
                        browser.close()
                        return {"url": page.url, "es_ingles": es_en}
            except Exception:
                continue

        browser.close()
    return None


# Prefijos de subdominio donde algunos sitios alojan su política de privacidad
# en lugar de en el dominio principal (ej: saladeprensa.decathlon.es)
_SUBDOMINIOS_SONDEO = ["saladeprensa", "legal", "press", "info", "media", "ayuda"]
_RUTAS_SUBDOMINIO = [
    "/politica-de-privacidad/",
    "/politica-de-privacidad",
    "/politica-privacidad",
    "/privacy-policy",
    "/privacy",
    "/privacidad",
]


def _sondear_subdominios(base_url: str, timeout_ms: int = 8_000) -> dict | None:
    """
    Prueba rutas comunes en subdominios típicos donde algunos sitios alojan
    su política (ej: saladeprensa.decathlon.es/politica-de-privacidad/).
    Se llama como último recurso si el sondeo de rutas en el dominio principal falla.
    """
    parsed = urlparse(base_url)
    # Extraer el dominio registrable (sin www.)
    netloc = parsed.netloc.lstrip("www.")
    scheme = parsed.scheme

    _KEYWORDS_POLITICA = re.compile(
        r"privacidad|privacy|datenschutz|politique de confidentialité",
        re.IGNORECASE,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _nuevo_contexto(browser)
        page = context.new_page()

        for subdominio in _SUBDOMINIOS_SONDEO:
            host = f"{scheme}://{subdominio}.{netloc}"
            for ruta in _RUTAS_SUBDOMINIO:
                url = host + ruta
                try:
                    resp = page.goto(url, wait_until="domcontentloaded",
                                     timeout=timeout_ms)
                    if resp and resp.status == 200:
                        title = page.title()
                        body_preview = page.inner_text("body")[:500]
                        if _KEYWORDS_POLITICA.search(title + body_preview):
                            es_en = "/privacy" in ruta and "politica" not in ruta
                            logger.info(f"Sondeo subdominio encontró: {url}")
                            browser.close()
                            return {"url": page.url, "es_ingles": es_en}
                except Exception:
                    continue

        browser.close()
    return None


def buscar_url_politica(url_sitio: str, timeout_ms: int = 30_000) -> dict:
    """
    Dado un sitio web, devuelve la URL de su política de privacidad.

    Returns:
        {
            "url": str | None,          # URL seleccionada
            "es_ingles": bool,          # True si se determinó que está en inglés
            "fuente": str,              # "hreflang" | "url_en" | "texto_en" | "mejor_candidato"
            "candidatos": list[dict],   # todos los candidatos encontrados
        }
    """
    resultado = {"url": None, "es_ingles": False, "fuente": None, "candidatos": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _nuevo_contexto(browser)
        page = context.new_page()

        try:
            logger.info(f"Navegando a {url_sitio}")
            page.goto(url_sitio, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            # Scroll al fondo para activar lazy loading de footer
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        except PlaywrightTimeout:
            logger.warning(f"Timeout al cargar {url_sitio}")
            browser.close()
            return resultado
        except Exception as e:
            logger.error(f"Error al navegar a {url_sitio}: {e}")
            browser.close()
            return resultado

        # ── Estrategia 1: hreflang="en" en el <head> ──────────────────────────
        hreflang_en = page.eval_on_selector_all(
            'link[hreflang]',
            """links => links
                .filter(l => /^en($|-)/i.test(l.hreflang))
                .map(l => l.href)""",
        )
        # hreflang puede apuntar a la home; filtrar solo los que parecen política
        hreflang_politica = [
            h for h in hreflang_en
            if _es_candidato(h, "") or _RELEVANCIA_ALTA.search(h)
        ]

        # ── Extracción de todos los enlaces de la página ───────────────────────
        enlaces_raw = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                  .map(a => ({href: a.href, texto: (a.innerText || '').trim()}))"""
        )

        base = url_sitio.rstrip("/")
        parsed_base = urlparse(base)
        dominio_sitio = parsed_base.netloc  # ej: "www.decathlon.es"
        # URL de la homepage para no confundirla con una página de política
        homepage_paths = {"", "/", "/index.html", "/index.php"}

        candidatos = []
        for item in enlaces_raw:
            href = item.get("href", "")
            texto = item.get("texto", "")
            if not href or href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            href = urljoin(base, href)
            parsed = urlparse(href)
            # Descartar si apunta a la homepage (path trivial del mismo dominio)
            if parsed.netloc == parsed_base.netloc and parsed.path.rstrip("/") in homepage_paths:
                continue
            if _es_candidato(href, texto):
                score = _puntuacion(href, texto, dominio_sitio)
                candidatos.append({
                    "url": href,
                    "texto": texto[:80],
                    "score": score,
                    "en_url": _es_ingles_url(href),
                    "en_texto": _es_ingles_texto(texto),
                })

        # Deduplicar por URL
        vistos = {}
        for c in candidatos:
            if c["url"] not in vistos or c["score"] > vistos[c["url"]]["score"]:
                vistos[c["url"]] = c
        candidatos = sorted(vistos.values(), key=lambda x: -x["score"])
        resultado["candidatos"] = candidatos

        browser.close()

    # ── Selección final ────────────────────────────────────────────────────────

    # 1. hreflang inglés apuntando a política
    if hreflang_politica:
        resultado["url"] = hreflang_politica[0]
        resultado["es_ingles"] = True
        resultado["fuente"] = "hreflang"

    # 2. Candidato con URL en inglés (mayor score primero, ya ordenado)
    elif any(c["en_url"] for c in candidatos):
        c = next(c for c in candidatos if c["en_url"])
        resultado["url"] = c["url"]
        resultado["es_ingles"] = True
        resultado["fuente"] = "url_en"

    # 3. Candidato cuyo texto del enlace está en inglés
    elif any(c["en_texto"] for c in candidatos):
        c = next(c for c in candidatos if c["en_texto"])
        resultado["url"] = c["url"]
        resultado["es_ingles"] = True
        resultado["fuente"] = "texto_en"

    # 4. Mejor candidato por puntuación (sin importar idioma)
    # Solo elegir si score >= 0; score negativo indica dominio externo sin relación
    else:
        candidatos_validos = [c for c in candidatos if c["score"] >= 0]
        if candidatos_validos:
            resultado["url"] = candidatos_validos[0]["url"]
            resultado["es_ingles"] = False
            resultado["fuente"] = "mejor_candidato"
        else:
            # 5. Búsqueda en DuckDuckGo — resuelve Cloudflare/DataDome
            dominio = urlparse(url_sitio).netloc
            logger.info(f"Sin candidatos. Buscando en DDG para {dominio}...")
            ddg = _buscar_en_ddg(dominio)
            if ddg:
                resultado["url"] = ddg["url"]
                resultado["es_ingles"] = ddg["es_ingles"]
                resultado["fuente"] = "ddg"
            else:
                # 6. Sondear rutas comunes en el dominio principal
                logger.info("DDG sin resultado. Sondeando rutas comunes...")
                sondeo = _sondear_rutas(url_sitio)
                if sondeo:
                    resultado["url"] = sondeo["url"]
                    resultado["es_ingles"] = sondeo["es_ingles"]
                    resultado["fuente"] = "sondeo_rutas"
                else:
                    # 7. Sondear subdominios comunes (ej: saladeprensa.decathlon.es)
                    logger.info("Sondeo principal sin resultado. Probando subdominios...")
                    sondeo_sub = _sondear_subdominios(url_sitio)
                    if sondeo_sub:
                        resultado["url"] = sondeo_sub["url"]
                        resultado["es_ingles"] = sondeo_sub["es_ingles"]
                        resultado["fuente"] = "sondeo_subdominios"

    # Limpiar fragmentos de tracking del resultado final (#utm_source=..., #vca=...)
    if resultado["url"]:
        p = urlparse(resultado["url"])
        resultado["url"] = p._replace(fragment="").geturl()

    # Validar que la URL seleccionada sirve HTML y no un fichero descargable
    if resultado["url"] and not _es_url_html(resultado["url"]):
        logger.warning(
            "URL '%s' sirve un fichero descargable — buscando alternativa HTML...",
            resultado["url"],
        )
        url_descartada = resultado["url"]
        resultado["url"] = None

        # Intentar candidatos alternativos del DOM (siguiente por score, mismo umbral)
        candidatos_alternativos = [
            c for c in resultado["candidatos"]
            if c["url"] != url_descartada and c["score"] >= 0
        ]
        for c in candidatos_alternativos:
            if _es_url_html(c["url"]):
                resultado["url"] = c["url"]
                resultado["es_ingles"] = c.get("en_url", False)
                resultado["fuente"] = "mejor_candidato_alt"
                logger.info("Alternativa HTML encontrada: %s", resultado["url"])
                break

        # Si ningún candidato DOM sirve HTML, intentar DDG
        if not resultado["url"] and _DDGS_DISPONIBLE:
            dominio = urlparse(url_sitio).netloc
            logger.info("Buscando alternativa en DDG para %s...", dominio)
            ddg = _buscar_en_ddg(dominio)
            if ddg and ddg.get("url") != url_descartada and _es_url_html(ddg["url"]):
                resultado["url"] = ddg["url"]
                resultado["es_ingles"] = ddg["es_ingles"]
                resultado["fuente"] = "ddg"

        # Sin alternativa HTML: devolver la URL de descarga para que el caller
        # intente extracción de texto (PDF). Si no es posible, quedará NO_EVALUABLE.
        if not resultado["url"]:
            logger.info(
                "Sin alternativa HTML. Se devolverá URL de descarga para extracción de PDF: %s",
                url_descartada,
            )
            resultado["url"] = url_descartada
            resultado["es_descarga"] = True

    return resultado


# ── CLI de prueba ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.abc.es"
    print(f"\nBuscando política de privacidad en: {url}\n")

    res = buscar_url_politica(url)

    if res["url"]:
        print(f"  URL encontrada : {res['url']}")
        print(f"  En inglés      : {res['es_ingles']}")
        print(f"  Fuente         : {res['fuente']}")
    else:
        print("  No se encontró ninguna política de privacidad.")

    if res["candidatos"]:
        print(f"\n  Todos los candidatos ({len(res['candidatos'])}):")
        for c in res["candidatos"][:8]:
            flags = []
            if c["en_url"]:
                flags.append("EN-url")
            if c["en_texto"]:
                flags.append("EN-texto")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            print(f"    score={c['score']}  {c['url']}{flag_str}")
            print(f"           texto: \"{c['texto']}\"")
    else:
        print("  (sin candidatos)")
