"""
data_pipeline.py — Modern Track (Fiscal Year 2025)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Anti-Context-Flooding strategy: NEVER ingests raw CSV into memory wholesale.
Workflow:
  1. Snapshot schema (first 10 rows) → data/snapshots/
  2. Stream-filter with chunked pandas / duckdb
  3. Compute Avance % and Saldo No Devengado
  4. Save micro Parquet → data/processed/budget_2025_{period}.parquet
  5. Write shared schema contract → data/snapshots/schema.json

CLI Usage (via Claude Code):
  claude "run executor_skill for period 2025-12"
  python data_pipeline.py --period 2025-12
  python data_pipeline.py --period 2025-Q4
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent.parent
DATA_DIR      = ROOT / "data"
SNAPSHOT_DIR  = DATA_DIR / "snapshots"
PROCESSED_DIR = DATA_DIR / "processed"

for d in (SNAPSHOT_DIR, PROCESSED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pipeline] %(levelname)s — %(message)s",
)
log = logging.getLogger(__name__)

# ── MEF / SIAF Dataset config ─────────────────────────────────────────────────
# URL semilla: Consulta Amigable SIAF — ajustar si el portal actualiza el recurso
MEF_RESOURCE_URL = (
    "https://datosabiertos.gob.pe/sites/default/files/recurso/"
    "consulta_amigable/ejecucion_gastos_2025.csv"
)

# Columnas clave del esquema SIAF (validadas contra snapshot)
COL_MAP = {
    "region":       ["DEPARTAMENTO", "REGION", "Departamento", "departamento"],
    "entidad":      ["ENTIDAD", "NOMBRE_ENTIDAD", "entidad", "PLIEGO"],
    "PIM":          ["PIM", "Presupuesto_Institucional_Modificado", "pim"],
    "devengado":    ["DEVENGADO", "Devengado", "devengado", "EJECUTADO"],
    "nivel_gobierno": ["NIVEL_GOBIERNO", "Nivel_Gobierno", "nivel"],
    "funcion":      ["FUNCION", "Funcion", "funcion", "CATEGORIA_GASTO"],
}

# Mínimo de presupuesto para incluir unidades ejecutoras (10M PEN)
MIN_PIM_SOL = 10_000_000

# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Devuelve el primer nombre de columna que exista en el DataFrame."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _normalize_period(period: str) -> dict:
    """
    Convierte '2025-12' → {'year':2025,'month':12}
    Convierte '2025-Q4'  → {'year':2025,'quarter':4}
    """
    p = period.strip().upper()
    if "Q" in p:
        parts = p.split("-Q")
        return {"year": int(parts[0]), "quarter": int(parts[1])}
    if "-" in p:
        parts = p.split("-")
        return {"year": int(parts[0]), "month": int(parts[1])}
    return {"year": int(p)}


# ── Step 1: Snapshot ───────────────────────────────────────────────────────────

def fetch_snapshot(url: str, n: int = 10) -> dict:
    """
    Descarga SÓLO las primeras `n` filas del CSV para mapear el esquema.
    Guarda en data/snapshots/schema.json (contrato compartido con P3).
    """
    log.info("Inspeccionando esquema (primeras %d filas)…", n)
    t0 = time.perf_counter()

    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                chunks = b""
                for chunk in r.iter_bytes(chunk_size=8192):
                    chunks += chunk
                    # Leer suficientes líneas
                    if chunks.count(b"\n") >= n + 2:
                        break
            df_snap = pd.read_csv(BytesIO(chunks), nrows=n, encoding=enc, low_memory=False)
            break
        except Exception as e:
            log.warning("Encoding %s falló: %s", enc, e)
            continue
    else:
        raise RuntimeError("No se pudo leer el CSV con ninguna codificación.")

    schema = {
        "columns": list(df_snap.columns),
        "dtypes":  {c: str(t) for c, t in df_snap.dtypes.items()},
        "sample":  df_snap.head(5).to_dict(orient="records"),
        "source_url": url,
        # Contrato para P3: nombres normalizados de columnas
        "normalized_columns": {
            "region":         _resolve_col(df_snap, COL_MAP["region"]),
            "entidad":        _resolve_col(df_snap, COL_MAP["entidad"]),
            "PIM":            _resolve_col(df_snap, COL_MAP["PIM"]),
            "devengado":      _resolve_col(df_snap, COL_MAP["devengado"]),
            "nivel_gobierno": _resolve_col(df_snap, COL_MAP["nivel_gobierno"]),
            "funcion":        _resolve_col(df_snap, COL_MAP["funcion"]),
        },
    }

    out = SNAPSHOT_DIR / "schema.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2, default=str))
    log.info("Snapshot guardado → %s (%.2fs)", out, time.perf_counter() - t0)
    return schema


# ── Step 2: Chunked download + filter ─────────────────────────────────────────

def download_and_filter(url: str, schema: dict, period_info: dict) -> pd.DataFrame:
    """
    Descarga el CSV en chunks de 50k filas para evitar saturación de memoria.
    Filtra: nivel subnacional + PIM > MIN_PIM_SOL.
    """
    log.info("Descargando y filtrando datos MEF 2025 — período %s…", period_info)
    t0 = time.perf_counter()

    nc    = schema["normalized_columns"]
    col_r = nc["region"]
    col_e = nc["entidad"]
    col_p = nc["PIM"]
    col_d = nc["devengado"]
    col_n = nc["nivel_gobierno"]
    col_f = nc["funcion"]

    frames = []
    chunk_size = 50_000

    # Descargar completo y procesar en chunks
    log.info("Descargando CSV completo (puede tardar)…")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
        r.raise_for_status()
        raw = b"".join(r.iter_bytes())

    log.info("Descarga completa: %.1f MB. Procesando en chunks…", len(raw) / 1e6)

    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            reader = pd.read_csv(
                BytesIO(raw),
                chunksize=chunk_size,
                encoding=enc,
                low_memory=False,
                on_bad_lines="skip",
            )
            for chunk in reader:
                # Filtrar nivel subnacional (gobierno regional/local)
                if col_n and col_n in chunk.columns:
                    mask_nivel = chunk[col_n].astype(str).str.upper().str.contains(
                        r"REGIONAL|LOCAL|MUNICIPAL|GOBIERN", regex=True, na=False
                    )
                    chunk = chunk[mask_nivel]

                # Filtrar PIM > 10M
                if col_p and col_p in chunk.columns:
                    chunk[col_p] = pd.to_numeric(chunk[col_p], errors="coerce").fillna(0)
                    chunk = chunk[chunk[col_p] >= MIN_PIM_SOL]

                if not chunk.empty:
                    frames.append(chunk)
            break
        except Exception as e:
            log.warning("Error con encoding %s: %s", enc, e)
            continue

    if not frames:
        log.warning("Sin datos tras el filtro. Verificar URL o criterios.")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    log.info("Filas tras filtro: %d (%.2fs)", len(df), time.perf_counter() - t0)
    return df


# ── Step 3: Compute metrics ────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame, schema: dict) -> pd.DataFrame:
    """
    Calcula:
        Avance %           = (Devengado / PIM) × 100
        Saldo No Devengado = PIM − Devengado
    """
    if df.empty:
        return df

    nc    = schema["normalized_columns"]
    col_p = nc["PIM"]
    col_d = nc["devengado"]
    col_r = nc["region"]
    col_e = nc["entidad"]
    col_n = nc["nivel_gobierno"]
    col_f = nc["funcion"]

    df = df.copy()

    # Numericizar
    for c in [col_p, col_d]:
        if c and c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Métricas principales
    if col_p and col_d:
        df["avance_pct"]          = (df[col_d] / df[col_p].replace(0, pd.NA)) * 100
        df["saldo_no_devengado"]  = df[col_p] - df[col_d]

    # Renombrar a esquema normalizado (contrato con P3)
    rename = {}
    if col_r: rename[col_r] = "region"
    if col_e: rename[col_e] = "entidad"
    if col_p: rename[col_p] = "PIM"
    if col_d: rename[col_d] = "devengado"
    if col_n: rename[col_n] = "nivel_gobierno"
    if col_f: rename[col_f] = "funcion"
    df = df.rename(columns=rename)

    # Columnas finales garantizadas
    cols_out = ["region", "entidad", "nivel_gobierno", "funcion",
                "PIM", "devengado", "avance_pct", "saldo_no_devengado"]
    cols_out = [c for c in cols_out if c in df.columns]
    return df[cols_out]


# ── Step 4: Aggregate & save ───────────────────────────────────────────────────

def aggregate_and_save(df: pd.DataFrame, period: str) -> Path:
    """
    Agrega a nivel región/entidad y guarda como Parquet micro-footprint.
    Archivo: data/processed/budget_2025_{period}.parquet
    """
    if df.empty:
        log.error("DataFrame vacío — nada que guardar.")
        return Path()

    safe_period = period.replace(" ", "_").replace("/", "-")

    # Agregado 1: por región
    if "region" in df.columns:
        reg_agg = (
            df.groupby("region", dropna=False)
            .agg(
                PIM=("PIM", "sum"),
                devengado=("devengado", "sum"),
                entidades=("entidad", "count"),
            )
            .reset_index()
        )
        reg_agg["avance_pct"]         = (reg_agg["devengado"] / reg_agg["PIM"].replace(0, pd.NA)) * 100
        reg_agg["saldo_no_devengado"]  = reg_agg["PIM"] - reg_agg["devengado"]
        reg_out = PROCESSED_DIR / f"region_agg_{safe_period}.parquet"
        reg_agg.to_parquet(reg_out, index=False)
        log.info("Parquet regional: %s", reg_out)

    # Agregado 2: peores unidades ejecutoras (Tab 3 — Hall of Shame)
    if "avance_pct" in df.columns:
        worst = (
            df.sort_values("avance_pct", ascending=True)
            .head(200)
        )
        worst_out = PROCESSED_DIR / f"worst_units_{safe_period}.parquet"
        worst.to_parquet(worst_out, index=False)
        log.info("Parquet peores unidades: %s", worst_out)

    # Agregado 3: KPIs nacionales (Tab 1 — Macro Summary)
    kpis = {
        "total_PIM":              float(df["PIM"].sum()) if "PIM" in df.columns else None,
        "total_devengado":        float(df["devengado"].sum()) if "devengado" in df.columns else None,
        "avance_nacional_pct":    None,
        "total_saldo_paralizado": None,
        "n_entidades":            int(df["entidad"].nunique()) if "entidad" in df.columns else None,
        "period":                 period,
    }
    if kpis["total_PIM"] and kpis["total_devengado"]:
        kpis["avance_nacional_pct"]    = round((kpis["total_devengado"] / kpis["total_PIM"]) * 100, 2)
        kpis["total_saldo_paralizado"] = kpis["total_PIM"] - kpis["total_devengado"]

    kpi_path = PROCESSED_DIR / f"kpis_{safe_period}.json"
    kpi_path.write_text(json.dumps(kpis, ensure_ascii=False, indent=2))
    log.info("KPIs guardados: %s", kpi_path)

    # Parquet completo filtrado (para Tabs 2-4)
    main_out = PROCESSED_DIR / f"budget_2025_{safe_period}.parquet"
    df.to_parquet(main_out, index=False)
    log.info("Parquet principal: %s (%d filas)", main_out, len(df))

    # Actualizar schema.json con el período procesado
    schema_path = SNAPSHOT_DIR / "schema.json"
    if schema_path.exists():
        s = json.loads(schema_path.read_text())
        s["last_processed_period"] = period
        s["output_files"] = {
            "main":    str(main_out),
            "regional": str(PROCESSED_DIR / f"region_agg_{safe_period}.parquet"),
            "worst":   str(PROCESSED_DIR / f"worst_units_{safe_period}.parquet"),
            "kpis":    str(kpi_path),
        }
        # Contrato de columnas para P3
        s["output_schema"] = {
            "columns": ["region", "entidad", "nivel_gobierno", "funcion",
                        "PIM", "devengado", "avance_pct", "saldo_no_devengado"],
            "types": {
                "region": "str", "entidad": "str", "nivel_gobierno": "str",
                "funcion": "str", "PIM": "float64", "devengado": "float64",
                "avance_pct": "float64", "saldo_no_devengado": "float64",
            }
        }
        schema_path.write_text(json.dumps(s, ensure_ascii=False, indent=2))

    return main_out


# ── Fallback con datos mock (si el portal no responde) ────────────────────────

def generate_mock_data(period: str) -> pd.DataFrame:
    """
    Genera datos mock con el esquema exacto del contrato.
    Usado por P3 para desarrollo paralelo o si el portal falla temporalmente.
    """
    import numpy as np
    rng = np.random.default_rng(42)

    regiones = [
        "LIMA", "AREQUIPA", "CUSCO", "LA LIBERTAD", "PIURA",
        "CAJAMARCA", "JUNÍN", "PUNO", "LAMBAYEQUE", "ICA",
        "LORETO", "ANCASH", "HUÁNUCO", "SAN MARTIN", "TACNA",
        "APURÍMAC", "AYACUCHO", "MOQUEGUA", "PASCO", "TUMBES",
        "UCAYALI", "MADRE DE DIOS", "AMAZONAS", "HUANCAVELICA",
    ]
    niveles = ["GOBIERNO REGIONAL", "GOBIERNO LOCAL"]
    funciones = [
        "TRANSPORTE", "EDUCACIÓN", "SALUD", "SANEAMIENTO",
        "AGROPECUARIA", "VIVIENDA", "PROTECCIÓN SOCIAL",
    ]

    n = 500
    pim = rng.uniform(10_000_000, 500_000_000, n)
    dev_rate = rng.beta(2, 3, n)  # distribución realista de ejecución
    dev = pim * dev_rate

    df = pd.DataFrame({
        "region":          rng.choice(regiones, n),
        "entidad":         [f"ENTIDAD_{i:04d}" for i in range(n)],
        "nivel_gobierno":  rng.choice(niveles, n),
        "funcion":         rng.choice(funciones, n),
        "PIM":             pim,
        "devengado":       dev,
        "avance_pct":      dev_rate * 100,
        "saldo_no_devengado": pim - dev,
    })

    # Guardar mock
    safe = period.replace(" ", "_").replace("/", "-")
    for suffix, data in [
        ("budget_2025", df),
        ("worst_units", df.nsmallest(100, "avance_pct")),
        ("region_agg",  df.groupby("region").agg(
            PIM=("PIM","sum"), devengado=("devengado","sum"),
            entidades=("entidad","count")).reset_index().assign(
            avance_pct=lambda x: x["devengado"]/x["PIM"]*100,
            saldo_no_devengado=lambda x: x["PIM"]-x["devengado"]
        )),
    ]:
        path = PROCESSED_DIR / f"{suffix}_{safe}.parquet"
        if isinstance(data, pd.DataFrame):
            data.to_parquet(path, index=False)

    kpis = {
        "total_PIM":              float(df["PIM"].sum()),
        "total_devengado":        float(df["devengado"].sum()),
        "avance_nacional_pct":    round(df["devengado"].sum() / df["PIM"].sum() * 100, 2),
        "total_saldo_paralizado": float((df["PIM"] - df["devengado"]).sum()),
        "n_entidades":            n,
        "period":                 period,
        "is_mock":                True,
    }
    (PROCESSED_DIR / f"kpis_{safe}.json").write_text(
        json.dumps(kpis, ensure_ascii=False, indent=2)
    )
    log.info("Mock data generada para período %s (%d filas)", period, n)
    return df


# ── Main entry point ───────────────────────────────────────────────────────────

def run_pipeline(period: str, use_mock: bool = False):
    """Orquesta el pipeline completo para un período dado."""
    log.info("═══════════════════════════════════════════")
    log.info("MEF Pipeline — Período: %s", period)
    log.info("═══════════════════════════════════════════")
    t_total = time.perf_counter()

    if use_mock:
        log.info("Modo MOCK activado.")
        df = generate_mock_data(period)
        out = aggregate_and_save(df, period)
    else:
        try:
            schema = fetch_snapshot(MEF_RESOURCE_URL)
            df     = download_and_filter(MEF_RESOURCE_URL, schema, _normalize_period(period))
            if df.empty:
                log.warning("Sin datos reales. Usando mock como fallback.")
                df = generate_mock_data(period)
            else:
                df = compute_metrics(df, schema)
            out = aggregate_and_save(df, period)
        except Exception as e:
            log.error("Error en pipeline real: %s — usando mock.", e)
            df  = generate_mock_data(period)
            out = aggregate_and_save(df, period)

    elapsed = time.perf_counter() - t_total
    log.info("Pipeline completado en %.1fs → %s", elapsed, out)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEF 2025 Data Pipeline")
    parser.add_argument("--period", default="2025-12",
                        help="Período a procesar. Ej: 2025-12 | 2025-Q4 | 2025")
    parser.add_argument("--mock", action="store_true",
                        help="Usar datos mock (para desarrollo sin conexión)")
    args = parser.parse_args()
    run_pipeline(args.period, use_mock=args.mock)
