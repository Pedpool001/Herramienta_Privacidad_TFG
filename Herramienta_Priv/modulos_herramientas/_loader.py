"""Utilidades compartidas para módulos de herramientas."""

import json
import subprocess
import sys
from pathlib import Path

TFG_DIR          = Path("/home/pedro/Escritorio/UNI/CUARTO/tfg")
ANALYSIS_SCRIPTS = TFG_DIR / "privacy-pioneer-web-crawler/analysis_scripts"
ANALYSIS_DATA    = TFG_DIR / "privacy-pioneer-web-crawler/analysis_data"

# Cada script guarda su resultado con el número de requisito, no el nombre completo
# del script. Ej: r7_fingerprinting.py → r7_resultado.json (no r7_fingerprinting_resultado.json).
_RESULTADO_FILES = {
    "r2_r3_cookies_beacons": "r2_resultado.json",
    "r9_minimizacion":       "r9_resultado.json",
    "r6_keylogging":         "r6_resultado.json",
    "r7_fingerprinting":     "r7_resultado.json",
    "r8_storage_terceros":   "r8_resultado.json",
    "r10_persistencia":      "r10_resultado.json",
    "r11_desvinculacion":    "r11_resultado.json",
    "r17_r18_seguridad":     "r17_r18_resultado.json",
    "r1_capas":              "r1_resultado.json",
    "r5_revocabilidad":      "r5_resultado.json",
    "r14_lenguaje":          "r14_resultado.json",
    "r19_dpo":               "r19_resultado.json",
    "r12_software_terceros": "r12_resultado.json",
    "r15_responsables":      "r15_resultado.json",
    "r16_correspondencia":   "r16_resultado.json",
    "r13_dark_patterns":     "r13_resultado.json",
}


def ejecutar_analisis(script: str, *args, timeout: int = 120) -> dict:
    """
    Ejecuta un script de análisis como subproceso y devuelve su resultado JSON.

    Args:
        script:  Nombre del script sin extensión (ej: 'r6_keylogging').
        *args:   Argumentos adicionales (rutas, dominios…).
        timeout: Timeout en segundos.
    """
    ruta = ANALYSIS_SCRIPTS / f"{script}.py"
    cmd  = [sys.executable, str(ruta), "--no-detalle"] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{script}: {proc.stderr[-600:]}")
    nombre      = _RESULTADO_FILES.get(script, f"{script}_resultado.json")
    result_file = ANALYSIS_DATA / nombre
    if not result_file.exists():
        raise FileNotFoundError(f"Resultado no encontrado: {result_file}")
    with open(result_file, encoding="utf-8") as f:
        return json.load(f)
