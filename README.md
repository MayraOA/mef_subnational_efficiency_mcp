# MEF Subnational Efficiency MCP 🇵🇪

**Sistema de Auditoría de Gasto Público Subnacional — Perú 2025 + Archivo Histórico 1964**

Pipeline multi-agente local construido con Claude Code CLI, MCP y PaddleOCR para auditar la ejecución presupuestal de gobiernos regionales y locales del Perú.

---

## Arquitectura General

```
Claude Code CLI
      │
      ├── executor_skill.json  →  Orquesta extracción, transformación y composición del dashboard
      └── evaluator_skill.json →  Audita, optimiza y pule el output del Executor
              │
              ▼
      src/mcp_server.py        →  Servidor MCP local (7 herramientas CKAN + OCR)
              │
      ┌───────┴────────┐
      │                │
src/data_pipeline.py   src/ocr_engine.py
(Track 2025)           (Track 1964 — PaddleOCR)
      │                │
      └───────┬────────┘
              ▼
       data/processed/         →  Parquets micro-footprint + KPIs JSON
              │
              ▼
           app.py              →  Dashboard Streamlit 4 tabs
```

---

## Quick Start

### 1. Instalación

```bash
git clone <repo-url>
cd mef_subnational_efficiency_mcp
pip install -r requirements.txt
```

> **Poppler** es una dependencia de **sistema** (no de PyPI) que usa `pdf2image` en el track OCR:
> - Ubuntu/Debian: `sudo apt-get install poppler-utils`
> - macOS: `brew install poppler`
> - Windows: descargar los binarios de Poppler y añadirlos al `PATH`
>
> El dashboard (Tabs 1-4) corre solo con `requirements.txt`. El track OCR 1964 además requiere
> `paddleocr`/`paddlepaddle` (ya incluidos) + Poppler; sus resultados ya vienen procesados en
> `data/processed/`, así que no es necesario re-ejecutarlo para ver el dashboard.

### 2. Iniciar el MCP Server

```bash
python src/mcp_server.py
```

### 3. Ejecutar el pipeline vía Claude Code CLI

```bash
# Pipeline mensual
claude "run executor_skill for period 2025-12"

# Pipeline trimestral
claude "execute mef_update for 2025-Q4"

# Modo mock (desarrollo sin conexión)
python src/data_pipeline.py --period 2025-12 --mock
```

### 4. Lanzar el Dashboard

```bash
streamlit run app.py
```

---

## Estructura del Repositorio

```
mef_subnational_efficiency_mcp/
│
├── app.py                             # Dashboard Streamlit — 4 tabs
├── README.md                          # Este archivo
├── requirements.txt
│
├── .claude/
│   └── skills/
│       ├── executor_skill.json        # Skill de extracción y composición
│       └── evaluator_skill.json       # Skill de auditoría y optimización
│
├── src/
│   ├── mcp_server.py                  # Servidor MCP local (7 herramientas)
│   ├── data_pipeline.py               # Pipeline 2025: snapshot → filter → Parquet
│   ├── ocr_engine.py                  # PaddleOCR — mínimo 15 páginas del PDF 1964
│   ├── analytical_engine.py           # Métricas fiscales y agrupaciones
│   └── utils.py                       # Logging, parseo de períodos, helpers
│
├── data/
│   ├── raw_pdfs/                      # PDF 1964 descargado
│   ├── snapshots/                     # schema.json (contrato de columnas)
│   └── processed/                     # Parquets 2025 + JSONs KPI + logs de runs
│
└── video/
    └── link.txt                       # URL del video de presentación (5 min)
```

---

## Reglas Anti-Context-Flooding

> ⚠️ **CRÍTICO:** Los datasets del portal MEF pueden superar 200MB–1GB. Está estrictamente prohibido cargarlos completos en el contexto del LLM.

**Protocolo obligatorio:**

1. `inspeccionar_esquema_csv` → captura solo primeras 10 filas para mapear columnas
2. `data_pipeline.py` corre externamente en chunks de 50k filas con pandas
3. Solo el Parquet resultante (< 5MB) es leído por `app.py`

---

## Métricas Fiscales (Track 2025)

| Métrica | Fórmula |
|---------|---------|
| Avance % | `(Devengado / PIM) × 100` |
| Saldo No Devengado | `PIM − Devengado` |
| Clasificación | ≥70% ✅ Aceptable · 40-70% ⚠️ Riesgo · <40% 🔴 Crítico |

**Filtros aplicados:**
- Nivel gobierno: Regional o Local
- PIM mínimo: S/ 10,000,000

---

## Track Histórico 1964

El pipeline procesa mínimo **15 páginas** del PDF *"Ministerio de Hacienda y Comercio — Presupuesto, Balance y Cuenta General de la República 1964"* usando PaddleOCR.

Los resultados se presentan de forma **completamente independiente** en el Tab 1 del dashboard, sin comparaciones directas con cifras 2025 (los marcos contables son incompatibles).

---

## Dashboard (4 tabs)

| Tab | Contenido | Datos |
|-----|-----------|-------|
| **1 · Resumen Ejecutivo** | KPIs 2025 (`st.metric`) + sección histórica 1964 independiente (texto + ≥2 gráficos) | 2025 + 1964 (separados) |
| **2 · Distribución Territorial** | Coropleta de avance de ejecución + **heatmap de riesgo social** (estancamiento del gasto social × pobreza) + cuadrante de auditoría | Solo 2025 |
| **3 · Hall of Shame** | Tabla interactiva de peores unidades (PIM > S/ 10M) + desglose por función | Solo 2025 |
| **4 · Audit Log & Playground** | Reporte del Evaluator Skill + cronología de corridas + playground period-driven | Solo 2025 |

**Mapas (Tab 2):** se construyen con `plotly.express.choropleth` a nivel **departamental** sobre
`data/geo/peru_departamentos.geojson` (no se requiere geopandas/folium). El join SIAF↔GeoJSON usa
normalización NFKD (tildes → ASCII), cobertura 24/24.

**Índice de riesgo social** = `minmax(% no ejecutado en funciones sociales 2025)` × `minmax(pobreza 2025)`:
- *Estancamiento social*: derivado 100% del SIAF 2025 (funciones salud, educación, saneamiento,
  protección social, vivienda).
- *Vulnerabilidad*: pobreza monetaria 2025 por departamento — **INEI, "Perú: Evolución de la
  Pobreza Monetaria 2016-2025"** (publicado 05-may-2026; `data/geo/pobreza_monetaria_2025.csv`,
  punto medio de los grupos del Cuadro 4.2 con IC al 95%).

> Todas las pestañas 2-4 usan **exclusivamente datos modernos (2025)**; la era 1964 vive solo en el
> Tab 1. El render de cada pestaña usa `@st.cache_data` para tiempos sub-segundo.

---

## Contrato de Esquema (Para integración P1 ↔ P3)

```json
{
  "columns": ["region", "entidad", "nivel_gobierno", "funcion",
               "PIM", "devengado", "avance_pct", "saldo_no_devengado"],
  "types": {
    "region": "str", "entidad": "str",
    "nivel_gobierno": "str", "funcion": "str",
    "PIM": "float64", "devengado": "float64",
    "avance_pct": "float64", "saldo_no_devengado": "float64"
  }
}
```

Ver `data/snapshots/schema.json` para el contrato completo con rutas de archivos.

---

## GitHub Workflow

```bash
# Ramas de desarrollo (NUNCA commitear directo a main)
git checkout -b feature/mcp-server-core
git checkout -b feature/data-snapshot-pipeline
git checkout -b feature/historical-1964-paddle-ocr
git checkout -b feature/executor-dashboard-draft
git checkout -b feature/evaluator-qa-refinement
```

Merge exclusivamente vía **Pull Requests** con descripción del cambio.

---

## Team

| Persona | Responsabilidad |
|---------|----------------|
| Mayra (P1) | MCP Server + Pipeline 2025 + Skills JSON |
| Camila (P2) | OCR Engine 1964 + Tab 1 del Dashboard |
| P3 | Tabs 2-4 + Evaluator + Video |

---

## Video de Presentación

Ver `video/link.txt` — máximo 5 minutos, 3-4 slides + demo live del dashboard.
