import yaml
import json
import os

def cargar_actores_poligraph(ruta_grafo):
    """Extrae los actores (entidades declaradas) del grafo YAML de PoliGraph."""
    actores = set()
    try:
        with open(ruta_grafo, 'r', encoding='utf-8') as f:
            grafo = yaml.safe_load(f)
            for node in grafo.get('nodes', []):
                if node.get('type') == 'ACTOR':
                    # Guardamos en minúsculas para normalizar la búsqueda
                    actores.add(node.get('id').lower())
        return actores
    except FileNotFoundError:
        print(f"[-] Error: No se encontró el archivo de PoliGraph en {ruta_grafo}")
        return set()

def auditar_correspondencia_r16(ruta_grafo, ruta_webxray, ruta_ddg_optimizado):
    print("\n[*] Iniciando Auditoría R16: Correspondencia Legal vs Realidad Técnica...")

    # 1. Cargar las promesas legales (Lo que dice la política)
    actores_declarados = cargar_actores_poligraph(ruta_grafo)
    if not actores_declarados: return

    # 2. Cargar el Diccionario Maestro (DuckDuckGo Tracker Radar)
    try:
        with open(ruta_ddg_optimizado, 'r', encoding='utf-8') as f:
            diccionario_entidades = json.load(f)
        print(f"[+] Diccionario Tracker Radar cargado: {len(diccionario_entidades)} corporaciones matrices.")
    except FileNotFoundError:
        print(f"[-] Error: No se encontró el diccionario DDG en {ruta_ddg_optimizado}")
        return

    # 3. Cargar la Realidad Técnica (Salida de WebXray / Blacklight)
    try:
        with open(ruta_webxray, 'r', encoding='utf-8') as f:
            datos_tecnicos = json.load(f)
    except FileNotFoundError:
        print(f"[-] Error: No se encontró la salida de WebXray en {ruta_webxray}")
        return

    discrepancias = []
    # Asumimos que WebXray devuelve una lista de trackers detectados
    trackers_interceptados = datos_tecnicos.get('trackers', [])
    
    print(f"[+] Cruzando {len(trackers_interceptados)} dominios técnicos con el NLP de PoliGraph...\n")

    # 4. Motor de Resolución de Entidades (El Cruce)
    for tracker in trackers_interceptados:
        dominio = tracker.get('domain', 'desconocido')
        dueño_tecnico = tracker.get('owner', 'desconocido').lower()
        
        encontrado_legalmente = False

        # CASO A: Coincidencia directa (El texto menciona a la empresa matriz directamente)
        if dueño_tecnico in actores_declarados:
            encontrado_legalmente = True
        
        # CASO B: Coincidencia por alias (El texto menciona una marca comercial o filial)
        else:
            alias_oficiales = diccionario_entidades.get(dueño_tecnico, [])
            for alias in alias_oficiales:
                if alias.lower() in actores_declarados:
                    encontrado_legalmente = True
                    break

        # Si el dueño del dominio no se justificó en la política, marcamos la alerta
        if not encontrado_legalmente and dueño_tecnico != 'desconocido':
            discrepancias.append({
                "dominio": dominio,
                "empresa_responsable": dueño_tecnico.title()
            })

    # --- INFORME FINAL DE LA AUDITORÍA ---
    print("="*65)
    print("      VEREDICTO DE CORRESPONDENCIA TÉCNICA (REQUISITO 16)")
    print("="*65)
    
    if not discrepancias:
        print("[✔] RESULTADO: CUMPLE TOTALMENTE (ALTA COHERENCIA)")
        print("Justificación: Todo el tráfico de red enviado a terceros está")
        print("respaldado por la declaración de actores en la política de privacidad.")
    else:
        print("[✘] RESULTADO: NO CUMPLE (INCOHERENCIA Y FUGA DE DATOS)")
        print("Justificación: La ejecución técnica de la web envía datos a entidades")
        print("que NO han sido declaradas (ni sus filiales) en el texto legal.\n")
        print("Detalle de las infracciones:")
        
        # Agrupamos por empresa para un reporte más limpio
        fugas_por_empresa = {}
        for d in discrepancias:
            empresa = d['empresa_responsable']
            fugas_por_empresa.setdefault(empresa, []).append(d['dominio'])
            
        for empresa, dominios in fugas_por_empresa.items():
            print(f"  -> Entidad Oculta: {empresa}")
            print(f"     Dominios involucrados: {', '.join(dominios)}")
    print("="*65)

if __name__ == "__main__":
    # Rutas relativas a los archivos de tu proyecto
    ARCHIVO_POLIGRAPH = "example/graph-original.yml"
    ARCHIVO_WEBXRAY = "ejemplo_webxray.json"
    ARCHIVO_DDG = "diccionario_duckduckgo_optimizado.json"
    
    # Lanzar auditoría
    auditar_correspondencia_r16(ARCHIVO_POLIGRAPH, ARCHIVO_WEBXRAY, ARCHIVO_DDG)
