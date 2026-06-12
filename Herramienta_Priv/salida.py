"""
Generador del informe HTML de auditoría de privacidad.

Uso (desde main.py):
    from salida import generar_informe_unico
    generar_informe_unico(url_sitio, resultados, ruta_salida)
"""

import json
from datetime import datetime
from pathlib import Path

# ── Metadatos de cada requisito ───────────────────────────────────────────────

REQUISITOS = {
    "R1":  ("Información por capas",                "RGPD Art. 12 / AEPD"),
    "R2":  ("Bloqueo de cookies no exentas",        "RGPD Art. 7 / LSSI Art. 22.2"),
    "R3":  ("Ausencia de web beacons en PRE",       "RGPD Art. 5.1.a / Directiva ePrivacy"),
    "R4":  ("Granularidad en la elección",          "RGPD Art. 7 / Considerando 43"),
    "R5":  ("Revocabilidad sencilla",               "RGPD Art. 7.3 / AEPD"),
    "R6":  ("Anti-keylogging",                      "RGPD Art. 5.1.f / LSSI Art. 12"),
    "R7":  ("Protección contra fingerprinting",     "RGPD Art. 5 / AEPD"),
    "R8":  ("Storage de terceros",                  "RGPD Art. 5.1.f / Considerando 49"),
    "R9":  ("Minimización de datos",                "RGPD Art. 5.1.c"),
    "R10": ("Limitación del plazo de persistencia", "RGPD Art. 5.1.e"),
    "R11": ("Desvinculación y aislamiento",         "RGPD Art. 5.1.f / Considerando 26"),
    "R12": ("Software de terceros desactivado",     "RGPD / Privacy by Design"),
    "R13": ("Ausencia de dark patterns",            "RGPD Art. 7 / EDPB 3/2022"),
    "R14": ("Lenguaje claro y sencillo",            "RGPD Art. 12 / AEPD"),
    "R15": ("Identificación de responsables",       "RGPD Art. 13/14"),
    "R16": ("Correspondencia aviso-ejecución",      "RGPD Art. 13/14 / Art. 5.1.a"),
    "R17": ("Redirección HTTPS",                    "RGPD Art. 32"),
    "R18": ("Content Security Policy",              "RGPD Art. 32"),
    "R19": ("Designación y contacto del DPO",       "RGPD Art. 37-39"),
}

COLORES = {
    "PASSED":        ("#1a7f37", "#dafbe1", "✅"),
    "WARNING":       ("#9a6700", "#fff8c5", "⚠️"),
    "FAILED":        ("#cf222e", "#ffebe9", "❌"),
    "ERROR":         ("#57606a", "#f6f8fa", "⚙️"),
    "NO_EVALUABLE":  ("#57606a", "#f6f8fa", "—"),
    "NO_SOLICITADO": ("#8c959f", "#f0f3f6", "○"),
}


def _color(veredicto: str) -> tuple:
    return COLORES.get(veredicto, COLORES["ERROR"])


def _resumen(resultados: dict) -> dict:
    conteo = {k: 0 for k in COLORES if k != "NO_SOLICITADO"}
    for v in resultados.values():
        veredicto = v.get("veredicto", "ERROR")
        if veredicto == "NO_SOLICITADO":
            continue
        conteo[veredicto] = conteo.get(veredicto, 0) + 1
    return conteo


def _esc(text: object) -> str:
    """Escapa caracteres HTML en texto plano."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Extractor de información legible por requisito ────────────────────────────

def _extraer_info(req: str, detalle) -> dict:
    """
    Devuelve un dict con tres campos para renderizar el detalle de forma legible:
      texto  — párrafo de explicación principal (str)
      chips  — lista de (etiqueta, valor) para mostrar como métricas
      items  — lista de strings HTML con hallazgos concretos (primer plano)
    """
    if not detalle:
        return {"texto": "", "chips": [], "items": []}
    if isinstance(detalle, str):
        return {"texto": detalle, "chips": [], "items": []}

    texto = ""
    chips = []
    items = []

    # Extraer el texto de evaluacion.detalle (patrón más común en los scripts)
    # detalle puede ser dict, list (R9) o str — solo los dicts tienen .get()
    ev = (detalle.get("evaluacion") or {}) if isinstance(detalle, dict) else {}
    texto = ev.get("detalle") or ""

    # ── R1: Información por capas ─────────────────────────────────────────────
    if req == "R1":
        h = detalle.get("hallazgos") or {}
        if not h.get("banner_encontrado"):
            return {"texto": texto or "No se detectó banner de cookies.", "chips": [], "items": []}
        a = h.get("capa2_nivel_a", [])
        b = h.get("capa2_nivel_b", [])
        c = h.get("capa2_nivel_c", [])
        items += [f"🔵 Acceso técnico: <em>{_esc(i['nombre'][:60])}</em>" for i in a[:3]]
        items += [f"🟢 Política de cookies: <em>{_esc(i['nombre'][:60])}</em>" for i in b[:3]]
        items += [f"🟡 Política privacidad general: <em>{_esc(i['nombre'][:60])}</em>" for i in c[:2]]

    # ── R2 / R3: Cookies no exentas / web beacons ────────────────────────────
    elif req in ("R2", "R3"):
        violaciones = detalle.get("violaciones", [])
        if violaciones:
            chips.append(("Rastreadores antes del consentimiento", str(len(violaciones))))
            tipos: dict = {}
            for v in violaciones:
                t = v.get("tipo_descripcion") or v.get("tipo") or "—"
                tipos[t] = tipos.get(t, 0) + 1
            items = [f"• {_esc(t)}: {n}" for t, n in sorted(tipos.items(), key=lambda x: -x[1])]
            empresas = list(dict.fromkeys(
                v.get("empresa", "") for v in violaciones if v.get("empresa")
            ))
            if empresas:
                sufijo = f" (+{len(empresas) - 5} más)" if len(empresas) > 5 else ""
                items.append(f"Empresas implicadas: {_esc(', '.join(empresas[:5]))}{sufijo}")

    # ── R4: Granularidad ──────────────────────────────────────────────────────
    elif req == "R4":
        total = detalle.get("totalOpciones") or detalle.get("total_opciones")
        if total is not None:
            chips.append(("Categorías con control independiente", str(total)))
        categorias = detalle.get("categorias") or detalle.get("nombres_categoria") or []
        items = [f"☑️ {_esc(c)}" for c in categorias[:8]]
        if not texto:
            texto = detalle.get("mensaje") or detalle.get("detalle") or ""

    # ── R5: Revocabilidad ─────────────────────────────────────────────────────
    elif req == "R5":
        h = detalle.get("hallazgos") or {}
        rechazar   = h.get("rechazar", [])
        aceptar    = h.get("aceptar", [])
        configurar = h.get("configurar", [])
        items += [f"🔴 Rechazo directo: <em>{_esc(i['nombre'][:50])}</em>" for i in rechazar[:2]]
        items += [f"🟢 Aceptar: <em>{_esc(i['nombre'][:50])}</em>" for i in aceptar[:1]]
        if configurar and not rechazar:
            items += [f"🔵 Configuración (click extra): <em>{_esc(i['nombre'][:50])}</em>" for i in configurar[:2]]

    # ── R6: Anti-keylogging ───────────────────────────────────────────────────
    elif req == "R6":
        fallos = detalle.get("fallos", [])
        avisos = detalle.get("avisos", [])
        if fallos:
            chips.append(("Fallos graves", str(len(fallos))))
        if avisos:
            chips.append(("Avisos", str(len(avisos))))
        items  = [f"❌ {_esc(f.get('tipo', '?'))}: <code>{_esc(f.get('script_url', '')[:70])}</code>" for f in fallos[:4]]
        items += [f"⚠️ {_esc(a.get('tipo', '?'))}: <code>{_esc(a.get('script_url', '')[:70])}</code>" for a in avisos[:3]]
        if not texto and not fallos and not avisos:
            texto = "No se detectó monitorización de teclado o ratón por scripts de terceros."

    # ── R7: Fingerprinting ────────────────────────────────────────────────────
    elif req == "R7":
        scripts = detalle.get("scripts_detectados", [])
        total   = detalle.get("total_scripts_terceros", 0)
        chips.append(("Scripts de terceros analizados", str(total)))
        if scripts:
            chips.append(("Con indicios de fingerprinting", str(len(scripts))))
            items = [
                f"📍 <code>{_esc(s.get('dominio', '?'))}</code> — puntuación {s.get('puntuacion', '?')}"
                for s in scripts[:5]
            ]
            if len(scripts) > 5:
                items.append(f"… y {len(scripts) - 5} más")
        elif not texto:
            texto = "No se detectaron scripts de fingerprinting de terceros."

    # ── R8: Storage de terceros ───────────────────────────────────────────────
    elif req == "R8":
        fallos  = detalle.get("fallos", [])
        resumen = detalle.get("resumen") or {}
        n_escrituras = resumen.get("FAILED", 0)
        n_lecturas   = resumen.get("WARNING", 0)
        if n_escrituras:
            chips.append(("Scripts con escrituras en storage", str(n_escrituras)))
        if n_lecturas:
            chips.append(("Con lecturas en storage", str(n_lecturas)))
        for f in fallos[:5]:
            ops = list(dict.fromkeys(o.get("symbol", "") for o in f.get("operaciones", [])))
            items.append(
                f"💾 <code>{_esc(f.get('dominio', '?'))}</code>: {_esc(', '.join(ops[:3]))}"
            )

    # ── R9: Minimización de datos ─────────────────────────────────────────────
    elif req == "R9":
        lista = detalle if isinstance(detalle, list) else []
        item0 = lista[0] if lista else {}
        texto = item0.get("motivo") or texto
        hallazgos_pre = item0.get("hallazgos_PRE") or {}
        for cat, vals in hallazgos_pre.items():
            n = len(vals) if isinstance(vals, list) else vals
            if n:
                items.append(f"• {_esc(cat)}: {n} ocurrencias detectadas en fase PRE")

    # ── R10: Limitación de persistencia ──────────────────────────────────────
    elif req == "R10":
        violaciones = (
            detalle.get("cookies_problema")
            or detalle.get("violaciones")
            or detalle.get("cookies_exceso")
            or []
        )
        if isinstance(violaciones, list) and violaciones:
            chips.append(("Cookies con plazo excesivo", str(len(violaciones))))
            for v in violaciones[:5]:
                nombre = v.get("nombre") or v.get("cookie") or "?"
                dias   = v.get("expiresDays") or v.get("dias") or "?"
                motivo = v.get("motivo") or v.get("estado") or ""
                items.append(
                    f"🍪 <em>{_esc(nombre)}</em> — {_esc(str(dias))} días"
                    + (f" | {_esc(motivo)}" if motivo else "")
                )
            if len(violaciones) > 5:
                items.append(f"… y {len(violaciones) - 5} más")
        if not texto:
            texto = detalle.get("resumen") or detalle.get("mensaje") or ""

    # ── R11: Desvinculación y aislamiento ─────────────────────────────────────
    elif req == "R11":
        syncs_tt = detalle.get("syncs_tercero_tercero", [])
        syncs_st = detalle.get("syncs_sitio_tercero", [])
        if syncs_tt:
            chips.append(("Sincronizaciones tercero→tercero (graves)", str(len(syncs_tt))))
        if syncs_st:
            chips.append(("Sincronizaciones sitio→tercero", str(len(syncs_st))))
        items = [
            f"🔗 <code>{_esc(s.get('trigger_dominio', '?'))}</code> → "
            f"<code>{_esc(s.get('receptor_dominio', '?'))}</code>"
            for s in syncs_tt[:5]
        ]
        if len(syncs_tt) > 5:
            items.append(f"… y {len(syncs_tt) - 5} más")
        if not items and not texto:
            texto = "No se detectaron eventos de cookie syncing entre terceros."

    # ── R12: Software de terceros en PRE ──────────────────────────────────────
    elif req == "R12":
        for _url, sitio_data in (detalle.items() if isinstance(detalle, dict) else []):
            violaciones  = sitio_data.get("violaciones", [])
            total        = sitio_data.get("total_unicas", 0)
            confirmadas  = sitio_data.get("confirmadas_ambas_herramientas", 0)
            solo_pp      = sitio_data.get("solo_privacy_pioneer", 0)
            if total:
                chips.append(("Rastreadores comerciales activos en PRE", str(total)))
            if confirmadas:
                chips.append(("Confirmados por ambas herramientas", str(confirmadas)))
            if solo_pp:
                chips.append(("Solo detectados por Privacy Pioneer", str(solo_pp)))
            for v in violaciones[:6]:
                flag    = "🔴" if v.get("en_webxray") else "🟡"
                empresa = v.get("empresa_pp") or v.get("owner_wx") or "?"
                items.append(
                    f"{flag} <code>{_esc(v.get('dominio', '?'))}</code> — "
                    f"{_esc(v.get('descripcion', '?'))} ({_esc(empresa)})"
                )
            if len(violaciones) > 6:
                items.append(f"… y {len(violaciones) - 6} más")
            if not texto:
                estado = sitio_data.get("estado", "")
                if estado == "FALLO":
                    texto = (
                        f"Se detectaron {total} rastreadores comerciales activos ANTES del "
                        f"consentimiento. {confirmadas} confirmados por ambas herramientas."
                    )
                elif estado == "PASS":
                    texto = "No se detectaron rastreadores comerciales activos antes del consentimiento."
            break

    # ── R13: Dark patterns ────────────────────────────────────────────────────
    elif req == "R13":
        banner       = detalle.get("banner_detectado", False)
        evaluaciones = detalle.get("evaluaciones") or {}
        if not banner:
            texto = (
                "No se detectó banner de cookies en el sitio. "
                "Puede deberse a bloqueo anti-bot, sitio sin CMP o política de solo cookies propias."
            )
        else:
            for patron, ev_data in evaluaciones.items():
                if isinstance(ev_data, dict):
                    falla      = ev_data.get("falla") or ev_data.get("detectado") or False
                    detalle_ev = ev_data.get("detalle") or ev_data.get("descripcion") or ""
                    items.append(
                        f"{'❌' if falla else '✅'} {_esc(patron)}: {_esc(str(detalle_ev)[:90])}"
                    )

    # ── R14: Lenguaje claro ───────────────────────────────────────────────────
    elif req == "R14":
        politica = detalle.get("politica") or {}
        score    = politica.get("szigriszt_munoz")
        palabras = politica.get("palabras_por_frase")
        if score is not None:
            chips.append(("Índice Szigriszt-Muñoz", f"{score:.1f} / 100"))
        if palabras is not None:
            chips.append(("Palabras por frase (media)", f"{palabras:.1f}"))
        tecnicismos = detalle.get("tecnicismos") or politica.get("tecnicismos") or {}
        if isinstance(tecnicismos, dict) and tecnicismos:
            top = sorted(tecnicismos.items(), key=lambda x: -x[1])[:5]
            items = [f"📖 «{_esc(t)}» ({n}×)" for t, n in top]

    # ── R15: Identificación de responsables ───────────────────────────────────
    elif req == "R15":
        for _sitio, fases in (detalle.items() if isinstance(detalle, dict) else []):
            for fase, fd in (fases.items() if isinstance(fases, dict) else {}.items()):
                total  = fd.get("total", 0)
                no_id  = fd.get("no_identificadas", 0)
                estado = fd.get("estado", "")
                icono  = "✅" if estado == "PASS" else "❌"
                chips.append((f"Cookies fase {fase}", f"{total} total, {no_id} sin propietario"))
                no_id_cookies = [c for c in fd.get("cookies", []) if c.get("propietario") == "No identificado"]
                for c in no_id_cookies[:3]:
                    items.append(
                        f"❓ <em>{_esc(c.get('nombre', '?'))}</em> — {_esc(c.get('dominio', '?'))}"
                    )
            break
        if not texto:
            texto = "Análisis de identificación de propietarios de cookies por fase de consentimiento."

    # ── R16: Correspondencia aviso-ejecución ──────────────────────────────────
    elif req == "R16":
        h          = detalle.get("hallazgos") or {}
        no_decl    = h.get("no_declarados_identificados", [])
        decl       = h.get("declarados", [])
        sin_owner  = h.get("sin_propietario", [])
        if no_decl:
            chips.append(("Terceros no declarados en la política", str(len(no_decl))))
        if decl:
            chips.append(("Declarados correctamente", str(len(decl))))
        if sin_owner:
            chips.append(("Sin propietario identificable", str(len(sin_owner))))
        for d in no_decl[:6]:
            entidades = d.get("nombres_entidad") or []
            nombre_e  = entidades[0] if entidades else "propietario desconocido"
            items.append(
                f"❌ <code>{_esc(d.get('dominio', '?'))}</code> — {_esc(nombre_e)}"
            )
        if len(no_decl) > 6:
            items.append(f"… y {len(no_decl) - 6} más")

    # ── R17: Redirección HTTPS ────────────────────────────────────────────────
    elif req == "R17":
        redir    = detalle.get("redirect_http") or {}
        hsts     = detalle.get("hsts") or {}
        r_estado = redir.get("estado", "")
        r_motivo = redir.get("motivo") or ""
        h_estado = hsts.get("estado", "")
        h_motivo = hsts.get("motivo") or ""
        sc       = redir.get("status_code")
        if sc:
            chips.append(("Código HTTP recibido", str(sc)))
        max_age = hsts.get("max_age")
        if max_age:
            dias = int(max_age) // 86400
            chips.append(("HSTS max-age", f"{dias} días"))
        if r_motivo:
            items.append(f"{'✅' if r_estado == 'PASSED' else '❌'} Redirección: {_esc(r_motivo)}")
        if h_motivo:
            items.append(f"{'✅' if h_estado == 'PASSED' else '⚠️'} HSTS: {_esc(h_motivo)}")
        if not texto:
            texto = r_motivo

    # ── R18: Content Security Policy ─────────────────────────────────────────
    elif req == "R18":
        modo        = detalle.get("modo")
        directivas  = detalle.get("directivas_problematicas") or detalle.get("advertencias") or []
        if modo:
            chips.append(("Modo CSP", str(modo)))
        for d in directivas[:5]:
            if isinstance(d, dict):
                items.append(
                    f"⚠️ <code>{_esc(d.get('directiva', ''))}</code>: {_esc(d.get('motivo', ''))}"
                )
            elif isinstance(d, str):
                items.append(f"⚠️ {_esc(d)}")
        if not texto and not directivas:
            texto = "No se detectaron problemas en la Content Security Policy."

    # ── R19: Designación del DPO ──────────────────────────────────────────────
    elif req == "R19":
        h       = detalle.get("hallazgos") or {}
        mencion = h.get("mencion_dpo", False)
        emails  = h.get("emails_dpo") or h.get("emails_otros") or []
        postal  = h.get("direccion_postal", False)
        nombre  = h.get("nombre_dpo") or ""
        items.append("✅ DPO mencionado en la política" if mencion else "❌ No se encontró mención al DPO")
        if nombre:
            items.append(f"👤 Nombre: {_esc(nombre)}")
        if emails:
            items.append(f"📧 Email de contacto: {_esc(emails[0])}")
        if postal:
            items.append("📬 Dirección postal indicada")
        elif mencion and not emails:
            items.append("⚠️ Sin datos de contacto específicos publicados")

    return {"texto": texto, "chips": chips, "items": items}


# ── Renderizado HTML del detalle ──────────────────────────────────────────────

def _detalle_html(req: str, detalle) -> str:
    info  = _extraer_info(req, detalle)
    texto = info["texto"]
    chips = info["chips"]
    items = info["items"]

    if not texto and not chips and not items and not detalle:
        return "<span class='sin-datos'>Sin datos disponibles</span>"

    html = '<div class="det-wrap">'

    if texto:
        html += f'<p class="det-texto">{_esc(texto)}</p>'

    if chips:
        html += '<div class="det-chips">'
        for label, valor in chips:
            html += (
                f'<span class="det-chip">'
                f'<span class="chip-label">{_esc(label)}</span>'
                f'<span class="chip-valor">{_esc(valor)}</span>'
                f'</span>'
            )
        html += '</div>'

    if items:
        html += '<ul class="det-lista">'
        for item in items:
            html += f'<li class="det-item">{item}</li>'
        html += '</ul>'

    if detalle:
        json_texto = json.dumps(detalle, ensure_ascii=False, indent=2)
        json_esc   = json_texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += f'''<details class="det-json-details">
            <summary>Ver datos técnicos (JSON)</summary>
            <pre class="detalle-json">{json_esc}</pre>
        </details>'''

    html += '</div>'
    return html


def _fila(req: str, datos: dict) -> str:
    nombre, normativa = REQUISITOS.get(req, (req, ""))
    veredicto         = datos.get("veredicto", "ERROR")
    color_texto, color_fondo, icono = _color(veredicto)
    detalle           = datos.get("detalle", "")

    return f"""
    <tr>
        <td class="req-id">{req}</td>
        <td>
            <span class="req-nombre">{nombre}</span>
            <span class="req-norm">{normativa}</span>
        </td>
        <td>
            <span class="badge"
                  style="color:{color_texto};background:{color_fondo};border-color:{color_texto}">
                {icono}&nbsp;{veredicto}
            </span>
        </td>
        <td>{_detalle_html(req, detalle)}</td>
    </tr>"""


def generar_informe_unico(url_sitio: str, resultados: dict, ruta_salida: str) -> None:
    """
    Genera un informe HTML completo con los resultados de la auditoría.

    Args:
        url_sitio:   URL del sitio auditado.
        resultados:  Dict {R1: {veredicto, detalle}, ...}
        ruta_salida: Ruta del fichero HTML a generar.
    """
    fecha   = datetime.now().strftime("%d/%m/%Y %H:%M")
    resumen = _resumen(resultados)

    # Ordenar requisitos numéricamente (R1, R2, ..., R19)
    filas = "".join(
        _fila(req, resultados.get(req, {"veredicto": "ERROR"}))
        for req in sorted(REQUISITOS, key=lambda r: int(r[1:]))
    )
    total = len(REQUISITOS)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auditoría de Privacidad — {_esc(url_sitio)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 14px;
    color: #1f2328;
    background: #f6f8fa;
    padding: 32px 24px;
  }}

  /* ── Cabecera ── */
  .header {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    padding: 24px 28px;
    margin-bottom: 24px;
  }}
  .header h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 6px; }}
  .header .meta {{ color: #57606a; font-size: 13px; }}
  .header .url {{
    font-family: monospace;
    font-size: 14px;
    color: #0969da;
    word-break: break-all;
    margin-top: 8px;
  }}

  /* ── Tarjetas de resumen ── */
  .resumen {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
  }}
  .card {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    padding: 16px 20px;
    min-width: 120px;
    text-align: center;
  }}
  .card .num   {{ font-size: 28px; font-weight: 700; line-height: 1.1; }}
  .card .label {{
    font-size: 12px;
    color: #57606a;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .card.passed  .num {{ color: #1a7f37; }}
  .card.warning .num {{ color: #9a6700; }}
  .card.failed  .num {{ color: #cf222e; }}
  .card.other   .num {{ color: #57606a; }}

  /* ── Tabla de resultados ── */
  .tabla-wrap {{
    background: #fff;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    overflow: hidden;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead tr {{ background: #f6f8fa; }}
  th {{
    padding: 10px 16px;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #57606a;
    border-bottom: 1px solid #d0d7de;
  }}
  td {{
    padding: 14px 16px;
    border-bottom: 1px solid #eaeef2;
    vertical-align: top;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f6f8fa; }}

  .req-id {{
    font-family: monospace;
    font-weight: 700;
    font-size: 13px;
    color: #57606a;
    white-space: nowrap;
    width: 48px;
  }}
  .req-nombre {{ display: block; font-weight: 500; }}
  .req-norm   {{ display: block; font-size: 12px; color: #57606a; margin-top: 2px; }}

  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }}

  /* ── Detalle legible ── */
  .det-wrap {{ display: flex; flex-direction: column; gap: 8px; }}

  .det-texto {{
    font-size: 13px;
    line-height: 1.5;
    color: #1f2328;
  }}

  .det-chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .det-chip {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 12px;
  }}
  .chip-label {{
    color: #57606a;
    white-space: nowrap;
  }}
  .chip-valor {{
    font-weight: 600;
    color: #1f2328;
    white-space: nowrap;
  }}

  .det-lista {{
    list-style: none;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .det-item {{
    font-size: 12px;
    line-height: 1.5;
    color: #1f2328;
    padding: 3px 0;
    border-bottom: 1px solid #f0f0f0;
  }}
  .det-item:last-child {{ border-bottom: none; }}
  .det-item code {{
    background: #f0f4f8;
    border: 1px solid #d0d7de;
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 11px;
    font-family: monospace;
  }}
  .det-item em {{ font-style: normal; font-weight: 500; }}

  /* ── Detalles técnicos colapsables ── */
  .det-json-details {{ margin-top: 4px; }}
  .det-json-details summary {{
    cursor: pointer;
    font-size: 11px;
    color: #57606a;
    user-select: none;
    padding: 2px 0;
  }}
  .det-json-details summary:hover {{ color: #0969da; text-decoration: underline; }}
  .detalle-json {{
    margin-top: 6px;
    padding: 10px;
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    font-size: 11px;
    font-family: monospace;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 280px;
    overflow-y: auto;
    color: #57606a;
  }}

  .sin-datos {{ font-size: 12px; color: #57606a; font-style: italic; }}
</style>
</head>
<body>

<div class="header">
  <h1>Auditoría de Privacidad Web</h1>
  <p class="meta">Generado el {fecha}</p>
  <p class="url">{_esc(url_sitio)}</p>
</div>

<div class="resumen">
  <div class="card">
    <div class="num">{total}</div>
    <div class="label">Total</div>
  </div>
  <div class="card passed">
    <div class="num">{resumen.get("PASSED", 0)}</div>
    <div class="label">✅ Cumple</div>
  </div>
  <div class="card warning">
    <div class="num">{resumen.get("WARNING", 0)}</div>
    <div class="label">⚠️ Aviso</div>
  </div>
  <div class="card failed">
    <div class="num">{resumen.get("FAILED", 0)}</div>
    <div class="label">❌ Falla</div>
  </div>
  <div class="card other">
    <div class="num">{resumen.get("ERROR", 0) + resumen.get("NO_EVALUABLE", 0)}</div>
    <div class="label">— Sin dato</div>
  </div>
</div>

<div class="tabla-wrap">
  <table>
    <thead>
      <tr>
        <th>Req.</th>
        <th>Descripción</th>
        <th>Veredicto</th>
        <th>Resultado</th>
      </tr>
    </thead>
    <tbody>
      {filas}
    </tbody>
  </table>
</div>

</body>
</html>"""

    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(html, encoding="utf-8")
    print(f"[✓] Informe guardado en {ruta}")
