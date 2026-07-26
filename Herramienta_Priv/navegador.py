"""
Navegación resiliente frente a sistemas anti-bot.

Ofrece tres piezas que el resto de módulos combinan según lo que necesiten:

  1. `esperar_turno(url)` — limita el ritmo de peticiones por dominio.
  2. `abrir_pagina(url, motor)` — context manager que devuelve una página ya
     cargada con el motor indicado.
  3. `descargar_html(url)` — descarga solo el HTML imitando el fingerprint TLS
     de un navegador real, sin ejecutar JavaScript.

**Por qué varios motores.** La medición sobre los sitios problemáticos mostró que
cada sistema de protección bloquea en una capa distinta:

  - Cloudflare (decathlon.es) rechaza por *fingerprint TLS*: `curl` recibe 403
    pero `curl_cffi` imitando a Chrome recibe 200 sin ejecutar nada de JS.
    Camoufox, que es un Firefox parcheado en C++, también pasa.
  - DataDome (elpais.com) rechaza por *reputación de IP*: devuelve 403 incluso
    con TLS correcto, sin JavaScript y sin señales de automatización. Ningún
    motor lo resuelve; haría falta cambiar de IP.

Por eso la cascada prueba primero Chromium (rápido, y suficiente para la mayoría
de sitios) y solo recurre a Camoufox cuando el detector confirma un bloqueo.
Patchright se descartó tras medirlo: parchea el protocolo de automatización, que
no es la capa por la que bloquean estos sitios.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from anti_bot import detectar_bloqueo, detectar_bloqueo_pagina

log = logging.getLogger(__name__)

# Orden de la cascada: del más rápido al más resistente
MOTORES = ("chromium", "camoufox")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ── Limitación de ritmo ───────────────────────────────────────────────────────
#
# Las mediciones mostraron que la reputación de IP se degrada con cada petición:
# elpais.com devolvió 200 en una prueba y 403 en las siguientes con el mismo
# motor. Espaciar las visitas al mismo dominio reduce los bloqueos y es la
# práctica correcta en medición web: la auditoría no debe castigar al sitio.

RETARDO_DOMINIO = 4.0   # segundos mínimos entre peticiones al mismo dominio

_ultima_visita: dict[str, float] = {}
_ritmo_lock = threading.Lock()


def _dominio(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def esperar_turno(url: str, retardo: float | None = None) -> None:
    """
    Bloquea hasta que haya pasado `retardo` segundos desde la última petición
    al mismo dominio. Seguro entre hilos: la auditoría batch procesa varios
    sitios a la vez y dos de ellos pueden compartir dominio base.
    """
    retardo = RETARDO_DOMINIO if retardo is None else retardo
    if retardo <= 0:
        return
    dom = _dominio(url)
    with _ritmo_lock:
        ahora = time.monotonic()
        anterior = _ultima_visita.get(dom)
        espera = 0.0 if anterior is None else retardo - (ahora - anterior)
        # Se reserva el turno ya, para que otro hilo no calcule sobre el valor viejo
        _ultima_visita[dom] = ahora + max(espera, 0.0)
    if espera > 0:
        log.debug("Ritmo: esperando %.1fs antes de visitar %s", espera, dom)
        time.sleep(espera)


# ── Descarga de HTML sin navegador ────────────────────────────────────────────

def descargar_html(url: str, timeout: int = 25) -> tuple[str, int | None]:
    """
    Descarga el HTML imitando el fingerprint TLS de Chrome real.

    Resuelve los bloqueos de capa 2 (Cloudflare por TLS) sin coste de navegador,
    pero no ejecuta JavaScript: sirve para extraer enlaces o texto, no para
    medir un banner renderizado.

    Returns:
        (html, status). html es "" si la descarga falla.
    """
    try:
        from curl_cffi import requests as _cr
    except ImportError:
        log.debug("curl_cffi no disponible; se omite la vía HTML")
        return "", None

    esperar_turno(url)
    try:
        r = _cr.get(url, impersonate="chrome", timeout=timeout)
        return r.text or "", r.status_code
    except Exception as e:
        log.debug("descargar_html falló para %s: %s", url, e)
        return "", None


# ── Apertura de página por motor ──────────────────────────────────────────────

@contextmanager
def abrir_pagina(url: str, motor: str = "chromium", *,
                 timeout_ms: int = 45000, espera_ms: int = 3000,
                 aplicar_ritmo: bool = True):
    """
    Context manager que entrega una página ya cargada con el motor indicado.

    Yields:
        (page, bloqueo). `page` es None si el motor no pudo arrancar o navegar;
        `bloqueo` es el dict de anti_bot o None.

    Nota: Camoufox es Firefox, por lo que **no expone CDP**. Los usos que
    dependen de `Accessibility.getFullAXTree` (árbol de accesibilidad para R1/R5)
    solo pueden emplear Chromium.
    """
    if aplicar_ritmo:
        esperar_turno(url)

    if motor == "camoufox":
        ctx_mgr, page, cerrar = _abrir_camoufox(url, timeout_ms, espera_ms)
    else:
        ctx_mgr, page, cerrar = _abrir_chromium(url, timeout_ms, espera_ms)

    try:
        bloqueo = detectar_bloqueo_pagina(page) if page else {
            "sistema": "Desconocido", "senal": "navegación fallida",
            "status": None, "confianza": "media"}
        yield page, bloqueo
    finally:
        try:
            cerrar()
        except Exception:
            pass


def _abrir_chromium(url: str, timeout_ms: int, espera_ms: int):
    """Chromium + playwright-stealth: el camino rápido y por defecto."""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    pw = sync_playwright().start()
    browser = ctx = page = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=_UA, locale="es-ES", timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 768},
        )
        Stealth().apply_stealth_sync(ctx)
        page = ctx.new_page()
        page.on("pageerror", lambda _: None)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            log.debug("chromium: fallo al navegar a %s: %s", url, e)
        page.wait_for_timeout(espera_ms)
    except Exception as e:
        log.warning("chromium no pudo arrancar: %s", e)
        page = None

    def cerrar():
        for obj in (ctx, browser):
            try:
                obj and obj.close()
            except Exception:
                pass
        pw.stop()

    return pw, page, cerrar


def _abrir_camoufox(url: str, timeout_ms: int, espera_ms: int):
    """
    Camoufox: Firefox con el fingerprint parcheado a nivel C++. Más lento que
    Chromium, pero supera los bloqueos por fingerprint TLS de Cloudflare.
    """
    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        log.warning("camoufox no está instalado; no hay motor de reserva")
        return None, None, lambda: None

    cm = Camoufox(headless=True, locale="es-ES")
    browser = page = None
    try:
        browser = cm.__enter__()
        page = browser.new_page()
        page.on("pageerror", lambda _: None)   # evita el crash del driver (Bug 21)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            log.debug("camoufox: fallo al navegar a %s: %s", url, e)
        page.wait_for_timeout(espera_ms)
    except Exception as e:
        log.warning("camoufox no pudo arrancar: %s", e)
        page = None

    def cerrar():
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass

    return cm, page, cerrar


# ── Cascada ───────────────────────────────────────────────────────────────────

def con_cascada(url: str, accion, *, motores=MOTORES, **kw):
    """
    Ejecuta `accion(page)` con el primer motor que no resulte bloqueado.

    Si un motor entrega la página real, se usa su resultado. Si el detector
    confirma bloqueo, se pasa al siguiente motor. Cuando todos fallan se
    devuelve el resultado del último intento junto con el bloqueo, para que el
    llamante pueda registrar el motivo.

    Args:
        url:     URL a visitar.
        accion:  callable que recibe la página y devuelve lo que necesite.
        motores: orden de motores a probar.

    Returns:
        (resultado, motor_usado, bloqueo)
    """
    resultado = None
    bloqueo_final = None
    motor_usado = None

    for i, motor in enumerate(motores):
        with abrir_pagina(url, motor, **kw) as (page, bloqueo):
            motor_usado, bloqueo_final = motor, bloqueo
            if page is None:
                continue
            if bloqueo:
                log.info("%s bloqueado con %s (%s)%s", url, motor,
                         bloqueo.get("sistema"),
                         "; reintentando con otro motor"
                         if i + 1 < len(motores) else "")
                # Se guarda lo que haya podido extraerse, por si no hay más motores
                try:
                    resultado = accion(page)
                except Exception:
                    pass
                continue
            try:
                resultado = accion(page)
                if i > 0:
                    log.info("%s recuperado con motor de reserva '%s'", url, motor)
                return resultado, motor, None
            except Exception as e:
                log.warning("acción falló sobre %s con %s: %s", url, motor, e)

    return resultado, motor_usado, bloqueo_final
