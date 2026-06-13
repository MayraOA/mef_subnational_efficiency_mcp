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
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
PROCESSED_DIR = ROOT / "data" / "processed"
SNAPSHOT_DIR  = ROOT / "data" / "snapshots"

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


# ── Data loaders (ALL must use @st.cache_data) ────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_kpis(period: str) -> dict:
    path = PROCESSED_DIR / f"kpis_{_safe_period(period)}.json"
    if not path.exists():
        return {"error": f"Ejecutar: python src/data_pipeline.py --period {period}"}
    return json.loads(path.read_text())


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
    return json.loads(path.read_text())


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
        return json.loads(path.read_text())
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def load_evaluator_report(period: str) -> str:
    path = PROCESSED_DIR / f"evaluator_report_{_safe_period(period)}.md"
    if not path.exists():
        return "_Reporte del Evaluator Skill pendiente. Ejecutar `claude \"run evaluator_skill\"`._"
    return path.read_text()


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
        if "categories_df" in ocr_frames:
            st.markdown("#### 📊 Gráfico 1 — Categorías Presupuestarias 1964")
            cat_df = ocr_frames["categories_df"]
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
        if "departments_df" in ocr_frames:
            st.markdown("#### 📊 Gráfico 2 — Distribución Departamental 1964")
            dep_df = ocr_frames["departments_df"]
            if not dep_df.empty:
                fig2 = px.pie(
                    dep_df,
                    names=dep_df.columns[0],
                    values=dep_df.columns[1] if len(dep_df.columns) > 1 else None,
                    title="Repartición por Departamentos/Ramos — 1964",
                    color_discrete_sequence=px.colors.sequential.Oranges,
                    template="plotly_dark",
                )
                fig2.update_layout(paper_bgcolor="#1c2128")
                st.plotly_chart(fig2, use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Territorial Distribution & Geospatial Analysis (2025 ONLY)
# ════════════════════════════════════════════════════════════════════════════════

with tab2:
    st.header("🗺️ Distribución Territorial — Perú 2025")
    st.caption("Análisis geoespacial de ejecución presupuestal por departamento · Solo datos 2025")

    # ── COMPLETAR POR P3 ──────────────────────────────────────────────────────
    with st.spinner("Cargando datos territoriales…"):
        df_regional = load_regional(selected_period)

    if df_regional.empty:
        st.info(f"Sin datos para período {selected_period}. Ejecutar el pipeline primero.")
    else:
        # KPIs territoriales rápidos
        col1, col2, col3 = st.columns(3)
        col1.metric("Regiones analizadas", len(df_regional))
        worst_reg = df_regional.nsmallest(1, "avance_pct").iloc[0] if not df_regional.empty else None
        if worst_reg is not None:
            col2.metric("Región más rezagada", worst_reg.get("region", "—"),
                        delta=f"{worst_reg.get('avance_pct', 0):.1f}%", delta_color="inverse")
        best_reg = df_regional.nlargest(1, "avance_pct").iloc[0] if not df_regional.empty else None
        if best_reg is not None:
            col3.metric("Región líder", best_reg.get("region", "—"),
                        delta=f"{best_reg.get('avance_pct', 0):.1f}%")

        # Gráfico base — P3 puede enriquecer con mapa geoespacial
        fig_bar = px.bar(
            df_regional.sort_values("avance_pct", ascending=True),
            x="avance_pct",
            y="region",
            orientation="h",
            color="avance_pct",
            color_continuous_scale=["#f85149", "#d29922", "#3fb950"],
            title=f"Avance de Ejecución por Región — {selected_period}",
            labels={"avance_pct": "Avance (%)", "region": "Región"},
            template="plotly_dark",
        )
        fig_bar.add_vline(x=60, line_dash="dash", line_color="white",
                          annotation_text="Meta 60%", annotation_position="top right")
        fig_bar.update_layout(
            paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
            height=600, showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        # Heatmap saldo paralizado — P3 puede añadir capa de vulnerabilidad social
        fig_heat = px.scatter(
            df_regional,
            x="avance_pct",
            y="saldo_no_devengado",
            size="PIM",
            color="avance_pct",
            color_continuous_scale=["#f85149", "#d29922", "#3fb950"],
            hover_name="region",
            title=f"Capital Paralizado vs Avance — {selected_period}",
            labels={"avance_pct": "Avance (%)", "saldo_no_devengado": "Saldo No Devengado (S/)"},
            template="plotly_dark",
        )
        fig_heat.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22")
        st.plotly_chart(fig_heat, use_container_width=True)

        st.markdown("---")
        st.markdown("*P3: Aquí agregar mapa geoespacial con geopandas/folium y heatmap de vulnerabilidad social.*")


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
        st.dataframe(runs_df, use_container_width=True, height=300)

        fig_runs = px.timeline(
            runs_df if "timestamp" in runs_df.columns else pd.DataFrame(),
            x_start="timestamp" if "timestamp" in runs_df.columns else None,
            y="period" if "period" in runs_df.columns else None,
            title="Línea de Tiempo de Ejecuciones",
            template="plotly_dark",
        ) if "timestamp" in runs_df.columns else None

        if fig_runs:
            fig_runs.update_layout(paper_bgcolor="#0d1117")
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
