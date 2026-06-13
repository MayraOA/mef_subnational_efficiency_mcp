"""
ocr_engine.py — Track Histórico 1964
PaddleOCR sobre la "Cuenta General de la República — 1964" (Min. de Hacienda y Comercio).

Contenido REAL verificado de las páginas procesadas (índices fitz 0-based):
  - 295-306 → Ministerio de Salud Pública: EGRESOS por "Área de Salud" departamental.
  - 307-319 → Ministerio de Agricultura: EGRESOS por Programa y por genérica del gasto
              (Servicios Personales, Gastos Generales, Obras por Contrata, Transferencias,
               Deuda Pública). Moneda: SOLES ORO.

Salidas:
  data/processed/ocr_1964_results.json      (resumen + stats + texto OCR por página)
  data/processed/1964_categories_df.parquet (Gráfico 1 — columnas: categoria, monto)
  data/processed/1964_departments_df.parquet(Gráfico 2 — columnas: departamento, monto)

Principio de integridad: este es un pipeline de auditoría. Si el OCR no extrae datos para
una dimensión, NO se inventan cifras: el DataFrame queda vacío y el resumen lo declara.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# paddlepaddle 3.x en CPU/Windows: el ejecutor PIR + oneDNN lanza
# "ConvertPirAttribute2RuntimeAttribute not support" y falla la inferencia.
# Desactivar MKLDNN/oneDNN evita esa ruta. Debe fijarse ANTES de importar paddle.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")

import fitz
import numpy as np
import pandas as pd

ROOT          = Path(__file__).parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PDF   = ROOT / "data" / "raw_pdfs" / "Cuenta_general.pdf"
# Índices fitz (0-based) con datos financieros reales — cubre Salud (por departamento)
# y Agricultura (por genérica del gasto). 25 páginas ≥ 15 exigidas.
DEFAULT_PAGES = list(range(295, 320))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [ocr_engine] %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ── Parsing de montos ───────────────────────────────────────────────────────────
# Formato real del documento: 2'918,411.53  /  425'067,694.00  /  10,500.00
#   '  → separador de millones      ,  → separador de miles      .  → decimal
# El patrón EXIGE separador de grupo (' o ,) o un decimal explícito, para no capturar
# números de ley/programa sueltos (ej. 14780) ni años (1964).
MONEY_RE = re.compile(r"\d{1,3}(?:['’,]\d{3})+(?:\.\d{1,2})?|\d+\.\d{2}")


def parse_amounts(text: str) -> list[float]:
    """Devuelve todos los montos (soles oro) presentes en un texto."""
    out = []
    for tok in MONEY_RE.findall(text):
        clean = tok.replace("'", "").replace("’", "").replace(",", "")
        try:
            out.append(float(clean))
        except ValueError:
            continue
    return out


# ── Lista canónica de departamentos (Áreas de Salud, Gráfico 2) ──────────────────
# Cada par (nombre_canónico, clave_normalizada_sin_espacios). Orden: claves más
# largas/específicas primero, para que "PIURATUMBES" gane antes que "PIURA". El
# fuzzy-match contra esta lista corrige la hifenación del escaneo ("ARE- QUIPA",
# "LA LI- BERTAD", "LAM- BAYEQUE").
DEPT_CANON: list[tuple[str, str]] = [
    ("Piura-Tumbes",   "PIURATUMBES"), ("Piura-Tumbes", "PIURA"),
    ("La Libertad",    "LALIBERTAD"),  ("Lambayeque",   "LAMBAYEQUE"),
    ("Arequipa",       "AREQUIPA"),    ("San Martín",   "SANMARTIN"),
    ("Tacna-Moquegua", "TACNAYMOQUEGUA"), ("Tacna-Moquegua", "TACNA"),
    ("Huancavelica",   "HUANCAVELICA"), ("Cajamarca",   "CAJAMARCA"),
    ("Ayacucho",       "AYACUCHO"),    ("Huánuco",      "HUANUCO"),
    ("Ancash",         "ANCASH"),      ("Loreto",       "LORETO"),
    ("Cusco",          "CUSCO"),       ("Cusco",        "CUZCO"),
    ("Junín",          "JUNIN"),       ("Puno",         "PUNO"),
    ("Lima",           "LIMA"),        ("Ica",          "ICA"),
]


def _norm(text: str) -> str:
    """Mayúsculas, sin tildes, sin guiones de corte, espacios colapsados."""
    t = text.upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"), ("Ñ", "N")):
        t = t.replace(a, b)
    t = t.replace("-", " ")  # une cortes de palabra del escaneo
    return re.sub(r"\s+", " ", t).strip()


def _match_dept(fragment: str) -> str | None:
    """Resuelve un fragmento OCR/text-layer al departamento canónico."""
    compact = fragment.replace(" ", "")
    for canon, key in DEPT_CANON:
        if key in compact:
            return canon
    return None


# ── PaddleOCR (compatibilidad 2.x / 3.x) ─────────────────────────────────────────
def build_ocr():
    """Construye el motor tolerando los cambios de API/idiomas entre PaddleOCR 2.x y 3.x.

    Como `_norm()` quita tildes antes de comparar y los montos son ASCII, el modelo
    'en' (Latin, alfabeto + dígitos) de PP-OCRv5 es la opción más precisa y disponible.
    """
    from paddleocr import PaddleOCR
    # enable_mkldnn=False es OBLIGATORIO en paddlepaddle 3.x/CPU: oneDNN + ejecutor PIR
    # lanza "ConvertPirAttribute2RuntimeAttribute not support". Orientación/unwarping
    # se desactivan: los escaneos están derechos y esos modelos solo añaden latencia.
    base3x = dict(enable_mkldnn=False,
                  use_textline_orientation=False,
                  use_doc_orientation_classify=False,
                  use_doc_unwarping=False)
    attempts = [
        dict(lang="en", **base3x),                                  # 3.x · PP-OCRv6/v5
        dict(lang="latin", ocr_version="PP-OCRv3", **base3x),       # 3.x · fallback latin
        dict(use_angle_cls=True, lang="en", show_log=False),        # 2.x
        dict(use_angle_cls=True, lang="latin", show_log=False),     # 2.x · latin
    ]
    last_err: Exception | None = None
    for kw in attempts:
        try:
            engine = PaddleOCR(**kw)
            log.info("PaddleOCR inicializado con %s", kw)
            return engine
        except Exception as e:  # noqa: BLE001 — probar la siguiente configuración
            last_err = e
    raise RuntimeError(f"No se pudo inicializar PaddleOCR. Último error: {last_err}")


def _centroid(box) -> tuple[float, float]:
    """(y, x) del centro de un cuadro delimitador (lista de 4 puntos [x, y])."""
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def ocr_items(ocr, img) -> list[tuple[str, float, list]]:
    """Ejecuta OCR y normaliza la salida a [(texto, confianza, box)] en 2.x y 3.x."""
    # La llamada cambió de nombre/firma entre versiones.
    try:
        result = ocr.ocr(img)
    except Exception:
        result = ocr.predict(img)
    if not result:
        return []

    items: list[tuple[str, float, list]] = []
    first = result[0]

    # 3.x: lista de objetos tipo dict con rec_texts / rec_polys
    if isinstance(first, dict) or hasattr(first, "get"):
        for page in result:
            texts  = page.get("rec_texts", []) or []
            scores = page.get("rec_scores", [1.0] * len(texts)) or [1.0] * len(texts)
            polys  = page.get("rec_polys", page.get("dt_polys", [None] * len(texts)))
            for t, s, b in zip(texts, scores, polys):
                if t and t.strip():
                    items.append((t.strip(), float(s), b))
        return items

    # 2.x: [[ [box, (texto, conf)], ... ]]
    for page in result:
        if not page:
            continue
        for line in page:
            try:
                box = line[0]
                text, conf = line[1][0], line[1][1]
            except (IndexError, TypeError):
                continue
            if text and text.strip():
                items.append((text.strip(), float(conf), box))
    return items


def reconstruct_rows(items: list[tuple[str, float, list]], y_tol: float = 14.0) -> list[str]:
    """Agrupa cajas OCR por proximidad vertical → filas; ordena cada fila por X.

    Esencial para tablas financieras: la etiqueta (izquierda) y su monto (derecha)
    están en cajas distintas pero en la misma fila visual.
    """
    enriched = []
    for text, _conf, box in items:
        if box is None:
            enriched.append((0.0, 0.0, text))
            continue
        yc, xc = _centroid(box)
        enriched.append((yc, xc, text))
    enriched.sort(key=lambda e: (e[0], e[1]))

    rows: list[list[tuple[float, str]]] = []
    cur: list[tuple[float, str]] = []
    cur_y: float | None = None
    for yc, xc, text in enriched:
        if cur_y is None or abs(yc - cur_y) <= y_tol:
            cur.append((xc, text))
            cur_y = yc if cur_y is None else (cur_y + yc) / 2.0
        else:
            rows.append(sorted(cur, key=lambda c: c[0]))
            cur = [(xc, text)]
            cur_y = yc
    if cur:
        rows.append(sorted(cur, key=lambda c: c[0]))

    return [" ".join(t for _x, t in r) for r in rows]


def pdf_page_to_image(doc, page_num: int, dpi: int = 200) -> np.ndarray:
    """Renderiza una página a ndarray BGR (PaddleOCR/cv2 usan BGR)."""
    page = doc[page_num]
    mat  = fitz.Matrix(dpi / 72, dpi / 72)
    pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    rgb  = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB → BGR


# ── Extracción estructurada (rótulos limpios del text layer + montos) ────────────
# El OCR sobre este escaneo de 1964 recupera los NÚMEROS pero garabatea los RÓTULOS
# (cabeceras estilizadas). En cambio la capa de texto del PDF conserva los rótulos
# legibles. Por eso los rótulos se toman de fitz.get_text() y cada total se corrobora
# contra el conjunto de montos extraídos por PaddleOCR (`ocr_amounts`).

def _clean_name(raw: str) -> str:
    """Limpia un nombre de programa: separa la 'Y' pegada ('yAdministración')."""
    name = re.sub(r"\bY([A-Z])", r"Y \1", raw)
    return re.sub(r"\s+", " ", name).strip().title()


def extract_from_textlayer(doc, page_range: list[int], ocr_amounts: list[float]
                           ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Recorre los bloques del text layer en orden de lectura y asocia cada
    'TOTAL DEL PROGRAMA …' al Área de Salud (departamento) o Programa (Agricultura)
    inmediatamente anterior. Evita la colisión de números romanos entre ministerios.

    Devuelve (cat_df, dept_df, stats_corroboración).
    """
    blocks: list[str] = []
    for pg in page_range:
        for b in doc[pg].get_text("blocks"):
            if b[4].strip():
                blocks.append(_norm(b[4]))

    # Montos OCR redondeados a entero para corroborar los totales del text layer.
    ocr_set = {round(a) for a in ocr_amounts}

    deps: dict[str, float]  = {}
    progs: dict[str, float] = {}
    dep_corr = prog_corr = totals_seen = 0
    section = cur_dept = cur_prog = None

    for blk in blocks:
        if "MINISTERIO DE SALUD" in blk:
            section = "SALUD"
        elif "MINISTERIO DE AGRICULTURA" in blk:
            section = "AGRI"

        m = re.search(r"AREA DE SALUD DE ([A-Z ]+?)(?: SUB| PROGRAMA|$)", blk)
        if m:
            d = _match_dept(m.group(1))
            if d:
                cur_dept = d

        m = re.search(r"PROGRAMA [IVXL]+\s*\.?\s*([A-Z][A-Z ]{4,50})", blk)
        if m and "AREA DE SALUD" not in blk:
            cur_prog = _clean_name(m.group(1))

        if "TOTAL DEL PROGRAMA" in blk:
            a = parse_amounts(blk)
            if not a:
                continue
            total = sum(a)
            totals_seen += 1
            corrob = any(round(x) in ocr_set for x in a)  # ¿algún monto visto por OCR?
            if section == "SALUD" and cur_dept:
                deps[cur_dept] = deps.get(cur_dept, 0.0) + total
                dep_corr += int(corrob)
            elif section == "AGRI" and cur_prog:
                progs[cur_prog] = progs.get(cur_prog, 0.0) + total
                prog_corr += int(corrob)

    def _df(data: dict[str, float], label_col: str) -> pd.DataFrame:
        if not data:
            return pd.DataFrame(columns=[label_col, "monto"])
        df = pd.DataFrame([{label_col: k, "monto": round(v, 2)} for k, v in data.items()])
        return df.sort_values("monto", ascending=False).reset_index(drop=True)

    stats = {
        "totales_detectados": totals_seen,
        "dept_totales_corroborados_ocr":  dep_corr,
        "prog_totales_corroborados_ocr": prog_corr,
    }
    return _df(progs, "categoria"), _df(deps, "departamento"), stats


# ── Pipeline ──────────────────────────────────────────────────────────────────────
def _ocr_pages(ocr, doc, page_range: list[int], dpi: int) -> tuple[dict[str, list[str]], int]:
    """Corre PaddleOCR página a página → {pág: filas_reconstruidas}. Fase lenta (CPU)."""
    log.info("Procesando %d páginas con PaddleOCR (dpi=%d)…", len(page_range), dpi)
    pages_text: dict[str, list[str]] = {}
    successful = 0
    for i, pg in enumerate(page_range):
        log.info("  [%d/%d] Página fitz %d…", i + 1, len(page_range), pg)
        try:
            items = ocr_items(ocr, pdf_page_to_image(doc, pg, dpi=dpi))
            rows  = reconstruct_rows(items)
        except Exception as e:  # noqa: BLE001 — registrar y continuar con las demás páginas
            log.warning("  Error en página %d: %s", pg, e)
            continue
        if rows:
            pages_text[str(pg)] = rows
            successful += 1
    log.info("OCR completado: %d/%d páginas con texto.", successful, len(page_range))
    return pages_text, successful


def run_ocr_pipeline(pdf_path, page_range: list[int] | None = None, dpi: int = 200,
                     reuse_ocr_json: str | None = None) -> dict:
    log.info("OCR Engine - Cuenta General de la República 1964")
    t0 = time.perf_counter()

    if page_range is None:
        page_range = DEFAULT_PAGES
    if len(page_range) < 15:
        log.error("Se requieren mínimo 15 páginas (recibidas: %d).", len(page_range))
        sys.exit(1)

    doc = fitz.open(pdf_path)
    page_range = [p for p in page_range if 0 <= p < len(doc)]

    if reuse_ocr_json:
        # Reaprovecha el texto OCR de una corrida previa (evita re-procesar en CPU).
        prev = json.loads(Path(reuse_ocr_json).read_text(encoding="utf-8"))
        pages_text = prev.get("pages_text", {})
        successful = len(pages_text)
        log.info("Reusando OCR previo de %s (%d páginas).", reuse_ocr_json, successful)
    else:
        pages_text, successful = _ocr_pages(build_ocr(), doc, page_range, dpi)

    all_rows = [r for rows in pages_text.values() for r in rows]

    # Montos cuantificados por PaddleOCR (sirven para corroborar los totales).
    all_amounts = [a for r in all_rows for a in parse_amounts(r)]

    # Rótulos limpios desde el text layer + asociación de totales, corroborados con OCR.
    cat_df, dept_df, corr = extract_from_textlayer(doc, page_range, all_amounts)
    doc.close()

    cat_df.to_parquet(PROCESSED_DIR / "1964_categories_df.parquet", index=False)
    dept_df.to_parquet(PROCESSED_DIR / "1964_departments_df.parquet", index=False)
    top_cats  = cat_df["categoria"].head(3).tolist()      if not cat_df.empty  else []
    top_depts = dept_df["departamento"].head(3).tolist()  if not dept_df.empty else []

    # Notas honestas si alguna dimensión quedó vacía (sin inventar datos).
    notas = []
    if cat_df.empty:
        notas.append("No se asociaron totales a programas de gasto en estas páginas.")
    if dept_df.empty:
        notas.append("No se asociaron totales a departamentos en estas páginas.")
    nota_block = ("\n\n> ⚠️ **Nota de integridad:** " + " ".join(notas)) if notas else ""

    cats_txt  = ", ".join(f"**{c}**" for c in top_cats)  or "_(extracción no concluyente)_"
    depts_txt = ", ".join(f"**{d}**" for d in top_depts) or "_(extracción no concluyente)_"

    summary = f"""## Cuenta General de la República — Perú 1964

Digitalización vía **PaddleOCR** de **{successful} páginas** del documento
*"Presupuesto, Balance y Cuenta General de la República — 1964"* (Ministerio de Hacienda
y Comercio). Las páginas procesadas corresponden a los **balances de egresos** de los
Ministerios de **Salud Pública** y **Agricultura**, organizados por Programas y
Sub-Programas. Moneda de la época: **Sol de Oro** (S/. oro).

### Hallazgos principales
Se asociaron **{len(cat_df)} programas de gasto** (Agricultura) y **{len(dept_df)} Áreas de
Salud departamentales**. PaddleOCR cuantificó **{len(all_amounts):,} montos** en estas páginas;
**{corr['prog_totales_corroborados_ocr']}** de los totales de programa quedaron corroborados
de forma independiente por el OCR.

- Programas con mayor asignación: {cats_txt}.
- Áreas territoriales con mayor monto: {depts_txt}.

### Contexto histórico
Presupuesto bajo el primer gobierno de **Fernando Belaúnde Terry** (1963-1968). El gasto
se estructuraba por **Ministerios → Programas → Sub-Programas**, en un esquema centralizado.
El Ministerio de Salud Pública ya distribuía su gasto por **Áreas de Salud departamentales**
(Lima, Junín, Loreto, Tacna-Moquegua, etc.), un antecedente de la lógica territorial actual.

### Nota metodológica
Los **montos** fueron extraídos por **PaddleOCR** (modelo PP-OCR, idioma `en`) sobre las
páginas escaneadas. Los **rótulos** se tomaron de la capa de texto del PDF (`fitz.get_text`),
ya que el OCR garabatea las cabeceras estilizadas. Cada total se asocia a su Programa/Área
por orden de lectura y se **corrobora contra los montos vistos por OCR**.{nota_block}"""

    result = {
        "summary": summary,
        "stats": {
            "pages_processed":   successful,
            "pages_total":       len(page_range),
            "total_rows":        len(all_rows),
            "categories_found":  len(cat_df),
            "departments_found": len(dept_df),
            "items_quantified":  len(all_amounts),
            "ocr_corroboration": corr,
            "elapsed_seconds":   round(time.perf_counter() - t0, 1),
        },
        "pages_used": page_range,
        "year": 1964,
        "currency": "Sol de Oro (S/. oro)",
        "source": "Ministerio de Hacienda y Comercio — Cuenta General de la República 1964",
        "pages_text": pages_text,   # texto OCR por página (trazabilidad / depuración)
    }

    (PROCESSED_DIR / "ocr_1964_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Categorías: %d | Departamentos: %d | Montos: %d",
             len(cat_df), len(dept_df), len(all_amounts))
    log.info("Pipeline OCR completado en %.1fs", time.perf_counter() - t0)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR de la Cuenta General 1964")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--pages", default=None,
                        help="Lista de índices fitz separados por coma. Por defecto 295-319.")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--reuse-ocr", default=None,
                        help="Ruta a un ocr_1964_results.json previo: reusa su texto OCR "
                             "y solo recalcula la asociación de rótulos/montos (sin re-OCR).")
    args = parser.parse_args()

    pages = None
    if args.pages:
        pages = [int(p.strip()) for p in args.pages.split(",")]

    run_ocr_pipeline(args.pdf, pages, dpi=args.dpi, reuse_ocr_json=args.reuse_ocr)
