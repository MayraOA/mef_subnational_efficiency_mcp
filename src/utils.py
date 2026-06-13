"""
utils.py — Helpers, system logging and shared utilities
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.parent
LOG_DIR      = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Logging setup ─────────────────────────────────────────────────────────────

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Logger con salida a consola y archivo rotante."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s — %(message)s")

    # Consola
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Archivo
    log_file = LOG_DIR / f"pipeline_{datetime.now():%Y%m%d}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── Timing decorator ──────────────────────────────────────────────────────────

class Timer:
    """Context manager para medir tiempos de ejecución."""

    def __init__(self, label: str, logger: logging.Logger | None = None):
        self.label  = label
        self.logger = logger or get_logger("timer")

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        elapsed = time.perf_counter() - self._t0
        self.elapsed = elapsed
        self.logger.info("%s completado en %.2fs", self.label, elapsed)


# ── Period parsing ─────────────────────────────────────────────────────────────

def parse_period(period_str: str) -> dict[str, Any]:
    """
    Parsea strings de período usados en CLI:
      '2025-12'   → {year:2025, month:12, type:'monthly'}
      '2025-Q4'   → {year:2025, quarter:4, type:'quarterly'}
      '2025'      → {year:2025, type:'annual'}
    """
    p = period_str.strip().upper()

    if "Q" in p:
        parts = p.split("-Q")
        return {"raw": period_str, "year": int(parts[0]), "quarter": int(parts[1]), "type": "quarterly"}

    if "-" in p:
        parts = p.split("-")
        return {"raw": period_str, "year": int(parts[0]), "month": int(parts[1]), "type": "monthly"}

    return {"raw": period_str, "year": int(p), "type": "annual"}


def safe_period_filename(period: str) -> str:
    """Convierte un período en un string seguro para nombres de archivo."""
    return period.strip().replace(" ", "_").replace("/", "-").upper()


# ── JSON helpers ───────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ── Número → texto legible ────────────────────────────────────────────────────

def human_number(n: float) -> str:
    """Convierte un número grande a texto legible (miles de millones, millones)."""
    if abs(n) >= 1e9:
        return f"{n/1e9:.2f} mil millones"
    if abs(n) >= 1e6:
        return f"{n/1e6:.2f} millones"
    if abs(n) >= 1e3:
        return f"{n/1e3:.1f} mil"
    return f"{n:.0f}"


def pen(value: float) -> str:
    """Formatea en Soles peruanos."""
    return f"S/ {value:,.0f}"


# ── Pipeline run log ──────────────────────────────────────────────────────────

def log_pipeline_run(period: str, status: str, details: dict, log_dir: Path | None = None) -> None:
    """Persiste un registro de cada ejecución del pipeline (para Tab 4 del dashboard)."""
    log_dir = log_dir or (ROOT / "data" / "processed")
    log_dir.mkdir(parents=True, exist_ok=True)

    run_log_path = log_dir / "pipeline_runs.json"
    runs = []
    if run_log_path.exists():
        try:
            runs = json.loads(run_log_path.read_text())
        except Exception:
            runs = []

    entry = {
        "timestamp": datetime.now().isoformat(),
        "period":    period,
        "status":    status,
        **details,
    }
    runs.append(entry)

    # Mantener solo los últimos 50 runs
    run_log_path.write_text(json.dumps(runs[-50:], ensure_ascii=False, indent=2, default=str))


# ── Schema contract ────────────────────────────────────────────────────────────

SCHEMA_CONTRACT = {
    "description": "Contrato de esquema compartido entre P1 (Mayra) y P3 para integración sin fricciones.",
    "output_schema": {
        "columns": [
            "region", "entidad", "nivel_gobierno", "funcion",
            "PIM", "devengado", "avance_pct", "saldo_no_devengado",
        ],
        "types": {
            "region":              "str",
            "entidad":             "str",
            "nivel_gobierno":      "str",
            "funcion":             "str",
            "PIM":                 "float64",
            "devengado":           "float64",
            "avance_pct":          "float64",
            "saldo_no_devengado":  "float64",
        },
    },
    "output_files": {
        "main":     "data/processed/budget_2025_{period}.parquet",
        "regional": "data/processed/region_agg_{period}.parquet",
        "worst":    "data/processed/worst_units_{period}.parquet",
        "kpis":     "data/processed/kpis_{period}.json",
    },
    "notes": [
        "Filtro aplicado: PIM >= 10,000,000 PEN",
        "Filtro aplicado: nivel_gobierno contiene 'REGIONAL' o 'LOCAL'",
        "avance_pct = (devengado / PIM) * 100",
        "saldo_no_devengado = PIM - devengado",
    ],
}


def write_schema_contract() -> Path:
    """Escribe el contrato de esquema para que P3 pueda leerlo."""
    path = ROOT / "data" / "snapshots" / "schema.json"
    save_json(SCHEMA_CONTRACT, path)
    return path
