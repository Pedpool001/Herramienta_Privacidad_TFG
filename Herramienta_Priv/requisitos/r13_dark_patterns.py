"""
R13 - Ausencia de Dark Patterns en el banner de cookies
(RGPD Art. 7 / Directriz EDPB 3/2022 sobre Dark Patterns)

Analiza el banner de cookies de un sitio web en vivo para detectar prácticas
de diseño engañosas que dificultan al usuario rechazar el consentimiento.

Sub-patrones evaluados:
  1. OBSTRUCCIÓN   — no hay botón de rechazo visible en la primera capa
  2. ASIMETRÍA     — el botón de aceptar es visualmente más prominente que el de rechazar
  3. OCULTACIÓN    — opciones de rechazo con tamaño de fuente, opacidad o contraste
                     que dificultan su localización
  4. SIN BANNER    — no se detectó ningún banner (no evaluable)

Veredicto global:
  FAILED  — al menos un sub-patrón FAILED
  WARNING — al menos un sub-patrón WARNING (y ninguno FAILED)
  PASSED  — banner presente y sin patrones engañosos detectados
  UNKNOWN — no se detectó banner de cookies

Uso:
  python3 r13_dark_patterns.py https://elpais.com
  python3 r13_dark_patterns.py https://elpais.com --no-detalle
  python3 r13_dark_patterns.py https://elpais.com --screenshot
"""

import os
import sys
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

# Camoufox (Firefox parcheado) como motor de respaldo cuando Chromium es
# bloqueado por un anti-bot de fingerprint/headless (p. ej. DataDome en
# reuters.com, que sirve página vacía a Chromium headless pero sí carga con
# Camoufox desde la misma IP). Import guardado: si no está instalado, el
# fallback simplemente no se intenta y R13 funciona igual que antes.
try:
    from camoufox.sync_api import Camoufox
    _CAMOUFOX_OK = True
except Exception:
    _CAMOUFOX_OK = False

# Detector de bloqueo anti-bot (vive en Herramienta_Priv/). Se usa solo para
# etiquetar el motivo en el log; el disparador del fallback es el nº de enlaces.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from anti_bot import detectar_bloqueo, describir
except Exception:
    detectar_bloqueo = describir = None

# ── Rutas ─────────────────────────────────────────────────────────────────────
_ANALYSIS_DIR  = Path(os.getenv("ANALYSIS_DATA_DIR",
                                str(Path(__file__).resolve().parents[1] / "analysis_data")))
RESULTADO_PATH = _ANALYSIS_DIR / "r13_resultado.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── Palabras clave para clasificar botones ────────────────────────────────────
PALABRAS_ACEPTAR = {
    "aceptar", "aceptar todo", "aceptar todos", "aceptar todas", "acepto", "accept",
    "accept all", "accept cookies", "aceptar cookies", "aceptar y cerrar",
    "aceptar y continuar", "aceptar y seguir", "sí, acepto", "si acepto",
    "accepter", "accepter tout", "accetta", "accetta tutto", "alle akzeptieren",
    "agree", "i agree", "agree and close", "allow all", "allow", "permitir",
    "permitir todo", "permitir todas", "autorizar", "consentir", "consiento",
    "de acuerdo", "estoy de acuerdo", "vale", "aceptar y cerrar (recomendado)",
    "entendido", "understood", "got it", "ok",
}

PALABRAS_RECHAZAR = {
    "rechazar", "rechazar todo", "rechazar todos", "rechazar todas", "rechazo",
    "rechazar cookies", "rechazar y cerrar", "rechazar y continuar",
    "denegar", "denegar todo", "denegar todas",
    "no acepto", "no aceptar", "no consiento", "no, gracias", "no gracias",
    "reject", "reject all", "reject cookies", "refuse", "refuser", "tout refuser",
    "rifiuta", "ablehnen", "alle ablehnen", "deny", "deny all", "decline",
    "disagree", "disagree and close", "do not accept",
    "solo necesarias", "solo esenciales", "solo las necesarias", "solo funcionales",
    "only necessary", "only essential", "only required", "necessary only",
    "continuar sin aceptar", "continue without accepting", "seguir sin aceptar",
    "usar solo necesarias", "prescindir",
}

PALABRAS_CONFIGURAR = {
    "configurar", "configuración", "configuracion", "configurar cookies",
    "configurar preferencias", "configure", "settings", "cookie settings",
    "ajustes", "ajustes de cookies", "administrar", "administrar opciones",
    "preferencias", "preferences", "mis preferencias", "personalizar", "customize",
    "gestionar", "gestionar cookies", "gestionar opciones", "manage",
    "manage options", "manage preferences", "mehr optionen", "plus d'options",
    "more options", "más opciones", "mas opciones", "mis opciones",
    "más información", "mas informacion", "more info", "more information",
    "ver opciones", "ver preferencias", "opciones", "elegir",
    "mostrar finalidades", "show purposes", "purposes",
}

# ── Selectores de CMPs conocidos ──────────────────────────────────────────────
SELECTORES_CMP = [
    # OneTrust
    "#onetrust-banner-sdk",
    "#onetrust-consent-sdk",
    # Cookiebot
    "#CybotCookiebotDialog",
    # Didomi (shadow host — el contenido real está en su shadow root)
    "#didomi-host",
    ".didomi-popup-container",
    # Quantcast / Sourcepoint
    "#sp-cc",
    "[id^='sp_message_container']",
    ".message-container",
    "[data-testid='cookie-policy-dialog']",
    # Usercentrics
    "#usercentrics-root",
    "[data-testid='uc-app']",
    "#uc-center-container",
    # Genéricos con ARIA
    "[role='dialog'][aria-modal='true']",
    "[role='alertdialog']",
    # Genéricos por nombre en ID/clase
    "[id*='cookie-banner']",
    "[id*='cookieBanner']",
    "[id*='cookie_banner']",
    "[id*='consent-banner']",
    "[class*='cookie-banner']",
    "[class*='cookie-notice']",
    "[class*='consent-banner']",
    "[class*='gdpr-banner']",
    "[id*='gdpr']",
    # IAB TCF framework
    "#qc-cmp2-container",
    ".qc-cmp2-summary-buttons",
]

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌", "UNKNOWN": "❓"}


# ── Helpers de color ──────────────────────────────────────────────────────────
def parsear_rgb(color_str: str) -> tuple | None:
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", color_str)
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return (r, g, b, a)


def es_color_prominente(color_str: str) -> bool:
    rgb = parsear_rgb(color_str)
    if rgb is None:
        return False
    r, g, b, a = rgb
    if a < 0.15:
        return False
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    if max_c == 0:
        return False
    saturacion = (max_c - min_c) / max_c
    luminosidad = (r + g + b) / 3
    return saturacion > 0.15 and luminosidad < 230


def diferencia_luminosidad(c1: str, c2: str) -> float:
    rgb1 = parsear_rgb(c1)
    rgb2 = parsear_rgb(c2)
    if rgb1 is None or rgb2 is None:
        return 0.0
    lum1 = (rgb1[0] + rgb1[1] + rgb1[2]) / 3
    lum2 = (rgb2[0] + rgb2[1] + rgb2[2]) / 3
    return abs(lum1 - lum2)


# ── Clasificación de botones ──────────────────────────────────────────────────
def _frase_en_texto(frase: str, texto: str) -> bool:
    return (" " + frase + " ") in (" " + texto + " ")


# Rechazo del tipo "solo las esenciales/necesarias", robusto al orden de las
# palabras. En inglés el orden habitual es "essential cookies only" (no "only
# essential"), que las frases fijas no capturaban → CNN daba rechazar:0 y un
# falso obstrucción=FAILED. "only/solo" + "essential/necessary" en cualquier
# orden = rechazo (usar únicamente las cookies imprescindibles).
_RE_SOLO_ESENCIALES = re.compile(
    r"\b(only|just|solo|s[oó]lo)\b.*(essential|necessary|required|necesari|esencial|imprescindibl)"
    r"|(essential|necessary|required|necesari|esencial|imprescindibl).*\b(only|solo|s[oó]lo|[uú]nicament)",
    re.IGNORECASE,
)


def clasificar_texto(texto: str) -> str:
    t = texto.strip().lower()
    if t in PALABRAS_ACEPTAR:
        return "aceptar"
    if t in PALABRAS_RECHAZAR:
        return "rechazar"
    if t in PALABRAS_CONFIGURAR:
        return "configurar"
    if _RE_SOLO_ESENCIALES.search(t):
        return "rechazar"
    for p in PALABRAS_RECHAZAR:
        if _frase_en_texto(p, t):
            return "rechazar"
    for p in PALABRAS_ACEPTAR:
        if _frase_en_texto(p, t):
            return "aceptar"
    for p in PALABRAS_CONFIGURAR:
        if _frase_en_texto(p, t):
            return "configurar"
    return "otro"


def _etiqueta_boton(b: dict) -> str:
    """Etiqueta legible del botón para los informes: texto visible o, si no lo
    hay (botón-icono), su aria-label o title."""
    return (b.get("texto") or b.get("aria") or b.get("title") or "").strip()


def clasificar_boton(b: dict) -> str:
    """
    Clasifica un botón usando su texto visible y, como respaldo, su `aria-label`
    y `title`. Portado de la mejora de R4: los botones de icono (rechazo ✕,
    engranaje de configuración) no llevan texto visible pero sí exponen su nombre
    accesible por atributo. Sin esto se clasificaban como "otro" y R13 concluía
    erróneamente que no había botón de rechazo (falso obstrucción=FAILED).

    Se prioriza el texto visible: solo se recurre a aria-label/title cuando el
    texto no permite clasificar (evita que un aria genérico pise una etiqueta
    de texto clara).
    """
    cat = clasificar_texto(b.get("texto", "") or "")
    if cat != "otro":
        return cat
    for campo in ("aria", "title"):
        val = b.get(campo, "") or ""
        if val:
            c = clasificar_texto(val)
            if c != "otro":
                return c
    return "otro"


# ── JS compartido: traversal de Shadow DOM ────────────────────────────────────
# Muchos CMPs (Didomi, OneTrust v2, etc.) renderizan su banner dentro de un
# Shadow Root. La función deepQueryAll() atraviesa recursivamente todos los
# shadow roots del árbol para encontrar elementos que document.querySelectorAll
# estándar no vería.
_JS_DEEP_QUERY = """
function deepQueryAll(root, sel) {
    const results = Array.from(root.querySelectorAll(sel));
    for (const el of root.querySelectorAll('*')) {
        if (el.shadowRoot) results.push(...deepQueryAll(el.shadowRoot, sel));
    }
    return results;
}
"""


# ── Detección del banner ──────────────────────────────────────────────────────
def _detectar_en_frame(frame) -> str | None:
    """
    Detecta el banner de cookies dentro de UN frame (página principal o iframe):
    1. Selectores CSS conocidos de los principales CMPs.
    2. Fallback JS con Shadow DOM traversal.

    Se usa `query_selector` (inmediato) en vez de `wait_for_selector`, porque la
    espera global de 8 s ya se hizo antes de llamar aquí; esperar 2 s por cada
    selector y frame dispararía el tiempo con CMPs en iframe (varios frames).
    """
    # Fase 1: selectores conocidos
    for selector in SELECTORES_CMP:
        try:
            el = frame.query_selector(selector)
            if el and el.is_visible():
                return selector
        except Exception:
            continue

    # Fase 2: fallback genérico con Shadow DOM
    try:
        selector = frame.evaluate("""() => {
            """ + _JS_DEEP_QUERY + """
            // Incluir <a>: muchos CMP (p. ej. muros "consent or pay" como el de
            // 20minutos) usan enlaces con clase de botón, no <button>.
            const RE  = /rechazar|reject|decline|denegar|aceptar|accept|akzeptieren|consent|permitir|configurar|preferenc|personaliza|ajustes/i;
            const ACC = /aceptar|accept|akzeptieren|permitir|de acuerdo|estoy de acuerdo|agree|consent/i;
            const texto = b => (b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '');

            const todos    = deepQueryAll(document, 'button, a, [role="button"]');
            const matches  = todos.filter(b => RE.test(texto(b)));
            if (matches.length < 2) return null;

            // Semillas: botones de ACEPTAR, que viven en el banner y no en el
            // footer (donde suele haber solo un enlace "Personalizar cookies").
            // Si no hay ninguno (posible obstrucción), se usan todos.
            let seeds = matches.filter(b => ACC.test(texto(b)));
            if (seeds.length === 0) seeds = matches;

            // Desde cada semilla, subir hasta el contenedor que agrupa ≥ 2
            // controles de consentimiento y tiene id o clase con la que anclar.
            for (const seed of seeds) {
                let el = seed;
                for (let i = 0; i < 15; i++) {
                    const parent = el.parentElement
                        || (el.getRootNode && el.getRootNode() !== document
                            ? el.getRootNode().host : null);
                    if (!parent || parent === document.body) break;
                    el = parent;
                    const contained = deepQueryAll(el, 'button, a, [role="button"]')
                        .filter(b => RE.test(texto(b)));
                    if (contained.length >= 2) {
                        if (el.id) return '#' + CSS.escape(el.id);
                        const cls = (el.className + '').trim().split(/\\s+/)[0];
                        if (cls) return el.tagName.toLowerCase() + '.' + CSS.escape(cls);
                    }
                }
            }
            return null;
        }""")
        if selector:
            return selector
    except Exception:
        pass

    return None


def localizar_banner(page):
    """
    Busca el banner en TODOS los frames (principal + iframes) y devuelve
    `(frame, selector, botones)` del primero cuyo banner contenga botones.

    Iterar los iframes es imprescindible para los CMP que renderizan el banner
    dentro de un `<iframe>` (Sourcepoint en theguardian.com y bbc.com →
    `sourcepoint.*` / `cdn.privacy-mgmt.com`, TrustArc, algunas configs de
    Quantcast). En esos casos la página principal solo tiene un placeholder
    (`sp_message_container`) sin botones; los controles reales viven en el iframe.
    Por eso no basta con detectar el contenedor: nos quedamos con el frame que
    realmente aporta botones, y así el placeholder vacío no gana.

    Si algún frame tiene banner pero ningún frame aporta botones, se devuelve
    `(None, selector_visto, [])` para poder informar NO_EVALUABLE (no FAILED).
    """
    selector_visto = None
    for frame in page.frames:
        try:
            sel = _detectar_en_frame(frame)
        except Exception:
            sel = None
        if not sel:
            continue
        if selector_visto is None:
            selector_visto = sel
        botones = extraer_botones(frame, sel)
        if botones:
            return frame, sel, botones
    return None, selector_visto, []


# ── Extracción de propiedades de botones ──────────────────────────────────────
def extraer_botones(frame, banner_selector: str) -> list[dict]:
    """
    Extrae todos los botones e interactivos del banner, incluyendo los que están
    dentro de Shadow DOM (típico en Didomi, OneTrust v2).

    Opera sobre un `frame` (página principal o iframe) con `frame.evaluate()` y
    el selector como argumento, para poder manejar tanto shadow hosts como
    elementos normales sin cambiar el punto de llamada.
    """
    js = """
    (sel) => {
        """ + _JS_DEEP_QUERY + """

        // Localiza el banner en el DOM principal o en shadow roots
        let banner = document.querySelector(sel);
        if (!banner) {
            const hosts = Array.from(document.querySelectorAll('*'))
                .filter(el => el.shadowRoot);
            for (const h of hosts) {
                const found = h.shadowRoot.querySelector(sel);
                if (found) { banner = found; break; }
            }
        }
        if (!banner) return [];

        // Si el banner es un shadow host, buscar dentro del shadow root
        const searchRoot = banner.shadowRoot || banner;
        const botones = deepQueryAll(searchRoot, 'button, a, [role="button"]');

        return botones.map(el => {
            const st = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return {
                texto:     (el.innerText || el.textContent || '').trim().toLowerCase(),
                aria:      (el.getAttribute('aria-label') || '').trim().toLowerCase(),
                title:     (el.getAttribute('title') || '').trim().toLowerCase(),
                bg_color:  st.backgroundColor,
                color:     st.color,
                font_size: parseFloat(st.fontSize),
                opacity:   parseFloat(st.opacity),
                visible:   el.offsetHeight > 0 && el.offsetWidth > 0,
                width:     Math.round(rect.width),
                height:    Math.round(rect.height),
            };
        });
    }
    """
    try:
        botones = frame.evaluate(js, banner_selector)
        # Conservar los botones con CUALQUIER etiqueta (texto, aria-label o
        # title). Antes se exigía texto visible, lo que descartaba los
        # botones-icono (✕ de rechazo, engranaje de configuración) que solo
        # exponen su nombre por aria-label.
        return [b for b in botones
                if len(b.get("texto", "")) > 1 or b.get("aria") or b.get("title")]
    except Exception:
        return []


# ── Reglas de dark patterns ───────────────────────────────────────────────────

def evaluar_obstruccion(clasificados: dict) -> dict:
    rechazar = clasificados.get("rechazar", [])
    if not rechazar:
        return {
            "estado": "FAILED",
            "detalle": "No se encontró botón de rechazo en la primera capa del banner.",
        }
    visibles = [b for b in rechazar if b["visible"]]
    if not visibles:
        return {
            "estado": "WARNING",
            "detalle": "Botón de rechazo presente en el DOM pero no visible (oculto por CSS).",
        }
    return {
        "estado": "PASSED",
        "detalle": f"Botón de rechazo visible encontrado: '{_etiqueta_boton(visibles[0])}'.",
    }


def evaluar_asimetria(clasificados: dict) -> dict:
    aceptar  = clasificados.get("aceptar", [])
    rechazar = clasificados.get("rechazar", [])

    if not aceptar or not rechazar:
        return {
            "estado": "UNKNOWN",
            "detalle": "No hay par aceptar/rechazar para comparar.",
        }

    btn_ac = aceptar[0]
    btn_re = rechazar[0]

    ac_prominente = es_color_prominente(btn_ac["bg_color"])
    re_prominente = es_color_prominente(btn_re["bg_color"])

    if ac_prominente and not re_prominente:
        return {
            "estado": "FAILED",
            "detalle": (
                f"Asimetría cromática: aceptar='{btn_ac['bg_color']}' (color saturado) "
                f"vs rechazar='{btn_re['bg_color']}' (transparente/plano)."
            ),
        }

    diff_lum = diferencia_luminosidad(btn_ac["bg_color"], btn_re["bg_color"])
    if diff_lum > 80:
        return {
            "estado": "WARNING",
            "detalle": (
                f"Diferencia de luminosidad notable ({diff_lum:.0f}/255) entre "
                f"aceptar ('{btn_ac['bg_color']}') y rechazar ('{btn_re['bg_color']}')."
            ),
        }

    return {
        "estado": "PASSED",
        "detalle": (
            f"Sin asimetría cromática relevante. "
            f"aceptar='{btn_ac['bg_color']}' / rechazar='{btn_re['bg_color']}'."
        ),
    }


def evaluar_ocultacion(clasificados: dict) -> dict:
    candidatos = (
        clasificados.get("rechazar", []) +
        clasificados.get("configurar", [])
    )
    if not candidatos:
        return {
            "estado": "PASSED",
            "detalle": "No hay botones de rechazo/configuración que analizar.",
        }

    problemas = []
    for b in candidatos:
        etq = _etiqueta_boton(b)
        if b["opacity"] < 0.5:
            problemas.append(f"'{etq}': opacidad={b['opacity']:.2f}")
        if b["font_size"] and b["font_size"] < 11:
            problemas.append(f"'{etq}': fuente={b['font_size']}px")
        if b["width"] > 0 and b["height"] > 0:
            if b["width"] < 30 or b["height"] < 12:
                problemas.append(
                    f"'{etq}': tamaño={b['width']}×{b['height']}px"
                )

    if problemas:
        return {
            "estado": "FAILED",
            "detalle": "Señales de ocultación: " + "; ".join(problemas),
        }
    return {
        "estado": "PASSED",
        "detalle": "Sin señales de ocultación CSS en botones de rechazo/configuración.",
    }


# ── Veredicto global ──────────────────────────────────────────────────────────
def veredicto_global(evaluaciones: dict) -> str:
    estados = [v["estado"] for v in evaluaciones.values() if v["estado"] != "UNKNOWN"]
    if "FAILED" in estados:
        return "FAILED"
    if "WARNING" in estados:
        return "WARNING"
    if estados:
        return "PASSED"
    return "UNKNOWN"


# ── Salida por consola ────────────────────────────────────────────────────────
def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado["sitio"]
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluaciones"]

    print(f"\n{'='*64}")
    print(f"  R13 — Dark Patterns en banner de cookies")
    print(f"  Sitio   : {sitio}")
    print(f"  Veredicto: {ICONO.get(veredicto, '?')} {veredicto}")
    print(f"{'='*64}")

    if resultado.get("banner_detectado"):
        print(f"\nBanner detectado con selector: {resultado['banner_selector']}")
        print(f"Botones encontrados: {resultado['total_botones']}")
        resumen_tipos = {k: len(v) for k, v in resultado["clasificados"].items() if v}
        print(f"Clasificación: {resumen_tipos}")
    else:
        print("\n⚠️  No se detectó banner de cookies.")
        return

    if not detalle:
        print()
        return

    print(f"\n{'─'*64}")
    print("Sub-evaluaciones:\n")
    nombres = {
        "obstruccion": "Obstrucción (rechazo en 1ª capa)",
        "asimetria":   "Asimetría visual (color de botones)",
        "ocultacion":  "Ocultación (CSS sobre rechazo)",
    }
    for clave, nombre in nombres.items():
        e = ev.get(clave, {})
        estado = e.get("estado", "UNKNOWN")
        print(f"  {ICONO.get(estado, '?')} {nombre}: {estado}")
        if detalle and e.get("detalle"):
            print(f"       {e['detalle']}")
        print()

    if resultado.get("botones_detalle"):
        print(f"{'─'*64}")
        print("Botones del banner por categoría:\n")
        for cat, botones in resultado["clasificados"].items():
            if not botones:
                continue
            print(f"  [{cat.upper()}]")
            for b in botones:
                print(f"    · '{_etiqueta_boton(b)[:50]}'")
                print(f"       bg={b['bg_color']}  fuente={b['font_size']}px  "
                      f"opac={b['opacity']}  {b['width']}×{b['height']}px")
        print()


# ── Motor principal ───────────────────────────────────────────────────────────
def _analizar_pagina(page, url: str, screenshot: bool, motor: str,
                     espera_ms: int = 8000) -> dict:
    """
    Analiza una página YA navegada, con independencia del motor (Chromium o
    Camoufox): espera a que el CMP renderice, localiza el banner en todos los
    frames, extrae y clasifica los botones y evalúa los sub-patrones.

    Devuelve siempre el dict de resultado (UNKNOWN si no hay banner, o si el
    banner no aporta botones analizables). No cierra el navegador: el ciclo de
    vida lo gestiona el llamante. Toda la lógica de detección/clasificación es
    JS puro (`deepQueryAll`, `frame.evaluate`), por lo que funciona igual bajo
    Firefox/Camoufox que bajo Chromium.
    """
    resultado = {
        "sitio":            url,
        "veredicto":        "UNKNOWN",
        "banner_detectado": False,
        "evaluaciones":     {},
        "motor":            motor,
    }

    # Espera para que el CMP inyecte y renderice el banner (2-5 s lo habitual).
    page.wait_for_timeout(espera_ms)

    if screenshot:
        try:
            ss_path = RESULTADO_PATH.parent / f"r13_screenshot_{motor}.png"
            page.screenshot(path=str(ss_path), full_page=False)
            print(f"[*] Screenshot guardado en {ss_path}")
        except Exception:
            pass

    # ── Detección del banner + extracción (busca en todos los frames) ──
    _frame, banner_selector, botones = localizar_banner(page)

    if not banner_selector:
        return resultado  # UNKNOWN — no se detectó banner

    print(f"[*] ({motor}) Banner encontrado: {banner_selector}")
    print(f"[*] ({motor}) Botones extraídos del banner: {len(botones)}")

    # Banner detectado pero sin botones analizables (placeholder o iframe que
    # no pudimos leer): NO_EVALUABLE, no FAILED — es un límite de medición, no
    # un dark pattern real.
    if not botones:
        resultado["banner_detectado"] = True
        resultado["banner_selector"] = banner_selector
        resultado["total_botones"] = 0
        return resultado  # UNKNOWN

    clasificados: dict[str, list] = {
        "aceptar": [], "rechazar": [], "configurar": [], "otro": [],
    }
    for b in botones:
        clasificados[clasificar_boton(b)].append(b)

    evaluaciones = {
        "obstruccion": evaluar_obstruccion(clasificados),
        "asimetria":   evaluar_asimetria(clasificados),
        "ocultacion":  evaluar_ocultacion(clasificados),
    }

    resultado.update({
        "veredicto":        veredicto_global(evaluaciones),
        "banner_detectado": True,
        "banner_selector":  banner_selector,
        "total_botones":    len(botones),
        "clasificados":     clasificados,
        "evaluaciones":     evaluaciones,
        "botones_detalle":  True,
    })
    return resultado


def _contar_enlaces(page) -> int:
    try:
        return int(page.evaluate("() => document.querySelectorAll('a[href]').length"))
    except Exception:
        return -1


def auditar(url: str, screenshot: bool = False) -> dict:
    """
    Audita R13 con Chromium (+ playwright-stealth) y, si Chromium queda
    bloqueado por un anti-bot (página casi vacía, sin banner), reintenta con
    Camoufox (Firefox parcheado).

    Por qué Chromium primero: es rápido, con stealth basta para la mayoría de
    sitios de medios, y es el único con CDP (que usan R1/R5 en playwright_mod).
    Por qué Camoufox de respaldo: algunos anti-bot de fingerprint/headless
    (DataDome en reuters.com) sirven página vacía a Chromium headless pero sí
    cargan con Camoufox desde la MISMA IP. (No es el caso de DataDome por
    reputación de IP —elpais.com—, que ningún motor resuelve.) El fallback solo
    se intenta si Chromium no detectó banner Y la página vino casi vacía, para
    no pagar el coste de Camoufox en los sitios que ya funcionan o que
    legítimamente no tienen banner.
    """
    resultado = {
        "sitio":            url,
        "veredicto":        "UNKNOWN",
        "banner_detectado": False,
        "evaluaciones":     {},
    }
    n_enlaces, html_bloqueo, status_bloqueo = -1, "", None

    # ── Intento 1: Chromium + stealth ─────────────────────────────────────────
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="es-ES",
                timezone_id="Europe/Madrid",
                user_agent=_UA,
            )
            Stealth().apply_stealth_sync(ctx)
            page = ctx.new_page()
            page.on("pageerror", lambda _: None)
            page.on("console",   lambda _: None)

            print(f"[*] Navegando a {url} (chromium) …")
            resp = None
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

            resultado = _analizar_pagina(page, url, screenshot, "chromium")

            # Si no hubo banner, recoger señales de bloqueo para decidir fallback
            if not resultado.get("banner_detectado"):
                n_enlaces = _contar_enlaces(page)
                try:
                    html_bloqueo = page.content()
                except Exception:
                    html_bloqueo = ""
                status_bloqueo = resp.status if resp is not None else None
            browser.close()
    except Exception as _e:
        print(f"[!] Chromium terminó inesperadamente: {_e}")

    # ── ¿Parece bloqueado? (sin banner + página casi vacía, o firma anti-bot) ──
    bloqueo = None
    if detectar_bloqueo and not resultado.get("banner_detectado"):
        try:
            bloqueo = detectar_bloqueo(html=html_bloqueo, titulo="",
                                       status=status_bloqueo, cabeceras={},
                                       n_enlaces=n_enlaces)
        except Exception:
            bloqueo = None
    parece_bloqueado = (not resultado.get("banner_detectado")) and (
        bloqueo is not None or (0 <= n_enlaces < 10)
    )

    # ── Intento 2 (fallback): Camoufox, solo si Chromium parece bloqueado ─────
    if parece_bloqueado and _CAMOUFOX_OK:
        motivo = f" [{describir(bloqueo)}]" if (bloqueo and describir) else ""
        print(f"[*] Chromium sin banner y página casi vacía "
              f"({n_enlaces} enlaces){motivo}; reintentando con Camoufox…")
        try:
            with Camoufox(headless=True, locale="es-ES") as browser:
                page = browser.new_page()
                page.on("pageerror", lambda _: None)
                print(f"[*] Navegando a {url} (camoufox) …")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=40000)
                except Exception:
                    pass
                # Camoufox arranca más lento; se da algo más de margen al CMP.
                res2 = _analizar_pagina(page, url, screenshot, "camoufox",
                                        espera_ms=10000)
                if res2.get("banner_detectado"):
                    resultado = res2
                browser.close()
        except Exception as _e:
            print(f"[!] Camoufox terminó inesperadamente: {_e}")
    elif parece_bloqueado and not _CAMOUFOX_OK:
        print("[*] Chromium bloqueado pero Camoufox no está instalado; "
              "no se puede reintentar.")

    return resultado


# ── Main ──────────────────────────────────────────────────────────────────────
def _preparar_json(obj):
    if isinstance(obj, dict):
        return {k: _preparar_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_preparar_json(i) for i in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def main():
    args       = sys.argv[1:]
    detalle    = "--no-detalle" not in args
    screenshot = "--screenshot" in args
    args       = [a for a in args if not a.startswith("--")]

    if not args:
        print("Uso: python3 r13_dark_patterns.py <url> [--no-detalle] [--screenshot]")
        sys.exit(1)

    url = args[0]
    if not url.startswith("http"):
        url = "https://" + url

    resultado = auditar(url, screenshot=screenshot)

    resultado_json = _preparar_json(resultado)
    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado_json, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
