import streamlit as st
import pandas as pd
import numpy as np
import time
import plotly.graph_objects as go
import streamlit.components.v1 as components
from gex_engine import (
    get_live_spot_price,
    get_options_expirations,
    fetch_and_calculate_all,
    calculate_levels
)

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
            ax=0, ay=-45, bgcolor="black", font=dict(color="white", size=9)
        )
        
        fig.add_annotation(
            x=min_neg['strike'], y=min_neg['put_gex'],
            text=f"▼ Strike {min_neg['strike']:.1f} ({format_millions(min_neg['put_gex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="#348feb",
            ax=0, ay=45, bgcolor="black", font=dict(color="#348feb", size=9)
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
            ax=0, ay=-45, bgcolor="black", font=dict(color="white", size=9)
        )
        
        fig.add_annotation(
            x=min_neg['strike'], y=-abs(min_neg['put_dex']),
            text=f"▼ Strike {min_neg['strike']:.1f} ({format_millions(min_neg['put_dex'], mode)})",
            showarrow=True, arrowhead=1, arrowcolor="#348feb",
            ax=0, ay=45, bgcolor="black", font=dict(color="#348feb", size=9)
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
                ax=0, ay=ay_val, bgcolor="black", font=dict(color="white", size=9)
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
            exps = get_options_expirations(ticker, api_key)
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
                df_gex, df_dex, df_raw, spot = fetch_and_calculate_all(ticker, selected_expirations, spot_manual, api_key, mode=engine_mode)
                
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
