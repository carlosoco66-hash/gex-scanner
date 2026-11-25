import streamlit as st
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    CA_TZ = ZoneInfo("America/Los_Angeles")
except Exception:
    CA_TZ = timezone(timedelta(hours=-8))


def now_ca_str():
    return datetime.now(CA_TZ).strftime("%H:%M:%S")


import importlib
import json
import plotly.graph_objects as go
import streamlit.components.v1 as components
import gex_engine

# Forzar recarga en caliente de gex_engine para que Streamlit Cloud detecte funciones nuevas sin reiniciar contenedor
importlib.reload(gex_engine)

get_live_spot_price = gex_engine.get_live_spot_price
get_options_expirations = gex_engine.get_options_expirations
fetch_and_calculate_all = gex_engine.fetch_and_calculate_all
calculate_levels = gex_engine.calculate_levels
fetch_live_flow = getattr(
    gex_engine,
    "fetch_live_flow",
    lambda ticker, api_key: {"summary": {}, "gex": {}, "levels": {}, "strikes_df": pd.DataFrame(), "error": "Actualiza gex_engine.py en GitHub"},
)


def _fallback_tactical_metrics(df_gex, df_raw, spot, live_flow=None):
    return {
        "net_vex": float(df_gex["net_vex"].sum()) if "net_vex" in df_gex.columns else 0.0,
        "net_chex": float(df_gex["net_chex"].sum()) if "net_chex" in df_gex.columns else 0.0,
        "atm_iv": 0.35,
        "em_1d": spot * 0.35 * (1.0 / 252.0) ** 0.5,
        "em_1d_pct": 0.35 * ((1.0 / 252.0) ** 0.5) * 100.0,
        "em_low_1d": spot * (1.0 - 0.35 * (1.0 / 252.0) ** 0.5),
        "em_high_1d": spot * (1.0 + 0.35 * (1.0 / 252.0) ** 0.5),
        "em_exp": spot * 0.35 * (7.0 / 365.0) ** 0.5,
        "em_exp_pct": 0.35 * ((7.0 / 365.0) ** 0.5) * 100.0,
        "min_dte": 7,
        "pin_strike": spot,
        "pin_score": 50.0,
        "pin_label": "MEDIO",
        "call_wall": float(df_gex.loc[df_gex["call_gex"].idxmax()]["strike"]) if ("call_gex" in df_gex.columns and not df_gex.empty) else spot,
        "put_wall": float(df_gex.loc[df_gex["put_gex"].idxmin()]["strike"]) if ("put_gex" in df_gex.columns and not df_gex.empty) else spot,
        "pin_df": pd.DataFrame(),
        "radar_df": pd.DataFrame(),
        "total_call_prem_vol": 0.0,
        "total_put_prem_vol": 0.0,
    }


calculate_tactical_metrics = getattr(gex_engine, "calculate_tactical_metrics", _fallback_tactical_metrics)
get_hv_and_gap_context = getattr(
    gex_engine,
    "get_hv_and_gap_context",
    lambda ticker: {"hv_20d": 0.0, "prev_close": 0.0, "today_open": 0.0},
)
get_macro_climate = getattr(
    gex_engine,
    "get_macro_climate",
    lambda api_key: {"vix": 0.0, "vix_chg": 0.0, "vvix": 0.0, "vvix_chg": 0.0, "move": 0.0, "move_chg": 0.0, "indices_gex": {}},
)
fetch_discord_whales = getattr(
    gex_engine,
    "fetch_discord_whales",
    lambda bot_token, channel_ids, limit=50: {"golden": [], "sweeps": [], "dark_pool": [], "errors": {}},
)

# Funciones con caché para optimizar consultas de la API (60s para datos en vivo)
@st.cache_data(ttl=60)
def cached_discord_whales(bot_token, golden_id, sweeps_id, darkpool_id, limit=50):
    return fetch_discord_whales(
        bot_token,
        {"golden": golden_id, "sweeps": sweeps_id, "dark_pool": darkpool_id},
        limit=limit,
    )

@st.cache_data(ttl=300)
def cached_macro_climate(api_key):
    return get_macro_climate(api_key)

@st.cache_data(ttl=1800)
def cached_hv_and_gap_context(ticker):
    return get_hv_and_gap_context(ticker)

@st.cache_data(ttl=300)
def cached_options_expirations(ticker, api_key):
    return get_options_expirations(ticker, api_key)

@st.cache_data(ttl=60)
def cached_fetch_and_calculate_all(ticker, selected_expirations, spot_manual, api_key, mode):
    return fetch_and_calculate_all(ticker, selected_expirations, spot_manual, api_key, mode=mode)

@st.cache_data(ttl=60)
def cached_fetch_live_flow(ticker, api_key):
    return fetch_live_flow(ticker, api_key)


# Configuración de página
st.set_page_config(page_title="Gamma Scanner Options", page_icon="📊", layout="wide")

# Cargar API key de forma segura sin romper la ejecución local
try:
    default_api_key = st.secrets.get("FLASHALPHA_API_KEY", "teN86PS3lu9n97uDhy2ndASCBWuCjZiByr0j8YEw")
except Exception:
    default_api_key = "teN86PS3lu9n97uDhy2ndASCBWuCjZiByr0j8YEw"

# CSS para igualar los estilos premium oscuros y fuentes
st.markdown("""
<style>
    /* Estilos generales oscuros */
    .reportview-container {
        background-color: #0d0e15;
    }
    /* Estilos para el sidebar */
    section[data-testid="stSidebar"] {
        background-color: #161b22;
        border-right: 1px solid #30363d;
    }
    /* Estilos de las cajas métricas */
    .metric-box {
        margin-bottom: 25px;
    }
    .metric-label {
        color: #8b949e !important;
        font-size: 14px !important;
        margin-bottom: 2px !important;
    }
    .metric-value {
        color: #ffffff !important;
        font-size: 38px !important;
        font-weight: 700 !important;
        margin-bottom: 0px !important;
        font-family: 'Outfit', 'Inter', sans-serif;
    }
    /* Cabeceras */
    .custom-header {
        color: #ffffff;
        font-weight: 700;
        margin-top: 25px;
        margin-bottom: 15px;
    }
    
    /* Hack de inputs de Streamlit */
    div[data-baseweb="input"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
        color: white !important;
    }
    div[data-baseweb="select"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
    }
    button[kind="secondary"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
        color: white !important;
    }
    button[kind="primary"] {
        background-color: #ff4b4b !important;
        border: none !important;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar - Configuración y Personalización
st.sidebar.title("Configuración")
api_key = st.sidebar.text_input("FlashAlpha API Key", value=default_api_key, type="password")

# Selector de modo de cálculo
calc_mode = st.sidebar.selectbox(
    "Modo de Exposición", 
    ["USD (Dinero Real)", "Acciones (Fórmula del otro Bot)"]
)
engine_mode = "USD" if calc_mode == "USD (Dinero Real)" else "Acciones"

# Deslizador de rango de visualización
rango_pct = st.sidebar.slider(
    "Rango de Strikes (±%)",
    min_value=5,
    max_value=150,
    value=15,
    step=5
)

# Botón para forzar actualización (limpia la caché y fuerza recálculo en sesión)
if st.sidebar.button("🔄 Forzar Actualización", use_container_width=True):
    st.cache_data.clear()
    st.session_state.force_refresh = True
    st.sidebar.success("Caché limpiada. Actualizando datos en vivo...")
    st.rerun()


# Helper para formatear millones
def format_millions(val, mode="USD"):
    if val is None or pd.isna(val) or val == 0:
        return "$0" if mode == "USD" else "0$"
    is_neg = val < 0
    abs_val = abs(val)
    
    if abs_val >= 1e9:
        s = f"{abs_val/1e9:.2f}B"
    elif abs_val >= 1e6:
        s = f"{abs_val/1e6:.1f}M"
    elif abs_val >= 1e3:
        s = f"{abs_val/1e3:.0f}K"
    else:
        s = f"{abs_val:.2f}" if mode == "USD" else f"{abs_val:.0f}"
        
    if mode == "USD":
        prefix = "-$" if is_neg else "$"
        return f"{prefix}{s}"
    else:
        prefix = "-" if is_neg else ""
        return f"{prefix}{s}$"

# Helper para mapear ticker a TradingView (con detección automática de exchange para stocks y ETFs)
@st.cache_data(ttl=86400)
def get_tv_symbol(ticker):
    ticker = ticker.upper().strip()
    if not ticker:
        return "AMEX:SPY"
    if ":" in ticker:
        return ticker
    if ticker in {"BTC", "ETH", "SOL", "ADA", "XRP", "DOGE"}:
        return f"COINBASE:{ticker}USD"
    if ticker in {
        "SPY", "IWM", "DIA", "GLD", "SLV", "UNG", "USO", "IYW", "EWT", "EEM", "EFA", "EWZ",
        "FXI", "KWEB", "XLF", "XLE", "XLK", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
        "XLC", "XBI", "XOP", "KRE", "ARKK", "SOXL", "SOXS", "UVXY", "VXX", "HYG", "LQD",
        "BITO", "GDX", "GDXJ", "VTI", "VOO", "SCHD", "RSP",
    }:
        return f"AMEX:{ticker}"
    if ticker in {
        "QQQ", "TQQQ", "SQQQ", "SMH", "SOXX", "TLT", "IEF", "SHY", "IBIT", "ETHA",
        "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "NFLX", "GOOG", "GOOGL",
        "MU", "AVGO", "MRVL", "MRNA", "WDAY", "CRWV", "PLTR", "COIN", "MSTR", "INTC",
        "QCOM", "ARM", "COST", "PYPL", "PANW", "CRWD", "HOOD", "SOFI", "APP", "SMCI",
        "ASML", "LRCX", "KLAC", "AMAT", "ADBE", "SHOP", "MARA", "RIOT", "CLSK", "IREN",
        "CAR", "EOLS", "PEP", "SBUX", "CSCO", "TXN", "ISRG", "BKNG", "MELI", "PDD", "JD",
        "RIVN", "DKNG", "AFRM", "DDOG", "ZS", "MDB", "FTNT", "ENPH", "FSLR", "TTD",
    }:
        return f"NASDAQ:{ticker}"

    # Detección automática del exchange real vía yfinance para cualquier otro stock o ETF
    try:
        import yfinance as yf
        t_yf = yf.Ticker(ticker)
        exch = ""
        try:
            fi = t_yf.fast_info
            exch = str(fi.get("exchange", "") or "").upper()
        except Exception:
            pass
        if not exch:
            try:
                exch = str((t_yf.info or {}).get("exchange", "") or "").upper()
            except Exception:
                pass

        if exch in {"NMS", "NGM", "NCM", "NAS", "NASDAQ"}:
            return f"NASDAQ:{ticker}"
        if exch in {"NYQ", "NYS", "NYSE"}:
            return f"NYSE:{ticker}"
        if exch in {"PCX", "ASE", "ARC", "AMEX", "ARCA"}:
            return f"AMEX:{ticker}"
    except Exception:
        pass

    # Respaldo seguro: símbolo limpio sin prefijo forzado para que TradingView lo resuelva solo
    return ticker

# --- HELPERS PARA GRÁFICOS PLOTLY DE BARRAS ---
def plot_gex_plotly(df_gex, spot, mode="USD", pct=15):
    df_plot = df_gex.copy()
    if 'gex' in df_plot.columns:
        df_plot = df_plot.rename(columns={'gex': 'net_gex'})
        
    # Filtrar dinámicamente según el porcentaje seleccionado
    df_plot = df_plot[(df_plot['strike'] >= spot * (1 - pct/100.0)) & (df_plot['strike'] <= spot * (1 + pct/100.0))].copy()
    
    fig = go.Figure()
    
    # Barras de Calls (Arriba) - Blancas
    fig.add_trace(go.Bar(
        x=df_plot['strike'], y=df_plot['call_gex'],
        name='GEX Calls (Arriba)',
        marker_color='white',
        opacity=0.85
    ))
    
    # Barras de Puts (Abajo) - Azules
    fig.add_trace(go.Bar(
        x=df_plot['strike'], y=df_plot['put_gex'],
        name='GEX Puts (Abajo)',
        marker_color='#348feb',
        opacity=0.85
    ))
    
    # Línea neta - Roja
    fig.add_trace(go.Scatter(
        x=df_plot['strike'], y=df_plot['net_gex'],
        mode='lines+markers',
        line=dict(color='#ff003c', width=2),
        marker=dict(size=4, color='#ff003c'),
        name='Neto'
    ))
    
    # Línea vertical del Spot
    fig.add_vline(x=spot, line_dash="dash", line_color="#ffcc00", line_width=2,
                  annotation_text=f"Spot: ${spot:.2f}", annotation_position="top right",
                  annotation_font=dict(color="#ffcc00", size=10))
                  
    # Anotaciones de máximos si existen datos en el rango
    if not df_plot.empty:
        max_pos = df_plot.loc[df_plot['call_gex'].idxmax()]
        min_neg = df_plot.loc[df_plot['put_gex'].idxmin()]
        
        fig.add_annotation(
            x=max_pos['strike'], y=max_pos['call_gex'],
            text=f"▲ Strike {max_pos['strike']:.1f} ({format_millions(max_pos['call_gex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="white",
            ax=0, ay=-45, bgcolor="black", font=dict(color="white", size=9),
            standoff=8
        )
        
        fig.add_annotation(
            x=min_neg['strike'], y=min_neg['put_gex'],
            text=f"▼ Strike {min_neg['strike']:.1f} ({format_millions(min_neg['put_gex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="#348feb",
            ax=0, ay=45, bgcolor="black", font=dict(color="#348feb", size=9),
            standoff=8
        )
    
    y_label = "Exposición (USD)" if mode == "USD" else "Exposición (Acciones)"
    fig.update_layout(
        title=f"Gamma Exposure (GEX) por Strike ({'USD' if mode=='USD' else 'Acciones'})",
        xaxis_title="Precio Strike",
        yaxis_title=y_label,
        template="plotly_dark",
        plot_bgcolor="#0d0e15",
        paper_bgcolor="#0d0e15",
        margin=dict(l=30, r=30, t=50, b=30),
        barmode='relative',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    return fig

def plot_dex_plotly(df_dex, spot, mode="USD", pct=15):
    df_plot = df_dex.copy()
    if 'dex' in df_plot.columns:
        df_plot = df_plot.rename(columns={'dex': 'net_dex'})
        
    df_plot = df_plot[(df_plot['strike'] >= spot * (1 - pct/100.0)) & (df_plot['strike'] <= spot * (1 + pct/100.0))].copy()
    
    fig = go.Figure()
    
    # Barras de Calls (Arriba)
    fig.add_trace(go.Bar(
        x=df_plot['strike'], y=df_plot['call_dex'],
        name='DEX Calls (Arriba)',
        marker_color='white',
        opacity=0.85
    ))
    
    # Barras de Puts (Abajo)
    fig.add_trace(go.Bar(
        x=df_plot['strike'], y=-df_plot['put_dex'].abs(),
        name='DEX Puts (Abajo)',
        marker_color='#348feb',
        opacity=0.85
    ))
    
    # Línea neta
    fig.add_trace(go.Scatter(
        x=df_plot['strike'], y=df_plot['net_dex'],
        mode='lines+markers',
        line=dict(color='#ff003c', width=2),
        marker=dict(size=4, color='#ff003c'),
        name='Neto'
    ))
    
    # Línea vertical del Spot
    fig.add_vline(x=spot, line_dash="dash", line_color="#ffcc00", line_width=2,
                  annotation_text=f"Spot: ${spot:.2f}", annotation_position="top right",
                  annotation_font=dict(color="#ffcc00", size=10))
                  
    if not df_plot.empty:
        max_pos = df_plot.loc[df_plot['call_dex'].idxmax()]
        min_neg = df_plot.loc[df_plot['put_dex'].idxmin()]
        
        fig.add_annotation(
            x=max_pos['strike'], y=max_pos['call_dex'],
            text=f"▲ Strike {max_pos['strike']:.1f} ({format_millions(max_pos['call_dex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="white",
            ax=0, ay=-45, bgcolor="black", font=dict(color="white", size=9),
            standoff=8
        )
        
        fig.add_annotation(
            x=min_neg['strike'], y=-abs(min_neg['put_dex']),
            text=f"▼ Strike {min_neg['strike']:.1f} ({format_millions(min_neg['put_dex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="#348feb",
            ax=0, ay=45, bgcolor="black", font=dict(color="#348feb", size=9),
            standoff=8
        )
    
    y_label = "Exposición (USD)" if mode == "USD" else "Exposición (Acciones)"
    fig.update_layout(
        title=f"Delta Exposure (DEX) por Strike ({'USD' if mode=='USD' else 'Acciones'})",
        xaxis_title="Precio Strike",
        yaxis_title=y_label,
        template="plotly_dark",
        plot_bgcolor="#0d0e15",
        paper_bgcolor="#0d0e15",
        margin=dict(l=30, r=30, t=50, b=30),
        barmode='relative',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    return fig

def plot_oi_gex_plotly(df_gex, spot, pct=15):
    df_plot = df_gex.copy()
    if 'gex' in df_plot.columns:
        df_plot = df_plot.rename(columns={'gex': 'net_gex'})
        
    df_plot = df_plot[(df_plot['strike'] >= spot * (1 - pct/100.0)) & (df_plot['strike'] <= spot * (1 + pct/100.0))].copy()
    
    fig = go.Figure()
    
    # Barras de Net GEX con color de escala Viridis
    fig.add_trace(go.Bar(
        x=df_plot['strike'], y=df_plot['net_gex'],
        marker=dict(
            color=df_plot['net_gex'].abs(),
            colorscale='Viridis',
            showscale=True,
            colorbar=dict(title="Net GEX")
        ),
        name="Net GEX"
    ))
    
    # Línea vertical del Spot
    fig.add_vline(x=spot, line_dash="dash", line_color="#ffcc00", line_width=2,
                  annotation_text="Spot", annotation_position="top right",
                  annotation_font=dict(color="#ffcc00", size=11))
                  
    # Anotar los 4 strikes con mayor Open Interest
    if not df_plot.empty:
        top_oi = df_plot.nlargest(4, 'open_interest')
        for idx, row in top_oi.iterrows():
            s = row['strike']
            g = row['net_gex']
            oi = int(row['open_interest'])
            
            ay_val = -30 if g >= 0 else 30
            fig.add_annotation(
                x=s, y=g,
                text=f"{s:.0f}<br>OI {oi:,}",
                showarrow=True, arrowhead=1, arrowcolor="gray",
                ax=0, ay=ay_val, bgcolor="black", font=dict(color="white", size=9),
                standoff=8
            )
        
    fig.update_layout(
        title="OI vs Net GEX",
        xaxis_title="Strike",
        yaxis_title="Net GEX",
        template="plotly_dark",
        plot_bgcolor="#0d0e15",
        paper_bgcolor="#0d0e15",
        margin=dict(l=40, r=40, t=50, b=40)
    )
    
    return fig

# Expander de colores como en el original
with st.sidebar.expander("🎨 Personalizar Colores", expanded=False):
    st.color_picker("Color de Calls (Resistencias)", "#ffffff")
    st.color_picker("Color de Puts (Soportes)", "#348feb")
    st.color_picker("Color de Spot", "#ffcc00")

# --- UI PRINCIPAL ---
st.markdown("<h1 style='font-size: 36px; margin-bottom: 12px;'>Gamma Scanner Options</h1>", unsafe_allow_html=True)

# Inicializar estados
if 'expirations' not in st.session_state:
    st.session_state.expirations = []
if 'last_ticker' not in st.session_state:
    st.session_state.last_ticker = ""
if "main_ticker_input" not in st.session_state:
    st.session_state["main_ticker_input"] = "SPY"

# Panel colapsable superior: 🐋 Ballenas Hoy (Tu Discord en Vivo)
try:
    _disc_token = str(st.secrets.get("DISCORD_BOT_TOKEN", "") or "").strip()
    _disc_golden_id = str(st.secrets.get("DISCORD_GOLDEN_CHANNEL_ID", "") or "").strip()
    _disc_sweeps_id = str(st.secrets.get("DISCORD_SWEEPS_CHANNEL_ID", "") or "").strip()
    _disc_dp_id = str(st.secrets.get("DISCORD_DARKPOOL_CHANNEL_ID", "") or "").strip()
except Exception:
    _disc_token = ""
    _disc_golden_id = ""
    _disc_sweeps_id = ""
    _disc_dp_id = ""

_disc_configured = bool(_disc_token and (_disc_golden_id or _disc_sweeps_id or _disc_dp_id))

if _disc_configured:
    _dw_data = cached_discord_whales(_disc_token, _disc_golden_id, _disc_sweeps_id, _disc_dp_id, limit=50)
    _dw_golden = _dw_data.get("golden", [])
    _dw_sweeps = _dw_data.get("sweeps", [])
    _dw_dp = _dw_data.get("dark_pool", [])
    _dw_errors = _dw_data.get("errors", {})

    _g_today = [x for x in _dw_golden if x.get("is_today")]
    _top_ref = (_g_today[0] if _g_today else (_dw_golden[0] if _dw_golden else (_dw_sweeps[0] if _dw_sweeps else None)))
    _n_alerts = len(_g_today) if _g_today else (len(_dw_golden) + len(_dw_sweeps))
    if _top_ref:
        _top_p = format_millions(_top_ref.get("prem_usd", 0.0), "USD")
        _top_hits = f" ({_top_ref['hits']}x)" if _top_ref.get("hits", 1) > 1 else ""
        _exp_label = f"🐋 Ballenas Hoy (Tu Discord en Vivo) · ● Conectado · {_n_alerts} alertas · Top: {_top_ref['ticker']} {_top_p}{_top_hits}"
    else:
        _exp_label = "🐋 Ballenas Hoy (Tu Discord en Vivo) · ● Conectado"

    with st.expander(_exp_label, expanded=False):
        c_dw1, c_dw2, c_dw3, c_dw4 = st.columns([1.4, 1.8, 1.8, 1.4])
        with c_dw1:
            _dw_only_today = st.checkbox("Solo hoy", value=True, key="dw_only_today")
        with c_dw2:
            _dw_side = st.selectbox("Tipo", ["Todos", "Solo Calls", "Solo Puts"], key="dw_side_flt", label_visibility="collapsed")
        with c_dw3:
            _dw_min_prem_lbl = st.selectbox("Prima mínima", ["Prima: Todas", "≥ $250K", "≥ $500K", "≥ $1M"], key="dw_prem_flt", label_visibility="collapsed")
        with c_dw4:
            if st.button("🔄 Refrescar Discord", use_container_width=True, key="dw_refresh_btn"):
                cached_discord_whales.clear()
                st.rerun()

        _dw_min_prem = {"Prima: Todas": 0.0, "≥ $250K": 250_000.0, "≥ $500K": 500_000.0, "≥ $1M": 1_000_000.0}.get(_dw_min_prem_lbl, 0.0)

        def _render_discord_sweeps_tab(items, ch_key, ch_title):
            if ch_key in _dw_errors:
                st.warning(f"No se pudo leer {ch_title}: {_dw_errors[ch_key]}")
                return
            if not items:
                st.info(f"Sin alertas recientes en {ch_title}.")
                return

            filtered = [x for x in items if (not _dw_only_today or x.get("is_today"))]
            showing_recent_fallback = False
            if not filtered and _dw_only_today:
                filtered = items[:]
                showing_recent_fallback = True

            if _dw_side == "Solo Calls":
                filtered = [x for x in filtered if x.get("type") == "CALL"]
            elif _dw_side == "Solo Puts":
                filtered = [x for x in filtered if x.get("type") == "PUT"]
            if _dw_min_prem > 0:
                filtered = [x for x in filtered if float(x.get("prem_usd", 0.0)) >= _dw_min_prem]

            filtered = filtered[:20]
            if not filtered:
                st.info("Ninguna alerta cumple el filtro actual (prueba bajar la prima mínima o desmarcar 'Solo hoy').")
                return

            tot_prem = sum(float(x.get("prem_usd", 0.0)) for x in filtered)
            note_txt = " · mostrando últimas recientes (sin alertas hoy todavía)" if showing_recent_fallback else ""
            st.markdown(
                '<div style="background:#161b22;border:1px solid #30363d;border-radius:7px;padding:6px 10px;margin-bottom:6px;'
                'display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;font-size:12px;">'
                f'<span style="color:#e6edf3;"><b>{ch_title}</b> · {len(filtered)} señales agrupadas{note_txt}</span>'
                f'<span style="color:#fbbf24;font-weight:700;">Prima acumulada: {format_millions(tot_prem, "USD")}</span>'
                '</div>',
                unsafe_allow_html=True,
            )

            rows_h = ""
            for r in filtered:
                is_c = r["type"] == "CALL"
                t_pill = (
                    '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:800;font-size:11px;'
                    'background:rgba(34,197,94,0.16);color:#4ade80;border:1px solid rgba(34,197,94,0.45);">CALL</span>'
                    if is_c else
                    '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:800;font-size:11px;'
                    'background:rgba(239,68,68,0.16);color:#f87171;border:1px solid rgba(239,68,68,0.45);">PUT</span>'
                )
                dte_part = f" <span style='color:#6e7681;'>({r['dte']}d)</span>" if int(r.get("dte", -1)) >= 0 else ""
                p_usd = float(r.get("prem_usd", 0.0))
                p_clr = "#fde047" if p_usd >= 1_000_000 else ("#e6edf3" if p_usd >= 500_000 else "#c9d1d9")
                p_wt = "800" if p_usd >= 1_000_000 else "600"
                hits_n = int(r.get("hits", 1))
                hits_h = (
                    f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11px;'
                    f'background:rgba(168,85,247,0.18);color:#c084fc;border:1px solid rgba(168,85,247,0.45);">{hits_n}x 🔥</span>'
                    if hits_n > 1 else '<span style="color:#8b949e;">1x</span>'
                )
                tag_h = (
                    '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11px;'
                    'background:rgba(245,158,11,0.20);color:#fbbf24;border:1px solid rgba(245,158,11,0.55);">🥇 Golden</span>'
                    if r.get("is_golden") else '<span style="color:#8b949e;">⚡ Sweep</span>'
                )
                date_badge = "" if r.get("is_today") else f" <span style='color:#6e7681;font-size:10.5px;'>{r.get('date_str','')[5:]}</span>"
                rows_h += (
                    '<tr style="border-bottom:1px solid #21262d;">'
                    f'<td style="padding:5px 7px;text-align:left;font-weight:800;color:#ffffff;white-space:nowrap;">{r["ticker"]}</td>'
                    f'<td style="padding:5px 6px;text-align:left;white-space:nowrap;">{t_pill}</td>'
                    f'<td style="padding:5px 6px;text-align:left;font-weight:700;color:#e6edf3;white-space:nowrap;">${float(r["strike"]):g}</td>'
                    f'<td style="padding:5px 6px;text-align:left;color:#c9d1d9;white-space:nowrap;">{r["exp"]}{dte_part}</td>'
                    f'<td style="padding:5px 7px;text-align:right;font-weight:{p_wt};color:{p_clr};white-space:nowrap;">{format_millions(p_usd, "USD")}</td>'
                    f'<td style="padding:5px 6px;text-align:center;white-space:nowrap;">{hits_h}</td>'
                    f'<td style="padding:5px 6px;text-align:center;white-space:nowrap;">{tag_h}</td>'
                    f'<td style="padding:5px 7px;text-align:right;color:#8b949e;white-space:nowrap;">{r["time_str"]}{date_badge}</td>'
                    '</tr>'
                )

            st.markdown(
                '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:8px;">'
                '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:\'Inter\',sans-serif;font-variant-numeric:tabular-nums;">'
                '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                '<th style="padding:6px 7px;text-align:left;">Ticker</th>'
                '<th style="padding:6px 6px;text-align:left;">Tipo</th>'
                '<th style="padding:6px 6px;text-align:left;">Strike</th>'
                '<th style="padding:6px 6px;text-align:left;">Vence</th>'
                '<th style="padding:6px 7px;text-align:right;">Prima Total</th>'
                '<th style="padding:6px 6px;text-align:center;">Golpes</th>'
                '<th style="padding:6px 6px;text-align:center;">Señal</th>'
                '<th style="padding:6px 7px;text-align:right;">Hora</th>'
                '</tr></thead>'
                f'<tbody>{rows_h}</tbody></table></div>',
                unsafe_allow_html=True,
            )

            # Botones rápidos 1-clic para cargar cualquiera de los tickers detectados arriba
            uniq_syms = []
            for r in filtered:
                if r["ticker"] not in uniq_syms:
                    uniq_syms.append(r["ticker"])
                if len(uniq_syms) >= 8:
                    break
            if uniq_syms:
                st.caption("🔍 **Analizar en 1 clic** (carga el ticker abajo con su GEX, Gamma Flip y Scanner):")
                for row_start in range(0, len(uniq_syms), 4):
                    chunk = uniq_syms[row_start:row_start + 4]
                    btn_cols = st.columns(4)
                    for i_s, sym_pick in enumerate(chunk):
                        with btn_cols[i_s]:
                            if st.button(f"🔍 {sym_pick}", key=f"dw_go_{ch_key}_{sym_pick}", use_container_width=True):
                                st.session_state["main_ticker_input"] = sym_pick
                                st.session_state.force_refresh = True
                                st.rerun()

        def _render_discord_dp_tab(items):
            if "dark_pool" in _dw_errors:
                st.warning(f"No se pudo leer #dark-pool: {_dw_errors['dark_pool']}")
                return
            if not items:
                st.info("Sin bloques recientes en #dark-pool.")
                return

            filtered = [x for x in items if (not _dw_only_today or x.get("is_today"))]
            showing_recent_fallback = False
            if not filtered and _dw_only_today:
                filtered = items[:]
                showing_recent_fallback = True
            if _dw_min_prem > 0:
                filtered = [x for x in filtered if float(x.get("amount_usd", 0.0)) >= _dw_min_prem]

            filtered = filtered[:20]
            if not filtered:
                st.info("Ningún bloque Dark Pool cumple el filtro actual.")
                return

            note_txt = " · mostrando últimos recientes" if showing_recent_fallback else ""
            st.markdown(
                '<div style="background:#161b22;border:1px solid #30363d;border-radius:7px;padding:6px 10px;margin-bottom:6px;'
                'display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;font-size:12px;">'
                f'<span style="color:#e6edf3;"><b>🕶️ #dark-pool</b> · {len(filtered)} bloques institucionales{note_txt}</span>'
                '<span style="color:#38bdf8;font-weight:700;">Blocktrades en acciones</span>'
                '</div>',
                unsafe_allow_html=True,
            )

            rows_h = ""
            for r in filtered:
                amt_v = float(r.get("amount_usd", 0.0))
                amt_disp = format_millions(amt_v, "USD") if amt_v > 0 else r.get("amount_str", "-")
                fp_disp = r.get("float_pct") or "-"
                date_badge = "" if r.get("is_today") else f" <span style='color:#6e7681;font-size:10.5px;'>{r.get('date_str','')[5:]}</span>"
                rows_h += (
                    '<tr style="border-bottom:1px solid #21262d;">'
                    f'<td style="padding:5px 7px;text-align:left;font-weight:800;color:#ffffff;white-space:nowrap;">{r["ticker"]}</td>'
                    f'<td style="padding:5px 7px;text-align:right;font-weight:700;color:#e6edf3;white-space:nowrap;">${float(r["price"]):.2f}</td>'
                    f'<td style="padding:5px 7px;text-align:right;color:#c9d1d9;white-space:nowrap;">{r["shares_str"]}</td>'
                    f'<td style="padding:5px 7px;text-align:right;font-weight:800;color:#38bdf8;white-space:nowrap;">{amt_disp}</td>'
                    f'<td style="padding:5px 6px;text-align:center;color:#94a3b8;white-space:nowrap;">{fp_disp}</td>'
                    f'<td style="padding:5px 7px;text-align:right;color:#8b949e;white-space:nowrap;">{r["time_str"]}{date_badge}</td>'
                    '</tr>'
                )

            st.markdown(
                '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:8px;">'
                '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:\'Inter\',sans-serif;font-variant-numeric:tabular-nums;">'
                '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                '<th style="padding:6px 7px;text-align:left;">Ticker</th>'
                '<th style="padding:6px 7px;text-align:right;">Precio Nivel</th>'
                '<th style="padding:6px 7px;text-align:right;">Acciones</th>'
                '<th style="padding:6px 7px;text-align:right;">Monto Total</th>'
                '<th style="padding:6px 6px;text-align:center;">% Float</th>'
                '<th style="padding:6px 7px;text-align:right;">Hora</th>'
                '</tr></thead>'
                f'<tbody>{rows_h}</tbody></table></div>',
                unsafe_allow_html=True,
            )

            uniq_syms = []
            for r in filtered:
                if r["ticker"] not in uniq_syms:
                    uniq_syms.append(r["ticker"])
                if len(uniq_syms) >= 8:
                    break
            if uniq_syms:
                st.caption("🔍 **Analizar en 1 clic:**")
                for row_start in range(0, len(uniq_syms), 4):
                    chunk = uniq_syms[row_start:row_start + 4]
                    btn_cols = st.columns(4)
                    for i_s, sym_pick in enumerate(chunk):
                        with btn_cols[i_s]:
                            if st.button(f"🔍 {sym_pick}", key=f"dw_go_dp_{sym_pick}", use_container_width=True):
                                st.session_state["main_ticker_input"] = sym_pick
                                st.session_state.force_refresh = True
                                st.rerun()

        dw_t1, dw_t2, dw_t3 = st.tabs(["🥇 #golden-sweeps", "🌊 #options-sweeps", "🕶️ #dark-pool"])
        with dw_t1:
            _render_discord_sweeps_tab(_dw_golden, "golden", "🥇 #golden-sweeps")
        with dw_t2:
            _render_discord_sweeps_tab(_dw_sweeps, "sweeps", "🌊 #options-sweeps")
        with dw_t3:
            _render_discord_dp_tab(_dw_dp)
else:
    with st.expander("🐋 Ballenas Hoy (Conectar tu Discord · Pendiente de Secrets)", expanded=False):
        st.info(
            "Para activar tus alertas en vivo de **#golden-sweeps**, **#options-sweeps** y **#dark-pool**, "
            "agrega estas 4 líneas en **Streamlit Cloud → Manage app → Settings → Secrets**:"
        )
        st.code(
            'DISCORD_BOT_TOKEN = "TU_BOT_TOKEN_AQUI"\n'
            'DISCORD_GOLDEN_CHANNEL_ID = "ID_CANAL_GOLDEN_SWEEPS"\n'
            'DISCORD_SWEEPS_CHANNEL_ID = "ID_CANAL_OPTIONS_SWEEPS"\n'
            'DISCORD_DARKPOOL_CHANNEL_ID = "ID_CANAL_DARK_POOL"',
            language="toml",
        )

# Fila 1: Ticker, Spot Manual y Botón Descargar Data
col_t, col_s, col_b = st.columns([3, 2, 2])
with col_t:
    ticker = st.text_input("Ticker", key="main_ticker_input").upper().strip()
with col_s:
    spot_manual = st.number_input("Spot Manual", value=0.00, step=0.5, format="%.2f")
with col_b:
    st.write("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    btn_fetch = st.button("Descargar Data", use_container_width=True)

# Descargar expiraciones si cambia de ticker o se da clic en el botón
if btn_fetch or ticker != st.session_state.last_ticker:
    if not api_key:
        st.error("Por favor, ingresa tu clave API de FlashAlpha en el menú lateral.")
    else:
        with st.spinner("Cargando fechas de expiración..."):
            exps = cached_options_expirations(ticker, api_key)
            if exps:
                st.session_state.expirations = exps
                st.session_state.last_ticker = ticker
            else:
                st.error("No se pudieron obtener expiraciones. Revisa el ticker o tu API Key.")

# Fila 2: Análisis y Selector de Expiraciones
if st.session_state.expirations:
    st.markdown(f"<h2 class='custom-header'>Análisis de {ticker}</h2>", unsafe_allow_html=True)
    
    selected_expirations = st.multiselect(
        "2. Selecciona Fechas de Expiración",
        options=st.session_state.expirations,
        default=st.session_state.expirations[0] if st.session_state.expirations else []
    )
    
    col_calc_btn, col_refresh_btn, col_ts = st.columns([2, 2, 3])
    with col_calc_btn:
        btn_calc = st.button("3. Calcular GEX y Scanner", use_container_width=True, type="primary")
    with col_refresh_btn:
        btn_refresh_live = st.button("🔄 Actualizar En Vivo", use_container_width=True)
        if btn_refresh_live:
            cached_fetch_and_calculate_all.clear()
            cached_fetch_live_flow.clear()
            st.session_state.force_refresh = True
    with col_ts:
        ts_placeholder = st.empty()

    # Si se hace clic en calcular o actualizar, corremos el scanner
    force_refresh = st.session_state.pop("force_refresh", False)
    exp_key = tuple(selected_expirations)
    if btn_calc or force_refresh or 'gex_results' in st.session_state:
        if (
            btn_calc
            or force_refresh
            or ticker != st.session_state.get('results_ticker')
            or engine_mode != st.session_state.get('results_mode')
            or exp_key != st.session_state.get('results_exps')
        ):
            with st.spinner("Descargando libro de opciones y calculando GEX..."):
                df_gex, df_dex, df_raw, spot = cached_fetch_and_calculate_all(ticker, selected_expirations, spot_manual, api_key, mode=engine_mode)
                
                if df_gex.empty:
                    st.error("No hay suficientes datos de opciones disponibles para calcular los niveles.")
                else:
                    results = calculate_levels(df_gex, df_dex, spot)
                    st.session_state.gex_results = results
                    st.session_state.gex_data = df_gex
                    st.session_state.dex_data = df_dex
                    st.session_state.raw_data = df_raw
                    st.session_state.results_ticker = ticker
                    st.session_state.results_mode = engine_mode
                    st.session_state.results_exps = exp_key
                    st.session_state.last_updated_str = now_ca_str()

        last_ts = st.session_state.get("last_updated_str")
        if last_ts:
            ts_placeholder.markdown(
                f"<div style='padding-top:8px;color:#8b949e;font-size:13px;'>🕒 Última actualización: <b style='color:#c9d1d9;'>{last_ts} PT</b> (California · caché 60s)</div>",
                unsafe_allow_html=True,
            )

        # Mostrar Resultados si existen en sesión
        if 'gex_results' in st.session_state:
            res = st.session_state.gex_results
            df_gex = st.session_state.gex_data
            df_dex = st.session_state.dex_data
            df_raw = st.session_state.raw_data
            
            # Fila 3: Tres métricas grandes y Tabla de Niveles
            col_metrics, col_table = st.columns([1, 1])
            
            with col_metrics:
                # Métrica 1: Spot Price
                st.markdown(f"""
                <div class="metric-box">
                    <p class="metric-label">Spot Price</p>
                    <p class="metric-value">${res['Spot Price']:.2f}</p>
                </div>
                """, unsafe_allow_html=True)
                
                # Métrica 2: Total Net GEX
                st.markdown(f"""
                <div class="metric-box">
                    <p class="metric-label">Total Net GEX</p>
                    <p class="metric-value">{format_millions(res['Total Net GEX'], engine_mode)}</p>
                </div>
                """, unsafe_allow_html=True)
                
                # Métrica 3: Total Net DEX
                st.markdown(f"""
                <div class="metric-box">
                    <p class="metric-label">Total Net DEX</p>
                    <p class="metric-value">{format_millions(res['Total Net DEX'], engine_mode)}</p>
                </div>
                """, unsafe_allow_html=True)
                
            with col_table:
                st.markdown("<h3 style='margin-bottom: 10px;'>GEX Levels Intradía</h3>", unsafe_allow_html=True)
                
                # Generar tabla igual a la foto
                table_data = {
                    "Level": [
                        "PTrans (Resistance)",
                        "Zero GEX (Balance)",
                        "NTrans (Support)",
                        "COTMP (Pin)",
                        "Call OI",
                        "Put OI",
                        "Gamma Flip",
                        "Spot Price"
                    ],
                    "Value": [
                        f"{res['PTrans (Resistance)']:.1f}" if res['PTrans (Resistance)'] > 1000 else f"{res['PTrans (Resistance)']:.2f}",
                        f"{res['Zero GEX (Balance)']:.1f}" if res['Zero GEX (Balance)'] > 1000 else f"{res['Zero GEX (Balance)']:.2f}",
                        f"{res['NTrans (Support)']:.1f}" if res['NTrans (Support)'] > 1000 else f"{res['NTrans (Support)']:.2f}",
                        f"{res['COTMP (Pin)']:.1f}" if res['COTMP (Pin)'] > 1000 else f"{res['COTMP (Pin)']:.2f}",
                        f"{res['Call OI']:,}",
                        f"{res['Put OI']:,}",
                        f"{res['Gamma Flip']:.1f}" if res['Gamma Flip'] > 1000 else f"{res['Gamma Flip']:.2f}",
                        f"{res['Spot Price']:.2f}"
                    ]
                }
                df_table = pd.DataFrame(table_data)
                st.dataframe(df_table, hide_index=True, use_container_width=True)

            # Fila 4: Widget de TradingView + Niveles Clave e Interpretación
            st.markdown("<h2 class='custom-header'>Precio en Tiempo Real + Niveles GEX</h2>", unsafe_allow_html=True)
            
            col_chart, col_right = st.columns([2, 1])
            
            with col_chart:
                # Widget Avanzado de TradingView
                tv_symbol = get_tv_symbol(ticker)
                tv_html = f"""
                <div class="tradingview-widget-container" style="height:480px;width:100%">
                  <div id="tradingview_chart" style="height:480px;"></div>
                  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
                  <script type="text/javascript">
                  new TradingView.widget({{
                    "autosize": true,
                    "symbol": "{tv_symbol}",
                    "interval": "5",
                    "timezone": "Etc/UTC",
                    "theme": "dark",
                    "style": "1",
                    "locale": "es",
                    "enable_publishing": false,
                    "hide_side_toolbar": false,
                    "allow_symbol_change": true,
                    "container_id": "tradingview_chart"
                  }});
                  </script>
                </div>
                """
                components.html(tv_html, height=480, scrolling=False)
                
            with col_right:
                # Niveles Clave
                st.markdown("<h3 style='margin-bottom: 15px;'>Niveles Clave</h3>", unsafe_allow_html=True)
                
                levels_desc = [
                    ("Gamma Flip", res['Zero GEX (Balance)']),
                    ("PTrans (Resist.)", res['PTrans (Resistance)']),
                    ("NTrans (Soporte)", res['NTrans (Support)']),
                    ("COTMP (Pin)", res['COTMP (Pin)'])
                ]
                
                for name, val in levels_desc:
                    st.markdown(f"""
                    <div style="margin-bottom: 15px;">
                        <p style="color: #8b949e; margin: 0; font-size: 13px;">{name}</p>
                        <p style="color: white; margin: 0; font-size: 28px; font-weight: bold;">{val:.2f}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Interpretación de niveles
                st.markdown("<h3 style='margin-top: 20px; margin-bottom: 10px;'>Interpretación</h3>", unsafe_allow_html=True)
                st.markdown(f"""
                <div style="font-size: 14px; color: #b0bec5; line-height: 1.6;">
                    <p>• <b>Spot actual:</b> {res['Spot Price']:.2f}</p>
                    <p>• <b>Arriba del Gamma Flip:</b> mercado más estable (menor volatilidad).</p>
                    <p>• <b>Abajo del Gamma Flip:</b> mayor volatilidad (movimientos rápidos).</p>
                    <p>• <b>PTrans:</b> resistencia institucional principal.</p>
                    <p>• <b>NTrans:</b> soporte institucional principal.</p>
                </div>
                """, unsafe_allow_html=True)

            # Fila 4.5: Panel PREMIUM FLOW (Estilo Institucional Puts | Strike | Calls)
            st.markdown("<h2 class='custom-header'>1 · PREMIUM FLOW (Flujo de Dinero Institucional por Strike)</h2>", unsafe_allow_html=True)
            col_pf1, col_pf2 = st.columns([2, 2])
            with col_pf1:
                pf_basis = st.selectbox(
                    "Base del Cálculo de Prima ($)",
                    ["Open Interest (Dinero Acumulado en Contratos)", "Volumen del Día (Flujo Operado Hoy)"],
                    index=0
                )
            with col_pf2:
                pf_rows = st.slider("Cantidad de Strikes alrededor del Spot", min_value=6, max_value=30, value=10, step=2)

            if not df_gex.empty and 'call_prem_oi' in df_gex.columns:
                use_vol = "Volumen" in pf_basis
                c_col = 'call_prem_vol' if use_vol else 'call_prem_oi'
                p_col = 'put_prem_vol' if use_vol else 'put_prem_oi'

                # Si se eligió volumen pero el mercado está cerrado y el volumen es 0, usar OI automáticamente
                if use_vol and (df_gex[c_col].sum() + df_gex[p_col].sum() == 0):
                    c_col = 'call_prem_oi'
                    p_col = 'put_prem_oi'

                df_pf = df_gex.copy()
                df_pf['dist'] = (df_pf['strike'] - res['Spot Price']).abs()
                atm_strike = df_pf.loc[df_pf['dist'].idxmin()]['strike']

                # Tomar los N strikes más cercanos al Spot y ordenarlos de mayor a menor (como en la imagen)
                df_pf_sub = df_pf.nsmallest(pf_rows, 'dist').sort_values('strike', ascending=False).reset_index(drop=True)

                total_put_prem = df_pf_sub[p_col].sum()
                total_call_prem = df_pf_sub[c_col].sum()
                max_put_cell = max(df_pf_sub[p_col].max(), 1.0)
                max_call_cell = max(df_pf_sub[c_col].max(), 1.0)
                exp_badge_txt = ", ".join(selected_expirations[:2]) + (f" (+{len(selected_expirations)-2})" if len(selected_expirations) > 2 else "")

                rows_html = ""
                for _, r_pf in df_pf_sub.iterrows():
                    s_val = r_pf['strike']
                    p_val = r_pf[p_col]
                    c_val = r_pf[c_col]
                    is_atm = abs(s_val - atm_strike) < 1e-6

                    # Intensidad del mapa de calor (rojo para Puts, verde para Calls)
                    p_ratio = min(max(p_val / max_put_cell, 0.15), 1.0)
                    c_ratio = min(max(c_val / max_call_cell, 0.15), 1.0)

                    p_r = int(95 + 145 * p_ratio)
                    p_g = int(25 + 45 * (1.0 - p_ratio))
                    p_b = int(25 + 45 * (1.0 - p_ratio))

                    c_r = int(20 + 40 * (1.0 - c_ratio))
                    c_g = int(95 + 135 * c_ratio)
                    c_b = int(50 + 60 * c_ratio)

                    s_str = f"{s_val:.0f}" if s_val == int(s_val) else f"{s_val:.2f}"
                    row_border = "outline: 2.5px solid #ffcc00; box-shadow: 0 0 12px rgba(255, 204, 0, 0.55); position: relative; z-index: 2;" if is_atm else "border-bottom: 1px solid #1a1d24;"
                    strike_bg = "#4d3b08" if is_atm else "#08090c"
                    strike_color = "#ffd700" if is_atm else "#e6edf3"

                    rows_html += (
                        f'<tr style="{row_border}">'
                        f'<td style="background-color: rgb({p_r},{p_g},{p_b}); color: #ffffff; text-align: center; padding: 10px 12px; font-family: \'Courier New\', monospace; font-weight: 700; font-size: 15px; width: 40%;">${p_val:,.0f}</td>'
                        f'<td style="background-color: {strike_bg}; color: {strike_color}; text-align: center; padding: 10px 12px; font-family: \'Courier New\', monospace; font-weight: 800; font-size: 15px; width: 20%;">{s_str}</td>'
                        f'<td style="background-color: rgb({c_r},{c_g},{c_b}); color: #06150a; text-align: center; padding: 10px 12px; font-family: \'Courier New\', monospace; font-weight: 800; font-size: 15px; width: 40%;">${c_val:,.0f}</td>'
                        f'</tr>'
                    )

                pf_html = (
                    '<div style="border: 1.5px solid #d4a017; border-radius: 10px; background-color: #08090c; padding: 16px 20px; margin-bottom: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.6);">'
                    '<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-bottom: 6px;">'
                    '<div style="display: flex; align-items: center; gap: 12px;">'
                    '<span style="color: #f5b700; font-family: \'Courier New\', monospace; font-size: 20px; font-weight: 800; letter-spacing: 0.5px;">Premium Flow</span>'
                    f'<span style="background-color: #161922; border: 1px solid #3a3f50; color: #d0d7de; padding: 2px 10px; border-radius: 12px; font-family: monospace; font-size: 13px;">{exp_badge_txt}</span>'
                    '</div>'
                    '<div style="font-family: \'Courier New\', monospace; font-size: 16px; font-weight: 700;">'
                    f'<span style="color: #c9d1d9;">Put: </span><span style="color: #ff4d4d; margin-right: 16px;">${total_put_prem:,.0f}</span>'
                    f'<span style="color: #c9d1d9;">Call: </span><span style="color: #2ecc71;">${total_call_prem:,.0f}</span>'
                    '</div>'
                    '</div>'
                    f'<div style="color: #8b949e; font-family: \'Courier New\', monospace; font-size: 13px; margin-bottom: 14px;">Spot: {res["Spot Price"]:.2f} (ATM strike highlight: {atm_strike:g})</div>'
                    '<table style="width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 6px;">'
                    '<thead><tr style="border-bottom: 1px solid #262936;">'
                    '<th style="color: #d4a017; text-align: center; padding: 8px; font-family: \'Courier New\', monospace; font-size: 13px; letter-spacing: 1px;">PUTS</th>'
                    '<th style="color: #d4a017; text-align: center; padding: 8px; font-family: \'Courier New\', monospace; font-size: 13px; letter-spacing: 1px;">STRIKE</th>'
                    '<th style="color: #d4a017; text-align: center; padding: 8px; font-family: \'Courier New\', monospace; font-size: 13px; letter-spacing: 1px;">CALLS</th>'
                    '</tr></thead>'
                    f'<tbody>{rows_html}</tbody>'
                    '</table>'
                    '</div>'
                )
                st.markdown(pf_html, unsafe_allow_html=True)

            # Fila 4.75: Panel FLUJO EN VIVO (FlashAlpha Live Flow)
            st.markdown("<h2 class='custom-header'>2 · FLUJO EN VIVO (FlashAlpha Live Flow)</h2>", unsafe_allow_html=True)
            live_flow = cached_fetch_live_flow(ticker, api_key)
            lf_summary = live_flow.get("summary") or {}
            lf_gex = live_flow.get("gex") or {}
            lf_df = live_flow.get("strikes_df", pd.DataFrame())

            if live_flow.get("error") and not lf_summary and lf_df.empty:
                st.warning(f"No se pudo obtener el flujo en vivo: {live_flow['error']}")
            else:
                flow_dir = str(lf_summary.get("flow_direction", "no_flow"))
                dir_map = {
                    "bullish": ("ALCISTA (Compras)", "#2ecc71"),
                    "bearish": ("BAJISTA (Ventas)", "#ff4d4d"),
                    "neutral": ("NEUTRAL", "#ffcc00"),
                    "no_flow": ("SIN FLUJO (Mercado Cerrado)", "#8b949e"),
                }
                dir_label, dir_color = dir_map.get(flow_dir.lower(), (flow_dir.upper(), "#ffffff"))

                contracts_flow = int(lf_summary.get("contracts_with_flow", 0) or 0)
                contracts_total = int(lf_summary.get("contracts_total", 0) or 0)
                oi_delta = lf_summary.get("intraday_oi_delta", 0) or 0
                live_net_gex_val = lf_gex.get("live_net_gex", lf_summary.get("live_gex", 0)) or 0
                live_gex_lbl = str(lf_gex.get("live_net_gex_label", "")).upper()
                pct_shift = float(lf_summary.get("flow_gex_pct_shift", 0) or 0)
                live_flip = lf_gex.get("live_gamma_flip")

                c_lf1, c_lf2, c_lf3, c_lf4 = st.columns(4)
                with c_lf1:
                    st.markdown(f"""
                    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;">
                        <p style="color:#8b949e;margin:0;font-size:12px;">Dirección del Flujo</p>
                        <p style="color:{dir_color};margin:4px 0 0 0;font-size:20px;font-weight:800;">{dir_label}</p>
                    </div>
                    """, unsafe_allow_html=True)
                with c_lf2:
                    st.markdown(f"""
                    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;">
                        <p style="color:#8b949e;margin:0;font-size:12px;">Contratos con Flujo / Δ OI</p>
                        <p style="color:#ffffff;margin:4px 0 0 0;font-size:20px;font-weight:700;">{contracts_flow:,} / {contracts_total:,} <span style="font-size:13px;color:#8b949e;">(Δ {oi_delta:+,})</span></p>
                    </div>
                    """, unsafe_allow_html=True)
                with c_lf3:
                    st.markdown(f"""
                    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;">
                        <p style="color:#8b949e;margin:0;font-size:12px;">Live Net GEX ({live_gex_lbl or 'FLOW'})</p>
                        <p style="color:#ffffff;margin:4px 0 0 0;font-size:20px;font-weight:700;">{format_millions(live_net_gex_val, 'USD')} <span style="font-size:13px;color:#8b949e;">({pct_shift:+.2f}%)</span></p>
                    </div>
                    """, unsafe_allow_html=True)
                with c_lf4:
                    flip_txt = f"${float(live_flip):.2f}" if live_flip is not None else "N/A"
                    st.markdown(f"""
                    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;">
                        <p style="color:#8b949e;margin:0;font-size:12px;">Live Gamma Flip</p>
                        <p style="color:#ffcc00;margin:4px 0 0 0;font-size:20px;font-weight:700;">{flip_txt}</p>
                    </div>
                    """, unsafe_allow_html=True)

                if flow_dir.lower() == "no_flow":
                    st.info("🕒 Mercado de opciones cerrado o sin flujo intradía aún (`no_flow`). Al abrir el mercado (9:30 AM ET), pulsa **Forzar Actualización** para ver la dirección en vivo.")

                if not lf_df.empty:
                    lf_spot = float(lf_gex.get("underlying_price") or res["Spot Price"] or 0.0)
                    lf_sub = lf_df[
                        (lf_df["strike"] >= lf_spot * (1 - rango_pct / 100.0))
                        & (lf_df["strike"] <= lf_spot * (1 + rango_pct / 100.0))
                    ].copy()

                    if not lf_sub.empty:
                        st.markdown("<h4 style='margin-top:16px;margin-bottom:8px;'>Top Strikes Activos (Live Flow GEX & Volumen)</h4>", unsafe_allow_html=True)
                        top_flow_df = lf_sub.sort_values(["total_volume", "call_oi", "put_oi"], ascending=False).head(10)[
                            ["strike", "call_volume", "put_volume", "call_oi", "put_oi", "call_gex", "put_gex", "net_gex"]
                        ].copy().reset_index(drop=True)
                        for int_c in ["call_volume", "put_volume", "call_oi", "put_oi"]:
                            top_flow_df[int_c] = top_flow_df[int_c].astype(int)

                        def _top3_rank_map(df_src, col_name):
                            pos = df_src[df_src[col_name] > 0].sort_values(col_name, ascending=False).head(3)
                            return {idx: rank for rank, idx in enumerate(pos.index.tolist(), start=1)}, pos

                        cvol_ranks, cvol_top = _top3_rank_map(top_flow_df, "call_volume")
                        pvol_ranks, pvol_top = _top3_rank_map(top_flow_df, "put_volume")
                        coi_ranks, coi_top = _top3_rank_map(top_flow_df, "call_oi")
                        poi_ranks, poi_top = _top3_rank_map(top_flow_df, "put_oi")

                        def _fmt_k(v):
                            v = int(v)
                            if v >= 1_000_000:
                                return f"{v/1_000_000:.1f}M"
                            if v >= 1_000:
                                return f"{v/1_000:.1f}K"
                            return f"{v:,}"

                        def _vol_cell(val, rank, is_call):
                            txt = f"{int(val):,}"
                            if is_call:
                                if rank == 1:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:800;background:rgba(0,230,118,0.36);border:1.5px solid #00e676;color:#ffffff;">{txt}</span>'
                                if rank == 2:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:700;background:rgba(46,204,113,0.18);border:1px solid rgba(46,204,113,0.68);color:#e6edf3;">{txt}</span>'
                                if rank == 3:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:600;background:rgba(46,204,113,0.08);border:1px solid rgba(46,204,113,0.34);color:#b0bac5;">{txt}</span>'
                            else:
                                if rank == 1:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:800;background:rgba(255,59,48,0.38);border:1.5px solid #ff3b30;color:#ffffff;">{txt}</span>'
                                if rank == 2:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:700;background:rgba(255,77,77,0.18);border:1px solid rgba(255,77,77,0.68);color:#e6edf3;">{txt}</span>'
                                if rank == 3:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:600;background:rgba(255,77,77,0.08);border:1px solid rgba(255,77,77,0.34);color:#b0bac5;">{txt}</span>'
                            return f'<span style="color:#8b949e;">{txt}</span>'

                        def _oi_cell(val, rank, is_call):
                            txt = f"{int(val):,}"
                            if is_call:
                                if rank == 1:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:700;background:rgba(56,189,248,0.16);border:1.5px solid #38bdf8;color:#e0f2fe;">{txt}</span>'
                                if rank == 2:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:600;border:1px solid rgba(56,189,248,0.55);color:#bae6fd;">{txt}</span>'
                                if rank == 3:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;border:1px solid rgba(56,189,248,0.28);color:#94a3b8;">{txt}</span>'
                            else:
                                if rank == 1:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:700;background:rgba(251,191,36,0.16);border:1.5px solid #fbbf24;color:#fef3c7;">{txt}</span>'
                                if rank == 2:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:600;border:1px solid rgba(251,191,36,0.55);color:#fde68a;">{txt}</span>'
                                if rank == 3:
                                    return f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;border:1px solid rgba(251,191,36,0.28);color:#94a3b8;">{txt}</span>'
                            return f'<span style="color:#8b949e;">{txt}</span>'

                        def _chips_row(df_top, col_name, styles):
                            chips = []
                            for r_i, (_, row_t) in enumerate(df_top.iterrows()):
                                st_css = styles[min(r_i, len(styles) - 1)]
                                chips.append(f'<span style="padding:2px 7px;border-radius:4px;font-size:11.5px;{st_css}">${float(row_t["strike"]):g} ({_fmt_k(row_t[col_name])})</span>')
                            return " ".join(chips) if chips else '<span style="color:#8b949e;">N/D</span>'

                        call_vol_chips = _chips_row(cvol_top, "call_volume", [
                            "font-weight:800;background:rgba(0,230,118,0.36);border:1.5px solid #00e676;color:#ffffff;",
                            "font-weight:700;background:rgba(46,204,113,0.18);border:1px solid rgba(46,204,113,0.65);color:#e6edf3;",
                            "font-weight:600;background:rgba(46,204,113,0.08);border:1px solid rgba(46,204,113,0.32);color:#9da7b3;",
                        ])
                        put_vol_chips = _chips_row(pvol_top, "put_volume", [
                            "font-weight:800;background:rgba(255,59,48,0.38);border:1.5px solid #ff3b30;color:#ffffff;",
                            "font-weight:700;background:rgba(255,77,77,0.18);border:1px solid rgba(255,77,77,0.65);color:#e6edf3;",
                            "font-weight:600;background:rgba(255,77,77,0.08);border:1px solid rgba(255,77,77,0.32);color:#9da7b3;",
                        ])
                        call_oi_chips = _chips_row(coi_top, "call_oi", [
                            "font-weight:700;background:rgba(56,189,248,0.16);border:1.5px solid #38bdf8;color:#e0f2fe;",
                            "font-weight:600;border:1px solid rgba(56,189,248,0.55);color:#bae6fd;",
                            "border:1px solid rgba(56,189,248,0.28);color:#94a3b8;",
                        ])
                        put_oi_chips = _chips_row(poi_top, "put_oi", [
                            "font-weight:700;background:rgba(251,191,36,0.16);border:1.5px solid #fbbf24;color:#fef3c7;",
                            "font-weight:600;border:1px solid rgba(251,191,36,0.55);color:#fde68a;",
                            "border:1px solid rgba(251,191,36,0.28);color:#94a3b8;",
                        ])

                        summary_strip_html = (
                            '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:8px;">'
                            '<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid #00e676;border-radius:8px;padding:7px 10px;display:flex;flex-direction:column;gap:5px;">'
                            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:5px;"><span style="color:#00e676;font-size:11.5px;font-weight:800;">🟢 CALL VOL:</span>{call_vol_chips}</div>'
                            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:5px;"><span style="color:#38bdf8;font-size:11.5px;font-weight:700;">🏛️ CALL OI:</span>{call_oi_chips}</div>'
                            '</div>'
                            '<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid #ff3b30;border-radius:8px;padding:7px 10px;display:flex;flex-direction:column;gap:5px;">'
                            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:5px;"><span style="color:#ff4d4d;font-size:11.5px;font-weight:800;">🔴 PUT VOL:</span>{put_vol_chips}</div>'
                            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:5px;"><span style="color:#fbbf24;font-size:11.5px;font-weight:700;">🏛️ PUT OI:</span>{put_oi_chips}</div>'
                            '</div></div>'
                        )
                        st.markdown(summary_strip_html, unsafe_allow_html=True)

                        lf_rows_html = ""
                        for idx_f, rf in top_flow_df.iterrows():
                            cr = cvol_ranks.get(idx_f, 0)
                            pr = pvol_ranks.get(idx_f, 0)
                            coir = coi_ranks.get(idx_f, 0)
                            poir = poi_ranks.get(idx_f, 0)
                            battle_ic = ' <span title="Zona de batalla: Top 3 en Call Vol y Put Vol">⚔️</span>' if (cr > 0 and pr > 0) else ""
                            cg_v = float(rf["call_gex"])
                            pg_v = float(rf["put_gex"])
                            ng_v = float(rf["net_gex"])
                            ng_clr = "#2ecc71" if ng_v > 0 else ("#ff4d4d" if ng_v < 0 else "#8b949e")
                            cg_txt = f"+{format_millions(cg_v, 'USD')}" if cg_v > 0 else format_millions(cg_v, "USD")
                            ng_txt = f"+{format_millions(ng_v, 'USD')}" if ng_v > 0 else format_millions(ng_v, "USD")
                            lf_rows_html += (
                                f'<tr style="border-bottom:1px solid #21262d;">'
                                f'<td style="padding:6px 8px;text-align:left;font-weight:800;color:#ffffff;white-space:nowrap;">${float(rf["strike"]):g}{battle_ic}</td>'
                                f'<td style="padding:6px 8px;text-align:right;white-space:nowrap;">{_vol_cell(rf["call_volume"], cr, True)}</td>'
                                f'<td style="padding:6px 8px;text-align:right;white-space:nowrap;">{_vol_cell(rf["put_volume"], pr, False)}</td>'
                                f'<td style="padding:6px 8px;text-align:right;white-space:nowrap;">{_oi_cell(rf["call_oi"], coir, True)}</td>'
                                f'<td style="padding:6px 8px;text-align:right;white-space:nowrap;">{_oi_cell(rf["put_oi"], poir, False)}</td>'
                                f'<td style="padding:6px 8px;text-align:right;color:#2ecc71;white-space:nowrap;">{cg_txt}</td>'
                                f'<td style="padding:6px 8px;text-align:right;color:#ff4d4d;white-space:nowrap;">{format_millions(pg_v, "USD")}</td>'
                                f'<td style="padding:6px 8px;text-align:right;font-weight:700;color:{ng_clr};white-space:nowrap;">{ng_txt}</td>'
                                f'</tr>'
                            )

                        lf_tbl_html = (
                            '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:6px;">'
                            '<table style="width:100%;border-collapse:collapse;font-size:12.5px;font-family:\'Inter\',sans-serif;">'
                            '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                            '<th style="padding:7px 8px;text-align:left;">Strike</th>'
                            '<th style="padding:7px 8px;text-align:right;">Call Vol</th>'
                            '<th style="padding:7px 8px;text-align:right;">Put Vol</th>'
                            '<th style="padding:7px 8px;text-align:right;">Call OI</th>'
                            '<th style="padding:7px 8px;text-align:right;">Put OI</th>'
                            '<th style="padding:7px 8px;text-align:right;">Call GEX</th>'
                            '<th style="padding:7px 8px;text-align:right;">Put GEX</th>'
                            '<th style="padding:7px 8px;text-align:right;">Net GEX</th>'
                            '</tr></thead>'
                            f'<tbody>{lf_rows_html}</tbody>'
                            '</table></div>'
                        )
                        st.markdown(lf_tbl_html, unsafe_allow_html=True)
                        st.caption("💡 **Mapa de intensidad (fuerte → medio → suave):** 🟢 Verde = Top 3 Call Vol hoy · 🔴 Rojo = Top 3 Put Vol hoy · 🔵 Cian = Top 3 Call OI · 🟡 Ámbar = Top 3 Put OI · **⚔️ = Zona de batalla** (strike en Top 3 de Calls y Puts a la vez).")

            # Fila 4.85: Panel 3 · PANEL TÁCTICO INTRADÍA (VEX, CHEX, Pin Risk & Expected Move)
            st.markdown("<h2 class='custom-header'>3 · PANEL TÁCTICO INTRADÍA (VEX, CHEX, Pin Risk & Expected Move)</h2>", unsafe_allow_html=True)
            tac = calculate_tactical_metrics(df_gex, df_raw, res["Spot Price"], live_flow=live_flow)

            net_vex_val = tac["net_vex"]
            net_chex_val = tac["net_chex"]
            pin_st = tac["pin_strike"]
            pin_sc = tac["pin_score"]
            pin_lbl = tac["pin_label"]
            c_wall = tac["call_wall"]
            p_wall = tac["put_wall"]
            em_1d = tac["em_1d"]
            em_1d_pct = tac["em_1d_pct"]
            em_low = tac["em_low_1d"]
            em_high = tac["em_high_1d"]
            atm_iv_pct = tac["atm_iv"] * 100.0
            spot_now = res["Spot Price"]
            tot_gex_now = res["Total Net GEX"]
            f_dir_now = str(lf_summary.get("flow_direction", "no_flow")).lower()

            # Rastreador de tendencia de IV ATM y Spot en vivo (por ticker y vencimientos en la sesión)
            iv_hist_key = f"{ticker}|{'_'.join(map(str, selected_expirations))}"
            if "iv_history_map" not in st.session_state:
                st.session_state.iv_history_map = {}
            iv_hist = st.session_state.iv_history_map.get(iv_hist_key, [])
            cur_ts_lbl = st.session_state.get("last_updated_str") or now_ca_str()
            if not iv_hist or iv_hist[-1]["ts"] != cur_ts_lbl:
                iv_hist.append({"ts": cur_ts_lbl, "iv": round(atm_iv_pct, 2), "spot": round(spot_now, 4)})
                iv_hist = iv_hist[-20:]
                st.session_state.iv_history_map[iv_hist_key] = iv_hist

            iv_base = iv_hist[0]["iv"]
            spot_base = float(iv_hist[0].get("spot", spot_now) or spot_now)
            if len(iv_hist) >= 2:
                iv_prev = iv_hist[-2]["iv"]
                iv_diff_prev = atm_iv_pct - iv_prev
                iv_diff_base = atm_iv_pct - iv_base
                iv_ref_diff = iv_diff_prev if abs(iv_diff_prev) >= 0.1 else iv_diff_base
                if iv_ref_diff >= 0.15:
                    iv_state = "up"
                    iv_badge_txt = f"▲ +{iv_ref_diff:.1f}% SUBIENDO"
                    iv_badge_fg = "#ff4d4d"
                    iv_badge_bg = "rgba(255,77,77,0.18)"
                elif iv_ref_diff <= -0.15:
                    iv_state = "down"
                    iv_badge_txt = f"▼ {iv_ref_diff:.1f}% BAJANDO"
                    iv_badge_fg = "#2ecc71"
                    iv_badge_bg = "rgba(46,204,113,0.18)"
                else:
                    iv_state = "flat"
                    iv_badge_txt = f"● ESTABLE ({iv_ref_diff:+.1f}%)"
                    iv_badge_fg = "#8b949e"
                    iv_badge_bg = "rgba(139,148,158,0.18)"
                iv_meta_extra = f" · Base: <b>{iv_base:.1f}%</b>"

                spot_prev = float(iv_hist[-2].get("spot", spot_now) or spot_now)
                spot_pct_prev = ((spot_now - spot_prev) / spot_prev * 100.0) if spot_prev > 0 else 0.0
                spot_pct_base = ((spot_now - spot_base) / spot_base * 100.0) if spot_base > 0 else 0.0
                spot_ref_pct = spot_pct_prev if abs(spot_pct_prev) >= 0.08 else spot_pct_base
                if spot_ref_pct >= 0.08:
                    spot_state = "up"
                elif spot_ref_pct <= -0.08:
                    spot_state = "down"
                else:
                    spot_state = "flat"
            else:
                iv_state = "base"
                iv_badge_txt = "● BASE"
                iv_badge_fg = "#8b949e"
                iv_badge_bg = "rgba(139,148,158,0.18)"
                iv_meta_extra = " · Refresca para tendencia"
                spot_state = "base"
                spot_ref_pct = 0.0

            vex_color = "#2ecc71" if net_vex_val >= 0 else "#ff4d4d"
            vex_border_color = vex_color
            vex_pill_txt = "EN ESPERA"
            vex_pill_fg = "#8b949e"
            vex_pill_bg = "rgba(139,148,158,0.18)"
            if net_vex_val < 0:
                if iv_state == "up":
                    vex_sub = "VEX negativo + IV subiendo → Aceleración bajista ACTIVA"
                    vex_sub_color = "#ff4d4d"
                    vex_pill_txt = "ACTIVA"
                    vex_pill_fg = "#ff4d4d"
                    vex_pill_bg = "rgba(255,77,77,0.20)"
                elif iv_state == "down":
                    vex_sub = "IV bajando → Presión Vanna cediendo (posible freno/rebote)"
                    vex_sub_color = "#2ecc71"
                    vex_border_color = "#2ecc71"
                    vex_pill_txt = "CEDIENDO"
                    vex_pill_fg = "#2ecc71"
                    vex_pill_bg = "rgba(46,204,113,0.18)"
                else:
                    vex_sub = "Si IV sube → Acelera ventas (Bajista)"
                    vex_sub_color = "#c9d1d9"
            else:
                if iv_state == "down":
                    vex_sub = "VEX positivo + IV bajando → Empuje alcista ACTIVO"
                    vex_sub_color = "#2ecc71"
                    vex_pill_txt = "ACTIVO"
                    vex_pill_fg = "#2ecc71"
                    vex_pill_bg = "rgba(46,204,113,0.18)"
                elif iv_state == "up":
                    vex_sub = "IV subiendo → Empuje Vanna frenado / cautela"
                    vex_sub_color = "#ffcc00"
                    vex_border_color = "#ffcc00"
                    vex_pill_txt = "FRENADO"
                    vex_pill_fg = "#ffcc00"
                    vex_pill_bg = "rgba(255,204,0,0.18)"
                else:
                    vex_sub = "Si IV baja → Dealers compran (Alcista)"
                    vex_sub_color = "#c9d1d9"

            chex_color = "#2ecc71" if net_chex_val >= 0 else "#ff4d4d"
            chex_sub = "Paso del tiempo → Empuje alcista" if net_chex_val >= 0 else "Paso del tiempo → Presión bajista"
            pin_color = "#ffcc00" if pin_sc >= 65 else ("#2ecc71" if pin_sc >= 40 else "#8b949e")

            t_col1, t_col2, t_col3, t_col4 = st.columns(4)
            with t_col1:
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {vex_border_color};border-radius:8px;padding:12px 16px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">'
                    f'<span style="color:#8b949e;font-size:12px;">VEX Neto (Vanna Exposure)</span>'
                    f'<span style="background:{vex_pill_bg};color:{vex_pill_fg};font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;">{vex_pill_txt}</span>'
                    f'</div>'
                    f'<p style="color:{vex_color};margin:4px 0 2px 0;font-size:22px;font-weight:800;">{format_millions(net_vex_val, engine_mode)}</p>'
                    f'<p style="color:{vex_sub_color};margin:0;font-size:11px;">{vex_sub}</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with t_col2:
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {chex_color};border-radius:8px;padding:12px 16px;">'
                    f'<p style="color:#8b949e;margin:0;font-size:12px;">CHEX Neto (Charm / Día)</p>'
                    f'<p style="color:{chex_color};margin:4px 0 2px 0;font-size:22px;font-weight:800;">{format_millions(net_chex_val, engine_mode)}</p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">{chex_sub}</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with t_col3:
                mp_val = float(tac.get("max_pain", 0.0) or 0.0)
                mp_txt = f" | MP: <b>${mp_val:g}</b>" if mp_val > 0 else ""
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {pin_color};border-radius:8px;padding:12px 16px;">'
                    f'<p style="color:#8b949e;margin:0;font-size:12px;">Flow Levels & Pin Risk</p>'
                    f'<p style="color:#ffffff;margin:4px 0 2px 0;font-size:22px;font-weight:800;">Pin ${pin_st:g} <span style="font-size:13px;color:{pin_color};">({pin_sc:.0f}/100 {pin_lbl})</span></p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">Piso (Put): <b>${p_wall:g}</b> | Techo (Call): <b>${c_wall:g}</b>{mp_txt}</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with t_col4:
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid #348feb;border-radius:8px;padding:12px 16px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">'
                    f'<span style="color:#8b949e;font-size:12px;">Expected Move · <b style="color:#ffffff;">IV {atm_iv_pct:.1f}%</b></span>'
                    f'<span style="background:{iv_badge_bg};color:{iv_badge_fg};font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;">{iv_badge_txt}</span>'
                    f'</div>'
                    f'<p style="color:#ffffff;margin:4px 0 2px 0;font-size:22px;font-weight:800;">±${em_1d:.2f} <span style="font-size:13px;color:#348feb;">(±{em_1d_pct:.1f}%)</span></p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">Rango: <b>${em_low:.2f}</b> ◄──► <b>${em_high:.2f}</b>{iv_meta_extra}</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Cinta Macro del Mercado (justo arriba de Contexto Institucional — caché 5 min)
            macro_data = cached_macro_climate(api_key)
            m_vix = float(macro_data.get("vix", 0.0) or 0.0)
            m_vix_chg = float(macro_data.get("vix_chg", 0.0) or 0.0)
            m_vvix = float(macro_data.get("vvix", 0.0) or 0.0)
            m_move = float(macro_data.get("move", 0.0) or 0.0)
            m_move_chg = float(macro_data.get("move_chg", 0.0) or 0.0)
            m_idx = macro_data.get("indices_gex") or {}

            if m_vix > 0 or m_vvix > 0 or m_move > 0 or m_idx:
                macro_parts = []
                if m_vix > 0:
                    vc = "#ff4d4d" if m_vix >= 22 else ("#ffcc00" if m_vix >= 18 else "#2ecc71")
                    macro_parts.append(f'<span style="color:#c9d1d9;">VIX <b style="color:{vc};">{m_vix:.2f}</b> <span style="color:#8b949e;">({m_vix_chg:+.1f}%)</span></span>')
                if m_vvix > 0:
                    vvc = "#ff4d4d" if m_vvix >= 110 else ("#ffcc00" if m_vvix >= 95 else "#c9d1d9")
                    macro_parts.append(f'<span style="color:#c9d1d9;">VVIX <b style="color:{vvc};">{m_vvix:.1f}</b></span>')
                if m_move > 0:
                    mc = "#ff4d4d" if m_move >= 105 else ("#ffcc00" if m_move >= 95 else "#c9d1d9")
                    macro_parts.append(f'<span style="color:#c9d1d9;">MOVE <b style="color:{mc};">{m_move:.1f}</b> <span style="color:#8b949e;">({m_move_chg:+.1f}%)</span></span>')
                if m_idx:
                    idx_txts = []
                    for sym_i in ("SPY", "QQQ", "IWM"):
                        if sym_i in m_idx:
                            sgn = "+" if m_idx[sym_i] >= 0 else "−"
                            iclr = "#2ecc71" if m_idx[sym_i] >= 0 else "#ff4d4d"
                            idx_txts.append(f'<b style="color:{iclr};">{sym_i} {sgn}</b>')
                    if idx_txts:
                        macro_parts.append(" · ".join(idx_txts))

                neg_cnt = sum(1 for v in m_idx.values() if v < 0)
                pos_cnt = sum(1 for v in m_idx.values() if v >= 0)
                if m_vix >= 22 or (m_move >= 105 and neg_cnt >= 2) or (neg_cnt == 3 and m_vix >= 18):
                    m_clr, m_lbl = "#ff4d4d", "⛈️ CLIMA TENSO (RIESGO)"
                elif m_vix >= 18 or m_vvix >= 100 or (neg_cnt > pos_cnt):
                    m_clr, m_lbl = "#ffcc00", "🌤️ CLIMA MIXTO"
                else:
                    m_clr, m_lbl = "#2ecc71", "☀️ CLIMA ESTABLE"

                macro_sep = '<span style="color:#30363d;margin:0 8px;">|</span>'
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {m_clr};border-radius:8px;padding:7px 14px;margin-top:10px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;font-size:12.5px;">'
                    f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:2px;">'
                    f'<span style="color:#ffffff;font-weight:800;margin-right:10px;">🌍 MACRO</span>'
                    f'{macro_sep.join(macro_parts)}'
                    f'</div>'
                    f'<span style="background:rgba(255,255,255,0.06);border:1px solid {m_clr};color:{m_clr};font-size:11px;font-weight:800;padding:2px 8px;border-radius:5px;white-space:nowrap;">{m_lbl}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Contexto Institucional en Vivo (6 filtros separados en 3x2 — lee directo de la misma descarga FlashAlpha)
            hv_ctx = cached_hv_and_gap_context(ticker)
            hv_20d = float(hv_ctx.get("hv_20d", 0.0) or 0.0)
            prev_close_val = float(hv_ctx.get("prev_close", 0.0) or 0.0)
            today_open_val = float(hv_ctx.get("today_open", 0.0) or 0.0)

            # 1) IV Skew (OTM)
            c_otm_iv = float(tac.get("call_otm_iv_pct", atm_iv_pct))
            p_otm_iv = float(tac.get("put_otm_iv_pct", atm_iv_pct))
            skew_diff = float(tac.get("iv_skew_pct", c_otm_iv - p_otm_iv))
            if skew_diff >= 2.0:
                sk_pill, sk_clr = "CALL SKEW", "#2ecc71"
                sk_val = f"Calls +{skew_diff:.1f}% vs Puts"
                sk_sub = f"Call OTM {c_otm_iv:.1f}% vs Put OTM {p_otm_iv:.1f}% · FOMO alcista"
            elif skew_diff <= -2.0:
                sk_pill, sk_clr = "PUT SKEW", "#ff4d4d"
                sk_val = f"Puts +{abs(skew_diff):.1f}% vs Calls"
                sk_sub = f"Put OTM {p_otm_iv:.1f}% vs Call OTM {c_otm_iv:.1f}% · Miedo / cobertura"
            else:
                sk_pill, sk_clr = "NEUTRO", "#8b949e"
                sk_val = f"Dif. {skew_diff:+.1f}% (OTM)"
                sk_sub = f"Call {c_otm_iv:.1f}% vs Put {p_otm_iv:.1f}% · Sin sesgo extremo"

            # 2) IV vs HV 20d (+ VRP en puntos y riesgo vol-of-vol)
            if hv_20d > 1.0:
                iv_hv_ratio = atm_iv_pct / hv_20d
                vrp_pts = atm_iv_pct - hv_20d
                if vrp_pts <= -10.0:
                    ih_pill, ih_clr = "VRP RIESGO", "#ff8c00"
                    ih_sub = f"Ratio {iv_hv_ratio:.2f}x · IV muy barata vs volatilidad real (riesgo vol-of-vol)"
                elif iv_hv_ratio >= 1.15:
                    ih_pill, ih_clr = "CARAS", "#ff4d4d"
                    ih_sub = f"Ratio {iv_hv_ratio:.2f}x · Cuidado con IV crush al comprar"
                elif iv_hv_ratio <= 0.90:
                    ih_pill, ih_clr = "BARATAS", "#2ecc71"
                    ih_sub = f"Ratio {iv_hv_ratio:.2f}x · Buena relación para ruptura"
                else:
                    ih_pill, ih_clr = "JUSTAS", "#ffcc00"
                    ih_sub = f"Ratio {iv_hv_ratio:.2f}x · Prima en valor justo"
                ih_val = f"IV {atm_iv_pct:.1f}% vs HV {hv_20d:.1f}% ({vrp_pts:+.1f} pts)"
            else:
                ih_pill, ih_clr = "N/D", "#8b949e"
                ih_val = f"IV {atm_iv_pct:.1f}% (sin HV)"
                ih_sub = "Historial diario insuficiente para HV 20d"

            # 3) Proximidad & Cruce del Gamma Flip
            g_flip_val = float(res.get("Gamma Flip") or res.get("Zero GEX (Balance)") or 0.0)
            if g_flip_val > 0 and spot_now > 0:
                dist_flip = ((spot_now - g_flip_val) / g_flip_val) * 100.0
                sp_prev_f = float(iv_hist[-2].get("spot", spot_now)) if len(iv_hist) >= 2 else spot_now
                if len(iv_hist) >= 2 and sp_prev_f < g_flip_val <= spot_now:
                    fl_pill, fl_clr = "CRUCE ▲", "#2ecc71"
                    fl_sub = "Spot acaba de cruzar arriba → pasa a Gamma+"
                elif len(iv_hist) >= 2 and sp_prev_f > g_flip_val >= spot_now:
                    fl_pill, fl_clr = "CRUCE ▼", "#ff4d4d"
                    fl_sub = "Spot perdió el Flip → pasa a Gamma− acelerada"
                elif abs(dist_flip) <= 0.6:
                    fl_pill, fl_clr = "EN ZONA", "#ffcc00"
                    fl_sub = f"A solo {abs(dist_flip):.2f}% del Flip · Pivote de régimen"
                elif spot_now > g_flip_val:
                    fl_pill, fl_clr = "ARRIBA (+)", "#2ecc71"
                    fl_sub = "Arriba del Flip · Gamma+ amortiguada"
                else:
                    fl_pill, fl_clr = "BAJO FLIP", "#ff4d4d"
                    fl_sub = "Debajo del Flip · Gamma− movimientos rápidos"
                fl_val = f"${g_flip_val:.2f} ({dist_flip:+.2f}%)"
            else:
                fl_pill, fl_clr = "N/D", "#8b949e"
                fl_val = "Sin Gamma Flip"
                fl_sub = "No hay cruce de Gamma en el rango actual"

            # 4) Gap de Apertura & % de Expected Move usado
            if prev_close_val > 0 and em_1d > 0:
                day_move_abs = abs(spot_now - prev_close_val)
                em_used = (day_move_abs / em_1d) * 100.0
                gap_ref = today_open_val if today_open_val > 0 else spot_now
                gap_pct = ((gap_ref - prev_close_val) / prev_close_val) * 100.0
                outside_walls = (c_wall > 0 and spot_now > c_wall) or (p_wall > 0 and spot_now < p_wall)
                if outside_walls:
                    em_pill, em_clr = "UNPIN", "#ff8c00"
                    em_sub = "Spot fuera de muros · Posible re-hedge fuerte"
                elif em_used >= 75.0:
                    em_pill, em_clr = "EXTENDIDO", "#ffcc00"
                    em_sub = "Gran parte del rango diario ya recorrido · No perseguir"
                else:
                    em_pill, em_clr = "EN RANGO", "#2ecc71"
                    em_sub = "Aún queda recorrido disponible dentro del Expected Move"
                em_val = f"{em_used:.0f}% EM usado · Gap {gap_pct:+.1f}%"
            else:
                em_pill, em_clr = "N/D", "#8b949e"
                em_val = "Sin cierre previo"
                em_sub = f"Expected Move diario: ±${em_1d:.2f}"

            # 5) Semáforo de Liquidez de Opciones (±10% del Spot)
            n_oi = int(tac.get("near_oi_10", 0) or 0)
            n_vol = int(tac.get("near_vol_10", 0) or 0)
            oi_str = f"{n_oi/1000:.1f}K" if n_oi >= 1000 else f"{n_oi:,}"
            vol_str = f"{n_vol/1000:.1f}K" if n_vol >= 1000 else f"{n_vol:,}"
            if n_oi >= 15000 or n_vol >= 5000:
                lq_pill, lq_clr = "ALTA", "#2ecc71"
                lq_sub = "Cadena líquida · Muros GEX/VEX muy confiables"
            elif n_oi >= 2500 or n_vol >= 800:
                lq_pill, lq_clr = "MEDIA", "#ffcc00"
                lq_sub = "Liquidez moderada · Confirma muros con velas"
            else:
                lq_pill, lq_clr = "BAJA", "#ff4d4d"
                lq_sub = "Pocas opciones abiertas · Muros débiles, opera con velas"
            lq_val = f"{oi_str} OI · {vol_str} Vol"

            # 6) Detector de Trampas / Absorción en Muro (Put Wall & Call Wall)
            d_pwall = ((spot_now - p_wall) / spot_now * 100.0) if (spot_now > 0 and p_wall > 0) else 99.0
            d_cwall = ((c_wall - spot_now) / spot_now * 100.0) if (spot_now > 0 and c_wall > 0) else 99.0
            at_pwall = p_wall > 0 and (abs(d_pwall) <= 0.8 or spot_now <= p_wall * 1.005)
            at_cwall = c_wall > 0 and (abs(d_cwall) <= 0.8 or spot_now >= c_wall * 0.995)
            if at_pwall and iv_state == "down":
                wt_pill, wt_clr = "ABSORCIÓN PUT", "#2ecc71"
                wt_val = f"Toca Put Wall (${p_wall:g})"
                wt_sub = "IV bajando en soporte → posible trampa short / rebote"
            elif at_pwall and iv_state == "up":
                wt_pill, wt_clr = "PRESIÓN PUT", "#ff4d4d"
                wt_val = f"Presiona Put Wall (${p_wall:g})"
                wt_sub = "IV subiendo en soporte → riesgo de ruptura bajista"
            elif at_cwall and iv_state in ("down", "flat") and tot_gex_now >= 0:
                wt_pill, wt_clr = "RECHAZO CALL", "#ffcc00"
                wt_val = f"Toca Call Wall (${c_wall:g})"
                wt_sub = "Techo en Gamma+ con IV plana/baja → posible trampa long"
            elif at_cwall and iv_state == "up":
                wt_pill, wt_clr = "PRESIÓN CALL", "#2ecc71"
                wt_val = f"Presiona Call Wall (${c_wall:g})"
                wt_sub = "IV subiendo en techo → posible ruptura / Call Squeeze"
            elif at_pwall or at_cwall:
                wt_pill, wt_clr = "EN MURO", "#ffcc00"
                w_lbl = f"Put Wall ${p_wall:g}" if at_pwall else f"Call Wall ${c_wall:g}"
                wt_val = f"Pegado a {w_lbl}"
                wt_sub = "Refresca en vivo para confirmar si absorbe o rompe"
            else:
                wt_pill, wt_clr = "SIN TRAMPA", "#8b949e"
                wt_val = f"Put ${p_wall:g} · Call ${c_wall:g}"
                wt_sub = f"Put {-d_pwall:+.1f}% · Call {d_cwall:+.1f}% · Libre de muros"

            def _inst_cell(icon_title, pill, clr, val_txt, sub_txt, border_right=True):
                br = "border-right:1px solid #21262d;" if border_right else ""
                val_c = "#e6edf3" if clr == "#8b949e" else clr
                return (
                    f'<div style="padding:8px 11px;{br}min-width:0;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;gap:4px;margin-bottom:2px;">'
                    f'<span style="color:#8b949e;font-size:11px;font-weight:600;">{icon_title}</span>'
                    f'<span style="background:rgba(255,255,255,0.06);border:1px solid {clr};color:{clr};font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;white-space:nowrap;">{pill}</span>'
                    f'</div>'
                    f'<div style="color:{val_c};font-size:13.5px;font-weight:800;line-height:1.25;">{val_txt}</div>'
                    f'<div style="color:#9da7b3;font-size:11px;line-height:1.25;margin-top:2px;">{sub_txt}</div>'
                    f'</div>'
                )

            # Max Pain + Curva IV (Term Structure: Contango vs Backwardation) en el encabezado azul
            mp_hdr_val = float(tac.get("max_pain", 0.0) or 0.0)
            mp_hdr_html = (
                f'<span style="color:#c9d1d9;font-size:11px;">🎯 Max Pain: <b style="color:#ffcc00;">${mp_hdr_val:g}</b></span>'
                f'<span style="color:#30363d;margin:0 4px;">|</span>'
            ) if mp_hdr_val > 0 else ""

            ts_near = float(tac.get("ts_near_iv", 0.0) or 0.0)
            ts_far = float(tac.get("ts_far_iv", 0.0) or 0.0)
            ts_nd = int(tac.get("ts_near_dte", 0) or 0)
            ts_fd = int(tac.get("ts_far_dte", 0) or 0)
            if ts_near > 1.0 and ts_far > 1.0:
                ts_diff = ts_far - ts_near
                if ts_diff <= -2.0:
                    ts_lbl, ts_pill, ts_clr = f"BACKWARDATION ({ts_diff:+.1f}%)", "RISK", "#ff4d4d"
                elif ts_diff >= 2.0:
                    ts_lbl, ts_pill, ts_clr = f"CONTANGO ({ts_diff:+.1f}%)", "NORMAL", "#2ecc71"
                else:
                    ts_lbl, ts_pill, ts_clr = f"PLANA ({ts_diff:+.1f}%)", "NEUTRO", "#ffcc00"
                ts_header_html = (
                    f'<span style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;">'
                    f'{mp_hdr_html}'
                    f'<span style="color:#8b949e;">Curva IV ({ts_nd}d: {ts_near:.0f}% vs {ts_fd}d: {ts_far:.0f}%):</span>'
                    f'<b style="color:{ts_clr};">{ts_lbl}</b>'
                    f'<span style="background:rgba(255,255,255,0.06);border:1px solid {ts_clr};color:{ts_clr};font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;">{ts_pill}</span>'
                    f'</span>'
                )
            else:
                ts_header_html = (
                    f'<span style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;">'
                    f'{mp_hdr_html}'
                    f'<span style="color:#8b949e;">6 filtros en tiempo real · FlashAlpha</span>'
                    f'</span>'
                )

            inst_grid_html = (
                '<div style="background:#0f141c;border:1.5px solid #348feb;border-radius:8px;overflow:hidden;margin-top:10px;margin-bottom:10px;">'
                '<div style="background:rgba(52,143,235,0.14);border-bottom:1px solid #21262d;padding:5px 12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;">'
                f'<span style="color:#58a6ff;font-size:11.5px;font-weight:800;letter-spacing:0.3px;">✨ CONTEXTO INSTITUCIONAL EN VIVO ({ticker})</span>'
                f'{ts_header_html}'
                '</div>'
                '<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-bottom:1px solid #21262d;">'
                f'{_inst_cell("1. ⚖️ IV Skew (OTM)", sk_pill, sk_clr, sk_val, sk_sub, True)}'
                f'{_inst_cell("2. 💰 IV vs HV 20d", ih_pill, ih_clr, ih_val, ih_sub, True)}'
                f'{_inst_cell("3. 🎯 Gamma Flip", fl_pill, fl_clr, fl_val, fl_sub, False)}'
                '</div>'
                '<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));">'
                f'{_inst_cell("4. 🌅 Gap & Rango EM", em_pill, em_clr, em_val, em_sub, True)}'
                f'{_inst_cell("5. 💧 Liquidez Opciones", lq_pill, lq_clr, lq_val, lq_sub, True)}'
                f'{_inst_cell("6. 🛡️ Muros & Trampas", wt_pill, wt_clr, wt_val, wt_sub, False)}'
                '</div></div>'
            )
            st.markdown(inst_grid_html, unsafe_allow_html=True)

            # Detector de Squeeze & Confluencias en Vivo (Las 4 Opciones Visibles al Mismo Tiempo + Hora Exacta HH:MM:SS + Silbato Festivo, Sirena y Voz)
            c_prem_sq = tac["total_call_prem_vol"]
            p_prem_sq = tac["total_put_prem_vol"]
            tot_prem_sq = c_prem_sq + p_prem_sq
            call_share_sq = (c_prem_sq / tot_prem_sq * 100.0) if tot_prem_sq > 0 else 50.0
            put_share_sq = 100.0 - call_share_sq
            near_cwall = c_wall > 0 and spot_now >= c_wall * 0.995
            near_pwall = p_wall > 0 and spot_now <= p_wall * 1.005
            dist_pin_pct = (abs(spot_now - pin_st) / spot_now * 100.0) if spot_now > 0 else 99.0

            bull_sq_checks = [
                (spot_state == "up", f"Spot ▲ ({spot_ref_pct:+.2f}%)" if spot_state == "up" else "Spot ▲"),
                (iv_state == "up", "IV ▲"),
                (call_share_sq >= 58.0 or f_dir_now == "bullish", f"Calls {call_share_sq:.0f}%"),
                (tot_gex_now < 0 or near_cwall, "Gamma− / Call Wall"),
            ]
            bear_sq_checks = [
                (spot_state == "down", f"Spot ▼ ({spot_ref_pct:+.2f}%)" if spot_state == "down" else "Spot ▼"),
                (iv_state == "up", "IV ▲"),
                (put_share_sq >= 60.0 or f_dir_now == "bearish", f"Puts {put_share_sq:.0f}%"),
                ((tot_gex_now < 0 and net_vex_val < 0) or (tot_gex_now < 0 and near_pwall), "Gamma− + VEX−"),
            ]
            vanna_checks = [
                (spot_state == "up" or (spot_now >= p_wall and spot_state != "down"), "Spot firme"),
                (iv_state == "down", "IV ▼"),
                (net_vex_val > 0 or net_chex_val > 0, "VEX+ / CHEX+"),
                (tot_gex_now >= 0 or spot_now > p_wall, "Arriba Put Wall"),
            ]
            pin_checks = [
                (dist_pin_pct <= 0.6, f"Cerca Pin ${pin_st:g}"),
                (pin_sc >= 65.0, f"Pin {pin_sc:.0f}/100"),
                (iv_state in ("down", "flat"), "IV ▼/●"),
                ((p_wall <= spot_now <= c_wall) if c_wall > p_wall else (spot_state == "flat"), "En rango"),
            ]

            if "signal_activation_times" not in st.session_state:
                st.session_state.signal_activation_times = {}
            if "signal_history_log" not in st.session_state:
                st.session_state.signal_history_log = []

            four_scenarios = [
                {
                    "id": "bull_squeeze",
                    "icon": "🚀",
                    "title": "SQUEEZE ALCISTA",
                    "active_title": "SQUEEZE ALCISTA CONFIRMADO",
                    "pre_title": "PRE-ALERTA SQUEEZE ALCISTA",
                    "note": "Precio + IV subiendo con flujo agresivo en Calls (FOMO / cobertura alcista).",
                    "voice_name": "Squeeze Alcista",
                    "sound_type": "whistle",
                    "direction": "bullish",
                    "active_clr": "#2ecc71",
                    "active_bg": "rgba(46,204,113,0.14)",
                    "pre_clr": "#ffcc00",
                    "pre_bg": "rgba(255,204,0,0.10)",
                    "checks": bull_sq_checks,
                },
                {
                    "id": "bear_cascade",
                    "icon": "💥",
                    "title": "CASCADA BAJISTA",
                    "active_title": "CASCADA BAJISTA ACTIVA",
                    "pre_title": "ALERTA PRESIÓN BAJISTA",
                    "note": "Gamma− + VEX− + IV subiendo con Puts dominando (aceleración bajista).",
                    "voice_name": "Cascada Bajista",
                    "sound_type": "siren",
                    "direction": "bearish",
                    "active_clr": "#ff4d4d",
                    "active_bg": "rgba(255,77,77,0.14)",
                    "pre_clr": "#ff8c00",
                    "pre_bg": "rgba(255,140,0,0.11)",
                    "checks": bear_sq_checks,
                },
                {
                    "id": "vanna_rally",
                    "icon": "🟢",
                    "title": "VANNA RALLY / SUBIDA CONTROLADA",
                    "active_title": "VANNA RALLY ACTIVO",
                    "pre_title": "PRE-ALERTA VANNA RALLY",
                    "note": "IV bajando con soporte estructural (empuje alcista ordenado).",
                    "voice_name": "Vanna Rally Alcista",
                    "sound_type": "whistle",
                    "direction": "bullish",
                    "active_clr": "#2ecc71",
                    "active_bg": "rgba(46,204,113,0.12)",
                    "pre_clr": "#ffcc00",
                    "pre_bg": "rgba(255,204,0,0.10)",
                    "checks": vanna_checks,
                },
                {
                    "id": "pin_mode",
                    "icon": "🧲",
                    "title": "MODO PIN / COMPRESIÓN",
                    "active_title": "MODO PIN / COMPRESIÓN ACTIVO",
                    "pre_title": "CERCA DE MODO PIN",
                    "note": "Precio atrapado cerca del Pin quemando prima (precaución con theta).",
                    "voice_name": "Modo Pin y Compresión",
                    "sound_type": "pin",
                    "direction": "neutral",
                    "active_clr": "#ffcc00",
                    "active_bg": "rgba(255,204,0,0.13)",
                    "pre_clr": "#d4a017",
                    "pre_bg": "rgba(212,160,23,0.09)",
                    "checks": pin_checks,
                },
            ]

            now_clock_str = now_ca_str()
            newly_triggered_signals = []

            for sc in four_scenarios:
                met_n = sum(1 for ok, _ in sc["checks"] if ok)
                tot_n = len(sc["checks"])
                sc["met_n"] = met_n
                sc["tot_n"] = tot_n
                sc["is_active"] = (met_n == tot_n)

                st_key = f"{ticker}_{sc['id']}"
                prev_st = st.session_state.signal_activation_times.get(st_key)

                if sc["is_active"]:
                    if not prev_st or not prev_st.get("active"):
                        st.session_state.signal_activation_times[st_key] = {
                            "active": True,
                            "time_str": now_clock_str,
                            "timestamp": time.time(),
                        }
                        sc["activated_at"] = now_clock_str
                        sc["elapsed_txt"] = "recién activada"
                        newly_triggered_signals.append({
                            "id": sc["id"],
                            "title": sc["active_title"],
                            "voice_name": sc["voice_name"],
                            "sound_type": sc["sound_type"],
                            "direction": sc["direction"],
                            "time_str": now_clock_str,
                            "ticker": ticker,
                        })
                        st.session_state.signal_history_log.insert(0, {
                            "time_str": now_clock_str,
                            "ticker": ticker,
                            "icon": sc["icon"],
                            "title": sc["active_title"],
                            "direction": sc["direction"],
                        })
                        st.session_state.signal_history_log = st.session_state.signal_history_log[:12]
                    else:
                        sc["activated_at"] = prev_st["time_str"]
                        el_s = max(0, int(time.time() - prev_st["timestamp"]))
                        sc["elapsed_txt"] = f"hace {el_s}s" if el_s < 60 else f"hace {el_s // 60}m {el_s % 60:02d}s"
                else:
                    last_t = prev_st.get("time_str") if prev_st else None
                    st.session_state.signal_activation_times[st_key] = {
                        "active": False,
                        "time_str": last_t,
                        "timestamp": prev_st.get("timestamp", 0) if prev_st else 0,
                    }
                    sc["activated_at"] = None
                    sc["last_activated_at"] = last_t

            # Código de 1-Clic para copiar y pegar directo en el indicador de TradingView (Pine Script)
            gex_code_num = 1 if tot_gex_now >= 0 else -1
            vex_code_num = 1 if net_vex_val >= 0 else -1
            chex_code_num = 1 if net_chex_val >= 0 else -1
            gamma_flip_val = float(res.get("Zero GEX (Balance)", spot_now) or spot_now)
            tv_one_click_code = (
                f"FLIP={gamma_flip_val:.2f}|CW={c_wall:.2f}|PW={p_wall:.2f}|PIN={pin_st:.2f}|"
                f"SCORE={pin_sc:.0f}|GEX={gex_code_num}|VEX={vex_code_num}|CHEX={chex_code_num}|"
                f"CALLS={call_share_sq:.0f}|PUTS={put_share_sq:.0f}|EMH={em_high:.2f}|EML={em_low:.2f}"
            )

            # Barra de control de Audio (Silbato Festivo + Sirena + Voz en Español) + Copiar a TradingView en 1 Clic
            triggered_json = json.dumps(newly_triggered_signals)
            tv_code_json = json.dumps(tv_one_click_code)
            audio_component_html = f"""
            <style>body {{ margin: 0; padding: 0; background: transparent; }}</style>
            <div style="background:#11161f;border:1px solid #283040;border-radius:10px;padding:10px 14px;display:flex;flex-direction:column;gap:8px;font-family:'Inter',sans-serif;">
                <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <span style="color:#e6edf3;font-size:13px;font-weight:700;">🔊 Radar de 4 Señales y Alarmas ({ticker}):</span>
                        <span id="audio_status_badge" style="background:#132a1c;border:1px solid #2ecc71;color:#2ecc71;font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;">AUDIO + VOZ ACTIVOS</span>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <button onclick="testWhistleAlert()" style="background:#132f20;border:1px solid #2ecc71;color:#7ee787;padding:5px 11px;border-radius:7px;font-size:11.5px;font-weight:700;cursor:pointer;">
                            🎉 Probar Silbato + Voz (Subida)
                        </button>
                        <button onclick="testSirenAlert()" style="background:#341417;border:1px solid #ff4d4d;color:#ff7b72;padding:5px 11px;border-radius:7px;font-size:11.5px;font-weight:700;cursor:pointer;">
                            🚨 Probar Sirena + Voz (Caída)
                        </button>
                        <button id="mute_btn" onclick="toggleMute()" style="background:#1c212b;border:1px solid #3a4252;color:#c9d1d9;padding:5px 11px;border-radius:7px;font-size:11.5px;font-weight:600;cursor:pointer;">
                            🔇 Silenciar
                        </button>
                    </div>
                </div>
                <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;background:#0b0e14;border:1px solid #21262d;border-radius:8px;padding:6px 10px;">
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:240px;">
                        <span style="color:#58a6ff;font-size:12px;font-weight:700;white-space:nowrap;">📺 TradingView (1 Clic):</span>
                        <input id="tv_code_input" type="text" readonly value="{tv_one_click_code}" onclick="this.select();" style="background:#161b22;border:1px solid #30363d;color:#c9d1d9;font-family:monospace;font-size:11.5px;padding:4px 8px;border-radius:5px;width:100%;outline:none;" />
                    </div>
                    <button id="copy_tv_btn" onclick="copyTvCode()" style="background:#1f3a5f;border:1px solid #388bfd;color:#79c0ff;padding:5px 12px;border-radius:7px;font-size:11.5px;font-weight:700;cursor:pointer;white-space:nowrap;">
                        📋 Copiar para TradingView
                    </button>
                </div>
            </div>
            <script>
            (function() {{
                let isMuted = localStorage.getItem('gex_alarm_muted') === 'true';
                const badge = document.getElementById('audio_status_badge');
                const muteBtn = document.getElementById('mute_btn');

                function updateMuteUI() {{
                    if (isMuted) {{
                        badge.textContent = 'SILENCIADO';
                        badge.style.background = '#262933';
                        badge.style.borderColor = '#6e7681';
                        badge.style.color = '#8b949e';
                        muteBtn.textContent = '🔊 Activar Sonido';
                    }} else {{
                        badge.textContent = 'AUDIO + VOZ ACTIVOS';
                        badge.style.background = '#132a1c';
                        badge.style.borderColor = '#2ecc71';
                        badge.style.color = '#2ecc71';
                        muteBtn.textContent = '🔇 Silenciar';
                    }}
                }}
                updateMuteUI();

                window.toggleMute = function() {{
                    isMuted = !isMuted;
                    localStorage.setItem('gex_alarm_muted', isMuted ? 'true' : 'false');
                    if (isMuted && 'speechSynthesis' in window) {{
                        window.speechSynthesis.cancel();
                    }}
                    updateMuteUI();
                }};

                // Silbato Festivo de Alegría (Subida)
                window.playFestiveWhistle = function(onDone) {{
                    if (isMuted) return;
                    try {{
                        const AudioCtx = window.AudioContext || window.webkitAudioContext;
                        const ctx = new AudioCtx();
                        const now = ctx.currentTime;

                        const osc1 = ctx.createOscillator();
                        const gain1 = ctx.createGain();
                        const lfo = ctx.createOscillator();
                        const lfoGain = ctx.createGain();

                        osc1.type = 'triangle';
                        osc1.frequency.setValueAtTime(520, now);
                        osc1.frequency.exponentialRampToValueAtTime(1480, now + 0.38);
                        osc1.frequency.setValueAtTime(1480, now + 0.55);
                        osc1.frequency.exponentialRampToValueAtTime(1180, now + 0.68);

                        lfo.frequency.setValueAtTime(22, now);
                        lfoGain.gain.setValueAtTime(45, now);
                        lfo.connect(lfoGain);
                        lfoGain.connect(osc1.frequency);

                        gain1.gain.setValueAtTime(0.01, now);
                        gain1.gain.linearRampToValueAtTime(0.32, now + 0.06);
                        gain1.gain.setValueAtTime(0.30, now + 0.52);
                        gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.70);

                        osc1.connect(gain1);
                        gain1.connect(ctx.destination);
                        lfo.start(now);
                        osc1.start(now);
                        lfo.stop(now + 0.70);
                        osc1.stop(now + 0.70);

                        const notes = [1046.5, 1318.5, 1567.98, 2093.0];
                        notes.forEach((freq, idx) => {{
                            const osc = ctx.createOscillator();
                            const g = ctx.createGain();
                            const startT = now + 0.74 + idx * 0.11;
                            const dur = idx === notes.length - 1 ? 0.45 : 0.13;
                            osc.type = 'sine';
                            osc.frequency.setValueAtTime(freq * 0.92, startT);
                            osc.frequency.exponentialRampToValueAtTime(freq, startT + 0.04);
                            g.gain.setValueAtTime(0.01, startT);
                            g.gain.linearRampToValueAtTime(0.28, startT + 0.02);
                            g.gain.exponentialRampToValueAtTime(0.001, startT + dur);
                            osc.connect(g);
                            g.connect(ctx.destination);
                            osc.start(startT);
                            osc.stop(startT + dur);
                        }});

                        setTimeout(() => {{
                            if (typeof onDone === 'function') onDone();
                        }}, 1550);
                    }} catch (e) {{
                        if (typeof onDone === 'function') onDone();
                    }}
                }};

                // Sirena de Alarma (Caída)
                window.playSirenAlarm = function(onDone) {{
                    if (isMuted) return;
                    try {{
                        const AudioCtx = window.AudioContext || window.webkitAudioContext;
                        const ctx = new AudioCtx();
                        const now = ctx.currentTime;

                        const osc = ctx.createOscillator();
                        const gain = ctx.createGain();
                        osc.type = 'sawtooth';

                        osc.frequency.setValueAtTime(420, now);
                        osc.frequency.linearRampToValueAtTime(880, now + 0.35);
                        osc.frequency.linearRampToValueAtTime(380, now + 0.75);
                        osc.frequency.linearRampToValueAtTime(880, now + 1.10);
                        osc.frequency.linearRampToValueAtTime(340, now + 1.50);

                        gain.gain.setValueAtTime(0.01, now);
                        gain.gain.linearRampToValueAtTime(0.26, now + 0.05);
                        gain.gain.setValueAtTime(0.26, now + 1.35);
                        gain.gain.exponentialRampToValueAtTime(0.001, now + 1.55);

                        osc.connect(gain);
                        gain.connect(ctx.destination);
                        osc.start(now);
                        osc.stop(now + 1.55);

                        setTimeout(() => {{
                            if (typeof onDone === 'function') onDone();
                        }}, 1600);
                    }} catch (e) {{
                        if (typeof onDone === 'function') onDone();
                    }}
                }};

                window.speakSpanishAlert = function(text) {{
                    if (isMuted || !('speechSynthesis' in window)) return;
                    try {{
                        window.speechSynthesis.cancel();
                        const utter = new SpeechSynthesisUtterance(text);
                        utter.lang = 'es-MX';
                        utter.rate = 1.03;
                        utter.pitch = 1.05;
                        const voices = window.speechSynthesis.getVoices();
                        const esVoice = voices.find(v => v.lang && v.lang.toLowerCase().startsWith('es'));
                        if (esVoice) utter.voice = esVoice;
                        window.speechSynthesis.speak(utter);
                    }} catch (e) {{}}
                }};

                function getNowTimeVoice() {{
                    const d = new Date();
                    return d.getHours() + ' horas con ' + d.getMinutes() + ' minutos y ' + d.getSeconds() + ' segundos';
                }}

                window.testWhistleAlert = function() {{
                    if (isMuted) window.toggleMute();
                    const t = getNowTimeVoice();
                    window.playFestiveWhistle(() => {{
                        window.speakSpanishAlert('¡Señal de subida! Squeeze Alcista cuatro de cuatro activado en {ticker} a las ' + t);
                    }});
                }};

                window.testSirenAlert = function() {{
                    if (isMuted) window.toggleMute();
                    const t = getNowTimeVoice();
                    window.playSirenAlarm(() => {{
                        window.speakSpanishAlert('¡Alerta de caída! Cascada Bajista cuatro de cuatro activada en {ticker} a las ' + t);
                    }});
                }};

                window.copyTvCode = function() {{
                    const codeStr = {tv_code_json};
                    const btn = document.getElementById('copy_tv_btn');
                    const inp = document.getElementById('tv_code_input');
                    try {{
                        if (inp) {{
                            inp.select();
                            inp.setSelectionRange(0, 99999);
                        }}
                        if (navigator.clipboard && navigator.clipboard.writeText) {{
                            navigator.clipboard.writeText(codeStr);
                        }} else {{
                            document.execCommand('copy');
                        }}
                        btn.textContent = '✅ ¡Copiado! Pégalo en TradingView';
                        btn.style.background = '#132f20';
                        btn.style.borderColor = '#2ecc71';
                        btn.style.color = '#7ee787';
                        setTimeout(() => {{
                            btn.textContent = '📋 Copiar para TradingView';
                            btn.style.background = '#1f3a5f';
                            btn.style.borderColor = '#388bfd';
                            btn.style.color = '#79c0ff';
                        }}, 3000);
                    }} catch (e) {{
                        document.execCommand('copy');
                    }}
                }};

                const newlyTriggered = {triggered_json};
                if (newlyTriggered && newlyTriggered.length > 0 && !isMuted) {{
                    const firstSig = newlyTriggered[0];
                    const parts = (firstSig.time_str || '').split(':');
                    const spokenTime = parts.length === 3
                        ? (parseInt(parts[0], 10) + ' horas con ' + parseInt(parts[1], 10) + ' minutos y ' + parseInt(parts[2], 10) + ' segundos')
                        : firstSig.time_str;

                    if (firstSig.sound_type === 'siren') {{
                        window.playSirenAlarm(() => {{
                            window.speakSpanishAlert('¡Alerta de caída! ' + firstSig.voice_name + ' cuatro de cuatro activada en ' + firstSig.ticker + ' a las ' + spokenTime);
                        }});
                    }} else {{
                        window.playFestiveWhistle(() => {{
                            window.speakSpanishAlert('¡Atención! Señal ' + firstSig.voice_name + ' cuatro de cuatro activada en ' + firstSig.ticker + ' a las ' + spokenTime);
                        }});
                    }}
                }}
            }})();
            </script>
            """
            components.html(audio_component_html, height=118, scrolling=False)

            # Renderizar las 4 Opciones Simultáneamente en un Grid 2x2
            sq_col1, sq_col2 = st.columns(2)
            for idx_sc, sc in enumerate(four_scenarios):
                target_col = sq_col1 if idx_sc % 2 == 0 else sq_col2
                met_n = sc["met_n"]
                tot_n = sc["tot_n"]

                if met_n == 4:
                    sq_title = f"{sc['icon']} {sc['active_title']} (4/4)"
                    sq_clr = sc["active_clr"]
                    sq_bg = sc["active_bg"]
                    time_badge = (
                        f'<span style="background:rgba(0,0,0,0.35);border:1px solid {sq_clr};color:#ffffff;'
                        f'font-family:monospace;font-size:11.5px;font-weight:800;padding:2px 9px;border-radius:999px;">'
                        f'🕒 Activada: {sc["activated_at"]} PT ({sc["elapsed_txt"]})</span>'
                    )
                elif met_n == 3:
                    sq_title = f"🟠 {sc['pre_title']} (3/4)"
                    sq_clr = sc["pre_clr"]
                    sq_bg = sc["pre_bg"]
                    last_txt = f" · Últ: {sc['last_activated_at']}" if sc.get("last_activated_at") else ""
                    time_badge = (
                        f'<span style="background:rgba(255,204,0,0.14);border:1px solid {sq_clr};color:{sq_clr};'
                        f'font-family:monospace;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;">'
                        f'⏳ Falta 1 requisito{last_txt}</span>'
                    )
                else:
                    sq_title = f"{sc['icon']} {sc['title']} ({met_n}/4)"
                    sq_clr = "#8b949e"
                    sq_bg = "rgba(139,148,158,0.08)"
                    last_txt = f"Últ. activa: {sc['last_activated_at']} PT" if sc.get("last_activated_at") else "En espera"
                    time_badge = (
                        f'<span style="background:#161b22;border:1px solid #30363d;color:#8b949e;'
                        f'font-family:monospace;font-size:11px;padding:2px 8px;border-radius:999px;">'
                        f'🕒 {last_txt}</span>'
                    )

                checks_parts = []
                for ok, lbl in sc["checks"]:
                    bg_c = "rgba(46,204,113,0.14)" if ok else "rgba(139,148,158,0.12)"
                    fg_c = "#e6edf3" if ok else "#8b949e"
                    bd_c = "rgba(46,204,113,0.35)" if ok else "#30363d"
                    fw_c = "600" if ok else "400"
                    ic_c = "✅" if ok else "⬜"
                    checks_parts.append(
                        f'<span style="background:{bg_c};color:{fg_c};border:1px solid {bd_c};'
                        f'padding:2px 8px;border-radius:12px;font-size:11.5px;font-weight:{fw_c};">'
                        f'{ic_c} {lbl}</span>'
                    )
                checks_html = " ".join(checks_parts)

                with target_col:
                    st.markdown(
                        f'<div style="background:{sq_bg};border:1px solid {sq_clr};border-left:4px solid {sq_clr};border-radius:8px;padding:10px 14px;margin-top:8px;">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:4px;">'
                        f'<span style="color:{sq_clr};font-weight:800;font-size:13.5px;">{sq_title}</span>'
                        f'{time_badge}'
                        f'</div>'
                        f'<div style="color:#c9d1d9;font-size:12px;margin-bottom:8px;">{sc["note"]}</div>'
                        f'<div style="display:flex;flex-wrap:wrap;gap:6px;">{checks_html}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            if len(iv_hist) < 2:
                st.caption("💡 Pulsa **🔄 Actualizar En Vivo** para registrar nuevos puntos de Spot e IV ATM en la sesión y activar la comparación de tendencia.")

            if st.session_state.signal_history_log:
                log_badges = []
                for item in st.session_state.signal_history_log[:6]:
                    c_log = "#ff7b72" if item["direction"] == "bearish" else ("#7ee787" if item["direction"] == "bullish" else "#ffcc00")
                    log_badges.append(
                        f'<span style="background:#161b22;border:1px solid #30363d;padding:2px 9px;border-radius:999px;font-size:11.5px;font-family:monospace;color:{c_log};">'
                        f'🕒 <b>{item["time_str"]} PT</b> · {item["icon"]} {item["title"]} ({item["ticker"]})</span>'
                    )
                st.markdown(
                    f'<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:7px 12px;margin-top:8px;display:flex;align-items:center;flex-wrap:wrap;gap:8px;">'
                    f'<span style="color:#8b949e;font-size:11.5px;font-weight:700;">📜 Historial de Activaciones:</span>'
                    f'{" ".join(log_badges)}</div>',
                    unsafe_allow_html=True,
                )

            # Barra de Veredicto Operativo Automático

            bull_pts = 0
            bear_pts = 0
            if tot_gex_now >= 0:
                bull_pts += 1
            else:
                bear_pts += 1
            if net_vex_val >= 0:
                bull_pts += 1
            else:
                bear_pts += 1
            if f_dir_now == "bullish":
                bull_pts += 2
            elif f_dir_now == "bearish":
                bear_pts += 2
            if tac["total_call_prem_vol"] > tac["total_put_prem_vol"] * 1.15:
                bull_pts += 1
            elif tac["total_put_prem_vol"] > tac["total_call_prem_vol"] * 1.15:
                bear_pts += 1

            if bull_pts > bear_pts:
                v_badge = "🟢 SESGO TÁCTICO: ALCISTA / BUSCAR LONG EN SOPORTE"
                v_border = "#2ecc71"
            elif bear_pts > bull_pts:
                v_badge = "🔴 SESGO TÁCTICO: PRECAUCIÓN BAJISTA / RIESGO DE ACELERACIÓN"
                v_border = "#ff4d4d"
            else:
                v_badge = "🟡 SESGO TÁCTICO: NEUTRAL / OPERAR RANGO Y PIN"
                v_border = "#ffcc00"

            gex_regime_txt = (
                "GAMMA NEGATIVA (Volatilidad alta / Movimientos acelerados)"
                if tot_gex_now < 0
                else "GAMMA POSITIVA (Mercado amortiguado / Reversión a la media)"
            )

            # Construir Plan Long y Plan Short respetando el orden real de los niveles
            if p_wall < em_low < spot_now and (em_low - p_wall) > 0.05:
                long_support_txt = f"piso diario (<b>${em_low:.2f}</b>) o Put Wall (<b>${p_wall:g}</b>)"
            elif p_wall < spot_now:
                long_support_txt = f"soporte/Put Wall en <b>${p_wall:g}</b>"
            else:
                long_support_txt = f"recuperación sobre <b>${p_wall:g}</b>"

            long_targets = []
            if abs(pin_st - c_wall) < 1e-6:
                if c_wall > p_wall + 1e-6:
                    long_targets.append((c_wall, f"Imán/Call Wall <b>${c_wall:g}</b>"))
            else:
                if pin_st > p_wall + 1e-6:
                    long_targets.append((pin_st, f"Imán/Pin <b>${pin_st:g}</b>"))
                if c_wall > p_wall + 1e-6:
                    long_targets.append((c_wall, f"Call Wall <b>${c_wall:g}</b>"))
            long_targets.sort(key=lambda item: item[0])

            if len(long_targets) == 2:
                conj = "e" if long_targets[1][1].startswith("Imán") else "y"
                long_target_txt = f"apuntando a {long_targets[0][1]} {conj} {long_targets[1][1]}"
            elif len(long_targets) == 1:
                long_target_txt = f"apuntando a {long_targets[0][1]}"
            else:
                long_target_txt = f"apuntando al techo diario (<b>${em_high:.2f}</b>)"

            plan_long_html = (
                f"• <b>Plan Long:</b> Buscar compras si sostiene {long_support_txt} (con IV estable o bajando), "
                f"{long_target_txt}. Evitar perseguir arriba del límite diario (<b>${em_high:.2f}</b>)."
            )

            if em_low < p_wall - 0.05:
                plan_short_html = (
                    f"• <b>Plan Short:</b> Si pierde el Put Wall (<b>${p_wall:g}</b>) con aumento de IV y flujo vendedor, "
                    f"hay riesgo de aceleración rápida hacia el piso del rango esperado (<b>${em_low:.2f}</b>)."
                )
            elif em_low > p_wall + 0.05:
                plan_short_html = (
                    f"• <b>Plan Short:</b> Si pierde el piso del rango esperado (<b>${em_low:.2f}</b>) con aumento de IV y flujo vendedor, "
                    f"hay riesgo de aceleración hacia el Put Wall (<b>${p_wall:g}</b>); perder <b>${p_wall:g}</b> abriría extensión bajista fuera del rango diario."
                )
            else:
                plan_short_html = (
                    f"• <b>Plan Short:</b> Si pierde la confluencia de Put Wall y piso diario (<b>${p_wall:g}</b>) con aumento de IV y flujo vendedor, "
                    f"hay riesgo de extensión bajista acelerada fuera del rango esperado."
                )

            verdict_html = (
                f'<div style="background:#0b0e14;border:1.5px solid {v_border};border-radius:10px;padding:14px 18px;margin-top:14px;margin-bottom:16px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">'
                f'<span style="color:{v_border};font-weight:800;font-size:15px;letter-spacing:0.4px;">{v_badge}</span>'
                f'<span style="color:#8b949e;font-size:12px;font-family:monospace;">Régimen: {gex_regime_txt}</span>'
                f'</div>'
                f'<div style="color:#d0d7de;font-size:13.5px;line-height:1.55;">'
                f'{plan_long_html}<br>'
                f'{plan_short_html}'
                f'</div>'
                f'</div>'
            )
            st.markdown(verdict_html, unsafe_allow_html=True)

            # 4 Pestañas Interactivas Compactas
            tab_vex, tab_chex, tab_pin, tab_radar = st.tabs([
                "📊 VEX (Vanna) por Strike",
                "⏳ CHEX (Charm) por Strike",
                "🎯 Flow Levels & Pin Risk",
                "🌊 Trade Flow & Ratio Vol/OI",
            ])

            with tab_vex:
                df_vp = df_gex[(df_gex["strike"] >= spot_now * (1 - rango_pct / 100.0)) & (df_gex["strike"] <= spot_now * (1 + rango_pct / 100.0))].copy()
                fig_vex = go.Figure()
                if "call_vex" in df_vp.columns:
                    fig_vex.add_trace(go.Bar(x=df_vp["strike"], y=df_vp["call_vex"], name="VEX Calls (Arriba)", marker_color="#2ecc71", opacity=0.85))
                    fig_vex.add_trace(go.Bar(x=df_vp["strike"], y=df_vp["put_vex"], name="VEX Puts (Abajo)", marker_color="#348feb", opacity=0.85))
                    fig_vex.add_trace(go.Scatter(x=df_vp["strike"], y=df_vp["net_vex"], mode="lines+markers", line=dict(color="#ffcc00", width=2), name="VEX Neto"))
                fig_vex.add_vline(x=spot_now, line_dash="dash", line_color="#ffcc00", line_width=2, annotation_text=f"Spot: ${spot_now:.2f}", annotation_position="top right")
                fig_vex.update_layout(
                    title=f"Vanna Exposure (VEX) por Strike — Impacto por cada 1% de cambio en IV ({engine_mode})",
                    xaxis_title="Precio Strike",
                    yaxis_title=f"Exposición Vanna ({engine_mode})",
                    template="plotly_dark",
                    plot_bgcolor="#0d0e15",
                    paper_bgcolor="#0d0e15",
                    barmode="relative",
                    margin=dict(l=30, r=30, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_vex, use_container_width=True)
                # Barra compacta de historial de IV ATM en la sesión
                iv_trail_txt = "  →  ".join([f"{pt['ts'][:5]}: {pt['iv']:.1f}%" for pt in iv_hist[-6:]])
                if len(iv_hist) == 1:
                    spark_svg = (
                        f'<svg width="150" height="30" viewBox="0 0 150 30">'
                        f'<line x1="6" y1="15" x2="144" y2="15" stroke="{iv_badge_fg}" stroke-dasharray="4 4" stroke-width="1.5" opacity="0.6"/>'
                        f'<circle cx="75" cy="15" r="3.5" fill="{iv_badge_fg}"/></svg>'
                    )
                else:
                    pts_v = [pt["iv"] for pt in iv_hist[-10:]]
                    v_min = min(pts_v) - 0.25
                    v_max = max(pts_v) + 0.25
                    coords = []
                    for idx_p, val_p in enumerate(pts_v):
                        px = 6 + (idx_p / max(len(pts_v) - 1, 1)) * 138
                        py = 25 - ((val_p - v_min) / max(v_max - v_min, 0.01)) * 20
                        coords.append((px, py))
                    poly_str = " ".join([f"{x:.1f},{y:.1f}" for x, y in coords])
                    dots_str = "".join([
                        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3.5 if i == len(coords) - 1 else 2}" fill="{iv_badge_fg}"/>'
                        for i, (x, y) in enumerate(coords)
                    ])
                    spark_svg = (
                        f'<svg width="150" height="30" viewBox="0 0 150 30">'
                        f'<polyline fill="none" stroke="{iv_badge_fg}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="{poly_str}"/>'
                        f'{dots_str}</svg>'
                    )
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;margin-top:6px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">'
                    f'<div><div style="color:#ffffff;font-size:12px;font-weight:700;">Historial de IV ATM en tu sesión <span style="color:#8b949e;font-weight:400;">(con cada actualización)</span></div>'
                    f'<div style="color:#c9d1d9;font-size:12px;font-family:monospace;margin-top:2px;">{iv_trail_txt}</div></div>'
                    f'{spark_svg}</div>',
                    unsafe_allow_html=True,
                )
                st.caption("💡 **Cómo leer VEX:** En strikes con VEX positivo (verde), una caída de la Volatilidad Implícita (IV) obliga a los dealers a **comprar subyacente** (combustible alcista). En strikes con VEX negativo (azul), una subida de IV acelera las ventas.")

            with tab_chex:
                df_cp = df_gex[(df_gex["strike"] >= spot_now * (1 - rango_pct / 100.0)) & (df_gex["strike"] <= spot_now * (1 + rango_pct / 100.0))].copy()
                fig_chex = go.Figure()
                if "call_chex" in df_cp.columns:
                    fig_chex.add_trace(go.Bar(x=df_cp["strike"], y=df_cp["call_chex"], name="CHEX Calls (Arriba)", marker_color="#a371f7", opacity=0.85))
                    fig_chex.add_trace(go.Bar(x=df_cp["strike"], y=df_cp["put_chex"], name="CHEX Puts (Abajo)", marker_color="#348feb", opacity=0.85))
                    fig_chex.add_trace(go.Scatter(x=df_cp["strike"], y=df_cp["net_chex"], mode="lines+markers", line=dict(color="#ff003c", width=2), name="CHEX Neto"))
                fig_chex.add_vline(x=spot_now, line_dash="dash", line_color="#ffcc00", line_width=2, annotation_text=f"Spot: ${spot_now:.2f}", annotation_position="top right")
                fig_chex.update_layout(
                    title=f"Charm Exposure (CHEX) por Strike — Flujo diario por paso del tiempo ({engine_mode})",
                    xaxis_title="Precio Strike",
                    yaxis_title=f"Exposición Charm / Día ({engine_mode})",
                    template="plotly_dark",
                    plot_bgcolor="#0d0e15",
                    paper_bgcolor="#0d0e15",
                    barmode="relative",
                    margin=dict(l=30, r=30, t=50, b=30),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_chex, use_container_width=True)
                st.caption("💡 **Cómo leer CHEX:** Muestra cuánto delta se deshace cada día solo por el paso del tiempo (efecto muy fuerte después del mediodía y en contratos de 0 a 8 DTE).")

            with tab_pin:
                col_p1, col_p2, col_p3 = st.columns(3)
                col_p1.metric("🎯 Strike Imán (Top Pin)", f"${pin_st:g}", f"Score: {pin_sc:.1f}/100 ({pin_lbl})")
                col_p2.metric("🧱 Call Wall (Techo Activo)", f"${c_wall:g}", f"{((c_wall - spot_now)/spot_now)*100:+.2f}% vs Spot")
                col_p3.metric("🛡️ Put Wall (Piso Activo)", f"${p_wall:g}", f"{((p_wall - spot_now)/spot_now)*100:+.2f}% vs Spot")
                if not tac["pin_df"].empty:
                    pin_rows_html = ""
                    for _, pr in tac["pin_df"].iterrows():
                        gex_v = float(pr["net_gex"])
                        gex_clr = "#2ecc71" if gex_v > 0 else ("#ff4d4d" if gex_v < 0 else "#8b949e")
                        dist_v = float(pr["dist_pct"])
                        dist_clr = "#2ecc71" if dist_v >= 0 else "#ff4d4d"
                        pin_rows_html += (
                            f'<tr style="border-bottom:1px solid #21262d;">'
                            f'<td style="padding:7px 8px;text-align:right;font-weight:700;color:#ffffff;">${float(pr["strike"]):g}</td>'
                            f'<td style="padding:7px 8px;text-align:right;font-weight:700;color:#ffcc00;">{float(pr["pin_score"]):.1f}</td>'
                            f'<td style="padding:7px 8px;text-align:right;color:{dist_clr};">{dist_v:+.2f}%</td>'
                            f'<td style="padding:7px 8px;text-align:right;color:#c9d1d9;">{int(pr["call_oi"]):,}</td>'
                            f'<td style="padding:7px 8px;text-align:right;color:#c9d1d9;">{int(pr["put_oi"]):,}</td>'
                            f'<td style="padding:7px 8px;text-align:right;color:{gex_clr};font-weight:600;">{format_millions(gex_v, engine_mode)}</td>'
                            f'<td style="padding:7px 8px;text-align:left;color:#e6edf3;white-space:nowrap;">{pr["role"]}</td>'
                            f'</tr>'
                        )
                    pin_tbl_html = (
                        '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin:8px 0 10px 0;">'
                        '<table style="width:100%;border-collapse:collapse;font-size:12.5px;font-family:\'Inter\',sans-serif;">'
                        '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                        '<th style="padding:8px;text-align:right;">Strike</th>'
                        '<th style="padding:8px;text-align:right;">Pin Score</th>'
                        '<th style="padding:8px;text-align:right;">Dist. Spot</th>'
                        '<th style="padding:8px;text-align:right;">Call OI</th>'
                        '<th style="padding:8px;text-align:right;">Put OI</th>'
                        '<th style="padding:8px;text-align:right;">Net GEX</th>'
                        '<th style="padding:8px;text-align:left;">Rol Operativo</th>'
                        '</tr></thead>'
                        f'<tbody>{pin_rows_html}</tbody>'
                        '</table></div>'
                    )
                    st.markdown(pin_tbl_html, unsafe_allow_html=True)
                st.caption("💡 **Pin Risk (0-100):** Combina concentración de OI (30%), cercanía al Spot (25%), peso de vencimientos cortos (25%) y magnitud Gamma (20%). Un score >65 indica fuerte atracción/freno en ese strike.")

            with tab_radar:
                c_prem = tac["total_call_prem_vol"]
                p_prem = tac["total_put_prem_vol"]
                tot_prem = c_prem + p_prem
                c_pct = (c_prem / tot_prem * 100.0) if tot_prem > 0 else 50.0
                p_pct = (p_prem / tot_prem * 100.0) if tot_prem > 0 else 50.0

                r_c1, r_c2, r_c3 = st.columns(3)
                r_c1.metric("🟢 Prima Operada en Calls (Hoy)", f"${c_prem:,.0f}", f"{c_pct:.1f}% del volumen en $")
                r_c2.metric("🔴 Prima Operada en Puts (Hoy)", f"${p_prem:,.0f}", f"{p_pct:.1f}% del volumen en $")
                r_c3.metric("📐 Expected Move Venc. Frontal", f"±${tac['em_exp']:.2f} (±{tac['em_exp_pct']:.1f}%)", f"DTE más cercano: {tac['min_dte']} días")

                if not tac["radar_df"].empty:
                    radar_rows_html = ""
                    for _, rr in tac["radar_df"].iterrows():
                        ctype = str(rr["contract_type"])
                        type_clr = "#2ecc71" if ctype == "CALL" else "#ff4d4d"
                        type_bg = "rgba(46,204,113,0.14)" if ctype == "CALL" else "rgba(255,77,77,0.14)"
                        ratio_v = float(rr["vol_oi_ratio"])
                        ratio_clr = "#ffcc00" if ratio_v >= 2.0 else ("#348feb" if ratio_v >= 0.75 else "#c9d1d9")
                        prem_v = float(rr["prem_vol_usd"])
                        prem_txt = format_millions(prem_v, "USD") if prem_v >= 100000 else f"${prem_v:,.0f}"
                        sig_txt = str(rr["signal"]).replace(" (CALL)", "").replace(" (PUT)", "")
                        radar_rows_html += (
                            f'<tr style="border-bottom:1px solid #21262d;">'
                            f'<td style="padding:6px 6px;text-align:center;"><span style="background:{type_bg};color:{type_clr};font-weight:700;padding:2px 6px;border-radius:4px;font-size:11px;">{ctype}</span></td>'
                            f'<td style="padding:6px 6px;text-align:right;font-weight:700;color:#ffffff;">${float(rr["strike"]):g}</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#8b949e;">{int(rr["dte"])}d</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#e6edf3;font-weight:600;">{int(rr["volume"]):,}</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#c9d1d9;">{int(rr["open_interest"]):,}</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:{ratio_clr};font-weight:700;">{ratio_v:.2f}x</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#e6edf3;font-weight:600;">{prem_txt}</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#8b949e;">{float(rr["delta"]):.2f}</td>'
                            f'<td style="padding:6px 6px;text-align:left;color:#e6edf3;white-space:nowrap;">{sig_txt}</td>'
                            f'</tr>'
                        )
                    radar_tbl_html = (
                        '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin:8px 0 10px 0;">'
                        '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:\'Inter\',sans-serif;">'
                        '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                        '<th style="padding:7px 6px;text-align:center;">Tipo</th>'
                        '<th style="padding:7px 6px;text-align:right;">Strike</th>'
                        '<th style="padding:7px 6px;text-align:right;">DTE</th>'
                        '<th style="padding:7px 6px;text-align:right;">Vol</th>'
                        '<th style="padding:7px 6px;text-align:right;">OI</th>'
                        '<th style="padding:7px 6px;text-align:right;">Vol/OI</th>'
                        '<th style="padding:7px 6px;text-align:right;">Prima ($)</th>'
                        '<th style="padding:7px 6px;text-align:right;">Delta</th>'
                        '<th style="padding:7px 6px;text-align:left;">Señal</th>'
                        '</tr></thead>'
                        f'<tbody>{radar_rows_html}</tbody>'
                        '</table></div>'
                    )
                    st.markdown(radar_tbl_html, unsafe_allow_html=True)
                st.caption("💡 **Cómo detectar entradas institucionales:** Cuando el **Ratio Vol/OI es > 1.0x** (y especialmente > 2.0x, marcado con 🔥), significa que están entrando contratos nuevos agresivos hoy que superan todo lo que había abierto antes.")

            # Fila 5: Gráficas de Exposición Verticales con Filtro de Rango Dinámico
            st.markdown("<h2 class='custom-header'>Gráficos de Exposición y Flujos</h2>", unsafe_allow_html=True)
            
            # Gráfico GEX
            fig_gex = plot_gex_plotly(df_gex, res['Spot Price'], mode=engine_mode, pct=rango_pct)
            st.plotly_chart(fig_gex, use_container_width=True)
            
            # Gráfico DEX
            fig_dex = plot_dex_plotly(df_dex, res['Spot Price'], mode=engine_mode, pct=rango_pct)
            st.plotly_chart(fig_dex, use_container_width=True)
            
            # Gráfico OI vs Net GEX
            fig_oi = plot_oi_gex_plotly(df_gex, res['Spot Price'], pct=rango_pct)
            st.plotly_chart(fig_oi, use_container_width=True)

            # Fila 6: Scanner de Contratos por Horizonte (4 vistas: Completa + Intradía + Swing + Largo Plazo)
            if not df_raw.empty:
                st.markdown("<h2 class='custom-header'>Scanner de Contratos (Top OI &amp; Volume)</h2>", unsafe_allow_html=True)

                full_sc_records = df_raw.attrs.get("full_chain_scanner") if hasattr(df_raw, "attrs") else None
                using_full_chain = isinstance(full_sc_records, list) and len(full_sc_records) > 0
                df_contracts = pd.DataFrame(full_sc_records) if using_full_chain else df_raw.copy()

                df_contracts["contract_type"] = df_contracts["type"].apply(lambda t: "call" if t == "C" else "put")
                df_contracts["strike"] = pd.to_numeric(df_contracts["strike"], errors="coerce").fillna(0.0)
                df_contracts["dte"] = pd.to_numeric(df_contracts["dte"], errors="coerce").fillna(0).astype(int)
                df_contracts["open_interest"] = pd.to_numeric(df_contracts["open_interest"], errors="coerce").fillna(0).astype(int)
                df_contracts["volume"] = pd.to_numeric(df_contracts["volume"], errors="coerce").fillna(0).astype(int)
                df_contracts["delta"] = pd.to_numeric(df_contracts["delta"], errors="coerce").fillna(0.0)
                if "net_gex" not in df_contracts.columns:
                    df_contracts["net_gex"] = 0.0
                else:
                    df_contracts["net_gex"] = pd.to_numeric(df_contracts["net_gex"], errors="coerce").fillna(0.0)
                if "vol_gex" not in df_contracts.columns:
                    df_contracts["vol_gex"] = 0.0
                else:
                    df_contracts["vol_gex"] = pd.to_numeric(df_contracts["vol_gex"], errors="coerce").fillna(0.0)

                spot_sc = float(res["Spot Price"])
                df_contracts["ITM_State"] = df_contracts.apply(
                    lambda r: "ITM" if (r["contract_type"] == "call" and r["strike"] < spot_sc)
                    or (r["contract_type"] == "put" and r["strike"] > spot_sc)
                    else "OTM",
                    axis=1,
                )
                df_contracts["oi_vol_sum"] = df_contracts["open_interest"] + df_contracts["volume"]
                df_contracts["intra_score"] = df_contracts["volume"] * 2 + df_contracts["open_interest"]

                def _sc_vol_cell(val, rank, is_call):
                    txt = f"{int(val):,}"
                    if is_call:
                        if rank == 1:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:800;background:rgba(0,230,118,0.34);border:1.5px solid #00e676;color:#ffffff;">{txt}</span>'
                        if rank == 2:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(46,204,113,0.18);border:1px solid rgba(46,204,113,0.65);color:#e6edf3;">{txt}</span>'
                        if rank == 3:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:600;background:rgba(46,204,113,0.08);border:1px solid rgba(46,204,113,0.34);color:#b0bac5;">{txt}</span>'
                    else:
                        if rank == 1:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:800;background:rgba(255,59,48,0.36);border:1.5px solid #ff3b30;color:#ffffff;">{txt}</span>'
                        if rank == 2:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(255,77,77,0.18);border:1px solid rgba(255,77,77,0.65);color:#e6edf3;">{txt}</span>'
                        if rank == 3:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:600;background:rgba(255,77,77,0.08);border:1px solid rgba(255,77,77,0.34);color:#b0bac5;">{txt}</span>'
                    return f'<span style="color:#c9d1d9;">{txt}</span>'

                def _sc_oi_cell(val, rank, is_call):
                    txt = f"{int(val):,}"
                    if is_call:
                        if rank == 1:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(56,189,248,0.16);border:1.5px solid #38bdf8;color:#e0f2fe;">{txt}</span>'
                        if rank == 2:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:600;border:1px solid rgba(56,189,248,0.55);color:#bae6fd;">{txt}</span>'
                        if rank == 3:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;border:1px solid rgba(56,189,248,0.28);color:#94a3b8;">{txt}</span>'
                    else:
                        if rank == 1:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(251,191,36,0.16);border:1.5px solid #fbbf24;color:#fef3c7;">{txt}</span>'
                        if rank == 2:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:600;border:1px solid rgba(251,191,36,0.55);color:#fde68a;">{txt}</span>'
                        if rank == 3:
                            return f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;border:1px solid rgba(251,191,36,0.28);color:#94a3b8;">{txt}</span>'
                    return f'<span style="color:#8b949e;">{txt}</span>'

                def _render_contract_scanner_tab(df_sub, banner_left, banner_right, footnote_txt):
                    if df_sub.empty:
                        st.info("No hay contratos activos en este horizonte para el ticker seleccionado.")
                        return

                    df_top = df_sub.reset_index(drop=True)

                    # Top 3 Volume y Top 3 OI por tipo (Calls y Puts por separado)
                    def _ranks_by_type(df_t, col_n, ctype):
                        sub_t = df_t[(df_t["contract_type"] == ctype) & (df_t[col_n] > 0)].sort_values(col_n, ascending=False).head(3)
                        return {idx_t: rnk for rnk, idx_t in enumerate(sub_t.index.tolist(), start=1)}

                    cv_ranks = _ranks_by_type(df_top, "volume", "call")
                    pv_ranks = _ranks_by_type(df_top, "volume", "put")
                    co_ranks = _ranks_by_type(df_top, "open_interest", "call")
                    po_ranks = _ranks_by_type(df_top, "open_interest", "put")

                    # Detectar posibles Spreads (mismo tipo, mismo DTE, strike distinto, volumen >= 1000 y similitud >= 88%)
                    spread_indices = set()
                    n_rows = len(df_top)
                    for i_a in range(n_rows):
                        va = int(df_top.at[i_a, "volume"])
                        if va < 1000:
                            continue
                        for i_b in range(i_a + 1, n_rows):
                            vb = int(df_top.at[i_b, "volume"])
                            if vb < 1000:
                                continue
                            if (
                                df_top.at[i_a, "contract_type"] == df_top.at[i_b, "contract_type"]
                                and int(df_top.at[i_a, "dte"]) == int(df_top.at[i_b, "dte"])
                                and float(df_top.at[i_a, "strike"]) != float(df_top.at[i_b, "strike"])
                                and (min(va, vb) / max(va, vb)) >= 0.88
                            ):
                                spread_indices.add(i_a)
                                spread_indices.add(i_b)

                    st.markdown(
                        '<div style="background:#161b22;border:1px solid #30363d;border-radius:7px;padding:7px 11px;'
                        'margin-bottom:7px;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;font-size:12.5px;">'
                        f'<span style="color:#e6edf3;">{banner_left}</span>'
                        f'<span style="color:#8b949e;font-size:12px;">{banner_right}</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )

                    rows_html = ""
                    for idx_r, rr in df_top.iterrows():
                        is_call = rr["contract_type"] == "call"
                        type_pill = (
                            '<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:800;font-size:11.5px;'
                            'background:rgba(34,197,94,0.16);color:#4ade80;border:1px solid rgba(34,197,94,0.45);">CALL</span>'
                            if is_call else
                            '<span style="display:inline-block;padding:2px 7px;border-radius:4px;font-weight:800;font-size:11.5px;'
                            'background:rgba(239,68,68,0.16);color:#f87171;border:1px solid rgba(239,68,68,0.45);">PUT</span>'
                        )
                        strike_str = f"${float(rr['strike']):g}"
                        dte_str = f"{int(rr['dte'])}d"
                        oi_v = int(rr["open_interest"])
                        vol_v = int(rr["volume"])
                        delta_v = float(rr["delta"])
                        delta_str = f"{delta_v:+.2f}"

                        oi_rank = co_ranks.get(idx_r, 0) if is_call else po_ranks.get(idx_r, 0)
                        vol_rank = cv_ranks.get(idx_r, 0) if is_call else pv_ranks.get(idx_r, 0)

                        # Ratio Vol/OI y bandera de flujo nuevo
                        is_hot_ratio = False
                        if oi_v == 0 and vol_v >= 100:
                            ratio_html = (
                                '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11.5px;'
                                'background:rgba(251,146,60,0.18);color:#fb923c;border:1px solid rgba(251,146,60,0.45);">🔥 NUEVO</span>'
                            )
                        elif oi_v > 0:
                            ratio_val = vol_v / oi_v
                            if ratio_val >= 1.0 and vol_v >= 500:
                                is_hot_ratio = True
                                ratio_html = (
                                    f'<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11.5px;'
                                    f'background:rgba(251,146,60,0.18);color:#fb923c;border:1px solid rgba(251,146,60,0.45);">{ratio_val:.2f}x</span>'
                                )
                            else:
                                ratio_html = f'<span style="color:#8b949e;">{ratio_val:.2f}x</span>'
                        else:
                            ratio_html = '<span style="color:#8b949e;">0.00x</span>'

                        # GEX oficial (basado en OI) o estimado (~) con Volume si OI == 0
                        ng_val = float(rr["net_gex"])
                        vg_val = float(rr["vol_gex"])
                        if oi_v > 0 and abs(ng_val) > 0:
                            gex_clr = "#2ecc71" if ng_val > 0 else "#ff4d4d"
                            gex_fmt = format_millions(ng_val, engine_mode)
                            gex_txt = f"+{gex_fmt}" if (ng_val > 0 and not gex_fmt.startswith("+")) else gex_fmt
                            gex_html = f'<span style="color:{gex_clr};font-weight:700;">{gex_txt}</span>'
                        elif oi_v == 0 and vol_v > 0 and abs(vg_val) > 0:
                            gex_fmt = format_millions(vg_val, engine_mode)
                            gex_txt = f"~+{gex_fmt}" if (vg_val > 0 and not gex_fmt.startswith("+")) else f"~{gex_fmt}"
                            gex_html = f'<span title="Estimado con Volume de hoy (OI = 0)" style="color:#94a3b8;font-style:italic;">{gex_txt}</span>'
                        else:
                            gex_html = f'<span style="color:#6e7681;">{format_millions(0, engine_mode)}</span>'

                        # Estado ITM/OTM + badges (Spread / Nuevo)
                        itm_st = rr["ITM_State"]
                        itm_pill = (
                            '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11px;'
                            'background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.38);">ITM</span>'
                            if itm_st == "ITM" else
                            '<span style="display:inline-block;padding:2px 6px;border-radius:4px;font-size:11px;'
                            'background:rgba(148,163,184,0.10);color:#8b949e;border:1px solid #30363d;">OTM</span>'
                        )
                        extra_pills = ""
                        if idx_r in spread_indices:
                            extra_pills += (
                                ' <span title="Mismo tipo, mismo DTE y volumen espejo (>88%)" '
                                'style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11px;'
                                'background:rgba(168,85,247,0.18);color:#c084fc;border:1px solid rgba(168,85,247,0.45);">🔀 Spread</span>'
                            )
                        elif is_hot_ratio:
                            extra_pills += (
                                ' <span style="display:inline-block;padding:2px 6px;border-radius:4px;font-weight:700;font-size:11px;'
                                'background:rgba(251,146,60,0.18);color:#fb923c;border:1px solid rgba(251,146,60,0.45);">🔥 Nuevo</span>'
                            )

                        rows_html += (
                            '<tr style="border-bottom:1px solid #21262d;">'
                            f'<td style="padding:6px 7px;text-align:left;white-space:nowrap;">{type_pill}</td>'
                            f'<td style="padding:6px 7px;text-align:left;font-weight:800;color:#ffffff;white-space:nowrap;">{strike_str}</td>'
                            f'<td style="padding:6px 6px;text-align:center;color:#8b949e;white-space:nowrap;">{dte_str}</td>'
                            f'<td style="padding:6px 7px;text-align:right;white-space:nowrap;">{_sc_oi_cell(oi_v, oi_rank, is_call)}</td>'
                            f'<td style="padding:6px 7px;text-align:right;white-space:nowrap;">{_sc_vol_cell(vol_v, vol_rank, is_call)}</td>'
                            f'<td style="padding:6px 6px;text-align:center;white-space:nowrap;">{ratio_html}</td>'
                            f'<td style="padding:6px 6px;text-align:right;color:#c9d1d9;white-space:nowrap;">{delta_str}</td>'
                            f'<td style="padding:6px 7px;text-align:right;white-space:nowrap;">{gex_html}</td>'
                            f'<td style="padding:6px 7px;text-align:right;white-space:nowrap;">{itm_pill}{extra_pills}</td>'
                            '</tr>'
                        )

                    tbl_html = (
                        '<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:6px;">'
                        '<table style="width:100%;border-collapse:collapse;font-size:12.5px;font-family:\'Inter\',sans-serif;font-variant-numeric:tabular-nums;">'
                        '<thead><tr style="background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;">'
                        '<th style="padding:7px 7px;text-align:left;">Tipo</th>'
                        '<th style="padding:7px 7px;text-align:left;">Strike</th>'
                        '<th style="padding:7px 6px;text-align:center;">DTE</th>'
                        '<th style="padding:7px 7px;text-align:right;">OI</th>'
                        '<th style="padding:7px 7px;text-align:right;">Volume</th>'
                        '<th style="padding:7px 6px;text-align:center;">Vol/OI</th>'
                        '<th style="padding:7px 6px;text-align:right;">Delta</th>'
                        '<th style="padding:7px 7px;text-align:right;">GEX</th>'
                        '<th style="padding:7px 7px;text-align:right;">Estado / Señal</th>'
                        '</tr></thead>'
                        f'<tbody>{rows_html}</tbody>'
                        '</table></div>'
                    )
                    st.markdown(tbl_html, unsafe_allow_html=True)
                    st.caption(footnote_txt)

                source_tag = "Cadena completa" if using_full_chain else "Fechas seleccionadas"
                tab_sc_all, tab_sc_intra, tab_sc_swing, tab_sc_leaps = st.tabs([
                    "🌐 Todos (Completa)",
                    "⚡ Intradía (0–7d)",
                    "📈 Swing (8–60d)",
                    "🏛️ Largo Plazo (>60d)",
                ])

                with tab_sc_all:
                    _render_contract_scanner_tab(
                        df_contracts.nlargest(10, "oi_vol_sum"),
                        "<b>🌐 Vista Completa (Top 10 OI + Volume)</b> · Todos los vencimientos combinados",
                        f"Top 10 · {source_tag}",
                        "💡 **~** en GEX = estimado con Volume de hoy porque `OI = 0` (mañana pasa a GEX oficial). Cajitas llenas = Top 3 Volume (verde Calls / rojo Puts) · Bordes = Top 3 OI (cian Calls / ámbar Puts) · **🔀 Spread** = mismo tipo/DTE y volumen espejo.",
                    )

                with tab_sc_intra:
                    df_sc_intra = df_contracts[df_contracts["dte"] <= 7].nlargest(10, "intra_score")
                    _render_contract_scanner_tab(
                        df_sc_intra,
                        "<b>⚡ Intradía / Scalping (0–7 DTE)</b> · Prioriza Volume activo + GEX de hoy",
                        f"Top {len(df_sc_intra)} · 0–7 DTE · {source_tag}",
                        "💡 En **0–7 DTE** mandan **Volume**, **Vol/OI ≥ 1.0x** y **GEX**: muestran dónde los dealers están ajustando cobertura hoy.",
                    )

                with tab_sc_swing:
                    df_sc_swing = df_contracts[(df_contracts["dte"] >= 8) & (df_contracts["dte"] <= 60)].nlargest(10, "oi_vol_sum")
                    _render_contract_scanner_tab(
                        df_sc_swing,
                        "<b>📈 Swing (8–60 DTE)</b> · Posicionamiento semanal/mensual, flujo nuevo y Spreads",
                        f"Top {len(df_sc_swing)} · 8–60 DTE · {source_tag}",
                        "💡 Ideal para detectar apuestas institucionales de 2–8 semanas y **🔀 Spreads** sin ruido de 0DTE ni LEAPS.",
                    )

                with tab_sc_leaps:
                    df_sc_leaps = df_contracts[df_contracts["dte"] > 60].nlargest(10, "oi_vol_sum")
                    _render_contract_scanner_tab(
                        df_sc_leaps,
                        "<b>🏛️ Largo Plazo / LEAPS (&gt;60 DTE)</b> · Muros estructurales y coberturas de fondos",
                        f"Top {len(df_sc_leaps)} · >60 DTE · {source_tag}",
                        "💡 Úsalos como **mapa macro** de pisos y techos institucionales a varios meses (aquí manda **OI + Delta** más que el GEX diario).",
                    )
else:
    st.info("💡 Haz clic en 'Descargar Data' para cargar las fechas de expiración disponibles y comenzar.")
