import re
import sqlite3

def extraer_nombre_cookie(snippet):
    """
    Extrae el nombre de la cookie a partir del texto crudo guardado en la BBDD.
    Ejemplo: '_ga=GA1.2.1234; Path=/' -> Devuelve '_ga'
    """
    if not snippet:
        return None
        
    # Busca todo el texto desde el principio hasta el primer signo '='
    match = re.match(r"^([^=]+)=", snippet)
    if match:
        return match.group(1).strip()
    return None


def evaluar_bloque_pioneer(ruta_bbdd):
    """
    Evalúa todos los requisitos que dependen de la recolección de Privacy-Pioneer.
    Abre la BBDD una sola vez por eficiencia y devuelve los resultados agrupados.
    """
    print("[*] Procesando bloque de datos: Privacy-Pioneer...")
    resultados = {}
    
    try:
        # --- 1. CARGA DE LA "VERDAD ABSOLUTA" (Open Cookie Database) ---
        df_ocd = pd.read_csv(ruta_csv_ocd)  #TODO: Colocar ruta real
        cookies_ilegales = df_ocd[df_ocd['Category'].isin(['Marketing', 'Analytics'])]
        nombres_prohibidos = set(cookies_ilegales['Cookie / Data Key name'].str.lower())

        # --- 2. EXTRACCIÓN DE LA "REALIDAD TÉCNICA" (Privacy-Pioneer) ---
        conn = sqlite3.connect(ruta_bbdd)
        cursor = conn.cursor()
        
        # ATENCIÓN AQUÍ: Pedimos TODO lo que sea sospechoso en la carga inicial
        cursor.execute("""
            SELECT requestUrl, typ, parentCompany 
            FROM entries 
            WHERE consent_phase = 'PRE' 
            AND typ IN ('cookie', 'trackingPixel', 'fingerprinting', 'advertising', 'analytics')
        """)
        capturas_pre = cursor.fetchall()
        conn.close()

        # --- 3. MOTOR DE CLASIFICACIÓN Y REPARTO ---
        infracciones_r2 = []       # Para guardar cookies comerciales
        cookies_perdonadas = 0     # Cookies técnicas (exentas)
        infracciones_r3 = []       # Para guardar peticiones de rastreo/beacons

        for url, tipo, empresa in capturas_pre:
            # Formateamos el nombre de la empresa para que el reporte quede limpio
            empresa_texto = empresa if empresa != "Unknown" else url[:30]+"..."
            
            if tipo == 'cookie':
                # [LÓGICA DEL R2] -> Interrogamos a la Open Cookie Database
                nombre_cookie = extraer_nombre_cookie(url)
                if nombre_cookie and nombre_cookie.lower() in nombres_prohibidos:
                    infracciones_r2.append(f"{nombre_cookie} ({empresa_texto})")
                else:
                    cookies_perdonadas += 1
            else:
                # [LÓGICA DEL R3] -> Si es tracking, advertising o fingerprinting en PRE, 
                # es un Web Beacon / Script espía ilegal por definición.
                infracciones_r3.append(f"[{tipo.upper()}] enviado a: {empresa_texto}")

        # --- 4. GENERACIÓN DE VEREDICTOS ---
        
        # Veredicto R2 (Cookies)
        if len(infracciones_r2) == 0:
            resultados["R2"] = {
                "cumple": True, "estado": "CUMPLE",
                "detalle": f"0 cookies comerciales en carga inicial. (Se ignoraron {cookies_perdonadas} cookies técnicas/exentas)."
            }
        else:
            resultados["R2"] = {
                "cumple": False, "estado": "NO CUMPLE",
                "detalle": f"Se instalaron {len(infracciones_r2)} cookies NO exentas antes del banner (Ej: {infracciones_r2[0]})."
            }

        # Veredicto R3 (Web Beacons y Tracking)
        if len(infracciones_r3) == 0:
            resultados["R3"] = {
                "cumple": True, "estado": "CUMPLE",
                "detalle": "No se detectaron Web Beacons, advertising ni fingerprinting en la carga inicial."
            }
        else:
            resultados["R3"] = {
                "cumple": False, "estado": "NO CUMPLE",
                "detalle": f"Web Beacons detectados: {len(infracciones_r3)} peticiones de rastreo directo en fase PRE (Ej: {infracciones_r3[0]})."
            }

        # ---------------------------------------------------------
        # EVALUACIÓN R9 (Minimización de datos)
        # ---------------------------------------------------------
        # Extraemos fragmentos de datos recolectados para aplicar un filtro
        cursor.execute("""
            SELECT requestUrl, snippet 
            FROM entries 
            WHERE snippet IS NOT NULL AND snippet != '' 
            LIMIT 5
        """)
        datos_extraidos = cursor.fetchall()
        
        # Nota: Aquí más adelante insertaremos las RegEx de minimización que mencionas en tu croquis
        if datos_extraidos:
            resultados["R9"] = {
                "cumple": False, # Pendiente de ajustar la lógica RegEx final
                "estado": "AVISO",
                "detalle": f"Se han capturado {len(datos_extraidos)} snippets de datos. Requiere análisis de minimización."
            }
        else:
            resultados["R9"] = {"cumple": True, "estado": "CUMPLE", "detalle": "No se ha detectado exfiltración de snippets de texto."}

        # ---------------------------------------------------------
        # EVALUACIÓN R15 (Identificación de Responsables Técnicos)
        # ---------------------------------------------------------
        cursor.execute("""
            SELECT DISTINCT parentCompany 
            FROM entries 
            WHERE parentCompany IS NOT NULL AND parentCompany != 'Unknown'
        """)
        responsables = [fila[0] for fila in cursor.fetchall()]
        
        resultados["R15"] = {
            "cumple": len(responsables) > 0,
            "estado": "CUMPLE" if len(responsables) > 0 else "NO CUMPLE",
            "detalle": f"Identificadas {len(responsables)} empresas técnicas (Ej: {', '.join(responsables[:2])}...)" if responsables else "Todos los rastreadores figuran como 'Unknown'."
        }

        # Cerramos la conexión
        conn.close()
        
    except Exception as e:
        print(f"[-] Error al leer la base de datos de Privacy-Pioneer: {e}")
        # Si falla la BBDD, marcamos los requisitos como error
        for req in ["R2", "R3", "R9", "R15"]:
            resultados[req] = {"cumple": False, "estado": "ERROR", "detalle": "Fallo al acceder a SQLite."}

    return resultados


