# Herramienta de Auditoría de Privacidad Web

Herramienta para la auditoría automatizada de privacidad en sitios web, desarrollada como Trabajo de Fin de Grado. Mide el cumplimien
to de 19 requisitos de privacidad (R1–R19) derivados del RGPD, la LSSI y las directrices de la AEPD.

## Introducción

La herramienta integra seis analizadores de privacidad independientes -Privacy Pioneer, Website Evidence Collector (WEC), Blacklight,
 OpenWPM, webXray y PoliGraph- bajo un único orquestador que los lanza en paralelo, recoge sus resultados y genera un informe de audi
toría en HTML.

### Requisitos que evalúa

| Requisito | Descripción | Herramienta(s) |
|-----------|-------------|----------------|
| R1  | Información por capas | PoliGraph |
| R2  | Bloqueo de cookies no exentas | Privacy Pioneer |
| R3  | Web beacons antes del consentimiento | Privacy Pioneer |
| R4  | Granularidad en la elección | Playwright (Node.js) |
| R5  | Revocabilidad sencilla | PoliGraph |
| R6  | Anti-keylogging | Blacklight |
| R7  | Protección contra fingerprinting | OpenWPM |
| R8  | Storage de terceros | OpenWPM |
| R9  | Minimización de datos | Privacy Pioneer |
| R10 | Limitación del plazo de persistencia | WEC |
| R11 | Desvinculación y aislamiento | OpenWPM |
| R12 | Software de terceros activo en PRE | Privacy Pioneer + webXray |
| R13 | Dark patterns en el banner | Playwright (Python) |
| R14 | Lenguaje claro y sencillo | PoliGraph |
| R15 | Identificación de responsables | Privacy Pioneer + PoliGraph |
| R16 | Correspondencia aviso-ejecución | PoliGraph + webXray |
| R17 | Redirección forzosa HTTPS | WEC |
| R18 | Content Security Policy | WEC |
| R19 | Designación y contacto del DPO | PoliGraph |

### Cómo funciona

Al auditar un sitio, el orquestador (`Herramienta_Priv/main.py`) crea un directorio de trabajo en `output/` y lanza las herramientas
en hilos paralelos. Cada herramienta genera su salida raw, un script de análisis la procesa y produce un JSON con el veredicto (`PASSED`, `WARNING`, `FAILED`, `ERROR` o `NO_EVALUABLE`). Al finalizar todos los hilos se genera un informe HTML autocontenido.

Los requisitos que dependen de más de una herramienta (R12, R15, R16) esperan a que las herramientas implicadas terminen antes de eje
cutar su análisis.

### Modos de uso

**Interfaz web** (recomendado):

```bash
python3 Herramienta_Priv/api.py
# Abre http://localhost:5000 en el navegador
```

**Línea de comandos — sitio único:**

```bash
python3 Herramienta_Priv/main.py https://www.ejemplo.com
python3 Herramienta_Priv/main.py https://www.ejemplo.com --salida informe.html
```

**Línea de comandos — modo batch:**

```bash
python3 Herramienta_Priv/main.py --batch lista_sitios.txt
```

Donde `lista_sitios.txt` contiene una URL por línea (las líneas con `#` se ignoran).

---

## Instalación

La herramienta depende de varias tecnologías externas. A continuación se describen los pasos de instalación para cada una.

### Requisitos del sistema

- Ubuntu 22.04 o superior (recomendado)
- Python 3.10+
- Node.js 20 LTS
- [Conda](https://docs.conda.io/en/latest/miniconda.html) (para OpenWPM y PoliGraph)
- MySQL 8.0 (para Privacy Pioneer)
- Firefox Nightly (para Privacy Pioneer)

### 1. Clonar el repositorio

```bash
git clone --recurse-submodules <url-del-repositorio>
cd tfg
```

### 2. Privacy Pioneer

Privacy Pioneer es un crawler basado en Selenium + Firefox que detecta rastreadores antes y después del consentimiento. Consulta su [README](privacy-pioneer-web-crawler/README.md) para instrucciones detalladas.

**Pasos principales:**

```bash
# Instalar Firefox Nightly y definir su ruta
export FIREFOX_BINARY_PATH=/ruta/a/firefox-nightly/firefox

# Instalar dependencias del crawler
cd privacy-pioneer-web-crawler/selenium-crawler
npm install

# Instalar dependencias de la REST API
cd ../rest-api
npm install
```

**Base de datos MySQL:**
Privacy Pioneer almacena las evidencias en MySQL. Crea la base de datos y el usuario:

```sql
CREATE DATABASE analysis;
CREATE USER 'pioneer'@'localhost' IDENTIFIED BY 'abc';
GRANT ALL PRIVILEGES ON analysis.* TO 'pioneer'@'localhost';
```

Luego inicializa el esquema con el fichero `docker/mysql-init/01_schema.sql`.

### 3. Website Evidence Collector (WEC)

WEC recopila cookies, peticiones de red y cabeceras HTTP del sitio auditado. Consulta su [README](WEC/website-evidence-collector/READ
ME.md).

```bash
cd WEC/website-evidence-collector
npm install
npm run build
cp -r src/assets/. build/src/assets/   # copia assets estáticos al directorio de build
```

### 4. Blacklight

Blacklight detecta keyloggers, grabadores de sesión y listeners de teclado/ratón de terceros. Consulta su [README](BL/blacklight-coll
ector/README.md).

```bash
cd BL/blacklight-collector
npm install
npm run build
```

### 5. OpenWPM

OpenWPM es un framework de medición web que instrumenta Firefox para capturar peticiones HTTP, cookies y operaciones JavaScript. Consulta su [README](openWPM/OpenWPM/README.md).

```bash
cd openWPM/OpenWPM
# Crear el entorno conda (incluye Firefox instrumentado)
conda env create -f environment.yaml
```

### 6. webXray

webXray analiza el tráfico de red de terceros y los clasifica por empresa propietaria. Consulta su [README](webXray/README.md).

```bash
cd webXray
python3 -m venv venv_tfg
venv_tfg/bin/pip install -r requirements.txt
```

### 7. PoliGraph

PoliGraph analiza el texto de la política de privacidad mediante NLP y extrae entidades, relaciones y actores. Consulta su [README](P
oliGraph/README.md).

```bash
cd PoliGraph
# Crear el entorno conda
conda env create -f environment.yml -n poligraph
# Instalar el paquete en modo editable
conda run -n poligraph pip install -e .
# Instalar soporte de traducción automática (para políticas en español)
conda run -n poligraph pip install deep-translator
# Instalar los navegadores necesarios
conda run -n poligraph playwright install firefox chromium
```

### 8. Dependencias Python de la herramienta principal

```bash
pip3 install flask playwright playwright-stealth camoufox ddgs \
             pdfplumber mysql-connector-python beautifulsoup4 \
             lxml requests requests-cache langdetect pyyaml tldextract
playwright install chromium
python3 -m camoufox fetch
```

### 9. Dependencias Node.js del módulo de análisis (R4)

```bash
cd Herramienta_Priv/requisitos
npm install
```
