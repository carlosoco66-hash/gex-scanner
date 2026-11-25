from flashalpha import FlashAlpha
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import math


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inv_norm_cdf(p):
    """Aproximación de Acklam para inversa de la normal estándar (0 < p < 1)."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _solve_iv_from_price(price, spot, strike, T, opt_type, rate=0.045):
    """Resuelve la IV real por bisección de Black-Scholes desde el precio en vivo de la opción."""
    if price <= 0.005 or spot <= 0 or strike <= 0 or T <= 0:
        return 0.0
    disc_k = strike * math.exp(-rate * T)
    intrinsic = max(spot - disc_k, 0.0) if opt_type == 'C' else max(disc_k - spot, 0.0)
    if price <= intrinsic + 0.005:
        return 0.0

    lo, hi = 0.02, 3.50
    sqrt_T = math.sqrt(T)
    for _ in range(28):
        mid = 0.5 * (lo + hi)
        d1 = (math.log(spot / strike) + (rate + 0.5 * mid * mid) * T) / (mid * sqrt_T)
        d2 = d1 - mid * sqrt_T
        if opt_type == 'C':
            bs_val = spot * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
        else:
            bs_val = disc_k * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        if bs_val > price:
            hi = mid
        else:
            lo = mid
    iv_res = 0.5 * (lo + hi)
    return iv_res if 0.025 < iv_res < 3.45 else 0.0


def _solve_iv_from_greeks(gamma, delta, spot, T, opt_type):
    """Estima IV real invirtiendo Gamma y Delta en vivo de Black-Scholes."""
    if gamma <= 1e-6 or spot <= 0 or T <= 0:
        return 0.0
    c_delta = delta if opt_type == 'C' else (delta + 1.0)
    if c_delta <= 0.12 or c_delta >= 0.88:
        return 0.0
    try:
        d1 = _inv_norm_cdf(c_delta)
        pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        iv_est = pdf_d1 / (spot * gamma * math.sqrt(T))
        return iv_est if 0.04 <= iv_est <= 3.0 else 0.0
    except Exception:
        return 0.0

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

def get_hv_and_gap_context(ticker):
    """Obtiene Volatilidad Histórica (HV 20d), cierre previo y apertura del día con yfinance."""
    out = {"hv_20d": 0.0, "prev_close": 0.0, "today_open": 0.0}
    try:
        t = yf.Ticker(ticker.upper())
        hist = t.history(period="2mo", interval="1d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if len(closes) >= 6:
                log_ret = np.log(closes / closes.shift(1)).dropna().tail(20)
                if len(log_ret) >= 5:
                    out["hv_20d"] = float(log_ret.std() * math.sqrt(252.0) * 100.0)
            today_d = datetime.date.today()
            last_idx_d = hist.index[-1].date() if hasattr(hist.index[-1], "date") else None
            if last_idx_d == today_d and len(closes) >= 2:
                out["prev_close"] = float(closes.iloc[-2])
                out["today_open"] = float(pd.to_numeric(hist["Open"].iloc[-1], errors="coerce") or 0.0)
            elif len(closes) >= 1:
                out["prev_close"] = float(closes.iloc[-1])
                out["today_open"] = float(pd.to_numeric(hist["Open"].iloc[-1], errors="coerce") or 0.0)
        try:
            fi = t.fast_info
            pc = fi.get("previousClose") or fi.get("regularMarketPreviousClose")
            op = fi.get("open")
            if pc and float(pc) > 0:
                out["prev_close"] = float(pc)
            if op and float(op) > 0:
                out["today_open"] = float(op)
        except Exception:
            pass
    except Exception as e:
        print(f"Error fetching HV/gap context for {ticker}: {e}")
    return out


def get_macro_climate(api_key):
    """Obtiene clima macro (VIX, VVIX, MOVE vía yfinance + signo GEX en vivo de SPY/QQQ/IWM vía FlashAlpha)."""
    macro = {
        "vix": 0.0, "vix_chg": 0.0,
        "vvix": 0.0, "vvix_chg": 0.0,
        "move": 0.0, "move_chg": 0.0,
        "indices_gex": {},
    }
    for key, sym in (("vix", "^VIX"), ("vvix", "^VVIX"), ("move", "^MOVE")):
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5d", interval="1d")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
                if len(closes) >= 1:
                    last_v = float(closes.iloc[-1])
                    prev_v = float(closes.iloc[-2]) if len(closes) >= 2 else last_v
                    try:
                        fi_last = t.fast_info.get("lastPrice")
                        fi_prev = t.fast_info.get("previousClose") or t.fast_info.get("regularMarketPreviousClose")
                        if fi_last and float(fi_last) > 0:
                            last_v = float(fi_last)
                        if fi_prev and float(fi_prev) > 0:
                            prev_v = float(fi_prev)
                    except Exception:
                        pass
                    macro[key] = last_v
                    macro[f"{key}_chg"] = ((last_v - prev_v) / prev_v * 100.0) if prev_v > 0 else 0.0
        except Exception:
            pass

    if api_key:
        for idx_sym in ("SPY", "QQQ", "IWM"):
            try:
                resp = _flashalpha_get(f"/v1/gex/live-flow/{idx_sym}", api_key, timeout=6)
                if isinstance(resp, dict):
                    gex_blk = resp.get("gex") or {}
                    sum_blk = resp.get("summary") or {}
                    val = gex_blk.get("live_net_gex")
                    if val is None:
                        val = sum_blk.get("live_gex")
                    if val is not None:
                        macro["indices_gex"][idx_sym] = 1 if float(val) >= 0 else -1
            except Exception:
                pass
    return macro


def _compute_term_structure(df_all, spot):
    """Calcula Curva IV (Term Structure: Contango vs Backwardation) sin llamadas extra usando toda la cadena."""
    res = {"ts_near_iv": 0.0, "ts_far_iv": 0.0, "ts_near_dte": 0, "ts_far_dte": 0}
    if df_all is None or df_all.empty or spot <= 0 or "expiry" not in df_all.columns:
        return res
    try:
        today = datetime.date.today()
        exps_dte = []
        for exp in df_all["expiry"].dropna().unique():
            try:
                d = (datetime.datetime.strptime(str(exp), "%Y-%m-%d").date() - today).days
                if d >= 0:
                    exps_dte.append((d, str(exp)))
            except Exception:
                pass
        if len(exps_dte) < 2:
            return res
        exps_dte.sort(key=lambda x: x[0])

        # Vencimiento cercano (preferir >=1 DTE para evitar distorsión de último minuto en 0 DTE; si no, el menor)
        near_cands = [x for x in exps_dte if x[0] >= 1]
        near_dte, near_exp = near_cands[0] if near_cands else exps_dte[0]

        # Vencimiento lejano (más cercano a 30 DTE entre >= 14 DTE, o el más lejano disponible > near_dte)
        far_cands = [x for x in exps_dte if x[0] >= max(near_dte + 7, 14)]
        if far_cands:
            far_dte, far_exp = min(far_cands, key=lambda x: abs(x[0] - 30))
        else:
            longer = [x for x in exps_dte if x[0] > near_dte]
            if not longer:
                return res
            far_dte, far_exp = longer[-1]

        def _exp_atm_iv_pct(exp_str, dte_val):
            sub = df_all[df_all["expiry"] == exp_str].copy()
            if sub.empty:
                return 0.0
            sub["strike_n"] = pd.to_numeric(sub["strike"], errors="coerce")
            sub = sub.dropna(subset=["strike_n"])
            sub["dist_s"] = (sub["strike_n"] - spot).abs()
            sub = sub.nsmallest(8, "dist_s")

            # 1) IV directa si viene en FlashAlpha
            for iv_k in ("iv", "implied_volatility", "impliedVolatility", "mid_iv", "mark_iv", "volatility"):
                if iv_k in sub.columns:
                    vals = pd.to_numeric(sub[iv_k], errors="coerce").dropna()
                    vals = vals.apply(lambda v: v / 100.0 if v > 3.0 else v)
                    vals = vals[(vals > 0.02) & (vals < 3.5)]
                    if not vals.empty:
                        return float(vals.median() * 100.0)

            # 2) Resolver IV por Black-Scholes desde precio o gamma/delta
            T_iv = max(float(dte_val), 0.25) / 365.0
            ivs = []
            for _, r in sub.iterrows():
                k_v = float(r["strike_n"])
                opt_t = r.get("type", "C")
                p_v = 0.0
                for pk in ("mid", "mark", "last", "price"):
                    if pk in r and pd.notna(r[pk]) and float(r[pk] or 0) > 0:
                        p_v = float(r[pk])
                        break
                if p_v <= 0 and "bid" in r and "ask" in r:
                    bv = float(pd.to_numeric(r.get("bid"), errors="coerce") or 0.0)
                    av = float(pd.to_numeric(r.get("ask"), errors="coerce") or 0.0)
                    if bv > 0 and av > 0:
                        p_v = 0.5 * (bv + av)
                iv_c = _solve_iv_from_price(p_v, spot, k_v, T_iv, opt_t) if p_v > 0 else 0.0
                if iv_c <= 0:
                    g_v = float(pd.to_numeric(r.get("gamma"), errors="coerce") or 0.0)
                    d_v = float(pd.to_numeric(r.get("delta"), errors="coerce") or 0.0)
                    iv_c = _solve_iv_from_greeks(g_v, d_v, spot, T_iv, opt_t)
                if 0.02 < iv_c < 3.5:
                    ivs.append(iv_c * 100.0)
            return float(np.median(ivs)) if ivs else 0.0

        near_iv = _exp_atm_iv_pct(near_exp, near_dte)
        far_iv = _exp_atm_iv_pct(far_exp, far_dte)
        if near_iv > 1.0 and far_iv > 1.0:
            res["ts_near_iv"] = round(near_iv, 2)
            res["ts_far_iv"] = round(far_iv, 2)
            res["ts_near_dte"] = int(near_dte)
            res["ts_far_dte"] = int(far_dte)
    except Exception as e:
        print(f"Error computing term structure: {e}")
    return res


def _build_full_chain_scanner_records(df_full, spot, mode="USD"):
    """
    Prepara un resumen liviano de contratos de TODA la cadena (Intradía 0–7d, Swing 8–60d,
    Largo Plazo >60d y Vista Completa) sin llamadas extra a la API.
    """
    if df_full is None or df_full.empty or spot <= 0:
        return []
    try:
        df = df_full.copy()
        if 'expiry' not in df.columns or 'strike' not in df.columns or 'type' not in df.columns:
            return []

        today = datetime.date.today()

        def _parse_dte(e):
            try:
                return (datetime.datetime.strptime(str(e)[:10], "%Y-%m-%d").date() - today).days
            except Exception:
                return -1

        df['dte'] = df['expiry'].apply(_parse_dte)
        df = df[df['dte'] >= 0].copy()
        if df.empty:
            return []

        df['strike'] = pd.to_numeric(df['strike'], errors='coerce')
        df['gamma'] = pd.to_numeric(df['gamma'], errors='coerce') if 'gamma' in df.columns else pd.Series(np.nan, index=df.index)
        df['delta'] = pd.to_numeric(df['delta'], errors='coerce') if 'delta' in df.columns else pd.Series(np.nan, index=df.index)
        df['open_interest'] = (
            pd.to_numeric(df['open_interest'], errors='coerce').fillna(0.0)
            if 'open_interest' in df.columns else 0.0
        )
        df['volume'] = (
            pd.to_numeric(df['volume'], errors='coerce').fillna(0.0)
            if 'volume' in df.columns else 0.0
        )
        df = df.dropna(subset=['strike'])
        if df.empty:
            return []

        # Completar Gamma y Delta de Puts con paridad Call-Put
        df_c = df[df['type'] == 'C'][['strike', 'expiry', 'gamma', 'delta']].drop_duplicates(
            subset=['strike', 'expiry']
        ).rename(columns={'gamma': 'call_gamma', 'delta': 'call_delta'})
        df = df.merge(df_c, on=['strike', 'expiry'], how='left')
        df['gamma'] = df['call_gamma'].where(df['call_gamma'].notna(), df['gamma']).fillna(0.0).astype(float)
        is_put = df['type'] == 'P'
        df['delta'] = np.where(is_put & df['call_delta'].notna(), df['call_delta'] - 1.0, df['delta'])
        df['delta'] = pd.to_numeric(df['delta'], errors='coerce').fillna(0.0).astype(float)

        gex_mult = spot if mode == "Acciones" else (spot * spot)
        sign = np.where(df['type'] == 'C', 1.0, -1.0)
        df['net_gex'] = sign * df['open_interest'] * df['gamma'] * gex_mult
        df['vol_gex'] = sign * df['volume'] * df['gamma'] * gex_mult

        df['_score_all'] = df['open_interest'] + df['volume']
        df['_score_intra'] = df['volume'] * 2.0 + df['open_interest']

        df_active = df[(df['open_interest'] > 0) | (df['volume'] > 0)]
        buckets = [
            df_active.nlargest(1500, '_score_all') if not df_active.empty else df.nlargest(40, '_score_all'),
            df[df['dte'] <= 7].nlargest(40, '_score_intra'),
            df[(df['dte'] >= 8) & (df['dte'] <= 60)].nlargest(40, '_score_all'),
            df[df['dte'] > 60].nlargest(40, '_score_all'),
        ]
        non_empty = [b for b in buckets if not b.empty]
        if not non_empty:
            return []
        combined = pd.concat(non_empty, ignore_index=True)
        combined = combined.drop_duplicates(subset=['type', 'strike', 'expiry'], keep='first')
        cols = ['type', 'strike', 'expiry', 'dte', 'open_interest', 'volume', 'delta', 'net_gex', 'vol_gex']
        return combined[cols].to_dict('records')
    except Exception as e:
        print(f"Error building full chain scanner records: {e}")
        return []


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
    term_struct_info = {"ts_near_iv": 0.0, "ts_far_iv": 0.0, "ts_near_dte": 0, "ts_far_dte": 0}
    full_chain_scanner_records = []
    try:
        data_quote = fa.option_quote(ticker)
        df_raw = pd.DataFrame(data_quote)
        if not df_raw.empty:
            term_struct_info = _compute_term_structure(df_raw, spot)
            full_chain_scanner_records = _build_full_chain_scanner_records(df_raw, spot, mode=mode)
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
            
            # Extraer precio de la opción si viene en FlashAlpha (mid, mark, last, price o promedio bid/ask)
            if 'mid' in df_raw.columns:
                df_raw['opt_price'] = pd.to_numeric(df_raw['mid'], errors='coerce').fillna(0.0)
            elif 'mark' in df_raw.columns:
                df_raw['opt_price'] = pd.to_numeric(df_raw['mark'], errors='coerce').fillna(0.0)
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

            # 1) Buscar IV directa en cualquiera de los campos que pueda devolver FlashAlpha
            iv_found = False
            for iv_key in ('iv', 'implied_volatility', 'impliedVolatility', 'mid_iv', 'mark_iv', 'volatility', 'imp_vol', 'theo_iv'):
                if iv_key in df_raw.columns:
                    iv_col = pd.to_numeric(df_raw[iv_key], errors='coerce').fillna(0.0)
                    iv_norm = iv_col.apply(lambda v: v / 100.0 if v > 3.0 else v)
                    if (iv_norm > 0.01).any():
                        df_raw['implied_volatility'] = iv_norm
                        iv_found = True
                        break

            # 2) Si FlashAlpha no trajo IV directa, calcular IV real en vivo desde opt_price o gamma/delta
            if not iv_found:
                inferred_ivs = []
                for _, r in df_raw.iterrows():
                    k_val = float(r.get('strike', 0.0) or 0.0)
                    dte_val = float(r.get('dte', 0.0) or 0.0)
                    T_iv = max(dte_val, 0.25) / 365.0
                    opt_t = r.get('type', 'C')
                    p_val = float(r.get('opt_price', 0.0) or 0.0)
                    iv_calc = 0.0
                    if spot > 0 and k_val > 0 and abs(k_val - spot) / spot <= 0.25:
                        if p_val > 0:
                            iv_calc = _solve_iv_from_price(p_val, spot, k_val, T_iv, opt_t)
                        if iv_calc <= 0:
                            g_val = float(r.get('gamma', 0.0) or 0.0)
                            d_val = float(r.get('delta', 0.0) or 0.0)
                            iv_calc = _solve_iv_from_greeks(g_val, d_val, spot, T_iv, opt_t)
                    inferred_ivs.append(iv_calc)
                df_raw['implied_volatility'] = inferred_ivs

                # Completar filas sin IV válida con la mediana ATM de su vencimiento (o global)
                valid_mask = (df_raw['implied_volatility'] > 0.02) & (df_raw['implied_volatility'] < 3.5)
                if valid_mask.any():
                    df_valid = df_raw[valid_mask].copy()
                    df_valid['dist_s'] = (df_valid['strike'] - spot).abs()
                    exp_meds = {}
                    for exp_k, grp in df_valid.groupby('expiry'):
                        exp_meds[exp_k] = float(grp.nsmallest(6, 'dist_s')['implied_volatility'].median())
                    global_med = float(df_valid.nsmallest(8, 'dist_s')['implied_volatility'].median())
                    df_raw['implied_volatility'] = df_raw.apply(
                        lambda r: r['implied_volatility']
                        if 0.02 < float(r['implied_volatility']) < 3.5
                        else exp_meds.get(r['expiry'], global_med),
                        axis=1,
                    )
                else:
                    # 3) Respaldo ligero a yfinance solo para obtener IV real si no hubo precio ni gamma válida
                    yf_iv_med = 0.0
                    try:
                        t_yf = yf.Ticker(ticker.upper())
                        exps_avail = selected_expirations if selected_expirations else list(t_yf.options[:1])
                        if exps_avail:
                            ch = t_yf.option_chain(exps_avail[0])
                            df_ch = pd.concat([ch.calls, ch.puts], ignore_index=True)
                            df_ch = df_ch[(df_ch['impliedVolatility'] > 0.02) & (df_ch['impliedVolatility'] < 3.5)].copy()
                            if not df_ch.empty:
                                df_ch['dist_s'] = (df_ch['strike'] - spot).abs()
                                yf_iv_med = float(df_ch.nsmallest(6, 'dist_s')['impliedVolatility'].median())
                    except Exception:
                        pass
                    df_raw['implied_volatility'] = yf_iv_med if yf_iv_med > 0.02 else 0.20
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
                gammas = []
                deltas = []
                for idx, row in df_raw.iterrows():
                    k_strike = row['strike']
                    k_dte = row['dte']
                    k_iv = row['implied_volatility']
                    k_type = row['type']
                    
                    if k_dte < 0 or spot <= 0 or k_strike <= 0:
                        gammas.append(0.0)
                        deltas.append(0.0)
                        continue
                        
                    k_iv_eff = k_iv if (0.03 < k_iv < 4.0) else 0.35
                    T = max(float(k_dte), 0.25) / 365.0
                    r = 0.045
                    try:
                        d1 = (math.log(spot / k_strike) + (r + 0.5 * k_iv_eff ** 2) * T) / (k_iv_eff * math.sqrt(T))
                        pdf = math.exp(-0.5 * d1**2) / math.sqrt(2.0 * math.pi)
                        gamma = pdf / (spot * k_iv_eff * math.sqrt(T))
                        
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

    # Normalizar tipo ('C'/'P') y asegurar columnas numéricas válidas
    df_raw['type'] = df_raw['type'].astype(str).str.strip().str.upper().str[0]
    for _num_c in ('strike', 'dte', 'open_interest', 'volume', 'gamma', 'delta'):
        if _num_c not in df_raw.columns:
            df_raw[_num_c] = 0.0
        else:
            df_raw[_num_c] = pd.to_numeric(df_raw[_num_c], errors='coerce').fillna(0.0).astype(float)
    if 'implied_volatility' not in df_raw.columns:
        df_raw['implied_volatility'] = 0.35
    else:
        df_raw['implied_volatility'] = pd.to_numeric(df_raw['implied_volatility'], errors='coerce').fillna(0.0).astype(float)

    # Limpiar IVs inválidas (ej. 0.00001 de Yahoo) y reparar Gamma/Delta faltantes con Black-Scholes (incluyendo 0 DTE)
    valid_ivs = df_raw[(df_raw['implied_volatility'] > 0.03) & (df_raw['implied_volatility'] < 4.0)]['implied_volatility']
    fallback_iv = float(valid_ivs.median()) if not valid_ivs.empty else 0.35
    df_raw['implied_volatility'] = df_raw['implied_volatility'].apply(
        lambda v: v if (0.03 < v < 4.0) else fallback_iv
    )

    if spot > 0 and ((df_raw['gamma'].abs().sum() <= 0) or (df_raw['delta'].abs().sum() <= 0) or (df_raw['gamma'] <= 0).any()):
        bs_gammas = []
        bs_deltas = []
        r_bs_fix = 0.045
        sqrt_2pi_fix = math.sqrt(2.0 * math.pi)
        for _, rr_g in df_raw.iterrows():
            g_cur = float(rr_g['gamma'])
            d_cur = float(rr_g['delta'])
            k_cur = float(rr_g['strike'])
            if g_cur > 0.0 and d_cur != 0.0:
                bs_gammas.append(g_cur)
                bs_deltas.append(d_cur)
                continue
            if k_cur <= 0.0:
                bs_gammas.append(g_cur)
                bs_deltas.append(d_cur)
                continue
            iv_cur = float(rr_g['implied_volatility'])
            if not (0.03 < iv_cur < 4.0):
                iv_cur = fallback_iv
            T_cur = max(float(rr_g['dte']), 0.25) / 365.0
            try:
                sqrt_T_c = math.sqrt(T_cur)
                d1_c = (math.log(spot / k_cur) + (r_bs_fix + 0.5 * iv_cur * iv_cur) * T_cur) / (iv_cur * sqrt_T_c)
                pdf_c = math.exp(-0.5 * d1_c * d1_c) / sqrt_2pi_fix
                g_calc = pdf_c / (spot * iv_cur * sqrt_T_c)
                cdf_c = 0.5 * (1.0 + math.erf(d1_c / math.sqrt(2.0)))
                d_calc = cdf_c if rr_g['type'] == 'C' else (cdf_c - 1.0)
                bs_gammas.append(g_cur if g_cur > 0.0 else g_calc)
                bs_deltas.append(d_cur if d_cur != 0.0 else d_calc)
            except Exception:
                bs_gammas.append(g_cur)
                bs_deltas.append(d_cur)
        df_raw['gamma'] = bs_gammas
        df_raw['delta'] = bs_deltas

    # Si todo el OI del vencimiento vino en 0 (típico en 0DTE o fallos de OI en Yahoo), usar Volume como respaldo para no mostrar $0 plano
    use_vol_weight = (df_raw['open_interest'].sum() <= 0.0) and (df_raw['volume'].sum() > 0.0)
    eff_oi = df_raw['volume'] if use_vol_weight else df_raw['open_interest']

    # Calcular exposiciones locales según el modo
    is_c_mask = df_raw['type'] == 'C'
    is_p_mask = df_raw['type'] == 'P'
    if mode == "Acciones":
        # MODO ACCIONES: Exposición en acciones (sin el 100x y multiplicado por spot lineal)
        df_raw['call_gex'] = np.where(is_c_mask, eff_oi * df_raw['gamma'] * spot, 0.0)
        df_raw['put_gex'] = np.where(is_p_mask, -eff_oi * df_raw['gamma'] * spot, 0.0)
        df_raw['net_gex'] = df_raw['call_gex'] + df_raw['put_gex']
        df_raw['vol_gex'] = np.where(is_c_mask, df_raw['volume'] * df_raw['gamma'] * spot, -df_raw['volume'] * df_raw['gamma'] * spot)
        
        df_raw['call_dex'] = np.where(is_c_mask, eff_oi * df_raw['delta'] * spot, 0.0)
        df_raw['put_dex'] = np.where(is_p_mask, eff_oi * df_raw['delta'] * spot, 0.0)
        df_raw['net_dex'] = df_raw['call_dex'] + df_raw['put_dex']
    else:
        # MODO USD: Fórmula oficial en USD (con spot*spot para GEX y delta*spot*100 para DEX)
        df_raw['call_gex'] = np.where(is_c_mask, eff_oi * df_raw['gamma'] * spot * spot, 0.0)
        df_raw['put_gex'] = np.where(is_p_mask, -eff_oi * df_raw['gamma'] * spot * spot, 0.0)
        df_raw['net_gex'] = df_raw['call_gex'] + df_raw['put_gex']
        df_raw['vol_gex'] = np.where(is_c_mask, df_raw['volume'] * df_raw['gamma'] * spot * spot, -df_raw['volume'] * df_raw['gamma'] * spot * spot)
        
        df_raw['call_dex'] = np.where(is_c_mask, eff_oi * df_raw['delta'] * spot * 100.0, 0.0)
        df_raw['put_dex'] = np.where(is_p_mask, eff_oi * df_raw['delta'] * spot * 100.0, 0.0)
        df_raw['net_dex'] = df_raw['call_dex'] + df_raw['put_dex']

    # Campos de soporte comunes
    df_raw['call_oi'] = df_raw.apply(lambda r: r['open_interest'] if r['type'] == 'C' else 0.0, axis=1)
    df_raw['put_oi'] = df_raw.apply(lambda r: r['open_interest'] if r['type'] == 'P' else 0.0, axis=1)
    df_raw['call_volume'] = df_raw.apply(lambda r: r['volume'] if r['type'] == 'C' else 0.0, axis=1)
    df_raw['put_volume'] = df_raw.apply(lambda r: r['volume'] if r['type'] == 'P' else 0.0, axis=1)
    
    # Calcular prima teórica (Black-Scholes) cuando opt_price no venga en la respuesta
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
        oi_val = float(r.get('volume', 0.0) or 0.0) if use_vol_weight else float(r.get('open_interest', 0.0) or 0.0)
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

    # Adjuntar metadatos de Term Structure al DataFrame crudo para el Panel Táctico
    df_raw['ts_near_iv'] = term_struct_info["ts_near_iv"]
    df_raw['ts_far_iv'] = term_struct_info["ts_far_iv"]
    df_raw['ts_near_dte'] = term_struct_info["ts_near_dte"]
    df_raw['ts_far_dte'] = term_struct_info["ts_far_dte"]
    
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

    # Adjuntar resumen liviano de toda la cadena para el Scanner de 4 horizontes
    df_raw.attrs['full_chain_scanner'] = full_chain_scanner_records

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

    try:
        result["levels"] = _flashalpha_get(f"/v1/flow/levels/{ticker}", api_key)
    except Exception:
        pass

    try:
        result["pin_risk"] = _flashalpha_get(f"/v1/flow/pin-risk/{ticker}", api_key)
    except Exception:
        result["pin_risk"] = {}

    try:
        result["vex_api"] = _flashalpha_get(f"/v1/exposure/vex/{ticker}", api_key)
    except Exception:
        result["vex_api"] = {}

    try:
        result["chex_api"] = _flashalpha_get(f"/v1/exposure/chex/{ticker}", api_key)
    except Exception:
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
    }

    if df_gex.empty or spot <= 0:
        return tactical

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

    # 2b. IV Skew OTM (Calls OTM vs Puts OTM) y Liquidez cerca del Spot (±10%)
    call_otm_iv_pct = atm_iv * 100.0
    put_otm_iv_pct = atm_iv * 100.0
    if not df_raw.empty and "implied_volatility" in df_raw.columns and "type" in df_raw.columns:
        df_sk = df_raw[(df_raw["implied_volatility"] > 0.02) & (df_raw["implied_volatility"] < 3.5)].copy()
        if not df_sk.empty:
            c_otm = df_sk[(df_sk["type"] == "C") & (df_sk["strike"] > spot * 1.005) & (df_sk["strike"] <= spot * 1.12)].copy()
            if c_otm.empty:
                c_otm = df_sk[(df_sk["type"] == "C") & (df_sk["strike"] > spot)].copy()
            if not c_otm.empty:
                c_otm["dist_t"] = (c_otm["strike"] - spot * 1.035).abs()
                call_otm_iv_pct = float(c_otm.nsmallest(4, "dist_t")["implied_volatility"].median() * 100.0)

            p_otm = df_sk[(df_sk["type"] == "P") & (df_sk["strike"] < spot * 0.995) & (df_sk["strike"] >= spot * 0.88)].copy()
            if p_otm.empty:
                p_otm = df_sk[(df_sk["type"] == "P") & (df_sk["strike"] < spot)].copy()
            if not p_otm.empty:
                p_otm["dist_t"] = (p_otm["strike"] - spot * 0.965).abs()
                put_otm_iv_pct = float(p_otm.nsmallest(4, "dist_t")["implied_volatility"].median() * 100.0)

    df_liq = df_gex[(df_gex["strike"] >= spot * 0.90) & (df_gex["strike"] <= spot * 1.10)]
    near_oi_10 = int(df_liq["open_interest"].sum()) if ("open_interest" in df_liq.columns and not df_liq.empty) else 0
    near_vol_10 = int(df_liq["volume"].sum()) if ("volume" in df_liq.columns and not df_liq.empty) else 0

    tactical["call_otm_iv_pct"] = call_otm_iv_pct
    tactical["put_otm_iv_pct"] = put_otm_iv_pct
    tactical["iv_skew_pct"] = call_otm_iv_pct - put_otm_iv_pct
    tactical["near_oi_10"] = near_oi_10
    tactical["near_vol_10"] = near_vol_10
    tactical["max_pain"] = float(calc_max_pain(df_gex))
    tactical["ts_near_iv"] = float(df_raw["ts_near_iv"].iloc[0]) if (not df_raw.empty and "ts_near_iv" in df_raw.columns) else 0.0
    tactical["ts_far_iv"] = float(df_raw["ts_far_iv"].iloc[0]) if (not df_raw.empty and "ts_far_iv" in df_raw.columns) else 0.0
    tactical["ts_near_dte"] = int(df_raw["ts_near_dte"].iloc[0]) if (not df_raw.empty and "ts_near_dte" in df_raw.columns) else 0
    tactical["ts_far_dte"] = int(df_raw["ts_far_dte"].iloc[0]) if (not df_raw.empty and "ts_far_dte" in df_raw.columns) else 0

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
        # Solo usar pin_risk de FlashAlpha como respaldo si la tabla local está vacía
        pr_api = live_flow.get("pin_risk") or {}
        if isinstance(pr_api, dict):
            api_st = pr_api.get("pin_strike") or pr_api.get("magnet_strike") or pr_api.get("strike")
            api_ps = pr_api.get("pin_score") or pr_api.get("score")
            if api_st is not None:
                try:
                    pin_strike = float(api_st)
                except Exception:
                    pass
            if api_ps is not None:
                try:
                    pin_score = float(api_ps)
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

        def classify_signal(r):
            vol = float(r.get("volume", 0) or 0)
            ratio = float(r.get("vol_oi_ratio", 0) or 0)
            if vol <= 0:
                return "⏸️ Sin Volumen"
            if ratio >= 2.0:
                return "🔥 Apertura Masiva"
            if ratio >= 0.75:
                return "⚡ Flujo Activo"
            if ratio >= 0.25:
                return "📈 Rotación Mod."
            return "🏛️ Posición OI"

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


def _parse_money_suffix(raw_txt):
    """Convierte textos como '1.56M', '508.08K', '$31.8M' o '254.1K' a float."""
    import re
    if raw_txt is None:
        return 0.0
    s = str(raw_txt).strip().upper().replace("$", "").replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([KMBT])?", s)
    if not m:
        return 0.0
    val = float(m.group(1))
    suf = m.group(2) or ""
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(suf, 1.0)
    return val * mult


def _discord_fetch_messages(channel_id, bot_token, max_messages=100, cutoff_epoch=None, timeout=10):
    """Descarga mensajes recientes de un canal de Discord (con paginación segura) usando Bot Token."""
    import json
    import urllib.error
    import urllib.request

    cid = str(channel_id or "").strip()
    tok = str(bot_token or "").strip()
    if not cid or not tok:
        return [], None

    collected = []
    before_id = None
    remaining = max(int(max_messages), 1)

    while remaining > 0:
        batch_size = min(remaining, 100)
        url = f"https://discord.com/api/v10/channels/{cid}/messages?limit={batch_size}"
        if before_id:
            url += f"&before={before_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bot {tok}",
                "Accept": "application/json",
                "User-Agent": "DiscordBot (https://github.com/carlosoco66-hash/gex-scanner, 1.0)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if collected:
                break
            try:
                body = e.read().decode("utf-8", errors="ignore")[:180]
            except Exception:
                body = ""
            return [], f"HTTP {e.code}: {body or e.reason}"
        except Exception as e:
            if collected:
                break
            return [], str(e)

        if not isinstance(batch, list) or not batch:
            break

        collected.extend(batch)
        remaining -= len(batch)
        before_id = batch[-1].get("id")

        if len(batch) < batch_size or not before_id:
            break

        if cutoff_epoch is not None:
            try:
                oldest_ts = datetime.datetime.fromisoformat(
                    str(batch[-1].get("timestamp", "")).replace("Z", "+00:00")
                ).timestamp()
                if oldest_ts < cutoff_epoch:
                    break
            except Exception:
                pass

    return collected, None


def fetch_discord_whales(bot_token, channel_ids, days=1, limit=None):
    """
    Lee alertas de Tradytics desde tus canales privados de Discord:
      - golden (#golden-sweeps)
      - sweeps (#options-sweeps)
      - dark_pool (#dark-pool)
    Convierte todas las horas a Pacífico (PT) y agrupa golpes acumulados en el rango elegido (1, 3, 7 o 14 días).
    """
    import re
    try:
        from zoneinfo import ZoneInfo
        tz_pt = ZoneInfo("America/Los_Angeles")
        tz_et = ZoneInfo("America/New_York")
    except Exception:
        tz_pt = datetime.timezone(datetime.timedelta(hours=-8))
        tz_et = datetime.timezone(datetime.timedelta(hours=-5))

    now_pt = datetime.datetime.now(tz_pt)
    today_pt = now_pt.date()
    today_et = datetime.datetime.now(tz_et).date()

    days_int = max(int(days or 1), 1)
    cutoff_date_pt = today_pt - datetime.timedelta(days=days_int - 1)
    cutoff_dt_pt = datetime.datetime(
        cutoff_date_pt.year, cutoff_date_pt.month, cutoff_date_pt.day, 0, 0, 0, tzinfo=tz_pt
    )
    # Margen de 12h para no cortar mensajes por diferencia ET/PT al paginar
    cutoff_epoch = cutoff_dt_pt.timestamp() - 43200.0

    if limit is not None:
        max_msgs = max(int(limit), 50)
    else:
        max_msgs = {1: 100, 3: 250, 7: 400, 14: 500}.get(days_int, min(max(days_int * 60, 100), 500))

    out = {"golden": [], "sweeps": [], "dark_pool": [], "errors": {}, "days": days_int}
    if not bot_token or not isinstance(channel_ids, dict):
        return out

    def _msg_dt_pt_et(ts_iso):
        try:
            dt_utc = datetime.datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
            return dt_utc.astimezone(tz_pt), dt_utc.astimezone(tz_et), dt_utc.timestamp()
        except Exception:
            n_pt = datetime.datetime.now(tz_pt)
            n_et = datetime.datetime.now(tz_et)
            return n_pt, n_et, n_pt.timestamp()

    def _parse_sweep_channel(messages, default_golden=False):
        raw_hits = []
        for msg in messages:
            embeds = msg.get("embeds") or []
            dt_pt, dt_et, ts_epoch = _msg_dt_pt_et(msg.get("timestamp", ""))
            for emb in embeds:
                author_name = str((emb.get("author") or {}).get("name") or "")
                title_txt = str(emb.get("title") or "")
                desc_txt = str(emb.get("description") or "")
                header_all = f"{author_name} {title_txt} {desc_txt}".lower()

                fields = {}
                for fld in (emb.get("fields") or []):
                    fname = str(fld.get("name") or "").strip().lower()
                    fval = str(fld.get("value") or "").strip()
                    fields[fname] = fval

                sym = str(fields.get("symbol") or fields.get("ticker") or "").strip().upper()
                sym = re.sub(r"[^A-Z0-9.\-]", "", sym)
                if not sym:
                    continue

                strike_raw = fields.get("strike", "")
                try:
                    strike_val = float(re.sub(r"[^0-9.]", "", strike_raw))
                except Exception:
                    strike_val = 0.0

                exp_raw = str(fields.get("expiration") or fields.get("expiry") or "").strip()
                exp_disp = exp_raw
                dte_val = -1
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
                    try:
                        exp_dt = datetime.datetime.strptime(exp_raw, fmt).date()
                        dte_val = max((exp_dt - today_et).days, 0)
                        exp_disp = f"{exp_dt.month}/{exp_dt.day}/{str(exp_dt.year)[-2:]}"
                        break
                    except Exception:
                        continue

                prem_raw = fields.get("premiums") or fields.get("premium") or "0"
                prem_usd = _parse_money_suffix(prem_raw)

                opt_type = "PUT" if "put" in header_all else "CALL"
                is_golden = ("golden" in header_all) or (default_golden and prem_usd >= 1_000_000)

                # Convertir hora ET de Tradytics a Pacífico (PT) real
                time_raw = str(fields.get("time") or "").strip()
                trade_dt_pt = dt_pt
                ts_trade = ts_epoch
                m_dt = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::(\d{2}))?", time_raw)
                if m_dt:
                    try:
                        sec_v = int(m_dt.group(3) or 0)
                        dt_naive = datetime.datetime.strptime(
                            f"{m_dt.group(1)} {m_dt.group(2)}:{sec_v:02d}", "%Y-%m-%d %H:%M:%S"
                        )
                        trade_dt_pt = dt_naive.replace(tzinfo=tz_et).astimezone(tz_pt)
                        ts_trade = trade_dt_pt.timestamp()
                    except Exception:
                        pass

                trade_date_pt = trade_dt_pt.date()
                in_window = trade_date_pt >= cutoff_date_pt
                is_today = trade_date_pt == today_pt

                raw_hits.append({
                    "ticker": sym,
                    "type": opt_type,
                    "strike": strike_val,
                    "exp": exp_disp,
                    "dte": dte_val,
                    "prem_usd": prem_usd,
                    "is_golden": is_golden,
                    "time_str": trade_dt_pt.strftime("%H:%M PT"),
                    "date_str": str(trade_date_pt),
                    "is_today": is_today,
                    "in_window": in_window,
                    "ts_epoch": ts_trade,
                })

        # Usar los que caen en la ventana elegida; si no hubiera ninguno hoy (ej. fin de semana), usar los recientes
        in_win_hits = [h for h in raw_hits if h["in_window"]]
        use_hits = in_win_hits if in_win_hits else raw_hits

        grouped = {}
        for h in use_hits:
            g_key = (h["ticker"], h["type"], round(h["strike"], 3), h["exp"])
            if g_key not in grouped:
                grouped[g_key] = {
                    "ticker": h["ticker"],
                    "type": h["type"],
                    "strike": h["strike"],
                    "exp": h["exp"],
                    "dte": h["dte"],
                    "prem_usd": h["prem_usd"],
                    "hits": 1,
                    "days_set": {h["date_str"]},
                    "is_golden": h["is_golden"],
                    "time_str": h["time_str"],
                    "date_str": h["date_str"],
                    "is_today": h["is_today"],
                    "in_window": h["in_window"],
                    "ts_epoch": h["ts_epoch"],
                }
            else:
                rec = grouped[g_key]
                rec["prem_usd"] += h["prem_usd"]
                rec["hits"] += 1
                rec["days_set"].add(h["date_str"])
                rec["is_golden"] = rec["is_golden"] or h["is_golden"]
                rec["is_today"] = rec["is_today"] or h["is_today"]
                rec["in_window"] = rec["in_window"] or h["in_window"]
                if h["ts_epoch"] > rec["ts_epoch"]:
                    rec["ts_epoch"] = h["ts_epoch"]
                    rec["time_str"] = h["time_str"]
                    rec["date_str"] = h["date_str"]

        items = []
        for rec in grouped.values():
            rec["days_active"] = len(rec.pop("days_set"))
            items.append(rec)

        items.sort(key=lambda x: (x["in_window"], x["prem_usd"], x["ts_epoch"]), reverse=True)
        return items

    def _parse_darkpool_channel(messages):
        items = []
        for msg in messages:
            embeds = msg.get("embeds") or []
            dt_pt, dt_et, ts_epoch = _msg_dt_pt_et(msg.get("timestamp", ""))
            for emb in embeds:
                author_name = str((emb.get("author") or {}).get("name") or "")
                title_txt = str(emb.get("title") or "")
                head = (author_name or title_txt).strip()
                m_sym = re.match(r"^([A-Z]{1,6}(?:\.[A-Z])?)\b", head.upper())
                sym = m_sym.group(1) if m_sym else ""

                fields = {}
                for fld in (emb.get("fields") or []):
                    fname = str(fld.get("name") or "").strip().lower()
                    fval = str(fld.get("value") or "").strip()
                    fields[fname] = fval

                if not sym:
                    sym = str(fields.get("symbol") or fields.get("ticker") or "").strip().upper()
                if not sym:
                    continue

                price_raw = fields.get("price", "0")
                try:
                    price_val = float(re.sub(r"[^0-9.]", "", price_raw))
                except Exception:
                    price_val = 0.0

                shares_str = str(fields.get("shares") or "0").strip()
                amount_raw = str(fields.get("amount") or "0").strip()
                amount_usd = _parse_money_suffix(amount_raw)
                filled_str = str(fields.get("filled") or "").strip()

                details_txt = str(fields.get("ticker & trade details") or "")
                m_fp = re.search(r"Float\s*Proportion:\s*([0-9.]+%)", details_txt, flags=re.IGNORECASE)
                float_pct = m_fp.group(1) if m_fp else ""

                msg_date_pt = dt_pt.date()
                in_window = msg_date_pt >= cutoff_date_pt
                is_today = msg_date_pt == today_pt
                items.append({
                    "ticker": sym,
                    "price": price_val,
                    "shares_str": shares_str,
                    "amount_usd": amount_usd,
                    "amount_str": amount_raw,
                    "filled": filled_str,
                    "float_pct": float_pct,
                    "time_str": dt_pt.strftime("%H:%M PT"),
                    "date_str": str(msg_date_pt),
                    "is_today": is_today,
                    "in_window": in_window,
                    "ts_epoch": ts_epoch,
                })

        in_win = [x for x in items if x["in_window"]]
        use_items = in_win if in_win else items
        if days_int > 1:
            use_items.sort(key=lambda x: (x["in_window"], x["amount_usd"], x["ts_epoch"]), reverse=True)
        else:
            use_items.sort(key=lambda x: (x["in_window"], x["ts_epoch"], x["amount_usd"]), reverse=True)
        return use_items

    for key, is_dp in (("golden", False), ("sweeps", False), ("dark_pool", True)):
        cid = str(channel_ids.get(key) or "").strip()
        if not cid:
            continue
        msgs, err = _discord_fetch_messages(cid, bot_token, max_messages=max_msgs, cutoff_epoch=cutoff_epoch)
        if err:
            out["errors"][key] = err
            continue
        if is_dp:
            out[key] = _parse_darkpool_channel(msgs)
        else:
            out[key] = _parse_sweep_channel(msgs, default_golden=(key == "golden"))

    return out
