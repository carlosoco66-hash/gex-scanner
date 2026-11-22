import streamlit as st
import pandas as pd
import numpy as np
import time
import datetime
import json
import importlib
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
        "total_call_prem_oi": 0.0,
        "total_put_prem_oi": 0.0,
        "spot_change_pct": 0.0,
        "iv_rising": False,
    }


calculate_tactical_metrics = getattr(gex_engine, "calculate_tactical_metrics", _fallback_tactical_metrics)

# Funciones con caché para optimizar consultas de la API
@st.cache_data(ttl=300)
def cached_options_expirations(ticker, api_key):
    return get_options_expirations(ticker, api_key)

@st.cache_data(ttl=300)
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

# Botón para forzar actualización (limpia la caché)
if st.sidebar.button("🔄 Forzar Actualización", use_container_width=True):
    st.cache_data.clear()
    st.sidebar.success("Caché limpiada con éxito.")
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

# Helper para mapear ticker a TradingView
def get_tv_symbol(ticker):
    ticker = ticker.upper().strip()
    if ticker in ["SPY", "IWM", "DIA", "GLD", "SLV", "UNG", "USO"]:
        return f"AMEX:{ticker}"
    elif ticker in ["QQQ", "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "NFLX", "GOOG", "GOOGL"]:
        return f"NASDAQ:{ticker}"
    elif ticker in ["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE"]:
        return f"COINBASE:{ticker}USD"
    else:
        return f"NYSE:{ticker}"

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
st.markdown("<h1 style='font-size: 36px; margin-bottom: 20px;'>Gamma Scanner Options</h1>", unsafe_allow_html=True)

# Inicializar estados
if 'expirations' not in st.session_state:
    st.session_state.expirations = []
if 'last_ticker' not in st.session_state:
    st.session_state.last_ticker = ""

# Fila 1: Ticker, Spot Manual y Botón Descargar Data
col_t, col_s, col_b = st.columns([3, 2, 2])
with col_t:
    ticker = st.text_input("Ticker", value="SPY").upper().strip()
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
    
    col_calc_btn, _ = st.columns([2, 5])
    with col_calc_btn:
        btn_calc = st.button("3. Calcular GEX y Scanner", use_container_width=True, type="primary")

    # Si se hace clic en calcular, corremos el scanner
    if btn_calc or 'gex_results' in st.session_state:
        if btn_calc or ticker != st.session_state.get('results_ticker') or engine_mode != st.session_state.get('results_mode'):
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
                        st.markdown("<h4 style='margin-top:16px;'>Top Strikes Activos (Live Flow GEX & Volumen)</h4>", unsafe_allow_html=True)
                        top_flow_df = lf_sub.sort_values(["total_volume", "call_oi", "put_oi"], ascending=False).head(10)[
                            ["strike", "call_volume", "put_volume", "call_oi", "put_oi", "call_gex", "put_gex", "net_gex"]
                        ].copy()
                        for int_c in ["call_volume", "put_volume", "call_oi", "put_oi"]:
                            top_flow_df[int_c] = top_flow_df[int_c].astype(int)
                        st.dataframe(top_flow_df, use_container_width=True, hide_index=True)

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

            vex_color = "#2ecc71" if net_vex_val >= 0 else "#ff4d4d"
            vex_sub = "Si IV baja → Dealers compran (Alcista)" if net_vex_val >= 0 else "Si IV sube → Acelera ventas (Bajista)"
            chex_color = "#2ecc71" if net_chex_val >= 0 else "#ff4d4d"
            chex_sub = "Paso del tiempo → Empuje alcista" if net_chex_val >= 0 else "Paso del tiempo → Presión bajista"
            pin_color = "#ffcc00" if pin_sc >= 65 else ("#2ecc71" if pin_sc >= 40 else "#8b949e")

            t_col1, t_col2, t_col3, t_col4 = st.columns(4)
            with t_col1:
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {vex_color};border-radius:8px;padding:12px 16px;">'
                    f'<p style="color:#8b949e;margin:0;font-size:12px;">VEX Neto (Vanna Exposure)</p>'
                    f'<p style="color:{vex_color};margin:4px 0 2px 0;font-size:22px;font-weight:800;">{format_millions(net_vex_val, engine_mode)}</p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">{vex_sub}</p>'
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
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {pin_color};border-radius:8px;padding:12px 16px;">'
                    f'<p style="color:#8b949e;margin:0;font-size:12px;">Flow Levels & Pin Risk</p>'
                    f'<p style="color:#ffffff;margin:4px 0 2px 0;font-size:22px;font-weight:800;">Pin ${pin_st:g} <span style="font-size:13px;color:{pin_color};">({pin_sc:.0f}/100 {pin_lbl})</span></p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">Piso (Put): <b>${p_wall:g}</b> | Techo (Call): <b>${c_wall:g}</b></p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with t_col4:
                st.markdown(
                    f'<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid #348feb;border-radius:8px;padding:12px 16px;">'
                    f'<p style="color:#8b949e;margin:0;font-size:12px;">Expected Move Hoy (IV {atm_iv_pct:.1f}%)</p>'
                    f'<p style="color:#ffffff;margin:4px 0 2px 0;font-size:22px;font-weight:800;">±${em_1d:.2f} <span style="font-size:13px;color:#348feb;">(±{em_1d_pct:.1f}%)</span></p>'
                    f'<p style="color:#c9d1d9;margin:0;font-size:11px;">Rango: <b>${em_low:.2f}</b> ◄──► <b>${em_high:.2f}</b></p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Barra de Veredicto Operativo Automático + Radar de 4 Señales en Tiempo Real (con Hora Exacta HH:MM:SS, Silbato Festivo, Sirena y Voz)
            spot_now = res["Spot Price"]
            tot_gex_now = res["Total Net GEX"]
            gamma_flip_now = res.get("Zero GEX (Balance)", spot_now)
            f_dir_now = str(lf_summary.get("flow_direction", "no_flow")).lower()

            # Calcular variación % del Spot (desde tactical, o comparando contra sesión previa / yfinance)
            if "prev_spot_map" not in st.session_state:
                st.session_state.prev_spot_map = {}
            if "prev_iv_map" not in st.session_state:
                st.session_state.prev_iv_map = {}
            if "signal_activation_times" not in st.session_state:
                st.session_state.signal_activation_times = {}
            if "signal_history_log" not in st.session_state:
                st.session_state.signal_history_log = []

            spot_chg_pct = float(tac.get("spot_change_pct", 0.0) or 0.0)
            prev_spot_val = st.session_state.prev_spot_map.get(ticker)
            if spot_chg_pct == 0.0 and prev_spot_val and prev_spot_val > 0 and abs(spot_now - prev_spot_val) > 1e-6:
                spot_chg_pct = ((spot_now - prev_spot_val) / prev_spot_val) * 100.0
            if spot_chg_pct == 0.0:
                try:
                    import yfinance as yf
                    t_fast = yf.Ticker(ticker).fast_info
                    p_close = float(t_fast.get("previousClose") or t_fast.get("regularMarketPreviousClose") or 0.0)
                    if p_close > 0:
                        spot_chg_pct = ((spot_now - p_close) / p_close) * 100.0
                except Exception:
                    pass
            st.session_state.prev_spot_map[ticker] = spot_now

            # Determinar tendencia de IV (IV ▲ vs IV ▼)
            prev_iv_val = st.session_state.prev_iv_map.get(ticker)
            if prev_iv_val is not None and abs(atm_iv_pct - prev_iv_val) >= 0.05:
                iv_is_rising = atm_iv_pct > prev_iv_val
            else:
                iv_is_rising = bool(tac.get("iv_rising", atm_iv_pct >= 25.0))
            st.session_state.prev_iv_map[ticker] = atm_iv_pct

            # Dominancia de Puts vs Calls (% de Prima de Volumen hoy, con fallback a Prima de OI si mercado cerrado)
            c_prem_flow = float(tac.get("total_call_prem_vol", 0.0) or 0.0)
            p_prem_flow = float(tac.get("total_put_prem_vol", 0.0) or 0.0)
            if (c_prem_flow + p_prem_flow) <= 0:
                c_prem_flow = float(tac.get("total_call_prem_oi", 0.0) or (df_gex["call_prem_oi"].sum() if "call_prem_oi" in df_gex.columns else 0.0))
                p_prem_flow = float(tac.get("total_put_prem_oi", 0.0) or (df_gex["put_prem_oi"].sum() if "put_prem_oi" in df_gex.columns else 0.0))
            tot_prem_flow = c_prem_flow + p_prem_flow
            puts_dom_pct = (p_prem_flow / tot_prem_flow * 100.0) if tot_prem_flow > 0 else 50.0
            calls_dom_pct = (c_prem_flow / tot_prem_flow * 100.0) if tot_prem_flow > 0 else 50.0

            spot_down = spot_chg_pct < 0
            spot_up = spot_chg_pct > 0
            spot_arrow = "▼" if spot_down else ("▲" if spot_up else "•")
            iv_arrow = "▲" if iv_is_rising else "▼"

            # Definición de las 4 Señales Tácticas Simultáneas (cada una con sus 4 requisitos)
            signals_config = [
                {
                    "id": "cascada_bajista",
                    "direction": "bearish",
                    "sound_type": "siren",
                    "icon": "💥",
                    "title": "CASCADA BAJISTA",
                    "desc": "Gamma- + VEX- + IV subiendo con Puts dominando (aceleración bajista).",
                    "voice_name": "Cascada Bajista",
                    "reqs": [
                        {"label": f"Spot ▼ ({spot_chg_pct:+.2f}%)", "met": spot_down},
                        {"label": f"IV ▲ ({atm_iv_pct:.1f}%)", "met": iv_is_rising},
                        {"label": f"Puts {puts_dom_pct:.0f}%", "met": puts_dom_pct >= 55.0},
                        {"label": "Gamma- + VEX-", "met": (tot_gex_now < 0 and net_vex_val < 0)},
                    ],
                },
                {
                    "id": "squeeze_alcista",
                    "direction": "bullish",
                    "sound_type": "whistle",
                    "icon": "🎉",
                    "title": "SQUEEZE ALCISTA",
                    "desc": "Gamma+ + VEX+ + IV bajando con Calls dominando (aceleración alcista).",
                    "voice_name": "Squeeze Alcista",
                    "reqs": [
                        {"label": f"Spot ▲ ({spot_chg_pct:+.2f}%)", "met": spot_up},
                        {"label": f"IV ▼ ({atm_iv_pct:.1f}%)", "met": not iv_is_rising},
                        {"label": f"Calls {calls_dom_pct:.0f}%", "met": calls_dom_pct >= 55.0},
                        {"label": "Gamma+ + VEX+", "met": (tot_gex_now >= 0 and net_vex_val >= 0)},
                    ],
                },
                {
                    "id": "rebote_vanna",
                    "direction": "bullish",
                    "sound_type": "whistle",
                    "icon": "🚀",
                    "title": "REBOTE VANNA (SHORT COVER)",
                    "desc": "Gamma- + IV cayendo con Calls dominando (quema de Puts y cierre forzado de cortos).",
                    "voice_name": "Rebote Vanna Alcista",
                    "reqs": [
                        {"label": f"Spot ▲ ({spot_chg_pct:+.2f}%)", "met": spot_up},
                        {"label": f"IV ▼ ({atm_iv_pct:.1f}%)", "met": not iv_is_rising},
                        {"label": f"Calls {calls_dom_pct:.0f}%", "met": calls_dom_pct >= 55.0},
                        {"label": "Gamma- (Rebote Rápido)", "met": tot_gex_now < 0},
                    ],
                },
                {
                    "id": "presion_bajista_charm",
                    "direction": "bearish",
                    "sound_type": "siren",
                    "icon": "🚨",
                    "title": "PRESIÓN BAJISTA (DEBAJO DE FLIP)",
                    "desc": "Spot debajo del Gamma Flip + CHEX/VEX negativos con flujo Put dominante.",
                    "voice_name": "Presión Bajista bajo Gamma Flip",
                    "reqs": [
                        {"label": f"Spot < Flip (${gamma_flip_now:.1f})", "met": spot_now < gamma_flip_now},
                        {"label": f"Spot ▼ ({spot_chg_pct:+.2f}%)", "met": spot_down},
                        {"label": f"Puts {puts_dom_pct:.0f}%", "met": puts_dom_pct >= 55.0},
                        {"label": "CHEX- / VEX-", "met": (net_chex_val < 0 or net_vex_val < 0)},
                    ],
                },
            ]

            now_dt = datetime.datetime.now()
            now_str = now_dt.strftime("%H:%M:%S")
            newly_triggered_signals = []

            for sig in signals_config:
                met_count = sum(1 for r in sig["reqs"] if r["met"])
                total_reqs = len(sig["reqs"])
                sig["met_count"] = met_count
                sig["total_reqs"] = total_reqs
                sig["is_active"] = (met_count == total_reqs)

                state_key = f"{ticker}_{sig['id']}"
                prev_info = st.session_state.signal_activation_times.get(state_key)

                if sig["is_active"]:
                    if not prev_info or not prev_info.get("active"):
                        # Se acaba de activar (4/4): guardar hora, minuto y segundo exactos
                        st.session_state.signal_activation_times[state_key] = {
                            "active": True,
                            "time_str": now_str,
                            "timestamp": time.time(),
                        }
                        sig["activated_at"] = now_str
                        sig["elapsed_txt"] = "recién activada"
                        newly_triggered_signals.append({
                            "id": sig["id"],
                            "title": sig["title"],
                            "voice_name": sig["voice_name"],
                            "sound_type": sig["sound_type"],
                            "direction": sig["direction"],
                            "time_str": now_str,
                            "ticker": ticker,
                        })
                        st.session_state.signal_history_log.insert(0, {
                            "time_str": now_str,
                            "ticker": ticker,
                            "icon": sig["icon"],
                            "title": sig["title"],
                            "direction": sig["direction"],
                        })
                        st.session_state.signal_history_log = st.session_state.signal_history_log[:12]
                    else:
                        # Ya estaba activa: mantener fija la hora exacta original en que se activó
                        sig["activated_at"] = prev_info["time_str"]
                        elapsed_s = max(0, int(time.time() - prev_info["timestamp"]))
                        if elapsed_s < 60:
                            sig["elapsed_txt"] = f"hace {elapsed_s}s"
                        else:
                            sig["elapsed_txt"] = f"hace {elapsed_s // 60}m {elapsed_s % 60:02d}s"
                else:
                    # Si baja de 4/4, guardar la última hora en que estuvo activa pero marcar active=False
                    last_t = prev_info.get("time_str") if prev_info else None
                    st.session_state.signal_activation_times[state_key] = {
                        "active": False,
                        "time_str": last_t,
                        "timestamp": prev_info.get("timestamp", 0) if prev_info else 0,
                    }
                    sig["activated_at"] = None
                    sig["last_activated_at"] = last_t

            # Renderizar Encabezado del Radar de 4 Señales + Controles de Audio (Silbato Festivo / Sirena + Voz)
            st.markdown(
                "<h3 style='margin-top:18px;margin-bottom:6px;color:#ffffff;font-size:18px;font-weight:800;'>"
                "🔔 RADAR DE 4 SEÑALES EN TIEMPO REAL (Checklist 4/4 + Hora Exacta + Alarma con Voz)"
                "</h3>",
                unsafe_allow_html=True,
            )

            # Componente HTML/JS de Audio (Web Audio API para Silbato Festivo y Sirena + speechSynthesis en Español)
            triggered_json = json.dumps(newly_triggered_signals)
            audio_component_html = f"""
            <div style="background:#11161f;border:1px solid #283040;border-radius:10px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;font-family:'Inter',sans-serif;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span style="color:#e6edf3;font-size:13px;font-weight:700;">🔊 Alarmas Sonoras y Voz ({ticker}):</span>
                    <span id="audio_status_badge" style="background:#132a1c;border:1px solid #2ecc71;color:#2ecc71;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px;">AUDIO + VOZ ACTIVOS</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <button onclick="testWhistleAlert()" style="background:#132f20;border:1px solid #2ecc71;color:#7ee787;padding:5px 11px;border-radius:7px;font-size:12px;font-weight:700;cursor:pointer;">
                        🎉 Probar Silbato + Voz (Subida)
                    </button>
                    <button onclick="testSirenAlert()" style="background:#341417;border:1px solid #ff4d4d;color:#ff7b72;padding:5px 11px;border-radius:7px;font-size:12px;font-weight:700;cursor:pointer;">
                        🚨 Probar Sirena + Voz (Caída)
                    </button>
                    <button id="mute_btn" onclick="toggleMute()" style="background:#1c212b;border:1px solid #3a4252;color:#c9d1d9;padding:5px 11px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;">
                        🔇 Silenciar
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

                // Silbato Festivo de Alegría (Party Whistle / Corneta de Cumpleaños + Fanfarria Alegre)
                window.playFestiveWhistle = function(onDone) {{
                    if (isMuted) return;
                    try {{
                        const AudioCtx = window.AudioContext || window.webkitAudioContext;
                        const ctx = new AudioCtx();
                        const now = ctx.currentTime;

                        // 1. Primer silbato festivo ascendente con vibrato alegre ("fiuuu-iiit!")
                        const osc1 = ctx.createOscillator();
                        const gain1 = ctx.createGain();
                        const lfo = ctx.createOscillator();
                        const lfoGain = ctx.createGain();

                        osc1.type = 'triangle';
                        osc1.frequency.setValueAtTime(520, now);
                        osc1.frequency.exponentialRampToValueAtTime(1480, now + 0.38);
                        osc1.frequency.setValueAtTime(1480, now + 0.55);
                        osc1.frequency.exponentialRampToValueAtTime(1180, now + 0.68);

                        // Vibrato rápido de silbato de fiesta
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

                        // 2. Segundo silbato festivo más alto y brillante + acorde alegre
                        const notes = [1046.5, 1318.5, 1567.98, 2093.0]; // Do6 - Mi6 - Sol6 - Do7
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

                // Sirena de Alarma para Caída (Sirena Oscilante de Emergencia)
                window.playSirenAlarm = function(onDone) {{
                    if (isMuted) return;
                    try {{
                        const AudioCtx = window.AudioContext || window.webkitAudioContext;
                        const ctx = new AudioCtx();
                        const now = ctx.currentTime;

                        const osc = ctx.createOscillator();
                        const gain = ctx.createGain();
                        osc.type = 'sawtooth';

                        // 2 ciclos de sirena intensa ("uuu-aaa, uuu-aaa")
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

                // Narrador de Voz en Español
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
                    const pad = n => String(n).padStart(2, '0');
                    return {{
                        clock: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
                        spoken: d.getHours() + ' horas con ' + d.getMinutes() + ' minutos y ' + d.getSeconds() + ' segundos'
                    }};
                }}

                window.testWhistleAlert = function() {{
                    if (isMuted) window.toggleMute();
                    const t = getNowTimeVoice();
                    window.playFestiveWhistle(() => {{
                        window.speakSpanishAlert('¡Señal de subida! Squeeze Alcista cuatro de cuatro activado en {ticker} a las ' + t.spoken);
                    }});
                }};

                window.testSirenAlert = function() {{
                    if (isMuted) window.toggleMute();
                    const t = getNowTimeVoice();
                    window.playSirenAlarm(() => {{
                        window.speakSpanishAlert('¡Alerta de caída! Cascada Bajista cuatro de cuatro activada en {ticker} a las ' + t.spoken);
                    }});
                }};

                // Disparar automáticamente cuando una señal acaba de llegar a 4/4
                const newlyTriggered = {triggered_json};
                if (newlyTriggered && newlyTriggered.length > 0 && !isMuted) {{
                    const firstSig = newlyTriggered[0];
                    const parts = (firstSig.time_str || '').split(':');
                    const spokenTime = parts.length === 3
                        ? (parseInt(parts[0], 10) + ' horas con ' + parseInt(parts[1], 10) + ' minutos y ' + parseInt(parts[2], 10) + ' segundos')
                        : firstSig.time_str;

                    if (firstSig.sound_type === 'whistle') {{
                        window.playFestiveWhistle(() => {{
                            window.speakSpanishAlert('¡Atención! Señal de subida: ' + firstSig.voice_name + ' cuatro de cuatro activada en ' + firstSig.ticker + ' a las ' + spokenTime);
                        }});
                    }} else {{
                        window.playSirenAlarm(() => {{
                            window.speakSpanishAlert('¡Alerta de caída! ' + firstSig.voice_name + ' cuatro de cuatro activada en ' + firstSig.ticker + ' a las ' + spokenTime);
                        }});
                    }}
                }}
            }})();
            </script>
            """
            components.html(audio_component_html, height=62, scrolling=False)

            # Renderizar las 4 Tarjetas Simultáneas en un Grid de 2x2
            grid_col1, grid_col2 = st.columns(2)
            for idx_sig, sig in enumerate(signals_config):
                target_col = grid_col1 if idx_sig % 2 == 0 else grid_col2
                is_act = sig["is_active"]
                m_cnt = sig["met_count"]
                t_cnt = sig["total_reqs"]
                is_bear = sig["direction"] == "bearish"

                if is_act:
                    bg_card = "#2b1216" if is_bear else "#102819"
                    border_col = "#ff4d4d" if is_bear else "#2ecc71"
                    title_col = "#ff4d4d" if is_bear else "#2ecc71"
                    status_lbl = f"{sig['title']} ACTIVA ({m_cnt}/{t_cnt})"
                    box_shadow = f"0 0 16px {'rgba(255, 77, 77, 0.35)' if is_bear else 'rgba(46, 204, 113, 0.35)'}"
                    time_badge_html = (
                        f'<span style="background:{"#4a181f" if is_bear else "#173d26"};'
                        f'border:1px solid {border_col};color:#ffffff;font-family:\'Courier New\',monospace;'
                        f'font-size:12.5px;font-weight:800;padding:3px 10px;border-radius:999px;">'
                        f'🕒 Activada: {sig["activated_at"]} ({sig["elapsed_txt"]})'
                        f'</span>'
                    )
                elif m_cnt == 3:
                    bg_card = "#1a1710"
                    border_col = "#ffcc00"
                    title_col = "#ffcc00"
                    status_lbl = f"{sig['title']} — CALENTANDO ({m_cnt}/{t_cnt})"
                    box_shadow = "none"
                    last_txt = f" | Últ. vez: {sig.get('last_activated_at')}" if sig.get("last_activated_at") else ""
                    time_badge_html = (
                        f'<span style="background:#2b2310;border:1px solid #ffcc00;color:#ffcc00;'
                        f'font-family:monospace;font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px;">'
                        f'⏳ Falta 1 requisito{last_txt}'
                        f'</span>'
                    )
                else:
                    bg_card = "#11151c"
                    border_col = "#30363d"
                    title_col = "#c9d1d9"
                    status_lbl = f"{sig['title']} ({m_cnt}/{t_cnt})"
                    box_shadow = "none"
                    last_txt = f"Últ. activa: {sig.get('last_activated_at')}" if sig.get("last_activated_at") else "En espera (00:00:00)"
                    time_badge_html = (
                        f'<span style="background:#161b22;border:1px solid #30363d;color:#8b949e;'
                        f'font-family:monospace;font-size:11.5px;padding:2px 9px;border-radius:999px;">'
                        f'🕒 {last_txt}'
                        f'</span>'
                    )

                pills_html = ""
                for req in sig["reqs"]:
                    if req["met"]:
                        p_bg = "#132a1c"
                        p_border = "#2ecc71"
                        p_color = "#e6edf3"
                        p_icon = "✅"
                    else:
                        p_bg = "#161b22"
                        p_border = "#30363d"
                        p_color = "#8b949e"
                        p_icon = "⬜"
                    pills_html += (
                        f'<span style="display:inline-flex;align-items:center;gap:5px;background:{p_bg};'
                        f'border:1px solid {p_border};color:{p_color};padding:4px 11px;border-radius:999px;'
                        f'font-size:12.5px;font-weight:700;">'
                        f'{p_icon} {req["label"]}'
                        f'</span>'
                    )

                pct_bar = int((m_cnt / t_cnt) * 100)
                bar_color = border_col if m_cnt >= 3 else ("#ff6b6b" if is_bear else "#3fb950")

                card_html = (
                    f'<div style="background:{bg_card};border:1.5px solid {border_col};border-left:5px solid {border_col};'
                    f'border-radius:10px;padding:14px 16px;margin-bottom:14px;box-shadow:{box_shadow};">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">'
                    f'<span style="color:{title_col};font-weight:800;font-size:14.5px;letter-spacing:0.3px;">'
                    f'{sig["icon"]} {status_lbl}'
                    f'</span>'
                    f'{time_badge_html}'
                    f'</div>'
                    f'<div style="color:#d0d7de;font-size:13px;margin-bottom:10px;">{sig["desc"]}</div>'
                    f'<div style="width:100%;height:5px;background:#1c212b;border-radius:999px;overflow:hidden;margin-bottom:10px;">'
                    f'<div style="width:{pct_bar}%;height:100%;background:{bar_color};"></div>'
                    f'</div>'
                    f'<div style="display:flex;flex-wrap:wrap;gap:8px;">{pills_html}</div>'
                    f'</div>'
                )
                with target_col:
                    st.markdown(card_html, unsafe_allow_html=True)

            # Bitácora / Historial de Señales Activadas con Hora, Minuto y Segundo Exactos
            if st.session_state.signal_history_log:
                log_items_html = ""
                for item in st.session_state.signal_history_log[:6]:
                    c_log = "#ff7b72" if item["direction"] == "bearish" else "#7ee787"
                    log_items_html += (
                        f'<span style="background:#161b22;border:1px solid #30363d;padding:3px 10px;'
                        f'border-radius:999px;font-size:12px;font-family:monospace;color:{c_log};">'
                        f'🕒 <b>{item["time_str"]}</b> · {item["icon"]} {item["title"]} ({item["ticker"]})'
                        f'</span>'
                    )
                st.markdown(
                    f'<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:8px 14px;margin-bottom:12px;'
                    f'display:flex;align-items:center;flex-wrap:wrap;gap:8px;">'
                    f'<span style="color:#8b949e;font-size:12px;font-weight:700;">📜 Historial de Activaciones (HH:MM:SS):</span>'
                    f'{log_items_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

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

            verdict_html = (
                f'<div style="background:#0b0e14;border:1.5px solid {v_border};border-radius:10px;padding:14px 18px;margin-top:14px;margin-bottom:16px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">'
                f'<span style="color:{v_border};font-weight:800;font-size:15px;letter-spacing:0.4px;">{v_badge}</span>'
                f'<span style="color:#8b949e;font-size:12px;font-family:monospace;">Régimen: {gex_regime_txt}</span>'
                f'</div>'
                f'<div style="color:#d0d7de;font-size:13.5px;line-height:1.55;">'
                f'• <b>Plan Long:</b> Buscar compras si sostiene soporte/Put Wall en <b>${p_wall:g}</b> (con IV estable o bajando), apuntando al Imán/Pin <b>${pin_st:g}</b> y Call Wall <b>${c_wall:g}</b>. Evitar perseguir arriba del límite diario (<b>${em_high:.2f}</b>).<br>'
                f'• <b>Plan Short:</b> Si pierde <b>${p_wall:g}</b> con aumento de IV y flujo vendedor, hay riesgo de aceleración rápida hacia el piso del rango esperado (<b>${em_low:.2f}</b>).'
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
                    pin_show = tac["pin_df"].rename(columns={
                        "strike": "Strike",
                        "pin_score": "Pin Score (0-100)",
                        "dist_pct": "Distancia Spot (%)",
                        "call_oi": "Call OI",
                        "put_oi": "Put OI",
                        "net_gex": "Net GEX",
                        "role": "Rol Operativo",
                    })
                    st.dataframe(pin_show, use_container_width=True, hide_index=True)
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
                    radar_show = tac["radar_df"].rename(columns={
                        "contract_type": "Tipo",
                        "strike": "Strike",
                        "dte": "DTE",
                        "volume": "Volumen Hoy",
                        "open_interest": "Open Interest",
                        "vol_oi_ratio": "Ratio Vol/OI",
                        "prem_vol_usd": "Prima Operada ($)",
                        "delta": "Delta",
                        "signal": "Señal de Flujo",
                    })
                    st.dataframe(radar_show, use_container_width=True, hide_index=True)
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

            # Fila 6: Tabla del Scanner de Contratos Frontales
            if not df_raw.empty:
                st.markdown("<h2 class='custom-header'>Scanner de Contratos Frontales (Top OI & Vol)</h2>", unsafe_allow_html=True)
                
                # Preparar datos de contratos
                df_contracts = df_raw.copy()
                df_contracts['contract_type'] = df_contracts['type'].apply(lambda t: 'call' if t == 'C' else 'put')
                
                # Calcular ITM_State
                df_contracts['ITM_State'] = df_contracts.apply(
                    lambda r: 'ITM' if (r['contract_type'] == 'call' and r['strike'] < res['Spot Price']) or 
                                      (r['contract_type'] == 'put' and r['strike'] > res['Spot Price']) 
                              else 'OTM',
                    axis=1
                )
                
                # Seleccionar columnas y filtrar top 10 por OI + Volumen
                df_contracts['oi_vol_sum'] = df_contracts['open_interest'] + df_contracts['volume']
                df_scanner = df_contracts.nlargest(10, 'oi_vol_sum')[
                    ['contract_type', 'strike', 'dte', 'open_interest', 'volume', 'delta', 'ITM_State']
                ].copy()
                
                # Formatear números en la tabla para legibilidad
                df_scanner['open_interest'] = df_scanner['open_interest'].astype(int)
                df_scanner['volume'] = df_scanner['volume'].astype(int)
                df_scanner['strike'] = df_scanner['strike'].astype(float)
                df_scanner['dte'] = df_scanner['dte'].astype(int)
                
                # Aplicar color morado de fondo a las filas de tipo 'put', y negro/oscuro a las de 'call'
                def style_row_by_type(row):
                    color = '#2d1b4e' if row['contract_type'] == 'put' else '#0b0d10'
                    return [f'background-color: {color}'] * len(row)
                    
                styled_df = df_scanner.style.apply(style_row_by_type, axis=1)
                
                st.dataframe(styled_df, use_container_width=True, hide_index=True)
else:
    st.info("💡 Haz clic en 'Descargar Data' para cargar las fechas de expiración disponibles y comenzar.")
