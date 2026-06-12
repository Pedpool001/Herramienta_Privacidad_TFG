"""
Módulo Playwright — R4, R13.

R13 (Dark Patterns): analiza el banner de cookies del sitio con Playwright
Python para detectar asimetría cromática, ausencia de botón de rechazo, etc.

R4 (Granularidad): detecta si el CMP del sitio ofrece control granular por
categoría de cookie. Usa r4_granularidad.js (Node.js + Playwright).
"""

import json
import logging
import subprocess
import threading
from pathlib import Path

from ._loader import ejecutar_analisis, TFG_DIR, ANALYSIS_DATA

log = logging.getLogger(__name__)

ANALYSIS_SCRIPTS   = TFG_DIR / "privacy-pioneer-web-crawler/analysis_scripts"
PLAYWRIGHT_TIMEOUT = 120
R4_TIMEOUT         = 90


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock,
             requisitos: set | None = None) -> None:
    """
    Analiza el banner de cookies (R13), captura el árbol de accesibilidad del
    sitio principal (para R1/R5 de PoliGraph) y evalúa la granularidad del CMP (R4).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sel = set(requisitos) if requisitos else {"R4", "R13", "R1", "R5"}

    # ── R13: Dark Patterns (Python + Playwright) ──────────────────────────────
    if "R13" in sel:
        try:
            data_13    = ejecutar_analisis("r13_dark_patterns", url, timeout=PLAYWRIGHT_TIMEOUT)
            veredicto  = data_13.get("veredicto", "ERROR")
            if veredicto == "UNKNOWN":
                veredicto = "NO_EVALUABLE"
            with lock:
                resultados["R13"] = {"veredicto": veredicto, "detalle": data_13}
        except Exception as e:
            log.error("r13_dark_patterns falló: %s", e)
            with lock:
                resultados["R13"] = {"veredicto": "ERROR", "detalle": str(e)}

    # ── Árbol de accesibilidad del sitio principal (para R1 y R5 en PoliGraph) ─
    # Solo se captura si R1 o R5 están seleccionados; el árbol lo usa poligraph.py.
    if {"R1", "R5"} & sel:
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth
            import json as _json
            from ._ax_tree import cdp_to_snapshot as _cdp_to_snapshot

            with sync_playwright() as _pw:
                _browser = _pw.chromium.launch(headless=True)
                _ctx = _browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    locale="es-ES",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                Stealth().apply_stealth_sync(_ctx)
                _page = _ctx.new_page()
                try:
                    _page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                _page.wait_for_timeout(5000)
                _cdp = _ctx.new_cdp_session(_page)
                _ax = _cdp.send("Accessibility.getFullAXTree")
                _cdp.detach()
                _browser.close()

            _snapshot = _cdp_to_snapshot(_ax.get("nodes", []))
            _tree_path = output_dir / "accessibility_tree.json"
            _tree_path.write_text(_json.dumps(_snapshot, ensure_ascii=False), encoding="utf-8")
            log.info("Árbol de accesibilidad del sitio principal guardado en %s", _tree_path)
        except Exception as _e:
            log.warning("No se pudo capturar el árbol de accesibilidad: %s", _e)

    # ── R4: Granularidad (Node.js) ────────────────────────────────────────────
    if "R4" in sel:
        r4_script = ANALYSIS_SCRIPTS / "r4_granularidad.js"
        r4_result  = ANALYSIS_DATA / "r4_resultado.json"
        try:
            proc = subprocess.run(
                ["node", str(r4_script), url, "--no-detalle"],
                capture_output=True,
                text=True,
                timeout=R4_TIMEOUT,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"r4_granularidad.js: {proc.stderr[-400:]}")
            if not r4_result.exists():
                raise FileNotFoundError("r4_resultado.json no generado")
            with open(r4_result, encoding="utf-8") as f:
                data_4 = json.load(f)
            with lock:
                resultados["R4"] = {
                    "veredicto": data_4.get("veredicto", "ERROR"),
                    "detalle":   data_4,
                }
        except Exception as e:
            log.error("r4_granularidad falló: %s", e)
            with lock:
                resultados["R4"] = {"veredicto": "ERROR", "detalle": str(e)}
