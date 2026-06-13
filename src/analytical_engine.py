"""
analytical_engine.py — Core Metric & Grouping Modules
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Centraliza todos los cálculos de indicadores fiscales.
Consumido por data_pipeline.py y app.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


# ── Carga de datos ────────────────────────────────────────────────────────────

def load_budget(period: str) -> pd.DataFrame:
    """Carga el Parquet principal del período. Falla descriptivamente si no existe."""
    safe = period.replace(" ", "_").replace("/", "-")
    path = PROCESSED_DIR / f"budget_2025_{safe}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró {path}. Ejecutar: python src/data_pipeline.py --period {period}"
        )
    return pd.read_parquet(path)


def load_kpis(period: str) -> dict:
    safe = period.replace(" ", "_").replace("/", "-")
    path = PROCESSED_DIR / f"kpis_{safe}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_regional(period: str) -> pd.DataFrame:
    safe = period.replace(" ", "_").replace("/", "-")
    path = PROCESSED_DIR / f"region_agg_{safe}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def load_worst_units(period: str) -> pd.DataFrame:
    safe = period.replace(" ", "_").replace("/", "-")
    path = PROCESSED_DIR / f"worst_units_{safe}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Métricas fiscales ─────────────────────────────────────────────────────────

def execution_rate(pim: float, devengado: float) -> float:
    """Avance % = (Devengado / PIM) × 100"""
    if pim == 0 or pd.isna(pim):
        return 0.0
    return round((devengado / pim) * 100, 2)


def unexecuted_budget(pim: float, devengado: float) -> float:
    """Saldo No Devengado (Presupuesto Paralizado) = PIM − Devengado"""
    return round(pim - devengado, 2)


def classify_execution(avance_pct: float) -> str:
    """Clasifica el nivel de ejecución en semáforo."""
    if avance_pct >= 70:
        return "✅ Aceptable"
    elif avance_pct >= 40:
        return "⚠️ Riesgo"
    else:
        return "🔴 Crítico"


# ── Agrupaciones ──────────────────────────────────────────────────────────────

def group_by_region(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega métricas por región y añade clasificación semáforo."""
    if df.empty or "region" not in df.columns:
        return pd.DataFrame()
    agg = (
        df.groupby("region", dropna=False)
        .agg(PIM=("PIM", "sum"), devengado=("devengado", "sum"), entidades=("entidad", "count"))
        .reset_index()
    )
    agg["avance_pct"]         = agg.apply(lambda r: execution_rate(r["PIM"], r["devengado"]), axis=1)
    agg["saldo_no_devengado"]  = agg["PIM"] - agg["devengado"]
    agg["estado"]             = agg["avance_pct"].apply(classify_execution)
    return agg.sort_values("avance_pct", ascending=True)


def group_by_function(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega métricas por función/categoría de gasto."""
    if df.empty or "funcion" not in df.columns:
        return pd.DataFrame()
    agg = (
        df.groupby("funcion", dropna=False)
        .agg(PIM=("PIM", "sum"), devengado=("devengado", "sum"), entidades=("entidad", "count"))
        .reset_index()
    )
    agg["avance_pct"]        = agg.apply(lambda r: execution_rate(r["PIM"], r["devengado"]), axis=1)
    agg["saldo_no_devengado"] = agg["PIM"] - agg["devengado"]
    return agg.sort_values("saldo_no_devengado", ascending=False)


def worst_performers(df: pd.DataFrame, n: int = 50, min_pim: float = 10_000_000) -> pd.DataFrame:
    """
    Lista las peores unidades ejecutoras:
      - PIM > min_pim (default 10M PEN)
      - Ordenadas por Avance % ascendente (peor primero)
    """
    if df.empty:
        return pd.DataFrame()
    mask = df["PIM"] >= min_pim if "PIM" in df.columns else pd.Series([True] * len(df))
    out  = df[mask].sort_values("avance_pct", ascending=True).head(n).copy()
    if "estado" not in out.columns:
        out["estado"] = out["avance_pct"].apply(classify_execution)
    return out


def national_summary(df: pd.DataFrame, period: str) -> dict:
    """KPIs nacionales a partir del DataFrame filtrado."""
    if df.empty:
        return {"period": period, "error": "Sin datos"}
    total_pim = df["PIM"].sum() if "PIM" in df.columns else 0
    total_dev = df["devengado"].sum() if "devengado" in df.columns else 0
    return {
        "period":                 period,
        "total_PIM":              round(total_pim, 2),
        "total_devengado":        round(total_dev, 2),
        "avance_nacional_pct":    execution_rate(total_pim, total_dev),
        "total_saldo_paralizado": unexecuted_budget(total_pim, total_dev),
        "n_entidades":            int(df["entidad"].nunique()) if "entidad" in df.columns else 0,
        "n_regiones":             int(df["region"].nunique()) if "region" in df.columns else 0,
    }


# ── Helpers de formato ─────────────────────────────────────────────────────────

def fmt_pen(value: float) -> str:
    """Formatea un valor en Soles con separador de miles."""
    return f"S/ {value:,.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"
