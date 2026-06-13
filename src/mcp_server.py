"""
Local MCP Server — MEF Subnational Efficiency Pipeline
Exposes tools to Claude Code CLI for interacting with datosabiertos.gob.pe
and orchestrating the dual-era analytics pipeline.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP] %(levelname)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
CKAN_BASE      = "https://datosabiertos.gob.pe"
CKAN_API       = f"{CKAN_BASE}/api/3/action"
DATA_DIR       = Path(__file__).parent.parent / "data"
RAW_PDF_DIR    = DATA_DIR / "raw_pdfs"
SNAPSHOT_DIR   = DATA_DIR / "snapshots"
PROCESSED_DIR  = DATA_DIR / "processed"
PDF_1964_URL   = (
    "https://fuenteshistoricasdelperu.com/2021/08/12/"
    "ministerio-de-hacienda-y-comercio-presupuesto-balance-y-cuenta-general-de-la-republica/"
)

for d in (RAW_PDF_DIR, SNAPSHOT_DIR, PROCESSED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── HTTP helper ───────────────────────────────────────────────────────────────
async def _get(url: str, params: dict | None = None, timeout: float = 30.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

async def _get_bytes(url: str, timeout: float = 120.0) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

# ── MCP Server ────────────────────────────────────────────────────────────────
server = Server("mef-mcp-server")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="buscar_datasets",
            description="Busca datasets en datosabiertos.gob.pe usando palabras clave vía CKAN.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query":      {"type": "string", "description": "Términos de búsqueda (ej. 'presupuesto MEF 2025')"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="obtener_detalle_dataset",
            description="Extrae URLs de descarga directa de recursos de un dataset dado su ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID o nombre del dataset en CKAN"},
                },
                "required": ["dataset_id"],
            },
        ),
        Tool(
            name="descargar_documento_1964",
            description=(
                "Descarga localmente el PDF histórico del Ministerio de Hacienda 1964 "
                "desde el portal de fuentes históricas del Perú."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "direct_url": {
                        "type": "string",
                        "description": "URL directa del PDF 1964 (obtenida previamente del portal).",
                    }
                },
                "required": ["direct_url"],
            },
        ),
        Tool(
            name="listar_entidades_publicas",
            description="Lista ministerios, gobiernos regionales y municipalidades desde el portal CKAN.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "enum": ["ministerios", "regional", "local", "todos"],
                        "default": "todos",
                    }
                },
            },
        ),
        Tool(
            name="listar_categorias_tematicas",
            description="Devuelve los grupos temáticos disponibles en el portal de datos abiertos.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="obtener_ultimas_actualizaciones",
            description="Retorna los datasets más recientemente actualizados en el portal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        ),
        Tool(
            name="inspeccionar_esquema_csv",
            description=(
                "⚠️  ANTI-CONTEXT-FLOODING: Descarga SÓLO las primeras N filas de un CSV "
                "para mapear columnas y tipos sin ingestar el archivo completo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_url": {"type": "string", "description": "URL directa al CSV/JSON del recurso"},
                    "n_rows":       {"type": "integer", "default": 10, "description": "Máximo de filas a descargar"},
                    "save_snapshot": {"type": "boolean", "default": True},
                },
                "required": ["resource_url"],
            },
        ),
        Tool(
            name="consultar_datastore_filtrado",
            description=(
                "Ejecuta queries SQL-like directamente en el Datastore de CKAN "
                "sin descargar el CSV completo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "filters":     {"type": "object", "description": "Filtros clave:valor"},
                    "fields":      {"type": "array",  "items": {"type": "string"}},
                    "limit":       {"type": "integer", "default": 100},
                    "sql":         {"type": "string",  "description": "SQL directo (opcional, prioridad sobre filters)"},
                },
                "required": ["resource_id"],
            },
        ),
        Tool(
            name="procesar_ocr_paginas_1964",
            description=(
                "Dispara el motor PaddleOCR sobre páginas seleccionadas del PDF 1964 "
                "y guarda los resultados en data/processed/. Mínimo 15 páginas."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_path":   {"type": "string", "description": "Ruta local al PDF 1964"},
                    "page_range": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Lista de índices de página (0-based). Mínimo 15.",
                    },
                },
                "required": ["pdf_path", "page_range"],
            },
        ),
        Tool(
            name="descargar_y_analizar_estadisticas",
            description=(
                "Descarga un recurso, corre agregaciones ligeras (pandas/polars) y devuelve "
                "un resumen estadístico sin saturar el contexto."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_url": {"type": "string"},
                    "group_by":     {"type": "array", "items": {"type": "string"}},
                    "agg_cols":     {"type": "array", "items": {"type": "string"}},
                    "filters":      {"type": "object"},
                    "period":       {"type": "string", "description": "Período a analizar ej. '2025-12' o '2025-Q4'"},
                },
                "required": ["resource_url"],
            },
        ),
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    log.info("Tool invoked: %s | args: %s", name, arguments)

    # 1. buscar_datasets
    if name == "buscar_datasets":
        data = await _get(
            f"{CKAN_API}/package_search",
            params={"q": arguments["query"], "rows": arguments.get("max_results", 5)},
        )
        results = data.get("result", {}).get("results", [])
        out = [
            {"id": r["id"], "name": r["name"], "title": r.get("title"), "notes": r.get("notes", "")[:200]}
            for r in results
        ]
        return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]

    # 2. obtener_detalle_dataset
    elif name == "obtener_detalle_dataset":
        data = await _get(
            f"{CKAN_API}/package_show",
            params={"id": arguments["dataset_id"]},
        )
        pkg = data.get("result", {})
        resources = [
            {
                "id":     r["id"],
                "name":   r.get("name"),
                "format": r.get("format"),
                "url":    r.get("url"),
            }
            for r in pkg.get("resources", [])
        ]
        return [TextContent(type="text", text=json.dumps(resources, ensure_ascii=False, indent=2))]

    # 3. descargar_documento_1964
    elif name == "descargar_documento_1964":
        url = arguments["direct_url"]
        filename = url.split("/")[-1].split("?")[0] or "hacienda_1964.pdf"
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        dest = RAW_PDF_DIR / filename
        if dest.exists():
            return [TextContent(type="text", text=f"Ya existe: {dest}")]
        content = await _get_bytes(url)
        dest.write_bytes(content)
        return [TextContent(type="text", text=f"PDF descargado: {dest} ({len(content):,} bytes)")]

    # 4. listar_entidades_publicas
    elif name == "listar_entidades_publicas":
        tipo = arguments.get("tipo", "todos")
        query_map = {
            "ministerios": "ministerio",
            "regional":    "gobierno regional",
            "local":       "municipalidad",
            "todos":       "gobierno",
        }
        data = await _get(
            f"{CKAN_API}/organization_list",
            params={"all_fields": True, "limit": 100},
        )
        orgs = data.get("result", [])
        q = query_map.get(tipo, "")
        if q and tipo != "todos":
            orgs = [o for o in orgs if q.lower() in (o.get("title") or "").lower()]
        out = [{"name": o.get("name"), "title": o.get("title"), "packages": o.get("package_count")} for o in orgs]
        return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]

    # 5. listar_categorias_tematicas
    elif name == "listar_categorias_tematicas":
        data = await _get(f"{CKAN_API}/group_list", params={"all_fields": True})
        groups = [
            {"name": g["name"], "title": g.get("title"), "packages": g.get("package_count")}
            for g in data.get("result", [])
        ]
        return [TextContent(type="text", text=json.dumps(groups, ensure_ascii=False, indent=2))]

    # 6. obtener_ultimas_actualizaciones
    elif name == "obtener_ultimas_actualizaciones":
        limit = arguments.get("limit", 10)
        data = await _get(
            f"{CKAN_API}/recently_changed_packages_activity_list",
            params={"limit": limit},
        )
        acts = data.get("result", [])
        out = [
            {
                "timestamp":   a.get("timestamp"),
                "activity":    a.get("activity_type"),
                "dataset_id":  a.get("object_id"),
                "user":        a.get("user_id"),
            }
            for a in acts
        ]
        return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]

    # 7. inspeccionar_esquema_csv  ← ANTI-CONTEXT-FLOODING
    elif name == "inspeccionar_esquema_csv":
        import io
        import pandas as pd

        resource_url  = arguments["resource_url"]
        n_rows        = arguments.get("n_rows", 10)
        save_snapshot = arguments.get("save_snapshot", True)

        raw = await _get_bytes(resource_url)
        # Intentar distintas encodings comunes en portales peruanos
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                df = pd.read_csv(io.BytesIO(raw), nrows=n_rows, encoding=enc, low_memory=False)
                break
            except Exception:
                continue
        else:
            return [TextContent(type="text", text="ERROR: No se pudo leer el CSV con ninguna codificación conocida.")]

        schema = {
            col: str(dtype)
            for col, dtype in df.dtypes.items()
        }
        snapshot = {
            "schema":    schema,
            "n_rows":    len(df),
            "sample":    df.head(5).to_dict(orient="records"),
            "source_url": resource_url,
        }

        if save_snapshot:
            snap_file = SNAPSHOT_DIR / "schema_latest.json"
            snap_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
            log.info("Snapshot guardado en %s", snap_file)

        return [TextContent(type="text", text=json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))]

    # 8. consultar_datastore_filtrado
    elif name == "consultar_datastore_filtrado":
        resource_id = arguments["resource_id"]
        limit       = arguments.get("limit", 100)

        if "sql" in arguments:
            data = await _get(
                f"{CKAN_API}/datastore_search_sql",
                params={"sql": arguments["sql"]},
            )
        else:
            params: dict = {"resource_id": resource_id, "limit": limit}
            if "filters" in arguments:
                params["filters"] = json.dumps(arguments["filters"])
            if "fields" in arguments:
                params["fields"] = ",".join(arguments["fields"])
            data = await _get(f"{CKAN_API}/datastore_search", params=params)

        records = data.get("result", {}).get("records", [])
        return [TextContent(type="text", text=json.dumps(records, ensure_ascii=False, indent=2, default=str))]

    # 9. procesar_ocr_paginas_1964
    elif name == "procesar_ocr_paginas_1964":
        pdf_path   = arguments["pdf_path"]
        page_range = arguments["page_range"]
        if len(page_range) < 15:
            return [TextContent(type="text", text="ERROR: Se requieren mínimo 15 páginas para el track 1964.")]

        # Delegar al motor OCR (src/ocr_engine.py — responsabilidad de Camila/P2)
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "ocr_engine.py"),
             "--pdf", pdf_path, "--pages", ",".join(map(str, page_range))],
            capture_output=True, text=True,
        )
        output = result.stdout or result.stderr
        return [TextContent(type="text", text=output)]

    # 10. descargar_y_analizar_estadisticas
    elif name == "descargar_y_analizar_estadisticas":
        import io
        import pandas as pd

        resource_url = arguments["resource_url"]
        group_by     = arguments.get("group_by", [])
        agg_cols     = arguments.get("agg_cols", [])
        filters      = arguments.get("filters", {})
        period       = arguments.get("period", "")

        raw = await _get_bytes(resource_url)
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                df = pd.read_csv(io.BytesIO(raw), low_memory=False, encoding=enc)
                break
            except Exception:
                continue
        else:
            return [TextContent(type="text", text="ERROR: No se pudo leer el CSV.")]

        # Aplicar filtros
        for col, val in filters.items():
            if col in df.columns:
                df = df[df[col].astype(str).str.contains(str(val), case=False, na=False)]

        # Agregación
        summary: dict = {"total_rows": len(df), "period": period}
        if group_by and agg_cols:
            valid_agg = [c for c in agg_cols if c in df.columns]
            valid_grp = [c for c in group_by if c in df.columns]
            if valid_grp and valid_agg:
                agg_df = df.groupby(valid_grp)[valid_agg].sum().reset_index()
                summary["aggregation"] = agg_df.head(20).to_dict(orient="records")
        else:
            summary["describe"] = df.describe(include="all").to_dict()

        return [TextContent(type="text", text=json.dumps(summary, ensure_ascii=False, indent=2, default=str))]

    else:
        return [TextContent(type="text", text=f"Tool '{name}' no reconocida.")]


# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    log.info("Iniciando MCP Server MEF — escuchando en stdio…")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
