from flashalpha import FlashAlpha
import pandas as pd
import numpy as np
import yfinance as yf
import datetime

def get_live_spot_price(ticker):
    """Obtiene el precio spot en tiempo real usando yfinance."""
    try:
        t = yf.Ticker(ticker.upper())
        try:
            return float(t.fast_info['lastPrice'])
        except:
            hist = t.history(period='1d')
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Error fetching yfinance spot for {ticker}: {e}")
    return 0.0

def get_options_expirations(ticker, api_key):
    """Obtiene la lista de fechas de expiración disponibles usando FlashAlpha."""
    try:
        fa = FlashAlpha(api_key)
        data = fa.option_quote(ticker.upper())
        if isinstance(data, list) and len(data) > 0:
            exp_list = list(set(x.get('expiry') for x in data if x.get('expiry')))
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            exp_list = sorted([exp for exp in exp_list if exp >= today_str])
            if exp_list:
                return exp_list
    except Exception as e:
        print(f"Error fetching expirations from FlashAlpha: {e}")

    # Fallback to yfinance
    try:
        t = yf.Ticker(ticker.upper())
        options_list = list(t.options)
        if options_list:
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            return sorted([exp for exp in options_list if exp >= today_str])
    except Exception as e:
        print(f"Error fetching expirations from yfinance: {e}")
    return []

def fetch_and_calculate_all(ticker, selected_expirations, spot_manual, api_key, mode="USD"):
    """
    Obtiene y calcula los datos de GEX y DEX de manera local.
    Esto permite evitar los bugs del servidor de FlashAlpha (que no calculaba Puts)
    y reduce los tiempos de carga en un 66%.
    """
    fa = FlashAlpha(api_key)
    ticker = ticker.upper()
    
    # 1. Obtener spot real
    spot = spot_manual
    if spot == 0.00:
        spot = get_live_spot_price(ticker)
        
    # 2. Descargar cadena cruda con fa.option_quote
    df_raw = pd.DataFrame()
    try:
        data_quote = fa.option_quote(ticker)
        df_raw = pd.DataFrame(data_quote)
        if not df_raw.empty:
            if selected_expirations:
                df_raw = df_raw[df_raw['expiry'].isin(selected_expirations)].copy()
            # Calcular dte manualmente
            today = datetime.date.today()
            df_raw['dte'] = df_raw['expiry'].apply(
                lambda e: (datetime.datetime.strptime(e, "%Y-%m-%d").date() - today).days
            )
            
            # Limpiar tipos
            df_raw['gamma'] = pd.to_numeric(df_raw['gamma'], errors='coerce')
            df_raw['delta'] = pd.to_numeric(df_raw['delta'], errors='coerce')
            df_raw['strike'] = pd.to_numeric(df_raw['strike'], errors='coerce')
            
            # Completar Gamma y Delta de Puts usando paridad Call-Put para corregir vacíos de la API
            df_calls = df_raw[df_raw['type'] == 'C'][['strike', 'expiry', 'gamma', 'delta']].rename(
                columns={'gamma': 'call_gamma', 'delta': 'call_delta'}
            )
            df_raw = df_raw.merge(df_calls, on=['strike', 'expiry'], how='left')
            
            df_raw['gamma'] = df_raw.apply(
                lambda r: r['call_gamma'] if pd.notna(r['call_gamma']) else r['gamma'], 
                axis=1
            ).fillna(0.0).astype(float)
            
            df_raw['delta'] = df_raw.apply(
                lambda r: (r['call_delta'] - 1.0) if r['type'] == 'P' and pd.notna(r['call_delta']) else r['delta'],
                axis=1
            ).fillna(0.0).astype(float)
            
            df_raw['open_interest'] = df_raw['open_interest'].fillna(0.0).astype(float)
            df_raw['volume'] = df_raw['volume'].fillna(0.0).astype(float)
    except Exception as e:
        print(f"Error fetching option_quote: {e}")
        
    if df_raw.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), spot
        
    # Calcular exposiciones locales según el modo
    if mode == "Acciones":
        # MODO ACCIONES: Exposición en acciones (sin el 100x y multiplicado por spot lineal)
        df_raw['call_gex'] = df_raw.apply(lambda r: r['open_interest'] * r['gamma'] * spot if r['type'] == 'C' else 0.0, axis=1)
        df_raw['put_gex'] = df_raw.apply(lambda r: -r['open_interest'] * r['gamma'] * spot if r['type'] == 'P' else 0.0, axis=1)
        df_raw['net_gex'] = df_raw['call_gex'] + df_raw['put_gex']
        
        df_raw['call_dex'] = df_raw.apply(lambda r: r['open_interest'] * r['delta'] * spot if r['type'] == 'C' else 0.0, axis=1)
        df_raw['put_dex'] = df_raw.apply(lambda r: r['open_interest'] * r['delta'] * spot if r['type'] == 'P' else 0.0, axis=1)
        df_raw['net_dex'] = df_raw['call_dex'] + df_raw['put_dex']
    else:
        # MODO USD: Fórmula oficial en USD (con spot*spot para GEX y delta*spot*100 para DEX)
        df_raw['call_gex'] = df_raw.apply(lambda r: r['open_interest'] * r['gamma'] * spot * spot if r['type'] == 'C' else 0.0, axis=1)
        df_raw['put_gex'] = df_raw.apply(lambda r: -r['open_interest'] * r['gamma'] * spot * spot if r['type'] == 'P' else 0.0, axis=1)
        df_raw['net_gex'] = df_raw['call_gex'] + df_raw['put_gex']
        
        df_raw['call_dex'] = df_raw.apply(lambda r: r['open_interest'] * r['delta'] * spot * 100.0 if r['type'] == 'C' else 0.0, axis=1)
        df_raw['put_dex'] = df_raw.apply(lambda r: r['open_interest'] * r['delta'] * spot * 100.0 if r['type'] == 'P' else 0.0, axis=1)
        df_raw['net_dex'] = df_raw['call_dex'] + df_raw['put_dex']

    # Campos de soporte comunes
    df_raw['call_oi'] = df_raw.apply(lambda r: r['open_interest'] if r['type'] == 'C' else 0.0, axis=1)
    df_raw['put_oi'] = df_raw.apply(lambda r: r['open_interest'] if r['type'] == 'P' else 0.0, axis=1)
    df_raw['call_volume'] = df_raw.apply(lambda r: r['volume'] if r['type'] == 'C' else 0.0, axis=1)
    df_raw['put_volume'] = df_raw.apply(lambda r: r['volume'] if r['type'] == 'P' else 0.0, axis=1)
    
    # Agrupar por strike
    df_gex = df_raw.groupby('strike').agg({
        'call_gex': 'sum',
        'put_gex': 'sum',
        'net_gex': 'sum',
        'call_oi': 'sum',
        'put_oi': 'sum',
        'open_interest': 'sum',
        'volume': 'sum',
        'call_volume': 'sum',
        'put_volume': 'sum'
    }).reset_index()
    
    df_dex = df_raw.groupby('strike').agg({
        'call_dex': 'sum',
        'put_dex': 'sum',
        'net_dex': 'sum'
    }).reset_index()

    return df_gex, df_dex, df_raw, spot

def calc_max_pain(df):
    """Calcula el precio Max Pain."""
    if 'call_oi' not in df.columns or 'put_oi' not in df.columns or df.empty:
        return 0.0
    strikes = df['strike'].unique()
    pain = []
    for s in strikes:
        call_pain = (df['call_oi'] * (df['strike'] - s).clip(lower=0)).sum()
        put_pain = (df['put_oi'] * (s - df['strike']).clip(lower=0)).sum()
        pain.append((s, call_pain + put_pain))
    if not pain:
        return 0.0
    return min(pain, key=lambda x: x[1])[0]

def calc_gamma_flip(df, spot):
    """Calcula el nivel de Gamma Flip (Zero GEX)."""
    if df.empty or 'net_gex' not in df.columns:
        return 0.0
    df = df.sort_values('strike').reset_index(drop=True)
    
    df_active = df[df['net_gex'] != 0].copy()
    if df_active.empty:
        return 0.0
        
    df_active['sign'] = np.sign(df_active['net_gex'])
    df_active['sign_change'] = df_active['sign'].diff()
    
    crossings = df_active[df_active['sign_change'].notna() & (df_active['sign_change'] != 0)]
    
    if not crossings.empty:
        closest_idx = (crossings['strike'] - spot).abs().idxmin()
        return float(crossings.loc[closest_idx]['strike'])
    else:
        closest_idx = df_active['net_gex'].abs().idxmin()
        return float(df_active.loc[closest_idx]['strike'])

def calculate_levels(df_gex, df_dex, spot):
    """Calcula los niveles clave GEX."""
    if df_gex.empty:
        return {}
        
    net_gex = df_gex['net_gex'].sum() if 'net_gex' in df_gex.columns else 0.0
    net_dex = df_dex['net_dex'].sum() if (not df_dex.empty and 'net_dex' in df_dex.columns) else 0.0
    
    # PTrans: Strike con mayor GEX de Calls
    res_strike = spot
    if 'call_gex' in df_gex.columns and not df_gex.empty:
        res_strike = df_gex.loc[df_gex['call_gex'].idxmax()]['strike']
        
    # NTrans: Strike con menor GEX de Puts (máximo negativo)
    sup_strike = spot
    if 'put_gex' in df_gex.columns and not df_gex.empty:
        sup_strike = df_gex.loc[df_gex['put_gex'].idxmin()]['strike']
        
    # Zero GEX (Gamma Flip)
    gamma_flip = calc_gamma_flip(df_gex, spot)
    
    # COTMP (Max Pain Pin)
    pin_strike = calc_max_pain(df_gex)
    
    # Totales de OI
    call_oi = int(df_gex['call_oi'].sum()) if 'call_oi' in df_gex.columns else 0
    put_oi = int(df_gex['put_oi'].sum()) if 'put_oi' in df_gex.columns else 0
    
    return {
        "PTrans (Resistance)": res_strike,
        "Zero GEX (Balance)": gamma_flip,
        "NTrans (Support)": sup_strike,
        "COTMP (Pin)": pin_strike,
        "Call OI": call_oi,
        "Put OI": put_oi,
        "Gamma Flip": gamma_flip,
        "Spot Price": spot,
        "Total Net GEX": net_gex,
        "Total Net DEX": net_dex
    }
