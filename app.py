"""
app.py — MEF Subnational Efficiency Dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4-tab Streamlit application:
  Tab 1 — Executive Macro Summary & Dual-Era Opening Dashboard
  Tab 2 — Territorial Distribution & Geospatial Analysis (2025)
  Tab 3 — Budget "Hall of Shame" & Anomaly Explorer (2025)
  Tab 4 — Multi-Agent Audit Log & Live Playground (2025)

Period-driven: controlled via DEFAULT_PERIOD (updated by executor_skill).
All data loaders use @st.cache_data for sub-second renders.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
PROCESSED_DIR = ROOT / "data" / "processed"
SNAPSHOT_DIR  = ROOT / "data" / "snapshots"
GEO_DIR       = ROOT / "data" / "geo"

# ── Period config (executor_skill updates this value) ─────────────────────────
DEFAULT_PERIOD = "2025-12"

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Auditoría MEF — Perú 2025",
    page_icon="🇵🇪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Fondo general */
[data-testid="stAppViewContainer"] { background: #0d1117; }
[data-testid="stSidebar"]          { background: #161b22; border-right: 1px solid #30363d; }

/* Métricas */
[data-testid="metric-container"] {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem;
}
[data-testid="metric-container"] label { color: #8b949e !important; font-size: 0.75rem; }
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #e6edf3 !important;
    font-size: 1.6rem;
    font-weight: 700;
}

/* Tabs */
button[data-baseweb="tab"] { font-size: 0.85rem; font-weight: 600; color: #8b949e; }
button[data-baseweb="tab"][aria-selected="true"] { color: #58a6ff; border-bottom-color: #58a6ff; }

/* Dataframes */
[data-testid="stDataFrame"] { border: 1px solid #30363d; border-radius: 8px; }

/* Sección histórica 1964 */
.era-1964 {
    background: #1c2128;
    border-left: 4px solid #f0883e;
    border-radius: 6px;
    padding: 1.2rem 1.5rem;
    margin: 1rem 0;
}
.era-2025 {
    background: #1c2128;
    border-left: 4px solid #3fb950;
    border-radius: 6px;
    padding: 1.2rem 1.5rem;
    margin: 1rem 0;
}

/* Badge semáforo */
.badge-ok     { color: #3fb950; font-weight: 700; }
.badge-warn   { color: #d29922; font-weight: 700; }
.badge-crit   { color: #f85149; font-weight: 700; }

h1, h2, h3 { color: #e6edf3 !important; }
p, li       { color: #c9d1d9; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_period(period: str) -> str:
    return period.strip().replace(" ", "_").replace("/", "-")


def _pen(v: float) -> str:
    return f"S/ {v:,.0f}"


def _norm_dep(name: str) -> str:
    """Normaliza un nombre (NFKD → ASCII → MAYÚSCULAS) para hacer joins robustos.

    Los datos del SIAF traen tildes (p.ej. ``APURÍMAC``, ``EDUCACIÓN``) mientras que
    el GeoJSON usa ASCII (``APURIMAC``); normalizar ambos lados da una cobertura 24/24.
    """
    if not isinstance(name, str):
        return ""
    return (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode()
        .strip()
        .upper()
    )


# Funciones de gasto consideradas "sociales" (clave normalizada → substring match)
SOCIAL_FUNC_KEYS = ("SALUD", "EDUCAC", "SANEAM", "PROTECC", "VIVIENDA")


# ── Data loaders (ALL must use @st.cache_data) ────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_kpis(period: str) -> dict:
    path = PROCESSED_DIR / f"kpis_{_safe_period(period)}.json"
    if not path.exists():
        return {"error": f"Ejecutar: python src/data_pipeline.py --period {period}"}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(ttl=3600, show_spinner=False)
def load_budget(period: str) -> pd.DataFrame:
    path = PROCESSED_DIR / f"budget_2025_{_safe_period(period)}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(ttl=3600, show_spinner=False)
def load_regional(period: str) -> pd.DataFrame:
    path = PROCESSED_DIR / f"region_agg_{_safe_period(period)}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(ttl=3600, show_spinner=False)
def load_worst_units(period: str) -> pd.DataFrame:
    path = PROCESSED_DIR / f"worst_units_{_safe_period(period)}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ocr_1964() -> dict:
    path = PROCESSED_DIR / "ocr_1964_results.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(ttl=3600, show_spinner=False)
def load_ocr_dataframes() -> dict[str, pd.DataFrame]:
    """Carga los DataFrames extraídos por PaddleOCR del documento 1964."""
    frames = {}
    for p in PROCESSED_DIR.glob("1964_*.parquet"):
        frames[p.stem] = pd.read_parquet(p)
    return frames


@st.cache_data(ttl=3600, show_spinner=False)
def load_pipeline_runs() -> list:
    path = PROCESSED_DIR / "pipeline_runs.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def load_evaluator_report(period: str) -> str:
    path = PROCESSED_DIR / f"evaluator_report_{_safe_period(period)}.md"
    if not path.exists():
        return "_Reporte del Evaluator Skill pendiente. Ejecutar `claude \"run evaluator_skill\"`._"
    return path.read_text(encoding="utf-8")


@st.cache_data(ttl=3600, show_spinner=False)
def load_geojson() -> dict:
    """GeoJSON de departamentos del Perú (propiedad de nombre: NOMBDEP, en ASCII)."""
    path = GEO_DIR / "peru_departamentos.geojson"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(ttl=3600, show_spinner=False)
def load_pobreza_2025() -> pd.DataFrame:
    """Pobreza monetaria 2025 por departamento (INEI — valores puntuales, Gráfico 4.7).

    Fuente: INEI, "Perú: Evolución de la Pobreza Monetaria 2016-2025" (publicado 05-may-2026).
    Se usa el estimado puntual por departamento del Gráfico 4.7; LIMA = Lima Metropolitana.
    La columna grupo_inei indica el grupo robusto del Cuadro 4.2: dentro de un mismo grupo las
    diferencias entre departamentos no son estadísticamente significativas.
    """
    path = GEO_DIR / "pobreza_monetaria_2025.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["dep"] = df["departamento"].map(_norm_dep)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def compute_social_stagnation(period: str) -> pd.DataFrame:
    """Estancamiento del gasto en funciones SOCIALES por departamento (100% SIAF 2025).

    Mide el % no ejecutado (1 − Devengado/PIM) restringido a las funciones de salud,
    educación, saneamiento, protección social y vivienda — un proxy del gasto social
    paralizado, derivado exclusivamente de los datos 2025.
    """
    df = load_budget(period)
    if df.empty or "funcion" not in df.columns or "region" not in df.columns:
        return pd.DataFrame()
    norm_f = df["funcion"].map(_norm_dep)
    mask = norm_f.apply(lambda f: any(k in f for k in SOCIAL_FUNC_KEYS))
    soc = df[mask].copy()
    if soc.empty:
        return pd.DataFrame()
    soc["dep"] = soc["region"].map(_norm_dep)
    agg = soc.groupby("dep", as_index=False).agg(
        PIM_social=("PIM", "sum"),
        dev_social=("devengado", "sum"),
    )
    agg["no_ejec_social_pct"] = (
        (1 - agg["dev_social"] / agg["PIM_social"]).where(agg["PIM_social"] > 0, 0.0) * 100
    )
    return agg


def _minmax(s: pd.Series) -> pd.Series:
    """Normaliza una serie a 0-1 (robusta a rango cero)."""
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi == lo:
        return pd.Series([0.0] * len(s), index=s.index)
    return (s - lo) / (hi - lo)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/Flag_of_Peru.svg/320px-Flag_of_Peru.svg.png", width=80)
    st.title("MEF Audit 🇵🇪")
    st.caption("Pipeline Multi-Agente · Gasto Subnacional")

    st.divider()
    selected_period = st.selectbox(
        "📅 Período fiscal",
        options=["2025-12", "2025-Q4", "2025-Q3", "2025-Q2", "2025-Q1", "2025"],
        index=0,
        help="El Executor Skill actualizará los datos al cambiar el período.",
    )
    st.caption(f"Período activo: `{selected_period}`")

    st.divider()
    st.markdown("**CLI Quick Commands**")
    st.code(f'claude "run executor_skill for period {selected_period}"', language="bash")

    st.divider()
    kpis = load_kpis(selected_period)
    if "is_mock" in kpis:
        st.warning("⚠️ Usando datos mock")
    elif "error" in kpis:
        st.error(kpis["error"])
    else:
        st.success("✅ Datos reales cargados")


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Resumen Ejecutivo",
    "🗺️ Distribución Territorial",
    "🚨 Hall of Shame",
    "🤖 Audit Log",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Executive Macro Summary & Dual-Era Opening Dashboard
# ════════════════════════════════════════════════════════════════════════════════

with tab1:
    st.header("Resumen Ejecutivo — Doble Era Fiscal")
    st.caption("Las dos eras se presentan de forma independiente. No se realizan comparaciones cruzadas.")

    # ── Sección 2025 ──────────────────────────────────────────────────────────
    st.markdown('<div class="era-2025">', unsafe_allow_html=True)
    st.subheader("🟢 Período Fiscal 2025 — Gobiernos Subnacionales")

    with st.spinner("Cargando KPIs 2025…"):
        kpis = load_kpis(selected_period)

    if "error" in kpis:
        st.error(kpis["error"])
    else:
        col1, col2, col3, col4 = st.columns(4)

        total_pim = kpis.get("total_PIM", 0)
        total_dev = kpis.get("total_devengado", 0)
        avance    = kpis.get("avance_nacional_pct", 0)
        paraliz   = kpis.get("total_saldo_paralizado", 0)

        col1.metric("💰 PIM Total Subnacional", _pen(total_pim),
                    help="Presupuesto Institucional Modificado — gobiernos regionales y locales con PIM > S/ 10M")
        col2.metric("✅ Total Devengado", _pen(total_dev))
        col3.metric("📈 Avance Nacional", f"{avance:.1f}%",
                    delta=f"{avance - 60:.1f}% vs meta 60%",
                    delta_color="normal")
        col4.metric("🧊 Capital Paralizado", _pen(paraliz),
                    help="Saldo No Devengado = PIM − Devengado")

        # AI Advisor narrative
        st.divider()
        st.markdown("#### 🤖 Análisis IA — Cuellos de Botella Fiscales")

        if avance < 40:
            nivel_riesgo = "**CRÍTICO**"
            color_msg    = "🔴"
        elif avance < 70:
            nivel_riesgo = "**MODERADO**"
            color_msg    = "⚠️"
        else:
            nivel_riesgo = "**ACEPTABLE**"
            color_msg    = "✅"

        st.markdown(f"""
{color_msg} Nivel de riesgo: {nivel_riesgo}

El análisis del período **{selected_period}** revela que los gobiernos subnacionales
peruanos con presupuestos superiores a S/ 10 millones registran un avance de ejecución
de **{avance:.1f}%**, con un capital paralizado de **{_pen(paraliz)}** que no ha sido
devengado. Este capital inmovilizado representa una oportunidad perdida de inversión
pública en infraestructura, salud y educación regional.

Los principales cuellos de botella identificados incluyen procesos de contratación
incompletos, ausencia de expedientes técnicos aprobados y limitaciones institucionales
en gobiernos locales con menor capacidad de gestión. Las regiones con avance inferior
al 40% requieren intervención prioritaria de la Contraloría General de la República.
        """)

    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()

    # ── Sección 1964 ──────────────────────────────────────────────────────────
    # NOTA: Esta sección es completada por Camila (P2) con los resultados OCR
    st.markdown('<div class="era-1964">', unsafe_allow_html=True)
    st.subheader("🟠 Archivo Histórico 1964 — Ministerio de Hacienda y Comercio")
    st.caption("Fuente: Presupuesto, Balance y Cuenta General de la República — Digitalizado vía PaddleOCR (≥15 páginas)")

    with st.spinner("Cargando resultados OCR 1964…"):
        ocr_data = load_ocr_1964()
        ocr_frames = load_ocr_dataframes()

    if not ocr_data and not ocr_frames:
        st.info(
            "📄 Sección pendiente — Camila (P2) completará esta sección con los resultados "
            "del pipeline PaddleOCR sobre el documento 1964.\n\n"
            "**Para activar:** `python src/ocr_engine.py --pdf data/raw_pdfs/hacienda_1964.pdf`"
        )
        # Placeholder visual mientras P2 completa su parte
        st.markdown("""
        **Estructura esperada de esta sección (P2 la completa):**
        - Conclusiones de texto extraídas de las 15+ páginas OCR
        - Gráfico 1: Distribución de categorías de ingresos/gastos 1964
        - Gráfico 2: Distribución por departamentos o ramos del presupuesto 1964
        """)
    else:
        # ── Conclusiones textuales ────────────────────────────────────────────
        if "summary" in ocr_data:
            st.markdown("#### 📝 Conclusiones del Análisis Histórico")
            st.markdown(ocr_data["summary"])

        if "stats" in ocr_data:
            stats = ocr_data["stats"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Páginas procesadas", stats.get("pages_processed", 0))
            c2.metric("Categorías identificadas", stats.get("categories_found", 0))
            c3.metric("Ítems cuantificados", stats.get("items_quantified", 0))

        # ── Gráfico 1 (completado por P2) ─────────────────────────────────────
        if "1964_categories_df" in ocr_frames:
            st.markdown("#### 📊 Gráfico 1 — Categorías Presupuestarias 1964")
            cat_df = ocr_frames["1964_categories_df"]
            # P2 definirá las columnas exactas según el OCR
            if not cat_df.empty:
                fig1 = px.bar(
                    cat_df,
                    x=cat_df.columns[0],
                    y=cat_df.columns[1] if len(cat_df.columns) > 1 else cat_df.columns[0],
                    title="Distribución de Categorías — Presupuesto 1964",
                    color_discrete_sequence=["#f0883e"],
                    template="plotly_dark",
                )
                fig1.update_layout(paper_bgcolor="#1c2128", plot_bgcolor="#1c2128")
                st.plotly_chart(fig1, use_container_width=True)

        # ── Gráfico 2 (completado por P2) ─────────────────────────────────────
        if "1964_departments_df" in ocr_frames:
            st.markdown("#### 📊 Gráfico 2 — Distribución Departamental 1964")
            dep_df = ocr_frames["1964_departments_df"]
            if not dep_df.empty:
                fig2 = px.pie(
                    dep_df,
                    names=dep_df.columns[0],
                    values=dep_df.columns[1] if len(dep_df.columns) > 1 else None,
                    title="Repartición por Departamentos/Ramos — 1964",
                    color_discrete_sequence=px.colors.sequential.Oranges,
                    template="plotly_dark",
                )
                fig2.update_layout(paper_bgcolor="#1c2128", font=dict(color="#e6edf3"))
                st.plotly_chart(fig2, use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Territorial Distribution & Geospatial Analysis (2025 ONLY)
# ════════════════════════════════════════════════════════════════════════════════

with tab2:
    st.header("🗺️ Distribución Territorial — Perú 2025")
    st.caption("Análisis geoespacial de ejecución presupuestal por departamento · Solo datos 2025")

    # try/except: Streamlit ejecuta todos los tabs en una pasada; un fallo aquí no
    # debe tumbar los Tabs 3 y 4.
    try:
        with st.spinner("Cargando datos territoriales…"):
            df_regional = load_regional(selected_period)
            geojson = load_geojson()

        if df_regional.empty:
            st.info(f"Sin datos para período {selected_period}. Ejecutar el pipeline primero.")
        elif not geojson:
            st.warning("Falta el GeoJSON de departamentos en `data/geo/peru_departamentos.geojson`.")
        else:
            # Clave de join normalizada (SIAF con tildes ↔ GeoJSON en ASCII)
            df_geo = df_regional.copy()
            df_geo["dep"] = df_geo["region"].map(_norm_dep)

            geo_deps = {f["properties"]["NOMBDEP"].strip().upper() for f in geojson["features"]}
            matched = set(df_geo["dep"]) & geo_deps
            if len(matched) < df_geo["dep"].nunique():
                faltan = sorted(set(df_geo["dep"]) - geo_deps)
                st.warning(f"⚠️ Solo {len(matched)} departamentos cruzaron con el mapa. Sin geometría: {faltan}")

            # ── KPIs territoriales ────────────────────────────────────────────
            col1, col2, col3 = st.columns(3)
            col1.metric("Departamentos analizados", df_geo["dep"].nunique())
            worst_reg = df_geo.nsmallest(1, "avance_pct").iloc[0]
            col2.metric("Más rezagado", worst_reg["region"],
                        delta=f"{worst_reg['avance_pct']:.1f}%", delta_color="inverse")
            best_reg = df_geo.nlargest(1, "avance_pct").iloc[0]
            col3.metric("Líder", best_reg["region"], delta=f"{best_reg['avance_pct']:.1f}%")

            # ── MAPA 1 — Desempeño de ejecución 2025 ──────────────────────────
            st.subheader("Mapa 1 · Desempeño de ejecución por departamento")
            fig_map1 = px.choropleth(
                df_geo,
                geojson=geojson,
                locations="dep",
                featureidkey="properties.NOMBDEP",
                color="avance_pct",
                color_continuous_scale=["#f85149", "#d29922", "#3fb950"],
                range_color=(0, 100),
                hover_name="region",
                hover_data={"dep": False, "PIM": ":,.0f", "devengado": ":,.0f",
                            "avance_pct": ":.1f", "saldo_no_devengado": ":,.0f"},
                labels={"avance_pct": "Avance (%)"},
                title=f"Avance de ejecución (Devengado/PIM) — {selected_period}",
            )
            fig_map1.update_geos(fitbounds="locations", visible=False)
            fig_map1.update_layout(paper_bgcolor="#0d1117", font_color="#e6edf3",
                                   margin=dict(l=0, r=0, t=50, b=0), height=520)
            st.plotly_chart(fig_map1, use_container_width=True)

            with st.expander("Ver ranking por departamento (barra)"):
                fig_bar = px.bar(
                    df_geo.sort_values("avance_pct"),
                    x="avance_pct", y="region", orientation="h", color="avance_pct",
                    color_continuous_scale=["#f85149", "#d29922", "#3fb950"], range_color=(0, 100),
                    title=f"Avance de ejecución por departamento — {selected_period}",
                    labels={"avance_pct": "Avance (%)", "region": "Departamento"},
                    template="plotly_dark",
                )
                fig_bar.add_vline(x=60, line_dash="dash", line_color="white",
                                  annotation_text="Meta 60%", annotation_position="top right")
                fig_bar.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
                                      height=600, showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True)

            # ── MAPA 2 — Riesgo social: estancamiento × vulnerabilidad ────────
            st.subheader("Mapa 2 · Riesgo social — estancamiento del gasto social × pobreza 2025")
            st.caption(
                "Estancamiento social = % no ejecutado en salud, educación, saneamiento, protección "
                "social y vivienda (SIAF 2025). Vulnerabilidad = pobreza monetaria 2025 por departamento "
                "(INEI, valores puntuales del Gráfico 4.7; las diferencias dentro de un mismo grupo no son "
                "estadísticamente significativas)."
            )

            social  = compute_social_stagnation(selected_period)
            pobreza = load_pobreza_2025()
            if social.empty or pobreza.empty:
                st.info("No hay datos de funciones sociales o de pobreza para construir el índice de riesgo.")
            else:
                risk = social.merge(
                    pobreza[["dep", "pobreza_pct", "grupo_inei"]],
                    on="dep", how="inner",
                ).merge(df_geo[["dep", "region", "avance_pct", "PIM"]], on="dep", how="left")
                risk["riesgo_social"] = _minmax(risk["no_ejec_social_pct"]) * _minmax(risk["pobreza_pct"])

                fig_map2 = px.choropleth(
                    risk,
                    geojson=geojson,
                    locations="dep",
                    featureidkey="properties.NOMBDEP",
                    color="riesgo_social",
                    color_continuous_scale="Inferno",
                    hover_name="region",
                    hover_data={"dep": False, "no_ejec_social_pct": ":.1f",
                                "pobreza_pct": ":.1f", "riesgo_social": ":.2f"},
                    labels={"riesgo_social": "Índice de riesgo"},
                    title="Índice de riesgo social (0–1) — mayor = más crítico",
                )
                fig_map2.update_geos(fitbounds="locations", visible=False)
                fig_map2.update_layout(paper_bgcolor="#0d1117", font_color="#e6edf3",
                                       margin=dict(l=0, r=0, t=50, b=0), height=520)
                st.plotly_chart(fig_map2, use_container_width=True)

                # ── Cuadrante: la "correlación" estancamiento ↔ pobreza ───────
                st.subheader("Cuadrante de auditoría — pobreza vs avance")
                med_pob = risk["pobreza_pct"].median()
                med_av  = risk["avance_pct"].median()
                fig_q = px.scatter(
                    risk, x="pobreza_pct", y="avance_pct", size="PIM",
                    color="riesgo_social", color_continuous_scale="Inferno",
                    hover_name="region", size_max=40,
                    labels={"pobreza_pct": "Pobreza monetaria 2025 (%)",
                            "avance_pct": "Avance de ejecución (%)"},
                    title="Alta pobreza + bajo avance = prioridad de auditoría",
                    template="plotly_dark",
                )
                fig_q.add_vline(x=med_pob, line_dash="dot", line_color="#8b949e")
                fig_q.add_hline(y=med_av, line_dash="dot", line_color="#8b949e")
                fig_q.add_annotation(x=risk["pobreza_pct"].max(), y=risk["avance_pct"].min(),
                                     text="⚠️ Zona crítica", showarrow=False,
                                     font=dict(color="#f85149", size=13), xanchor="right")
                fig_q.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22", height=480)
                st.plotly_chart(fig_q, use_container_width=True)

                # ── Top departamentos prioritarios ────────────────────────────
                st.markdown("**Departamentos prioritarios (mayor índice de riesgo social):**")
                top = risk.sort_values("riesgo_social", ascending=False).head(8)
                st.dataframe(
                    top[["region", "pobreza_pct", "no_ejec_social_pct", "avance_pct", "riesgo_social"]]
                    .rename(columns={"region": "Departamento", "pobreza_pct": "Pobreza 2025 (%)",
                                     "no_ejec_social_pct": "No ejec. social (%)",
                                     "avance_pct": "Avance (%)", "riesgo_social": "Índice riesgo"}),
                    use_container_width=True, hide_index=True,
                )

    except Exception as e:  # noqa: BLE001 — proteger los demás tabs ante cualquier fallo
        st.error(f"Error al construir la pestaña territorial: {e}")
        st.caption("La app sigue operativa; revisa los insumos (parquet / geojson / pobreza).")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Budget "Hall of Shame" & Anomaly Explorer (2025 ONLY)
# ════════════════════════════════════════════════════════════════════════════════

with tab3:
    st.header("🚨 Hall of Shame — Peores Unidades Ejecutoras 2025")
    st.caption("Entidades con PIM > S/ 10M y menor avance de ejecución · Solo datos 2025")

    with st.spinner("Cargando unidades críticas…"):
        df_worst = load_worst_units(selected_period)

    if df_worst.empty:
        st.info(f"Sin datos para período {selected_period}.")
    else:
        # Filtros interactivos
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            max_avance = st.slider("Mostrar entidades con avance menor a:", 0, 100, 50, step=5)
        with col_f2:
            if "funcion" in df_worst.columns:
                funciones = ["Todas"] + sorted(df_worst["funcion"].dropna().unique().tolist())
                funcion_sel = st.selectbox("Filtrar por función", funciones)
            else:
                funcion_sel = "Todas"

        df_display = df_worst[df_worst["avance_pct"] <= max_avance]
        if funcion_sel != "Todas" and "funcion" in df_display.columns:
            df_display = df_display[df_display["funcion"] == funcion_sel]

        st.metric("Unidades en riesgo", len(df_display),
                  help="Entidades con PIM > S/ 10M y avance por debajo del umbral seleccionado")

        # Tabla interactiva
        display_cols = [c for c in ["region", "entidad", "nivel_gobierno", "funcion",
                                     "PIM", "devengado", "avance_pct", "saldo_no_devengado"]
                        if c in df_display.columns]
        st.dataframe(
            df_display[display_cols].style
                .background_gradient(subset=["avance_pct"] if "avance_pct" in display_cols else [],
                                     cmap="RdYlGn", vmin=0, vmax=100)
                .format({"PIM": "{:,.0f}", "devengado": "{:,.0f}",
                         "avance_pct": "{:.1f}%", "saldo_no_devengado": "{:,.0f}"}),
            use_container_width=True,
            height=400,
        )

        # Breakdown por función (P3 puede enriquecer)
        if "funcion" in df_display.columns and not df_display.empty:
            fn_agg = (
                df_display.groupby("funcion")
                .agg(saldo=("saldo_no_devengado", "sum"), entidades=("entidad", "count"))
                .reset_index()
                .sort_values("saldo", ascending=False)
            )
            fig_fn = px.bar(
                fn_agg,
                x="funcion",
                y="saldo",
                color="entidades",
                title="Capital Paralizado por Función de Gasto",
                labels={"saldo": "Saldo No Devengado (S/)", "funcion": "Función"},
                template="plotly_dark",
            )
            fig_fn.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22")
            st.plotly_chart(fig_fn, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — Multi-Agent Audit Log & Live Playground (2025 ONLY)
# ════════════════════════════════════════════════════════════════════════════════

with tab4:
    st.header("🤖 Audit Log Multi-Agente & Playground")
    st.caption("Registro de ejecuciones del pipeline y reporte del Evaluator Skill · Solo datos 2025")

    # ── Evaluator report ──────────────────────────────────────────────────────
    st.subheader("📋 Reporte del Evaluator Skill")
    with st.spinner("Cargando reporte de auditoría…"):
        report_md = load_evaluator_report(selected_period)
    st.markdown(report_md)

    st.divider()

    # ── Pipeline run log ──────────────────────────────────────────────────────
    st.subheader("🕑 Historial de Ejecuciones del Pipeline")
    with st.spinner("Cargando historial…"):
        runs = load_pipeline_runs()

    if not runs:
        st.info("Sin ejecuciones registradas aún. Lanzar el pipeline para ver el historial.")
    else:
        runs_df = pd.DataFrame(runs)
        show_cols = [c for c in ["period", "status", "source", "rows", "duration_s", "ended_at"]
                     if c in runs_df.columns]
        st.dataframe(runs_df[show_cols] if show_cols else runs_df,
                     use_container_width=True, height=240, hide_index=True)

        # Cronología de ejecuciones (scatter sobre eje temporal; robusto a duraciones dispares).
        time_col = "ended_at" if "ended_at" in runs_df.columns else (
            "timestamp" if "timestamp" in runs_df.columns else None)
        if time_col and "period" in runs_df.columns:
            tl = runs_df.copy()
            tl[time_col] = pd.to_datetime(tl[time_col], errors="coerce")
            tl = tl.dropna(subset=[time_col])
            if not tl.empty:
                fig_runs = px.scatter(
                    tl, x=time_col, y="period",
                    color="source" if "source" in tl.columns else None,
                    size="rows" if "rows" in tl.columns else None,
                    color_discrete_map={"real": "#3fb950", "mock": "#d29922"},
                    hover_data=[c for c in ["status", "duration_s", "rows"] if c in tl.columns],
                    title="Cronología de ejecuciones del pipeline",
                    labels={time_col: "Fecha/hora de ejecución", "period": "Período"},
                    template="plotly_dark",
                )
                fig_runs.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22", height=300)
                st.plotly_chart(fig_runs, use_container_width=True)

    st.divider()

    # ── Live playground ───────────────────────────────────────────────────────
    st.subheader("🎮 Playground — Cambio de Período en Vivo")
    st.markdown("Lanza el Executor Skill directamente desde el dashboard para refrescar los datos.")

    play_period = st.selectbox(
        "Seleccionar período para re-procesar:",
        ["2025-12", "2025-Q4", "2025-Q3", "2025-Q2", "2025-Q1"],
        key="playground_period",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.code(f'claude "run executor_skill for period {play_period}"', language="bash")
        st.caption("Copiar y ejecutar en terminal con Claude Code CLI instalado")
    with col_b:
        st.code(f'python src/data_pipeline.py --period {play_period}', language="bash")
        st.caption("Alternativa: ejecutar pipeline directamente")

    if st.button("🔄 Limpiar caché y recargar datos", type="primary"):
        st.cache_data.clear()
        st.success("Caché limpiada. Recargando…")
        st.rerun()
