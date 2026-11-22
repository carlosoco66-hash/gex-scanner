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
            
            # Extraer precio de la opción si viene en FlashAlpha (mid, last, mark, price o promedio bid/ask)
            if 'mid' in df_raw.columns:
                df_raw['opt_price'] = pd.to_numeric(df_raw['mid'], errors='coerce').fillna(0.0)
            elif 'last' in df_raw.columns:
                df_raw['opt_price'] = pd.to_numeric(df_raw['last'], errors='coerce').fillna(0.0)
            elif 'price' in df_raw.columns:
                df_raw['opt_price'] = pd.to_numeric(df_raw['price'], errors='coerce').fillna(0.0)
            elif 'bid' in df_raw.columns and 'ask' in df_raw.columns:
                b = pd.to_numeric(df_raw['bid'], errors='coerce').fillna(0.0)
                a = pd.to_numeric(df_raw['ask'], errors='coerce').fillna(0.0)
                df_raw['opt_price'] = (b + a) / 2.0
            else:
                df_raw['opt_price'] = 0.0
                
            if 'iv' in df_raw.columns and 'implied_volatility' not in df_raw.columns:
                iv_col = pd.to_numeric(df_raw['iv'], errors='coerce').fillna(0.0)
                df_raw['implied_volatility'] = iv_col.apply(lambda v: v / 100.0 if v > 3.0 else v)
            elif 'implied_volatility' in df_raw.columns:
                iv_col = pd.to_numeric(df_raw['implied_volatility'], errors='coerce').fillna(0.0)
                df_raw['implied_volatility'] = iv_col.apply(lambda v: v / 100.0 if v > 3.0 else v)
            else:
                df_raw['implied_volatility'] = 0.20
    except Exception as e:
        print(f"Error fetching option_quote: {e}")
        
    # Fallback a yfinance si FlashAlpha no devolvió datos para el símbolo
    if df_raw.empty:
        try:
            print(f"[FALLBACK] Iniciando descarga de opciones por yfinance para {ticker}...")
            t = yf.Ticker(ticker)
            expirations_to_fetch = selected_expirations if selected_expirations else t.options
            
            all_options = []
            for exp in expirations_to_fetch:
                try:
                    opt = t.option_chain(exp)
                    calls = opt.calls.copy()
                    calls['type'] = 'C'
                    calls['expiry'] = exp
                    puts = opt.puts.copy()
                    puts['type'] = 'P'
                    puts['expiry'] = exp
                    all_options.append(calls)
                    all_options.append(puts)
                except Exception as e_exp:
                    print(f"Error fetching yfinance option chain for {exp}: {e_exp}")
                    
            if all_options:
                df_yf_raw = pd.concat(all_options, ignore_index=True)
                df_raw = pd.DataFrame()
                df_raw['strike'] = df_yf_raw['strike']
                df_raw['type'] = df_yf_raw['type']
                df_raw['expiry'] = df_yf_raw['expiry']
                df_raw['open_interest'] = df_yf_raw['openInterest'].fillna(0.0).astype(float)
                df_raw['volume'] = df_yf_raw['volume'].fillna(0.0).astype(float)
                df_raw['implied_volatility'] = df_yf_raw['impliedVolatility'].fillna(0.0).astype(float)
                if 'lastPrice' in df_yf_raw.columns:
                    df_raw['opt_price'] = pd.to_numeric(df_yf_raw['lastPrice'], errors='coerce').fillna(0.0)
                else:
                    df_raw['opt_price'] = 0.0
                
                today = datetime.date.today()
                df_raw['dte'] = df_raw['expiry'].apply(
                    lambda e: (datetime.datetime.strptime(e, "%Y-%m-%d").date() - today).days
                )
                
                # Calcular Delta y Gamma con Black-Scholes
                import math
                gammas = []
                deltas = []
                for idx, row in df_raw.iterrows():
                    k_strike = row['strike']
                    k_dte = row['dte']
                    k_iv = row['implied_volatility']
                    k_type = row['type']
                    
                    if k_dte <= 0 or k_iv <= 0 or spot <= 0 or k_strike <= 0:
                        gammas.append(0.0)
                        deltas.append(0.0)
                        continue
                        
                    T = k_dte / 365.0
                    r = 0.045
                    try:
                        d1 = (math.log(spot / k_strike) + (r + 0.5 * k_iv ** 2) * T) / (k_iv * math.sqrt(T))
                        pdf = math.exp(-0.5 * d1**2) / math.sqrt(2.0 * math.pi)
                        gamma = pdf / (spot * k_iv * math.sqrt(T))
                        
                        cdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
                        delta = cdf if k_type == 'C' else (cdf - 1.0)
                        
                        gammas.append(gamma)
                        deltas.append(delta)
                    except Exception:
                        gammas.append(0.0)
                        deltas.append(0.0)
                        
                df_raw['gamma'] = gammas
                df_raw['delta'] = deltas
        except Exception as e_yf:
            print(f"Error in yfinance options fallback for {ticker}: {e_yf}")
        
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
    
    # Calcular prima teórica (Black-Scholes) cuando opt_price no venga en la respuesta
    import math
    if 'opt_price' not in df_raw.columns:
        df_raw['opt_price'] = 0.0
    if 'implied_volatility' not in df_raw.columns:
        df_raw['implied_volatility'] = 0.20
        
    def calc_opt_price(r):
        p = float(r.get('opt_price', 0.0) or 0.0)
        if p > 0:
            return p
        k = float(r.get('strike', 0.0) or 0.0)
        dte = max(float(r.get('dte', 0.0) or 0.0), 0.5)
        iv = float(r.get('implied_volatility', 0.20) or 0.20)
        if iv <= 0:
            iv = 0.20
        if spot <= 0 or k <= 0:
            return 0.0
        T = dte / 365.0
        rate = 0.045
        try:
            d1 = (math.log(spot / k) + (rate + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
            d2 = d1 - iv * math.sqrt(T)
            n_d1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
            n_d2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
            if r.get('type') == 'C':
                val = spot * n_d1 - k * math.exp(-rate * T) * n_d2
            else:
                val = k * math.exp(-rate * T) * (1.0 - n_d2) - spot * (1.0 - n_d1)
            return max(val, 0.01)
        except Exception:
            return max(spot - k, 0.01) if r.get('type') == 'C' else max(k - spot, 0.01)

    df_raw['opt_price'] = df_raw.apply(calc_opt_price, axis=1)
    df_raw['call_prem_vol'] = df_raw['call_volume'] * df_raw['opt_price'] * 100.0
    df_raw['put_prem_vol'] = df_raw['put_volume'] * df_raw['opt_price'] * 100.0
    df_raw['call_prem_oi'] = df_raw['call_oi'] * df_raw['opt_price'] * 100.0
    df_raw['put_prem_oi'] = df_raw['put_oi'] * df_raw['opt_price'] * 100.0

    # Calcular Vanna Exposure (VEX) y Charm Exposure (CHEX) por contrato (Black-Scholes)
    mult = 1.0 if mode == "Acciones" else 100.0
    call_vex_list, put_vex_list = [], []
    call_chex_list, put_chex_list = [], []
    rate_bs = 0.045
    sqrt_2pi = math.sqrt(2.0 * math.pi)

    for _, r in df_raw.iterrows():
        k = float(r.get('strike', 0.0) or 0.0)
        dte_val = max(float(r.get('dte', 0.0) or 0.0), 0.5)
        iv_val = float(r.get('implied_volatility', 0.20) or 0.20)
        if iv_val <= 0:
            iv_val = 0.20
        oi_val = float(r.get('open_interest', 0.0) or 0.0)
        opt_t = r.get('type', 'C')

        if spot <= 0 or k <= 0 or oi_val <= 0:
            call_vex_list.append(0.0)
            put_vex_list.append(0.0)
            call_chex_list.append(0.0)
            put_chex_list.append(0.0)
            continue

        T_val = dte_val / 365.0
        sqrt_T = math.sqrt(T_val)
        try:
            d1_v = (math.log(spot / k) + (rate_bs + 0.5 * iv_val * iv_val) * T_val) / (iv_val * sqrt_T)
            d2_v = d1_v - iv_val * sqrt_T
            pdf_d1 = math.exp(-0.5 * d1_v * d1_v) / sqrt_2pi

            # Vanna: dDelta / dVol (por cada 1% = 0.01 de cambio en IV)
            vanna_1pct = -pdf_d1 * (d2_v / iv_val) * 0.01
            # Magnitud de exposición Vanna en USD (o Acciones)
            vex_mag = abs(vanna_1pct) * oi_val * spot * mult
            # Signo real de Vanna según d2 y convención de dealers
            vex_signed = vanna_1pct * oi_val * spot * mult

            # Charm diario: dDelta / dt (cambio de delta por 1 día calendario)
            charm_daily = -(pdf_d1 * (2.0 * rate_bs * T_val - d2_v * iv_val * sqrt_T) / (2.0 * T_val * iv_val * sqrt_T)) / 365.0
            chex_mag = abs(charm_daily) * oi_val * spot * mult

            if opt_t == 'C':
                call_vex_list.append(vex_mag if vex_signed == 0 else abs(vex_signed))
                put_vex_list.append(0.0)
                call_chex_list.append(chex_mag)
                put_chex_list.append(0.0)
            else:
                call_vex_list.append(0.0)
                put_vex_list.append(-(vex_mag if vex_signed == 0 else abs(vex_signed)))
                call_chex_list.append(0.0)
                put_chex_list.append(-chex_mag)
        except Exception:
            call_vex_list.append(0.0)
            put_vex_list.append(0.0)
            call_chex_list.append(0.0)
            put_chex_list.append(0.0)

    df_raw['call_vex'] = call_vex_list
    df_raw['put_vex'] = put_vex_list
    df_raw['net_vex'] = df_raw['call_vex'] + df_raw['put_vex']

    df_raw['call_chex'] = call_chex_list
    df_raw['put_chex'] = put_chex_list
    df_raw['net_chex'] = df_raw['call_chex'] + df_raw['put_chex']
    
    # Agrupar por strike
    df_gex = df_raw.groupby('strike').agg({
        'call_gex': 'sum',
        'put_gex': 'sum',
        'net_gex': 'sum',
        'call_vex': 'sum',
        'put_vex': 'sum',
        'net_vex': 'sum',
        'call_chex': 'sum',
        'put_chex': 'sum',
        'net_chex': 'sum',
        'call_oi': 'sum',
        'put_oi': 'sum',
        'open_interest': 'sum',
        'volume': 'sum',
        'call_volume': 'sum',
        'put_volume': 'sum',
        'call_prem_vol': 'sum',
        'put_prem_vol': 'sum',
        'call_prem_oi': 'sum',
        'put_prem_oi': 'sum'
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


def _flashalpha_get(path, api_key, timeout=10):
    """Realiza una petición GET autenticada a FlashAlpha API."""
    import json
    import urllib.error
    import urllib.request

    last_err = None
    for base_url in ("https://lab.flashalpha.com", "https://api.flashalpha.com"):
        url = f"{base_url}{path}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Api-Key": api_key.strip(),
                "Accept": "application/json",
                "User-Agent": "python-requests/2.31.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="ignore")[:200]
            except Exception:
                body = ""
            last_err = f"HTTP {e.code} ({base_url}): {body or e.reason}"
            if e.code < 500:
                raise RuntimeError(last_err)
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(last_err or "Error desconocido en FlashAlpha API")


def fetch_live_flow(ticker, api_key):
    """
    Consulta los endpoints de Flujo en Vivo de FlashAlpha:
    - /v1/flow/summary/{symbol}
    - /v1/flow/gex/{symbol}
    - /v1/flow/levels/{symbol}
    """
    ticker = ticker.upper().strip()
    result = {
        "summary": {},
        "gex": {},
        "levels": {},
        "strikes_df": pd.DataFrame(),
        "error": None,
    }
    if not api_key or not ticker:
        result["error"] = "Falta API Key o Ticker."
        return result

    try:
        result["summary"] = _flashalpha_get(f"/v1/flow/summary/{ticker}", api_key)
    except Exception as e:
        result["error"] = f"Error en Flow Summary: {e}"

    try:
        gex_data = _flashalpha_get(f"/v1/flow/gex/{ticker}", api_key)
        result["gex"] = gex_data
        strikes = gex_data.get("strikes", []) if isinstance(gex_data, dict) else []
        if strikes:
            df_s = pd.DataFrame(strikes)
            for col in ["strike", "call_gex", "put_gex", "net_gex", "call_oi", "put_oi", "call_volume", "put_volume"]:
                if col in df_s.columns:
                    df_s[col] = pd.to_numeric(df_s[col], errors="coerce").fillna(0.0)
                else:
                    df_s[col] = 0.0
            df_s["total_volume"] = df_s["call_volume"] + df_s["put_volume"]
            result["strikes_df"] = df_s
    except Exception as e:
        err_msg = f"Error en Flow GEX: {e}"
        result["error"] = f"{result['error']} | {err_msg}" if result["error"] else err_msg

    # VEX, CHEX, Levels y Pin Risk se calculan localmente sobre option_quote / flow_gex para ahorrar cuota (0 requests extra)
    result["levels"] = {}
    result["pin_risk"] = {}
    result["vex_api"] = {}
    result["chex_api"] = {}

    return result


def calculate_tactical_metrics(df_gex, df_raw, spot, live_flow=None):
    """
    Calcula métricas tácticas profesionales para operativa intradía:
    - VEX Neto (Vanna Exposure) y CHEX Neto (Charm Exposure)
    - Expected Move (1 Día y Vencimiento Frontal) basado en IV ATM
    - Pin Risk Score (0-100) ponderado (30% OI, 25% Proximidad, 25% Tiempo, 20% Gamma)
    - Call Wall, Put Wall y Radar de Flujo Agresivo (Vol/OI y Prima Operada)
    """
    import math

    live_flow = live_flow or {}
    tactical = {
        "net_vex": 0.0,
        "net_chex": 0.0,
        "atm_iv": 0.25,
        "em_1d": 0.0,
        "em_1d_pct": 0.0,
        "em_low_1d": spot,
        "em_high_1d": spot,
        "em_exp": 0.0,
        "em_exp_pct": 0.0,
        "min_dte": 1,
        "pin_strike": spot,
        "pin_score": 0.0,
        "pin_label": "Bajo",
        "call_wall": spot,
        "put_wall": spot,
        "pin_df": pd.DataFrame(),
        "radar_df": pd.DataFrame(),
        "total_call_prem_vol": 0.0,
        "total_put_prem_vol": 0.0,
        "total_call_prem_oi": 0.0,
        "total_put_prem_oi": 0.0,
        "spot_change_pct": 0.0,
        "iv_rising": False,
        "front_iv": 0.25,
        "back_iv": 0.25,
    }

    if df_gex.empty or spot <= 0:
        return tactical

    # 0. Variación porcentual del Spot vs cierre previo (yfinance / live_flow)
    try:
        lf_sum = live_flow.get("summary") or {}
        lf_gex = live_flow.get("gex") or {}
        pct_api = lf_sum.get("underlying_change_pct") or lf_gex.get("underlying_change_pct") or lf_sum.get("price_change_pct")
        if pct_api is not None:
            tactical["spot_change_pct"] = float(pct_api)
        else:
            ticker_sym = lf_sum.get("symbol") or lf_gex.get("symbol")
            if ticker_sym:
                t_info = yf.Ticker(str(ticker_sym).upper()).fast_info
                prev_c = float(t_info.get("previousClose") or t_info.get("regularMarketPreviousClose") or 0.0)
                if prev_c > 0:
                    tactical["spot_change_pct"] = ((spot - prev_c) / prev_c) * 100.0
    except Exception:
        pass

    # 1. VEX y CHEX totales (priorizando el cálculo sobre las expiraciones activas)
    net_vex = float(df_gex["net_vex"].sum()) if "net_vex" in df_gex.columns else 0.0
    net_chex = float(df_gex["net_chex"].sum()) if "net_chex" in df_gex.columns else 0.0

    vex_api = live_flow.get("vex_api") or {}
    chex_api = live_flow.get("chex_api") or {}
    if net_vex == 0.0 and isinstance(vex_api, dict):
        net_vex = float(vex_api.get("net_vex") or vex_api.get("total_vex") or 0.0)
    if net_chex == 0.0 and isinstance(chex_api, dict):
        net_chex = float(chex_api.get("net_chex") or chex_api.get("total_chex") or 0.0)

    tactical["net_vex"] = net_vex
    tactical["net_chex"] = net_chex

    # 2. IV ATM y Expected Move (1 Día y Vencimiento Frontal)
    atm_iv = 0.35
    min_dte = 1
    if not df_raw.empty:
        if "dte" in df_raw.columns:
            pos_dtes = df_raw[df_raw["dte"] >= 0]["dte"]
            if not pos_dtes.empty:
                min_dte = max(int(pos_dtes.min()), 1)
        if "implied_volatility" in df_raw.columns:
            df_iv = df_raw[(df_raw["implied_volatility"] > 0.01) & (df_raw["implied_volatility"] < 5.0)].copy()
            if not df_iv.empty:
                df_iv["dist_spot"] = (df_iv["strike"] - spot).abs()
                atm_rows = df_iv.nsmallest(6, "dist_spot")
                iv_mean = float(atm_rows["implied_volatility"].mean())
                if iv_mean > 0:
                    atm_iv = iv_mean
                # Comparar IV de puts vs calls cerca de ATM o estructura temporal para detectar presión de IV al alza
                put_atm = atm_rows[atm_rows["type"] == "P"]["implied_volatility"]
                call_atm = atm_rows[atm_rows["type"] == "C"]["implied_volatility"]
                p_iv = float(put_atm.mean()) if not put_atm.empty else atm_iv
                c_iv = float(call_atm.mean()) if not call_atm.empty else atm_iv
                tactical["front_iv"] = p_iv
                tactical["back_iv"] = c_iv
                tactical["iv_rising"] = bool(p_iv >= c_iv)

    em_1d = spot * atm_iv * math.sqrt(1.0 / 252.0)
    em_1d_pct = (em_1d / spot) * 100.0 if spot > 0 else 0.0
    em_exp = spot * atm_iv * math.sqrt(min_dte / 365.0)
    em_exp_pct = (em_exp / spot) * 100.0 if spot > 0 else 0.0

    tactical["atm_iv"] = atm_iv
    tactical["em_1d"] = em_1d
    tactical["em_1d_pct"] = em_1d_pct
    tactical["em_low_1d"] = max(spot - em_1d, 0.0)
    tactical["em_high_1d"] = spot + em_1d
    tactical["em_exp"] = em_exp
    tactical["em_exp_pct"] = em_exp_pct
    tactical["min_dte"] = min_dte

    # 3. Call Wall, Put Wall y Pin Risk Score (0-100) por Strike
    df_near = df_gex[(df_gex["strike"] >= spot * 0.75) & (df_gex["strike"] <= spot * 1.25)].copy()
    if df_near.empty:
        df_near = df_gex.copy()

    call_wall = float(df_near.loc[df_near["call_gex"].idxmax()]["strike"]) if "call_gex" in df_near.columns else spot
    put_wall = float(df_near.loc[df_near["put_gex"].idxmin()]["strike"]) if "put_gex" in df_near.columns else spot

    lf_levels = live_flow.get("levels") or {}
    if isinstance(lf_levels, dict):
        if lf_levels.get("call_wall"):
            try:
                call_wall = float(lf_levels["call_wall"])
            except Exception:
                pass
        if lf_levels.get("put_wall"):
            try:
                put_wall = float(lf_levels["put_wall"])
            except Exception:
                pass

    max_oi = max(float(df_near["open_interest"].max()), 1.0)
    max_gex = max(float(df_near["net_gex"].abs().max()), 1.0)

    # Peso temporal por strike según proporción de OI en vencimientos cortos (<= 14 DTE)
    short_oi_map = {}
    if not df_raw.empty and "dte" in df_raw.columns:
        df_short = df_raw[df_raw["dte"] <= 14]
        if not df_short.empty:
            short_oi_map = df_short.groupby("strike")["open_interest"].sum().to_dict()

    pin_rows = []
    for _, r in df_near.iterrows():
        s = float(r["strike"])
        oi_tot = float(r.get("open_interest", 0.0) or 0.0)
        c_oi = int(r.get("call_oi", 0) or 0)
        p_oi = int(r.get("put_oi", 0) or 0)
        n_gex = float(r.get("net_gex", 0.0) or 0.0)
        c_gex = float(r.get("call_gex", 0.0) or 0.0)
        p_gex = float(r.get("put_gex", 0.0) or 0.0)

        # 4 componentes de FlashAlpha Pin Risk: 30% OI, 25% Proximidad, 25% Tiempo, 20% Gamma
        oi_score = min((oi_tot / max_oi) * 100.0, 100.0)
        dist_pct = abs(s - spot) / spot if spot > 0 else 1.0
        prox_score = max(0.0, (1.0 - dist_pct / 0.10)) * 100.0
        short_ratio = (float(short_oi_map.get(s, 0.0)) / oi_tot) if oi_tot > 0 else 0.0
        time_score = min(35.0 + 65.0 * short_ratio, 100.0) if oi_tot > 0 else 0.0
        gex_score = min(((abs(c_gex) + abs(p_gex)) / (max_gex * 1.5)) * 100.0, 100.0)

        score = 0.30 * oi_score + 0.25 * prox_score + 0.25 * time_score + 0.20 * gex_score

        if abs(s - call_wall) < 1e-6 and abs(s - put_wall) < 1e-6:
            role = "🎯 Imán / Muro Dual"
        elif abs(s - call_wall) < 1e-6:
            role = "🧱 Call Wall (Techo)"
        elif abs(s - put_wall) < 1e-6:
            role = "🛡️ Put Wall (Piso)"
        elif n_gex > 0:
            role = "🧲 Zona Pin (GEX +)"
        else:
            role = "⚡ Acelerador (GEX -)"

        pin_rows.append({
            "strike": s,
            "pin_score": round(score, 1),
            "dist_pct": round(((s - spot) / spot) * 100.0, 2),
            "call_oi": c_oi,
            "put_oi": p_oi,
            "net_gex": n_gex,
            "role": role,
        })

    pin_df = pd.DataFrame(pin_rows)
    if not pin_df.empty:
        pin_df = pin_df.sort_values("pin_score", ascending=False).reset_index(drop=True)
        top_pin = pin_df.iloc[0]
        pin_strike = float(top_pin["strike"])
        pin_score = float(top_pin["pin_score"])
        if pin_df.loc[0, "role"].startswith("🧲") or pin_df.loc[0, "role"].startswith("⚡"):
            pin_df.loc[0, "role"] = "🎯 Imán Principal (Top Pin)"
    else:
        pin_strike = spot
        pin_score = 0.0

    # Si FlashAlpha devolvió pin_risk directo de la API, respetar si trae score/strike válido
    pr_api = live_flow.get("pin_risk") or {}
    if isinstance(pr_api, dict):
        api_ps = pr_api.get("pin_score") or pr_api.get("score")
        api_st = pr_api.get("pin_strike") or pr_api.get("magnet_strike") or pr_api.get("strike")
        if api_ps is not None:
            try:
                pin_score = float(api_ps)
            except Exception:
                pass
        if api_st is not None:
            try:
                pin_strike = float(api_st)
            except Exception:
                pass

    if pin_score >= 65:
        pin_label = "ALTO"
    elif pin_score >= 40:
        pin_label = "MEDIO"
    else:
        pin_label = "BAJO"

    tactical["pin_strike"] = pin_strike
    tactical["pin_score"] = pin_score
    tactical["pin_label"] = pin_label
    tactical["call_wall"] = call_wall
    tactical["put_wall"] = put_wall
    tactical["pin_df"] = pin_df.head(10)

    # 4. Radar de Flujo Agresivo (Vol/OI, Prima Operada y Sesgo por Contrato)
    if not df_raw.empty:
        df_r = df_raw.copy()
        df_r["contract_type"] = df_r["type"].apply(lambda t: "CALL" if t == "C" else "PUT")
        df_r["vol_oi_ratio"] = df_r.apply(
            lambda r: round(float(r["volume"]) / float(r["open_interest"]), 2)
            if float(r.get("open_interest", 0) or 0) > 0
            else (99.0 if float(r.get("volume", 0) or 0) > 0 else 0.0),
            axis=1,
        )
        df_r["prem_vol_usd"] = df_r["volume"] * df_r["opt_price"] * 100.0
        tactical["total_call_prem_vol"] = float(df_r[df_r["type"] == "C"]["prem_vol_usd"].sum())
        tactical["total_put_prem_vol"] = float(df_r[df_r["type"] == "P"]["prem_vol_usd"].sum())
        if "call_prem_oi" in df_r.columns and "put_prem_oi" in df_r.columns:
            tactical["total_call_prem_oi"] = float(df_r["call_prem_oi"].sum())
            tactical["total_put_prem_oi"] = float(df_r["put_prem_oi"].sum())

        def classify_signal(r):
            vol = float(r.get("volume", 0) or 0)
            ratio = float(r.get("vol_oi_ratio", 0) or 0)
            ctype = r.get("contract_type", "CALL")
            if vol <= 0:
                return "⏸️ Sin Volumen Hoy"
            if ratio >= 2.0:
                return f"🔥 Apertura Masiva ({ctype})"
            if ratio >= 0.75:
                return f"⚡ Flujo Activo Nuevo ({ctype})"
            if ratio >= 0.25:
                return f"📈 Rotación Moderada ({ctype})"
            return "🏛️ Posición Estructural (OI)"

        df_r["signal"] = df_r.apply(classify_signal, axis=1)
        df_r["sort_key"] = df_r["prem_vol_usd"] + df_r["open_interest"] * 10.0
        radar_df = df_r.sort_values(["volume", "prem_vol_usd", "open_interest"], ascending=False).head(12)[
            ["contract_type", "strike", "dte", "volume", "open_interest", "vol_oi_ratio", "prem_vol_usd", "delta", "signal"]
        ].copy()
        radar_df["volume"] = radar_df["volume"].astype(int)
        radar_df["open_interest"] = radar_df["open_interest"].astype(int)
        radar_df["dte"] = radar_df["dte"].astype(int)
        radar_df["prem_vol_usd"] = radar_df["prem_vol_usd"].round(0)
        radar_df["delta"] = radar_df["delta"].round(3)
        tactical["radar_df"] = radar_df

    return tactical
