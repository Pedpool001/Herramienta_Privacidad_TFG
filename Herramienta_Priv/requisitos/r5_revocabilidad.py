"""
R5 - Revocabilidad sencilla
(RGPD Art. 7.3 / AEPD)

Verifica que el usuario puede retirar su consentimiento de forma tan sencilla
como lo dio. RGPD Art. 7.3 exige explícitamente que "será tan fácil retirar
el consentimiento como darlo."

El test de simetría:
  Si el banner muestra "Aceptar todo" en primera capa, debe mostrar también
  "Rechazar todo" (u opción equivalente) en esa misma primera capa.
  Si rechazar requiere pasar por un panel de configuración, hay una asimetría
  que vulnera el principio de facilidad equivalente.

Criterio de evaluación:
  PASSED  — Botón de rechazo directo en el banner (mismo nivel que "Aceptar")
  WARNING — Solo existe acceso al panel de configuración desde el banner
             (rechazar requiere un click extra respecto a aceptar)
  FAILED  — No se detecta ningún mecanismo de revocación en el banner

Fuente de datos:
  accessibility_tree.json generado por PoliGraph

Nota sobre el script original (r5.py):
  El script anterior medía la "profundidad en el árbol JSON" como proxy del
  número de clicks necesarios. Esta métrica es incorrecta: un botón "Rechazar"
  visible en el banner puede estar en el nivel 15 del JSON pero ser lo primero
  que ve el usuario. La profundidad del árbol DOM no tiene relación con la
  accesibilidad visual. Se sustituyó por el test de simetría descrito arriba.

Uso:
  python3 r5_revocabilidad.py                                # directorio por defecto
  python3 r5_revocabilidad.py /ruta/poligraph/output/        # directorio con accessibility_tree.json
  python3 r5_revocabilidad.py /ruta/accessibility_tree.json  # fichero directo
  python3 r5_revocabilidad.py /ruta/poligraph/output/ --no-detalle
"""

import sys
import json
import re
from pathlib import Path

# ── Rutas por defecto ─────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
POLIGRAPH_DEFAULT = _TFG_DIR / "PoliGraph/example/elpais_audit"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r5_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}

# ── Patrones de detección ─────────────────────────────────────────────────────

# Detecta el banner de cookies (mismo criterio que R1 y R14)
RE_COOKIES = re.compile(
    r"cook(ie|ies)|privac(y|idad)|consent(imiento)?"
    r"|aceptaci[oó]n|datos\s+personales|personal\s+data|gdpr|rgpd",
    re.IGNORECASE,
)

# Botones de ACEPTAR — para detectar si hay opción de aceptar en el banner
RE_ACEPTAR = re.compile(
    r"aceptar(\s+todo[s]?)?|accept(\s+all)?|accepter(\s+tout)?"
    r"|agree(\s+and\s+close)?|akzeptieren|autoriser",
    re.IGNORECASE,
)

# Botones de RECHAZO DIRECTO — presencia en el banner = PASSED
RE_RECHAZAR = re.compile(
    r"rechazar(\s+todo[s]?)?|reject(\s+all)?"
    r"|denegar|no\s+acepto|decline(\s+all)?"
    r"|disagree(\s+and\s+close)?|refuse(\s+all)?"
    r"|continuar\s+sin\s+aceptar|continue\s+without\s+accepting"
    r"|solo\s+(las\s+)?necesarias?|necessary\s+only|only\s+necessary"
    r"|subscribe\s+and\s+decline",          # patrón de elpais
    re.IGNORECASE,
)

# Botones de CONFIGURACIÓN — presencia en el banner = WARNING (un click extra)
RE_CONFIGURAR = re.compile(
    r"configurar|preferencias|opciones|personalizar"
    r"|manage|settings|customize|choices"
    r"|learn\s+more|m[aá]s\s+info(rmaci[oó]n)?"
    r"|cookie\s*(settings|preferences|policy)",
    re.IGNORECASE,
)


# ── Recorrido del árbol ───────────────────────────────────────────────────────

def recopilar_texto(nodo: dict) -> str:
    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])
    if rol == "text leaf":
        return nombre
    partes = [nombre] if nombre else []
    for h in hijos:
        partes.append(recopilar_texto(h))
    return " ".join(p for p in partes if p)


def buscar_banner(nodo: dict, depth: int = 0, max_depth: int = 12) -> dict | None:
    """Mismo DFS que R1: busca el primer dialog/alertdialog con contenido de cookies."""
    if depth > max_depth:
        return None
    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])

    if rol in ("dialog", "alertdialog"):
        if RE_COOKIES.search(nombre) or RE_COOKIES.search(recopilar_texto(nodo)):
            return nodo

    for hijo in hijos:
        resultado = buscar_banner(hijo, depth + 1, max_depth)
        if resultado:
            return resultado
    return None


def buscar_banner_fallback(nodo: dict, depth: int = 0, max_depth: int = 12) -> dict | None:
    """Fallback para CMPs que usan region/complementary/banner."""
    if depth > max_depth:
        return None
    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])

    if rol in ("region", "complementary", "banner", "group"):
        if RE_COOKIES.search(nombre) or RE_COOKIES.search(recopilar_texto(nodo)):
            return nodo

    for hijo in hijos:
        resultado = buscar_banner_fallback(hijo, depth + 1, max_depth)
        if resultado:
            return resultado
    return None


def extraer_botones_y_enlaces(nodo: dict) -> list[dict]:
    """Recoge todos los nodos link y button del subárbol con su texto."""
    items  = []
    rol    = nodo.get("role", "")
    nombre = (nodo.get("name", "") or "").strip()
    hijos  = nodo.get("children", [])

    if rol in ("link", "button") and nombre:
        items.append({"rol": rol, "nombre": nombre})

    for hijo in hijos:
        items.extend(extraer_botones_y_enlaces(hijo))
    return items


# ── Análisis del banner ───────────────────────────────────────────────────────

def analizar_banner(banner: dict) -> dict:
    """
    Clasifica los elementos interactivos del banner en:
      - aceptar:     botones de aceptación
      - rechazar:    botones de rechazo directo (equivalen a "Aceptar" en simetría)
      - configurar:  botones de acceso al panel de configuración
      - otros:       el resto (log in, suscribirse, cerrar...)
    """
    acciones  = extraer_botones_y_enlaces(banner)
    aceptar   = []
    rechazar  = []
    configurar = []
    otros     = []

    for item in acciones:
        t = item["nombre"]
        if RE_RECHAZAR.search(t):
            rechazar.append(item)
        elif RE_ACEPTAR.search(t):
            aceptar.append(item)
        elif RE_CONFIGURAR.search(t):
            configurar.append(item)
        else:
            otros.append(item)

    return {
        "nombre_banner": (banner.get("name", "") or "").strip(),
        "aceptar":       aceptar,
        "rechazar":      rechazar,
        "configurar":    configurar,
        "otros":         otros,
        "total":         len(acciones),
    }


# ── Evaluación ────────────────────────────────────────────────────────────────

def evaluar(hallazgos: dict) -> dict:
    if not hallazgos.get("banner_encontrado"):
        return {
            "estado": "FAILED",
            "detalle": (
                "No se detectó ningún banner de cookies en el árbol de accesibilidad. "
                "Sin banner no es posible verificar si existe mecanismo de revocación "
                "accesible. RGPD Art. 7.3 exige que retirar el consentimiento sea "
                "tan fácil como darlo."
            ),
        }

    nombre = hallazgos.get("nombre_banner", "")[:50]
    rechazar   = hallazgos.get("rechazar", [])
    configurar = hallazgos.get("configurar", [])
    aceptar    = hallazgos.get("aceptar", [])

    if rechazar:
        nombres_r = [x["nombre"][:40] for x in rechazar[:2]]
        nombres_a = [x["nombre"][:40] for x in aceptar[:1]]
        simetria  = f'"{", ".join(nombres_a)}"' if nombres_a else "botón de aceptación"
        return {
            "estado": "PASSED",
            "detalle": (
                f"Banner '{nombre}': botón de rechazo directo detectado "
                f"({', '.join(repr(n) for n in nombres_r)}) al mismo nivel que "
                f"{simetria}. Revocación tan sencilla como aceptación. "
                f"Cumple RGPD Art. 7.3."
            ),
        }

    if configurar:
        nombres_c = [x["nombre"][:40] for x in configurar[:2]]
        return {
            "estado": "WARNING",
            "detalle": (
                f"Banner '{nombre}': no hay botón de rechazo directo en primera capa. "
                f"Solo existe acceso al panel de configuración "
                f"({', '.join(repr(n) for n in nombres_c)}), que requiere un click "
                f"adicional respecto a 'Aceptar'. Asimetría leve. RGPD Art. 7.3 "
                f"exige equivalencia de facilidad."
            ),
        }

    return {
        "estado": "FAILED",
        "detalle": (
            f"Banner '{nombre}' detectado pero sin botón de rechazo directo ni "
            f"acceso al panel de configuración. El usuario no puede retirar el "
            f"consentimiento desde el banner. Incumple RGPD Art. 7.3."
        ),
    }


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "—")
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluacion"]
    hallazgos = resultado["hallazgos"]

    print(f"\n{'='*64}")
    print(f"  R5 — Revocabilidad sencilla")
    print(f"  Sitio    : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{ev['detalle']}")

    if not detalle or not hallazgos.get("banner_encontrado"):
        print()
        return

    print(f"\n{'─'*64}")
    print("Elementos interactivos en el banner:\n")

    for item in hallazgos.get("rechazar", []):
        print(f"    🔴 [{item['rol']}] {item['nombre'][:60]}  ← rechazo directo")
    for item in hallazgos.get("aceptar", []):
        print(f"    🟢 [{item['rol']}] {item['nombre'][:60]}  ← aceptación")
    for item in hallazgos.get("configurar", []):
        print(f"    🔵 [{item['rol']}] {item['nombre'][:60]}  ← panel de configuración")
    for item in hallazgos.get("otros", []):
        print(f"    ⚪ [{item['rol']}] {item['nombre'][:60]}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def resolver_ruta(ruta_arg: str) -> Path:
    p = Path(ruta_arg)
    return p / "accessibility_tree.json" if p.is_dir() else p


def main():
    args    = sys.argv[1:]
    detalle = "--no-detalle" not in args
    args    = [a for a in args if a != "--no-detalle"]

    ruta_arg  = args[0] if args else str(POLIGRAPH_DEFAULT)
    tree_path = resolver_ruta(ruta_arg)

    if not tree_path.exists():
        print(f"[ERROR] No se encontró: {tree_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Leyendo árbol de accesibilidad: {tree_path}")
    with open(tree_path, encoding="utf-8") as f:
        arbol = json.load(f)

    sitio = arbol.get("name", "") or tree_path.parent.name

    # Buscar banner (misma lógica que R1)
    banner = buscar_banner(arbol)
    fuente = "dialog/alertdialog"
    if not banner:
        banner = buscar_banner_fallback(arbol)
        fuente = "region/complementary/banner (fallback)"

    if banner:
        print(f"[*] Banner encontrado ({fuente}): '{banner.get('name','')[:60]}'")
        analisis  = analizar_banner(banner)
        hallazgos = {
            "banner_encontrado": True,
            "fuente_deteccion":  fuente,
            "nombre_banner":     analisis["nombre_banner"],
            "aceptar":           analisis["aceptar"],
            "rechazar":          analisis["rechazar"],
            "configurar":        analisis["configurar"],
            "otros":             analisis["otros"],
            "total_acciones":    analisis["total"],
        }
    else:
        print("[*] No se detectó banner de cookies")
        hallazgos = {"banner_encontrado": False}

    evaluacion = evaluar(hallazgos)
    veredicto  = evaluacion["estado"]

    resultado = {
        "sitio":      sitio,
        "veredicto":  veredicto,
        "evaluacion": evaluacion,
        "hallazgos":  hallazgos,
    }

    RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"[✓] Resultado guardado en {RESULTADO_PATH}")

    imprimir(resultado, detalle)


if __name__ == "__main__":
    main()
