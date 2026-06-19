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

import sys
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

# ── Rutas ─────────────────────────────────────────────────────────────────────
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r13_resultado.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── Palabras clave para clasificar botones ────────────────────────────────────
PALABRAS_ACEPTAR = {
    "aceptar", "aceptar todo", "aceptar todos", "acepto", "accept", "accept all",
    "accepter", "accepter tout", "accetta", "accetta tutto", "alle akzeptieren",
    "agree", "i agree", "allow all", "permitir", "permitir todo", "autorizar",
    "entendido", "understood", "got it",
}

PALABRAS_RECHAZAR = {
    "rechazar", "rechazar todo", "rechazar todos", "rechazo",
    "no acepto", "no aceptar",
    "reject", "reject all", "refuse", "refuser", "tout refuser", "rifiuta",
    "ablehnen", "alle ablehnen", "deny", "decline", "solo necesarias",
    "solo esenciales", "only necessary", "only essential", "only required",
    "continuar sin aceptar", "continue without accepting", "usar solo necesarias",
}

PALABRAS_CONFIGURAR = {
    "configurar", "configuración", "configuracion", "configure", "settings",
    "preferencias", "preferences", "personalizar", "customize", "gestionar",
    "manage", "mehr optionen", "plus d'options", "more options", "más opciones",
    "más información", "more info", "ver opciones", "opciones",
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
    "[data-testid='cookie-policy-dialog']",
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


def clasificar_texto(texto: str) -> str:
    t = texto.strip().lower()
    if t in PALABRAS_ACEPTAR:
        return "aceptar"
    if t in PALABRAS_RECHAZAR:
        return "rechazar"
    if t in PALABRAS_CONFIGURAR:
        return "configurar"
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
def detectar_banner(page) -> str | None:
    """
    Detecta el banner de cookies en dos fases:
    1. Selectores CSS conocidos de los principales CMPs.
    2. Fallback JS con Shadow DOM traversal.
    """
    # Fase 1: selectores conocidos
    for selector in SELECTORES_CMP:
        try:
            page.wait_for_selector(selector, timeout=2000, state="visible")
            return selector
        except PlaywrightTimeout:
            continue
        except Exception:
            continue

    # Fase 2: fallback genérico con Shadow DOM
    try:
        selector = page.evaluate("""() => {
            """ + _JS_DEEP_QUERY + """
            const RE = /rechazar|reject|decline|aceptar|accept|akzeptieren/i;
            const btns = deepQueryAll(document, 'button, [role="button"]')
                .filter(b => RE.test(b.innerText || ''));
            if (btns.length < 2) return null;

            // Subir desde el primer botón hasta el contenedor con ≥ 2 botones relevantes.
            // Si el botón está en un shadow root, getRootNode().host sube al host element.
            let el = btns[0];
            for (let i = 0; i < 15; i++) {
                const parent = el.parentElement
                    || (el.getRootNode && el.getRootNode() !== document
                        ? el.getRootNode().host : null);
                if (!parent || parent === document.body) break;
                el = parent;
                const contained = deepQueryAll(el, 'button, [role="button"]')
                    .filter(b => RE.test(b.innerText || ''));
                if (contained.length >= 2) break;
            }
            if (!el || el === document.body) return null;

            if (el.id) return '#' + CSS.escape(el.id);
            const cls = (el.className + '').trim().split(/\\s+/)[0];
            if (cls) return el.tagName.toLowerCase() + '.' + CSS.escape(cls);
            return null;
        }""")
        if selector:
            return selector
    except Exception:
        pass

    return None


# ── Extracción de propiedades de botones ──────────────────────────────────────
def extraer_botones(page, banner_selector: str) -> list[dict]:
    """
    Extrae todos los botones e interactivos del banner, incluyendo los que están
    dentro de Shadow DOM (típico en Didomi, OneTrust v2).

    Usa page.evaluate() con el selector como argumento, en vez de
    eval_on_selector(), para poder manejar tanto shadow hosts como elementos
    normales sin cambiar el punto de llamada.
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
        botones = page.evaluate(js, banner_selector)
        return [b for b in botones if b["texto"] and len(b["texto"]) > 1]
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
        "detalle": f"Botón de rechazo visible encontrado: '{visibles[0]['texto']}'.",
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
        if b["opacity"] < 0.5:
            problemas.append(f"'{b['texto']}': opacidad={b['opacity']:.2f}")
        if b["font_size"] and b["font_size"] < 11:
            problemas.append(f"'{b['texto']}': fuente={b['font_size']}px")
        if b["width"] > 0 and b["height"] > 0:
            if b["width"] < 30 or b["height"] < 12:
                problemas.append(
                    f"'{b['texto']}': tamaño={b['width']}×{b['height']}px"
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
                print(f"    · '{b['texto'][:50]}'")
                print(f"       bg={b['bg_color']}  fuente={b['font_size']}px  "
                      f"opac={b['opacity']}  {b['width']}×{b['height']}px")
        print()


# ── Motor principal ───────────────────────────────────────────────────────────
def auditar(url: str, screenshot: bool = False) -> dict:
    """
    Abre Chromium headless con playwright-stealth, navega a la URL,
    espera a que el CMP renderice el banner y ejecuta todas las evaluaciones.

    Se usa Chromium (no camoufox/Firefox) porque:
    - playwright-stealth es suficiente para que los CMPs rendericen el banner
      en la mayoría de sitios españoles de medios.
    - CDP (usado para R1/R5 en playwright_mod.py) también requiere Chromium.
    - Los CMPs tipo Didomi usan Shadow DOM; Chromium + Playwright permiten
      traversal de Shadow DOM de forma más predecible que Firefox/camoufox.
    - camoufox (Firefox) no renderizaba correctamente los banners en ~60% de
      los sitios testados (marca.com, elmundo.es, lavanguardia.com…).
    """
    resultado_interno = {
        "sitio":            url,
        "veredicto":        "UNKNOWN",
        "banner_detectado": False,
        "evaluaciones":     {},
    }
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

            print(f"[*] Navegando a {url} …")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

            # Espera para que el CMP inyecte y renderice el banner.
            # La mayoría de CMPs lo hacen en 2-5 s; 8 s cubre los más lentos.
            page.wait_for_timeout(8000)

            if screenshot:
                try:
                    ss_path = RESULTADO_PATH.parent / "r13_screenshot.png"
                    page.screenshot(path=str(ss_path), full_page=False)
                    print(f"[*] Screenshot guardado en {ss_path}")
                except Exception:
                    pass

            # ── Detección del banner ──────────────────────────────────────────
            banner_selector = detectar_banner(page)

            if not banner_selector:
                browser.close()
                return resultado_interno  # UNKNOWN

            print(f"[*] Banner encontrado: {banner_selector}")

            # ── Extracción de botones (con Shadow DOM traversal) ──────────────
            botones = extraer_botones(page, banner_selector)
            print(f"[*] Botones extraídos del banner: {len(botones)}")

            clasificados: dict[str, list] = {
                "aceptar":    [],
                "rechazar":   [],
                "configurar": [],
                "otro":       [],
            }
            for b in botones:
                cat = clasificar_texto(b["texto"])
                clasificados[cat].append(b)

            evaluaciones = {
                "obstruccion": evaluar_obstruccion(clasificados),
                "asimetria":   evaluar_asimetria(clasificados),
                "ocultacion":  evaluar_ocultacion(clasificados),
            }

            veredicto = veredicto_global(evaluaciones)

            resultado_interno = {
                "sitio":            url,
                "veredicto":        veredicto,
                "banner_detectado": True,
                "banner_selector":  banner_selector,
                "total_botones":    len(botones),
                "clasificados":     clasificados,
                "evaluaciones":     evaluaciones,
                "botones_detalle":  True,
            }
            browser.close()
    except Exception as _e:
        print(f"[!] Browser terminó inesperadamente: {_e}")

    return resultado_interno


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
