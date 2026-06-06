import json
import re

def auditar_sencillez_r5(ruta_tree):
    print("[*] Iniciando auditoria topologica de eficiencia del R5...")

    try:
        with open(ruta_tree, 'r', encoding='utf-8') as f:
            tree = json.load(f)
    except FileNotFoundError:
        print("[-] Error: No se ha encontrado el archivo 'accessibility_tree.json'.")
        return

    mecanismos_encontrados = []

    def es_mecanismo_revocacion(nodo):
        role = nodo.get('role', '')
        if role not in ['link', 'button']:
            return False

        name = nodo.get('name', '').lower()
        value = nodo.get('value', '').lower()
        description = nodo.get('description', '').lower()

        contexto_texto = f"{name} {description}"

        # 1. CAPA TECNICA (Soporta JS dinámico y rutas HTML estáticas)
        patrones_tecnicos = [
            r"javascript:.*(didomi|consent|preferences)",
            r"cmp-container",
            r"onetrust",
            r"/(privacy-settings|configuracion-cookies|preferencias-privacidad)",
            r"/(manage-cookies|gestion-consentimiento)",
            r"\?manage_cookies=true",
            r"/(cookies/configurar)"
        ]
        if any(re.search(patron, value) for patron in patrones_tecnicos):
            return True

        # 2. CAPA LINGUISTICA DIRECTA (Español e Inglés)
        patrones_texto_directo = [
            r"(configurar|gestionar|ajustes|preferencias|panel)\s*(?:de|las)?\s*(cookies|privacidad|datos)",
            r"(cookie\s*settings|privacy\s*panel|manage\s*cookies|configure\s*(?:your\s*)?consents|preferences)",
            r"(opt-out|optout)",
            r"(retirar|revocar|cancelar)\s*(?:el)?\s*(consentimiento|permiso)"
        ]
        if any(re.search(patron, contexto_texto) for patron in patrones_texto_directo):
            return True

        # 3. CAPA DE EXCLUSION (Filtro anti falsos positivos)
        if re.search(r"(política|policy|aviso|legal|leer más)", contexto_texto):
            # Exigimos que también tenga verbo de acción si menciona "política"
            if not re.search(r"(configurar|gestionar|settings|manage|configure)", contexto_texto):
                return False

        return False

    def recorrer_arbol(nodo, profundidad_actual=0):
        # Si el nodo actual es un mecanismo de revocación, lo guardamos
        if es_mecanismo_revocacion(nodo):
            mecanismos_encontrados.append({
                "role": nodo.get('role'),
                "name": nodo.get('name'),
                "value": nodo.get('value'),
                "profundidad_clics": profundidad_actual
            })

        # Continuamos buscando en los hijos, sumando 1 nivel de profundidad
        for child in nodo.get('children', []):
            recorrer_arbol(child, profundidad_actual + 1)

    # Arrancamos la exploración desde la raíz
    recorrer_arbol(tree, profundidad_actual=0)

    # --- PRESENTACION DE RESULTADOS ---
    print("\n" + "="*60)
    print("           INFORME DE AUDITORIA TOPOLOGICA (R5)")
    print("="*60)

    if not mecanismos_encontrados:
        print("RESULTADO: NO CUMPLE (0 puntos)")
        print("Detalle: No se ha detectado ningun panel interactivo o enlace de revocacion directa.")
        print("="*60)
        return

    print(f"[+] Se han detectado {len(mecanismos_encontrados)} puntos de gestion de revocacion.")

    # Buscamos el mejor candidato (el que requiera menos clics/menor profundidad)
    mejor_mecanismo = min(mecanismos_encontrados, key=lambda x: x['profundidad_clics'])

    print(f"-> Mecanismo principal detectado : '{mejor_mecanismo['name']}'")
    print(f"-> Accion tecnica (Valor)        : {mejor_mecanismo['value'] if mejor_mecanismo['value'] else 'Enlace/Evento HTML estandar'}")
    print(f"-> Distancia (Profundidad JSON)  : Nivel {mejor_mecanismo['profundidad_clics']}")
    print("-" * 60)

    # Evaluador de eficiencia de cara a la normativa RGPD
    if mejor_mecanismo['profundidad_clics'] <= 4:
        print("VEREDICTO: CUMPLE - REVOCABILIDAD EXTREMADAMENTE SENCILLA")
        print("Justificacion: El acceso a la revocacion esta en la capa superficial.")
    elif mejor_mecanismo['profundidad_clics'] <= 7:
        print("VEREDICTO: CUMPLE PARCIALMENTE - COMPLEJIDAD MODERADA")
        print("Justificacion: El usuario debe explorar elementos anidados (ej. Modales o Submenus) para ejercer el derecho.")
    else:
        print("VEREDICTO: NO CUMPLE - INCUMPLIMIENTO POR COMPLEJIDAD")
        print("Justificacion: El derecho esta excesivamente oculto en el laberinto del DOM, violando la facilidad exigida.")
    print("="*60)

if __name__ == "__main__":
    # Este es el main que lanzará la prueba directamente sobre tu archivo
    archivo_json = '/home/pedro/Escritorio/UNI/CUARTO/PoliGraph/example/accessibility_tree.json'
    auditar_sencillez_r5(archivo_json)
