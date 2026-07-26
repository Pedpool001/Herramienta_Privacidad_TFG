"""Utilidades compartidas para módulos de herramientas."""

import json
import os
import subprocess
import sys
from pathlib import Path

# Derivado relativamente: _loader.py → modulos_herramientas/ → Herramienta_Priv/ → TFG_DIR
TFG_DIR          = Path(__file__).resolve().parents[2]
ANALYSIS_SCRIPTS = TFG_DIR / "Herramienta_Priv/requisitos"
ANALYSIS_DATA    = TFG_DIR / "Herramienta_Priv/analysis_data"

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


def dir_analisis(output_dir: Path, *, es_raiz: bool = False) -> Path:
    """
    Devuelve el directorio donde el script de análisis debe escribir su JSON.

    Los módulos de herramienta reciben `output_dir` = <sitio>/<herramienta>, por
    lo que la raíz del sitio es su directorio padre. `combinados` recibe ya la
    raíz del sitio y debe llamar con es_raiz=True.
    """
    base = Path(output_dir) if es_raiz else Path(output_dir).parent
    return base / "analysis_data"


def entorno_analisis(salida_dir: Path | None) -> dict:
    """
    Construye el entorno para el subproceso, aislando el directorio de resultados.

    Cada script de análisis escribe su JSON con un nombre fijo (r7_resultado.json,
    r2_resultado.json…). Si varios sitios se auditan en paralelo contra el
    directorio compartido `analysis_data/`, un sitio sobrescribe el resultado de
    otro antes de que este lo lea, y el veredicto se cruza silenciosamente.
    Para evitarlo se pasa ANALYSIS_DATA_DIR con un directorio propio por sitio;
    los scripts lo respetan y, si no está definido, usan el compartido de siempre.
    """
    env = os.environ.copy()
    if salida_dir is not None:
        salida_dir = Path(salida_dir)
        salida_dir.mkdir(parents=True, exist_ok=True)
        env["ANALYSIS_DATA_DIR"] = str(salida_dir)
    return env


def ejecutar_analisis(script: str, *args, timeout: int = 120,
                      salida_dir: Path | None = None) -> dict:
    """
    Ejecuta un script de análisis como subproceso y devuelve su resultado JSON.

    Args:
        script:     Nombre del script sin extensión (ej: 'r6_keylogging').
        *args:      Argumentos adicionales (rutas, dominios…).
        timeout:    Timeout en segundos.
        salida_dir: Directorio donde el script debe escribir su JSON. Si es None
                    se usa el directorio compartido `analysis_data/` (modo
                    secuencial); en modo paralelo debe ser propio de cada sitio.
    """
    ruta = ANALYSIS_SCRIPTS / f"{script}.py"
    cmd  = [sys.executable, str(ruta), "--no-detalle"] + [str(a) for a in args]
    env  = entorno_analisis(salida_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{script}: {proc.stderr[-600:]}")
    nombre      = _RESULTADO_FILES.get(script, f"{script}_resultado.json")
    base        = Path(salida_dir) if salida_dir is not None else ANALYSIS_DATA
    result_file = base / nombre
    if not result_file.exists():
        raise FileNotFoundError(f"Resultado no encontrado: {result_file}")
    with open(result_file, encoding="utf-8") as f:
        return json.load(f)
