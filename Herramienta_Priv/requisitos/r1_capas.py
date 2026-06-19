"""
R1 - Información por capas
(RGPD Art. 12 / AEPD)

Verifica que la información sobre cookies se presenta en capas:
  - Capa 1: Banner de cookies presente con información concisa y accesible
            mediante un rol ARIA semántico (dialog/alertdialog).
  - Capa 2: Desde ese banner, el usuario puede acceder a información técnica
            detallada (lista de socios/vendors, política de cookies,
            panel de configuración de consentimiento).

La estructura en capas exige que la Capa 1 sea breve y que desde ella
el usuario pueda acceder a la Capa 2 sin tener que buscarla por el sitio.

Fuente de datos:
  accessibility_tree.json generado por PoliGraph

Veredicto:
  PASSED  — Capa 1 detectada + enlace/botón a Capa 2 técnica
             (lista de socios, política de cookies, o panel de configuración)
  WARNING — Capa 1 detectada pero Capa 2 solo accesible mediante política
             de privacidad general (no específica de cookies)
  FAILED  — No se detecta banner de cookies (Capa 1) o no hay acceso
             a ninguna Capa 2 desde el banner

Uso:
  python3 r1_capas.py                                  # directorio por defecto
  python3 r1_capas.py /ruta/poligraph/output/          # directorio con accessibility_tree.json
  python3 r1_capas.py /ruta/accessibility_tree.json    # fichero directo
  python3 r1_capas.py /ruta/poligraph/output/ --no-detalle
"""

import sys
import json
import re
from pathlib import Path

# ── Rutas por defecto ─────────────────────────────────────────────────────────
_TFG_DIR = Path(__file__).resolve().parents[2]
POLIGRAPH_DEFAULT = _TFG_DIR / "PoliGraph/example/elpais_audit"
RESULTADO_PATH = Path(__file__).resolve().parents[1] / "analysis_data/r1_resultado.json"

ICONO = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}

# ── Roles ARIA del banner ─────────────────────────────────────────────────────
ROLES_BANNER   = {"dialog", "alertdialog"}
# Fallback: algunos CMPs usan role=region o role=complementary
ROLES_FALLBACK = {"region", "complementary", "banner", "group"}

# ── Patrones de detección ─────────────────────────────────────────────────────

# Detecta si el contenido de un nodo es relevante para cookies/privacidad
RE_COOKIES = re.compile(
    r"cook(ie|ies)|privac(y|idad)|consent(imiento)?"
    r"|aceptaci[oó]n|datos\s+personales|personal\s+data"
    r"|tracking|gdpr|rgpd|consentement",
    re.IGNORECASE,
)

# Capa 2 — Nivel A: acceso técnico específico (socios, configuración, más info)
# Estas palabras en un link/button indican claramente acceso a detalle técnico.
RE_CAPA2_A = re.compile(
    r"partner|soci[oa]|vendor|proveedor"            # lista de socios/vendors
    r"|config|preferenc|setting|gestionar|manage|ajust"  # configurar
    r"|learn\s+more|m[aá]s\s+info(rmaci[oó]n)?"    # más información
    r"|saber\s+m[aá]s|know\s+more|ver\s+m[aá]s"
    r"|\d+\s+(partner|vendor|soci)",                # "708 partners"
    re.IGNORECASE,
)

# Capa 2 — Nivel B: política de cookies (válida y específica como Capa 2)
RE_CAPA2_B = re.compile(
    r"cookie\s*polic|pol[ií]tica\s+de\s+cookies|cookies\s+polic",
    re.IGNORECASE,
)

# Capa 2 — Nivel C: política de privacidad general (menos específica)
RE_CAPA2_C = re.compile(
    r"privacy\s*polic|pol[ií]tica\s+de\s+privacidad"
    r"|aviso\s+legal|legal\s+(?:warning|notice)",
    re.IGNORECASE,
)


# ── Recorrido del árbol ───────────────────────────────────────────────────────

def recopilar_texto(nodo: dict) -> str:
    """Concatena todo el texto visible del subárbol de un nodo."""
    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])

    if rol == "text leaf":
        return nombre

    partes = [nombre] if nombre else []
    for hijo in hijos:
        partes.append(recopilar_texto(hijo))
    return " ".join(p for p in partes if p)


def buscar_banner(nodo: dict, max_depth: int = 12, depth: int = 0) -> dict | None:
    """
    DFS en el árbol de accesibilidad para localizar el banner de cookies.

    Estrategia:
    1. Busca nodos con role=dialog/alertdialog cuyo nombre o contenido mencione
       cookies/privacidad/consentimiento.
    2. Fallback: si no se encuentra ningún dialog relevante, busca nodos
       region/complementary/banner con contenido de cookies.
    """
    if depth > max_depth:
        return None

    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])

    if rol in ROLES_BANNER:
        texto = recopilar_texto(nodo)
        if RE_COOKIES.search(nombre) or RE_COOKIES.search(texto):
            return nodo

    for hijo in hijos:
        resultado = buscar_banner(hijo, max_depth, depth + 1)
        if resultado:
            return resultado

    return None


def buscar_banner_fallback(nodo: dict, max_depth: int = 12, depth: int = 0) -> dict | None:
    """
    Segundo pase: busca roles alternativos usados por algunos CMPs que no
    implementan correctamente el rol ARIA dialog.
    """
    if depth > max_depth:
        return None

    rol    = nodo.get("role", "")
    nombre = nodo.get("name", "") or ""
    hijos  = nodo.get("children", [])

    if rol in ROLES_FALLBACK:
        texto = recopilar_texto(nodo)
        if RE_COOKIES.search(nombre) or RE_COOKIES.search(texto):
            return nodo

    for hijo in hijos:
        resultado = buscar_banner_fallback(hijo, max_depth, depth + 1)
        if resultado:
            return resultado

    return None


def extraer_acciones(nodo: dict) -> list[dict]:
    """
    Recopila todos los nodos link y button del subárbol.
    Devuelve lista de {rol, nombre}.
    """
    items  = []
    rol    = nodo.get("role", "")
    nombre = (nodo.get("name", "") or "").strip()
    hijos  = nodo.get("children", [])

    if rol in ("link", "button") and nombre:
        items.append({"rol": rol, "nombre": nombre})

    for hijo in hijos:
        items.extend(extraer_acciones(hijo))

    return items


# ── Análisis del banner ───────────────────────────────────────────────────────

def analizar_banner(banner: dict) -> dict:
    """
    Extrae información de Capa 1 y clasifica los elementos de Capa 2
    encontrados dentro del banner.
    """
    nombre_banner = (banner.get("name", "") or "").strip()
    texto_capa1   = recopilar_texto(banner)
    acciones      = extraer_acciones(banner)

    capa2_a, capa2_b, capa2_c, otros = [], [], [], []

    for item in acciones:
        t = item["nombre"]
        if RE_CAPA2_A.search(t):
            capa2_a.append(item)
        elif RE_CAPA2_B.search(t):
            capa2_b.append(item)
        elif RE_CAPA2_C.search(t):
            capa2_c.append(item)
        else:
            otros.append(item)

    return {
        "nombre_banner":   nombre_banner,
        "texto_capa1":     texto_capa1[:400],
        "total_acciones":  len(acciones),
        "capa2_nivel_a":   capa2_a,
        "capa2_nivel_b":   capa2_b,
        "capa2_nivel_c":   capa2_c,
        "otros_acciones":  otros,
    }


# ── Evaluación ────────────────────────────────────────────────────────────────

def evaluar(hallazgos: dict) -> dict:
    if not hallazgos.get("banner_encontrado"):
        fuente = hallazgos.get("fuente_deteccion", "")
        motivo = (
            "No se detectó ningún banner de cookies en el árbol de accesibilidad. "
            "RGPD Art. 12 exige que la información se presente de forma concisa y "
            "accesible con estructura semántica correcta (role=dialog)."
        )
        return {"estado": "FAILED", "detalle": motivo}

    nombre = hallazgos.get("nombre_banner", "")[:50]
    a      = hallazgos.get("capa2_nivel_a", [])
    b      = hallazgos.get("capa2_nivel_b", [])
    c      = hallazgos.get("capa2_nivel_c", [])

    if a or b:
        partes = []
        if a:
            nombres = [x["nombre"][:40] for x in a[:2]]
            partes.append(f"acceso técnico: {', '.join(nombres)}")
        if b:
            nombres = [x["nombre"][:40] for x in b[:2]]
            partes.append(f"política de cookies: {', '.join(nombres)}")
        return {
            "estado": "PASSED",
            "detalle": (
                f"Capa 1 detectada ('{nombre}'). "
                f"Capa 2 accesible mediante {'; '.join(partes)}. "
                f"Cumple RGPD Art. 12 (estructura en capas)."
            ),
        }

    if c:
        nombres = [x["nombre"][:40] for x in c[:2]]
        return {
            "estado": "WARNING",
            "detalle": (
                f"Capa 1 detectada ('{nombre}'). Capa 2 solo accesible mediante "
                f"política de privacidad general: {', '.join(nombres)}. "
                f"Recomendable añadir enlace específico a política de cookies "
                f"o panel de configuración de consentimiento."
            ),
        }

    total = hallazgos.get("total_acciones", 0)
    return {
        "estado": "FAILED",
        "detalle": (
            f"Capa 1 detectada ('{nombre}') con {total} acciones (aceptar/rechazar), "
            f"pero sin ningún enlace o botón que dé acceso a información detallada "
            f"(Capa 2). RGPD Art. 12 exige acceso a detalles técnicos desde el banner."
        ),
    }


# ── Salida por consola ────────────────────────────────────────────────────────

def imprimir(resultado: dict, detalle: bool) -> None:
    sitio     = resultado.get("sitio", "—")
    veredicto = resultado["veredicto"]
    ev        = resultado["evaluacion"]
    hallazgos = resultado["hallazgos"]

    print(f"\n{'='*64}")
    print(f"  R1 — Información por capas")
    print(f"  Sitio    : {sitio}")
    print(f"  Veredicto: {ICONO[veredicto]} {veredicto}")
    print(f"{'='*64}")
    print(f"\n{ev['detalle']}")

    if not detalle or not hallazgos.get("banner_encontrado"):
        print()
        return

    print(f"\n{'─'*64}")
    print("Análisis de capas:\n")

    nombre_b = hallazgos.get("nombre_banner", "(sin nombre ARIA)")
    texto_b  = hallazgos.get("texto_capa1", "")
    fuente   = hallazgos.get("fuente_deteccion", "dialog")
    print(f"  Capa 1 — Banner detectado via [{fuente}]: '{nombre_b[:60]}'")
    if texto_b:
        extracto = texto_b[:160].replace("\n", " ")
        print(f"  Extracto: {extracto}…")

    total = hallazgos.get("total_acciones", 0)
    print(f"\n  Capa 2 — Elementos interactivos en el banner ({total} totales):\n")

    for item in hallazgos.get("capa2_nivel_a", []):
        print(f"    🔵 [{item['rol']}] {item['nombre'][:60]}  ← acceso técnico / configuración")
    for item in hallazgos.get("capa2_nivel_b", []):
        print(f"    🟢 [{item['rol']}] {item['nombre'][:60]}  ← política de cookies")
    for item in hallazgos.get("capa2_nivel_c", []):
        print(f"    🟡 [{item['rol']}] {item['nombre'][:60]}  ← política de privacidad general")
    for item in hallazgos.get("otros_acciones", []):
        print(f"    ⚪ [{item['rol']}] {item['nombre'][:60]}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def resolver_ruta(ruta_arg: str) -> Path:
    p = Path(ruta_arg)
    if p.is_dir():
        return p / "accessibility_tree.json"
    return p


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

    # Búsqueda principal: dialog/alertdialog semánticamente correcto
    banner = buscar_banner(arbol)
    fuente = "dialog/alertdialog"

    # Fallback: roles alternativos
    if not banner:
        banner = buscar_banner_fallback(arbol)
        fuente = "region/complementary/banner (fallback)"

    if banner:
        print(f"[*] Banner encontrado ({fuente}): '{banner.get('name', '')[:60]}'")
        analisis  = analizar_banner(banner)
        hallazgos = {
            "banner_encontrado": True,
            "fuente_deteccion":  fuente,
            "nombre_banner":     analisis["nombre_banner"],
            "texto_capa1":       analisis["texto_capa1"],
            "total_acciones":    analisis["total_acciones"],
            "capa2_nivel_a":     analisis["capa2_nivel_a"],
            "capa2_nivel_b":     analisis["capa2_nivel_b"],
            "capa2_nivel_c":     analisis["capa2_nivel_c"],
            "otros_acciones":    analisis["otros_acciones"],
        }
    else:
        print("[*] No se detectó banner de cookies en el árbol de accesibilidad")
        hallazgos = {"banner_encontrado": False, "fuente_deteccion": "ninguna"}

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
