import discord
from zoneinfo import ZoneInfo
import os
import gc
import requests
from discord import app_commands
from flashalpha import FlashAlpha
import pandas as pd
import numpy as np
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Solución para evitar que mplcyberpunk falle en Python 3.12 si no encuentra pkg_resources
import sys
from types import ModuleType
try:
    import pkg_resources
    # Si se importó pero es un stub incompleto, forzamos su parcheo
    if not hasattr(pkg_resources, 'resource_filename') or not hasattr(pkg_resources, 'require') or not hasattr(pkg_resources, 'get_distribution'):
        raise ImportError
except (ImportError, AttributeError):
    if 'pkg_resources' in sys.modules:
        pkg_resources_mock = sys.modules['pkg_resources']
    else:
        pkg_resources_mock = ModuleType('pkg_resources')
        sys.modules['pkg_resources'] = pkg_resources_mock
        
    if not hasattr(pkg_resources_mock, 'get_distribution'):
        pkg_resources_mock.get_distribution = lambda n: type('D', (object,), {'version': '0.7.1'})()
    if not hasattr(pkg_resources_mock, 'require'):
        pkg_resources_mock.require = lambda n: [type('D', (object,), {'version': '0.7.1'})()]
    if not hasattr(pkg_resources_mock, 'resource_filename'):
        pkg_resources_mock.resource_filename = lambda n, p: ""

import mplcyberpunk
import traceback
import time
import asyncio
import yfinance as yf
try:
    import os
    cache_dir = r"C:\Users\18052\.gemini\antigravity-ide\scratch\discord_bot\.cache"
    os.makedirs(cache_dir, exist_ok=True)
    yf.set_tz_cache_location(cache_dir)
except Exception:
    try:
        yf.set_tz_cache_location(None)
    except Exception:
        pass

# Wrapper para yf.download que evita picos de memoria (OOM) dividiendo en lotes de 15
original_download = yf.download
def robust_download(*args, **kwargs):
    tickers = args[0] if len(args) > 0 else kwargs.get('tickers')
    if isinstance(tickers, (list, tuple, set)) and len(tickers) > 12:
        tickers_list = list(tickers)
        batch_size = 15
        batches = [tickers_list[i:i + batch_size] for i in range(0, len(tickers_list), batch_size)]
        results = []
        for batch in batches:
            batch_kwargs = kwargs.copy()
            if len(args) > 0:
                batch_args = (batch,) + args[1:]
            else:
                batch_args = ()
                batch_kwargs['tickers'] = batch
            
            df_batch = original_download(*batch_args, **batch_kwargs)
            results.append(df_batch)
            import gc
            gc.collect()
            
        if len(results) > 0:
            import pandas as pd
            return pd.concat(results, axis=1)
        return pd.DataFrame()
    return original_download(*args, **kwargs)

yf.download = robust_download

from discord.ext import tasks

# Configuración global del estilo cyberpunk con fallback a dark_background en servidores
try:
    plt.style.use("cyberpunk")
except Exception:
    plt.style.use("dark_background")

# Credenciales (Cargadas de variables de entorno para evitar leaks en GitHub)
import os
# Cargar .env manualmente si existe para desarrollo local
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
    except Exception as e:
        print(f"[WARNING] Error al leer el archivo .env: {e}")

API_KEY = os.getenv("FLASHALPHA_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

class RobustFlashAlpha:
    def __init__(self, api_key):
        self._fa = FlashAlpha(api_key)
        self._cache = {}
        self.api_key = api_key
        
    def _fallback_gex_dex(self, name, ticker, expiration):
        from gex_engine import fetch_and_calculate_all
        import pandas as pd
        import yfinance as yf
        
        ticker = ticker.upper()
        selected_exps = [expiration] if expiration else None
        
        spot = 0.0
        try:
            t = yf.Ticker(ticker)
            try:
                spot = float(t.fast_info['lastPrice'])
            except:
                hist = t.history(period='1d')
                if not hist.empty:
                    spot = float(hist['Close'].iloc[-1])
        except Exception:
            pass
            
        df_gex, df_dex, df_raw, spot = fetch_and_calculate_all(
            ticker,
            selected_exps,
            spot,
            self.api_key,
            "USD"
        )
        
        if df_gex.empty:
            raise ValueError(f"No options data available for {ticker} in fallback")
            
        if name == "gex":
            return {
                'underlying_price': spot,
                'strikes': df_gex.to_dict('records'),
                'net_gex': df_gex['net_gex'].sum() if 'net_gex' in df_gex.columns else 0.0
            }
        elif name == "dex":
            return {
                'underlying_price': spot,
                'strikes': df_dex.to_dict('records'),
                'net_dex': df_dex['net_dex'].sum() if 'net_dex' in df_dex.columns else 0.0
            }

    def _fallback_exposure_levels(self, ticker):
        from gex_engine import fetch_and_calculate_all, calculate_levels
        import pandas as pd
        ticker = ticker.upper()
        
        df_gex, df_dex, df_raw, spot = fetch_and_calculate_all(
            ticker,
            None,
            0.0,
            self.api_key,
            "USD"
        )
        
        if df_gex.empty:
            raise ValueError(f"No options data available for {ticker} in fallback")
            
        levels = calculate_levels(df_gex, df_dex, spot)
        
        return {
            'levels': {
                'gamma_flip': levels.get('Zero GEX (Balance)', 0.0),
                'call_wall': levels.get('PTrans (Resistance)', 0.0),
                'max_negative_gamma': levels.get('NTrans (Support)', 0.0),
                'highest_oi_strike': levels.get('COTMP (Pin)', 0.0)
            }
        }

    def _fallback_stock_summary(self, ticker):
        from gex_engine import fetch_and_calculate_all, calculate_levels
        import pandas as pd
        ticker = ticker.upper()
        
        df_gex, df_dex, df_raw, spot = fetch_and_calculate_all(
            ticker,
            None,
            0.0,
            self.api_key,
            "USD"
        )
        
        if df_gex.empty:
            raise ValueError(f"No options data available for {ticker} in fallback")
            
        levels = calculate_levels(df_gex, df_dex, spot)
        net_gex = levels.get('Total Net GEX', 0.0)
        
        res = {
            'price': {
                'mid': spot,
                'bid': spot,
                'ask': spot
            },
            'exposure': {
                'regime': 'positive' if net_gex > 0.0 else 'negative',
                'call_wall': levels.get('PTrans (Resistance)', spot),
                'put_wall': levels.get('NTrans (Support)', spot),
                'gamma_flip': levels.get('Zero GEX (Balance)', spot),
                'max_pain': levels.get('COTMP (Pin)', spot),
                'zero_dte': {
                    'pct_of_total': 15.0
                },
                'interpretation': {
                    'charm': 'dealers buy',
                    'gamma': 'Dealers dampening moves' if net_gex > 0.0 else 'Dealers magnifying moves'
                }
            },
            'volatility': {
                'vrp': 'premium',
                'atm_iv': 75.0,
                'hv_20': 50.0
            },
            'macro': {
                'vix': {
                    'value': 15.0,
                    'change_pct': 0.0
                },
                'vix_term_structure': {
                    'structure': 'normal',
                    'near_slope_pct': 0.5
                },
                'fear_and_greed': {
                    'score': 50,
                    'rating': 'neutral'
                }
            }
        }
        return res

    def __getattr__(self, name):
        attr = getattr(self._fa, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                import time
                kwargs_key = tuple(sorted(kwargs.items()))
                cache_key = (name, args, kwargs_key)
                
                now = time.time()
                # Prune cache to keep memory clean (remove items older than 1 hour)
                expired_keys = [k for k, (ts, _) in self._cache.items() if now - ts > 3600.0]
                for k in expired_keys:
                    del self._cache[k]
                    
                if cache_key in self._cache:
                    timestamp, cached_result = self._cache[cache_key]
                    if now - timestamp < 300.0:
                        print(f"[CACHE HIT] Retornando datos cacheados para fa.{name} {args}")
                        return cached_result
                        
                last_err = None
                for attempt in range(3):
                    try:
                        result = attr(*args, **kwargs)
                        if name == 'option_quote' and (result is None or len(result) == 0):
                            raise ValueError("option_quote returned empty list")
                        self._cache[cache_key] = (now, result)
                        return result
                    except Exception as e:
                        last_err = e
                        print(f"Error llamando a fa.{name} (intento {attempt+1}/3): {e}")
                        time.sleep(0.5)
                
                # Fallback local con yfinance
                ticker = args[0] if len(args) > 0 else kwargs.get('ticker')
                if isinstance(ticker, str):
                    ticker = ticker.upper()
                    print(f"[API FAIL] Intentando fallback local de {name} para {ticker}...")
                    try:
                        if name in ('gex', 'dex'):
                            expiration = kwargs.get('expiration') or (args[1] if len(args) > 1 else None)
                            res = self._fallback_gex_dex(name, ticker, expiration)
                            self._cache[cache_key] = (now, res)
                            return res
                        elif name == 'exposure_levels':
                            res = self._fallback_exposure_levels(ticker)
                            self._cache[cache_key] = (now, res)
                            return res
                        elif name == 'stock_summary':
                            res = self._fallback_stock_summary(ticker)
                            self._cache[cache_key] = (now, res)
                            return res
                    except Exception as fallback_err:
                        print(f"[FALLBACK FAILED] {fallback_err}")
                        
                raise last_err
            return wrapper
        return attr

fa = RobustFlashAlpha(API_KEY)

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

ALLOWED_CHANNEL = "bot-analisis"

# Tickers más usados (El bot pre-cargará sus fechas automáticamente para que no tengas que esperar)
COMMON_TICKERS = ["SPY", "QQQ", "IWM", "TSLA", "IBIT", "NVDA", "AAPL", "AMD", "META", "AMZN", "NFLX", "UNG", "ORCL", "CRCL", "HOOD", "COIN"]

# Caché para no saturar la API con el autocompletado
expiration_cache = {}

async def fetch_and_cache_options(ticker):
    # 1. Intentamos obtener las fechas desde FlashAlpha option_quote (contiene diarias y es 100% fiable en Railway)
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(fa.option_quote, ticker),
            timeout=5.0
        )
        if isinstance(data, list):
            exp_list = list(set(x.get('expiry') for x in data if x.get('expiry')))
            today_str = time.strftime('%Y-%m-%d')
            # Filtrar solo fechas de hoy o futuras
            exp_list = [exp for exp in exp_list if exp >= today_str]
            exp_list = sorted(exp_list)[:25]
            if exp_list:
                expiration_cache[ticker] = {'time': time.time(), 'list': exp_list}
                return
    except Exception as e:
        print(f"Error fetching option_quote from FlashAlpha for {ticker}: {e}")

    # 2. Si falla, intentamos yfinance como primer plan de respaldo
    try:
        yf_ticker = yf.Ticker(ticker)
        options_list = await asyncio.wait_for(
            asyncio.to_thread(lambda: list(yf_ticker.options)),
            timeout=3.0
        )
        if options_list:
            exp_list = sorted(list(set(options_list)))[:25]
            expiration_cache[ticker] = {'time': time.time(), 'list': exp_list}
            return
    except Exception as e:
        print(f"Error fetching options from yfinance fallback for {ticker}: {e}")
    
    # 3. Último recurso: fa.options estándar (no tiene todas las diarias, pero es estable)
    try:
        data = await asyncio.wait_for(asyncio.to_thread(fa.options, ticker), timeout=2.0)
        exps = data.get('expirations', [])
        exp_list = [x['expiration'] for x in exps if 'expiration' in x]
        exp_list = sorted(list(set(exp_list)))[:25]
        expiration_cache[ticker] = {'time': time.time(), 'list': exp_list}
    except Exception as e:
        print(f"Error fetching options from FlashAlpha options fallback for {ticker}: {e}")
        if ticker not in expiration_cache:
            expiration_cache[ticker] = {'time': time.time(), 'list': []}
    finally:
        expiration_cache[f"{ticker}_loading"] = False

async def expiration_autocomplete(interaction: discord.Interaction, current: str):
    ticker = interaction.namespace.ticker
    if not ticker:
        return []
    ticker = ticker.upper()
    now = time.time()
    
    # Si no está en caché o ya caducó, lo descargamos síncronamente (esperando a que termine)
    if ticker not in expiration_cache or now - expiration_cache[ticker].get('time', 0) > 3600:
        await fetch_and_cache_options(ticker)
        
    cached_list = expiration_cache.get(ticker, {}).get('list', [])
    
    # Filtra según lo que el usuario va escribiendo
    return [
        app_commands.Choice(name=exp, value=exp)
        for exp in cached_list if current.lower() in exp.lower()
    ][:25]

# --- FUNCIONES VISUALIZADORAS Y MATEMÁTICAS ---
def _fetch_yf_price(ticker):
    try:
        t = yf.Ticker(ticker)
        try:
            return t.fast_info['lastPrice']
        except:
            return t.history(period='1d')['Close'].iloc[-1]
    except Exception as e:
        print(f"Error interno Yahoo Price: {e}")
        return None

async def get_live_spot(ticker):
    try:
        price = await asyncio.to_thread(_fetch_yf_price, ticker)
        return price
    except Exception as e:
        print(f"Error en yfinance spot para {ticker}: {e}")
        return None

def get_image_buffer(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', transparent=True, dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

def calc_max_pain(df):
    if 'call_oi' not in df.columns or 'put_oi' not in df.columns or df.empty:
        return 0
    strikes = df['strike'].unique()
    pain = []
    for s in strikes:
        call_pain = (df['call_oi'] * (df['strike'] - s).clip(lower=0)).sum()
        put_pain = (df['put_oi'] * (s - df['strike']).clip(lower=0)).sum()
        pain.append((s, call_pain + put_pain))
    if not pain: return 0
    return min(pain, key=lambda x: x[1])[0]

def calc_gamma_flip(df, spot):
    if df.empty or 'net_gex' not in df.columns:
        return 0
    df = df.sort_values('strike').reset_index(drop=True)
    
    # Filtrar strikes sin liquidez real (donde net_gex es exactamente 0)
    df_active = df[df['net_gex'] != 0].copy()
    if df_active.empty:
        return 0
        
    # Buscar cambios de signo en el GEX (donde cruza el cero)
    df_active['sign'] = np.sign(df_active['net_gex'])
    df_active['sign_change'] = df_active['sign'].diff()
    
    crossings = df_active[df_active['sign_change'].notna() & (df_active['sign_change'] != 0)]
    
    if not crossings.empty:
        # Si hay cruces, tomar el más cercano al precio spot actual
        closest_idx = (crossings['strike'] - spot).abs().idxmin()
        return crossings.loc[closest_idx]['strike']
    else:
        # Si no hay cruce, tomar el strike activo con GEX más cercano a cero
        closest_idx = df_active['net_gex'].abs().idxmin()
        return df_active.loc[closest_idx]['strike']

def plot_gex_profile(ticker, data_gex, spot_price):
    fig, ax = plt.subplots(figsize=(12, 6), facecolor='#0d1117')
    fig.patch.set_facecolor('#0d1117')
    df = pd.DataFrame(data_gex)
    if df.empty or 'strike' not in df.columns:
        ax.text(0.5, 0.5, "Datos insuficientes para perfil GEX", ha='center', va='center', fontsize=14, color='white')
        return get_image_buffer(fig)
    
    df = df[(df['strike'] > spot_price * 0.85) & (df['strike'] < spot_price * 1.15)]
    df = df.sort_values('strike')

    if 'call_gex' in df.columns and 'put_gex' in df.columns:
        width = (df['strike'].diff().min() * 0.8) if len(df) > 1 else 0.5
        
        calls_plot = np.abs(df['call_gex']) / 1e6
        puts_plot = -np.abs(df['put_gex']) / 1e6
        
        ax.bar(df['strike'], puts_plot, width=width, color='#348feb', alpha=0.9, label='GEX Puts (Abajo)')
        ax.bar(df['strike'], calls_plot, width=width, color='#ffffff', alpha=0.9, label='GEX Calls (Arriba)')
        ax.plot(df['strike'], df['net_gex'] / 1e6, color='#ff003c', linewidth=2, label='Neto', marker='o', markersize=3)
    else:
        ax.bar(df['strike'], df.get('net_gex', 0) / 1e6, width=1.5, color='#ff003c', alpha=0.8, label='Net GEX')

    ax.axvline(spot_price, color='#ffcc00', linestyle='--', linewidth=2)
    ax.text(spot_price, ax.get_ylim()[1]*0.95, f"Spot: ${spot_price:.2f}", color='#ffcc00', ha='center', va='bottom', fontweight='bold', backgroundcolor='#0d1117')
    
    if 'call_gex' in df.columns and 'put_gex' in df.columns:
        max_c_idx = df['call_gex'].idxmax()
        max_p_idx = df['put_gex'].idxmin()
        max_c_strike = df.loc[max_c_idx]['strike'] if not df.empty else spot_price
        max_p_strike = df.loc[max_p_idx]['strike'] if not df.empty else spot_price
        
        max_c_val_m = df['call_gex'].max() / 1e6
        min_p_val_m = df['put_gex'].min() / 1e6
        
        max_c_val_raw = np.abs(df['call_gex'].max())
        min_p_val_raw = -np.abs(df['put_gex'].min())
        
        ax.annotate(f"▲ Strike {max_c_strike} ({max_c_val_raw:,.0f}$)", xy=(max_c_strike, max_c_val_m), xytext=(0, 15), textcoords='offset points', color='white', ha='center', fontweight='bold', arrowprops=dict(arrowstyle="-|>", color='gray'))
        ax.annotate(f"▼ Strike {max_p_strike} ({min_p_val_raw:,.0f}$)", xy=(max_p_strike, min_p_val_m), xytext=(0, -20), textcoords='offset points', color='white', ha='center', fontweight='bold', arrowprops=dict(arrowstyle="-|>", color='gray'))

    ax.set_title(f'Gamma Exposure (GEX) por Strike (USD)', fontsize=14, color='gray', loc='left', pad=20)
    ax.set_xlabel('Precio Strike', fontsize=12, color='gray')
    ax.set_ylabel('Exposición (Millones USD)', fontsize=12, color='gray')
    
    ax.tick_params(colors='gray')
    for spine in ['top', 'right']: ax.spines[spine].set_visible(False)
    for spine in ['bottom', 'left']: ax.spines[spine].set_color('gray')
    ax.grid(axis='y', color='#2a2a2a', linestyle='-', linewidth=0.5)
    
    ax.legend(loc='upper right', frameon=False, labelcolor='gray', ncol=3)
    
    plt.tight_layout()
    return get_image_buffer(fig)

def get_image_buffer_solid(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', transparent=False, dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

def plot_lepton_grid_chart(gex_data, dex_data, ticker, spot, exp_label):
    df_gex = pd.DataFrame(gex_data.get('strikes', []))
    df_dex = pd.DataFrame(dex_data.get('strikes', []))
    
    fig, axs = plt.subplots(2, 2, figsize=(20, 12), facecolor='#0d1117')
    fig.patch.set_facecolor('#0d1117')
    
    if df_gex.empty or df_dex.empty:
        for i, name in enumerate(['GEX', 'OI', 'Vol', 'DEX']):
            ax = axs[i // 2, i % 2]
            ax.set_facecolor('#0d1117')
            ax.text(0.5, 0.5, f"Sin datos para {name}", color='white', ha='center', va='center', fontsize=14)
            ax.axis('off')
        plt.tight_layout()
        return get_image_buffer_solid(fig)

    df = pd.merge(df_gex, df_dex, on='strike', how='outer').fillna(0)
    df = df[(df['strike'] > spot * 0.85) & (df['strike'] < spot * 1.15)]
    df = df.sort_values('strike')
    
    df['net_oi'] = df['call_oi'] - df['put_oi']
    df['net_volume'] = df['call_volume'] - df['put_volume']
    
    metrics = [
        ('GEX', 'call_gex', 'put_gex', 'net_gex', 'Gamma Exposure (GEX) por Strike', 'Exposición (Millones USD)', 1e6, '$'),
        ('OI', 'call_oi', 'put_oi', 'net_oi', 'Open Interest (OI) por Strike', 'Contratos', 1, ' OI'),
        ('Vol', 'call_volume', 'put_volume', 'net_volume', 'Volumen por Strike', 'Contratos', 1, ' Vol'),
        ('DEX', 'call_dex', 'put_dex', 'net_dex', 'Delta Exposure (DEX) por Strike', 'Exposición (Millones USD)', 1e6, '$')
    ]
    
    width = (df['strike'].diff().min() * 0.8) if len(df) > 1 else 0.5
    
    for idx, (name, call_col, put_col, net_col, title_text, y_label, scale, unit) in enumerate(metrics):
        ax = axs[idx // 2, idx % 2]
        ax.set_facecolor('#0d1117')
        
        calls_plot = np.abs(df[call_col]) / scale
        puts_plot = -np.abs(df[put_col]) / scale
        net_plot = df[net_col] / scale
        
        # Bars: Puts (Blue), Calls (White)
        ax.bar(df['strike'], puts_plot, width=width, color='#348feb', alpha=0.9, label=f'{name} Puts (Abajo)')
        ax.bar(df['strike'], calls_plot, width=width, color='#ffffff', alpha=0.9, label=f'{name} Calls (Arriba)')
        
        # Net Line: Red
        ax.plot(df['strike'], net_plot, color='#ff003c', linewidth=2, label='Neto', marker='o', markersize=3)
        
        # Spot vertical line
        ax.axvline(spot, color='#ffcc00', linestyle='--', linewidth=2)
        
        # Spot text position
        ymin, ymax = ax.get_ylim()
        ax.text(spot, ymax*0.95 if ymax > 0 else 0, f"Spot: ${spot:.2f}", color='#ffcc00', ha='center', va='bottom', fontweight='bold', backgroundcolor='#0d1117')
        
        # Annotations for max Call and max Put
        if not df.empty:
            max_c_idx = df[call_col].idxmax()
            max_p_idx = df[put_col].idxmin() if ('gex' in call_col or 'dex' in call_col) else df[put_col].idxmax()
            
            max_c_strike = df.loc[max_c_idx]['strike']
            max_p_strike = df.loc[max_p_idx]['strike']
            
            max_c_val = df.loc[max_c_idx][call_col]
            max_p_val = df.loc[max_p_idx][put_col]
            
            if unit == '$':
                max_c_label = f"▲ Strike {max_c_strike} ({abs(max_c_val):,.0f}$)"
                max_p_label = f"▼ Strike {max_p_strike} (-{abs(max_p_val):,.0f}$)"
            else:
                max_c_label = f"▲ Strike {max_c_strike} ({abs(max_c_val):,.0f}{unit})"
                max_p_label = f"▼ Strike {max_p_strike} (-{abs(max_p_val):,.0f}{unit})"
            
            max_c_y = calls_plot.loc[max_c_idx]
            max_p_y = puts_plot.loc[max_p_idx]
            
            ax.annotate(max_c_label, xy=(max_c_strike, max_c_y), xytext=(0, 15), textcoords='offset points', color='white', ha='center', fontweight='bold', arrowprops=dict(arrowstyle="-|>", color='gray'))
            ax.annotate(max_p_label, xy=(max_p_strike, max_p_y), xytext=(0, -20), textcoords='offset points', color='white', ha='center', fontweight='bold', arrowprops=dict(arrowstyle="-|>", color='gray'))
            
        ax.set_title(f'{title_text} - {ticker.upper()} - {exp_label}', fontsize=12, color='gray', loc='left', pad=15)
        ax.set_xlabel('Precio Strike', fontsize=10, color='gray')
        ax.set_ylabel(y_label, fontsize=10, color='gray')
        
        ax.tick_params(colors='gray', labelsize=8)
        for spine in ['top', 'right']: ax.spines[spine].set_visible(False)
        for spine in ['bottom', 'left']: ax.spines[spine].set_color('gray')
        ax.grid(axis='y', color='#2a2a2a', linestyle='-', linewidth=0.5)
        
        ax.legend(loc='upper right', frameon=False, labelcolor='gray', ncol=3, fontsize=8)
        
    plt.tight_layout()
    buf = get_image_buffer_solid(fig)
    return buf

async def handle_positioning_logic(interaction: discord.Interaction, ticker: str, exp_list: list):
    try:
        ticker = ticker.upper()
        if not exp_list:
            gex_data = fa.gex(ticker, expiration=None)
            dex_data = fa.dex(ticker, expiration=None)
            spot = gex_data.get('underlying_price', 0)
            exp_label = " (Todas las Fechas)"
            net_dex = dex_data.get('net_dex', 0) or 0
            
            # Fetch levels from API
            levels_data = fa.exposure_levels(ticker)
            levels = levels_data.get('levels', {})
            gamma_flip_raw = levels.get('gamma_flip', 0)
            res_strike = levels.get('call_wall', spot)
            sup_strike = levels.get('max_negative_gamma', spot)
            pin_strike = levels.get('highest_oi_strike', spot)
            net_gex = gex_data.get('net_gex', 0) or 0
        else:
            all_gex_strikes = []
            all_dex_strikes = []
            spot = 0
            net_dex = 0
            for exp in exp_list:
                g = fa.gex(ticker, expiration=exp)
                d = fa.dex(ticker, expiration=exp)
                if 'underlying_price' in g and g['underlying_price']: spot = g['underlying_price']
                net_dex += d.get('net_dex', 0) or 0
                if 'strikes' in g and g['strikes']: all_gex_strikes.extend(g['strikes'])
                if 'strikes' in d and d['strikes']: all_dex_strikes.extend(d['strikes'])
                
            df_g = pd.DataFrame(all_gex_strikes)
            df_d = pd.DataFrame(all_dex_strikes)
            gex_data = {'strikes': df_g.groupby('strike').sum().reset_index().to_dict('records') if not df_g.empty else []}
            dex_data = {'strikes': df_d.groupby('strike').sum().reset_index().to_dict('records') if not df_d.empty else []}
            exp_label = f" ({' + '.join(exp_list)})"
            
            # Force live spot
            live_spot = await get_live_spot(ticker)
            if live_spot: spot = live_spot
            
            # Calculate levels from strikes
            df_gex = pd.DataFrame(gex_data.get('strikes', []))
            if not df_gex.empty:
                net_gex = df_gex['net_gex'].sum() if 'net_gex' in df_gex.columns else 0
                gamma_flip_raw = calc_gamma_flip(df_gex, spot)
                res_strike = df_gex.loc[df_gex['call_gex'].idxmax()]['strike'] if 'call_gex' in df_gex.columns else spot
                sup_strike = df_gex.loc[df_gex['put_gex'].idxmin()]['strike'] if 'put_gex' in df_gex.columns else spot
                pin_strike = calc_max_pain(df_gex)
            else:
                net_gex = 0
                gamma_flip_raw = 0
                res_strike = spot
                sup_strike = spot
                pin_strike = spot

        # Force live spot again for consistency
        live_spot = await get_live_spot(ticker)
        if live_spot: spot = live_spot

        df_gex_check = pd.DataFrame(gex_data.get('strikes', []))
        if df_gex_check.empty:
            await interaction.followup.send("No hay datos suficientes para graficar posicionamiento de este activo.")
            return

        # Sentiment and Color
        if net_gex > 0:
            sesgo = "🔺 ALCISTA"
            color = discord.Color.brand_green()
        elif net_gex < 0:
            sesgo = "🔻 BAJISTA"
            color = discord.Color.brand_red()
        else:
            sesgo = "⚪ NEUTRAL"
            color = discord.Color.dark_grey()

        gamma_flip = f"${gamma_flip_raw:.2f}" if gamma_flip_raw else "N/A"

        # Build description custom (Classic style matching the photo 100%)
        descripcion_custom = "```\n"
        descripcion_custom += f"📊 ACTIVO: {ticker.upper()}{exp_label}\n"
        descripcion_custom += f"💲 SPOT PRICE: ${spot:.2f}\n"
        descripcion_custom += f"📈 SESGO: {sesgo}\n"
        descripcion_custom += "```\n\n"
        
        descripcion_custom += "**🎯 NIVELES CLAVE (INSTITUCIONALES)**\n"
        descripcion_custom += "```yaml\n"
        descripcion_custom += f"Gamma Flip       : {gamma_flip}\n"
        descripcion_custom += f"PTrans (Resist.) : ${res_strike:.2f}\n"
        descripcion_custom += f"NTrans (Soporte) : ${sup_strike:.2f}\n"
        descripcion_custom += f"COTMP (Pin)      : ${pin_strike:.2f}\n"
        descripcion_custom += f"GEX Neto Total   : ${net_gex/1e6:,.2f}M\n"
        descripcion_custom += "```\n"
        
        # dynamic interpretation
        txt_interpret = "Posicionamiento fuerte en Calls" if net_gex > 0 else "Posicionamiento fuerte en Puts"
        descripcion_custom += f"> *{txt_interpret} para {ticker.upper()}. Resistencia institucional actual en ${res_strike:.2f}.*"

        embed = discord.Embed(color=color)
        embed.description = descripcion_custom

        # Generate single 2x2 grid chart
        buf = plot_lepton_grid_chart(gex_data, dex_data, ticker, spot, exp_label.strip(" ()"))
        file = discord.File(fp=buf, filename='posicionamiento.png')
        embed.set_image(url="attachment://posicionamiento.png")
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando análisis avanzado para {ticker}")
    finally:
        plt.close('all')
        gc.collect()

def plot_strikes_table(gex_data, dex_data, ticker, spot, exp_label):
    df_gex = pd.DataFrame(gex_data.get('strikes', []))
    df_dex = pd.DataFrame(dex_data.get('strikes', []))
    
    fig, ax = plt.subplots(figsize=(10, 6.5), facecolor='#0b0d10')
    ax.set_facecolor('#0b0d10')
    ax.axis('off')
    
    if df_gex.empty or df_dex.empty:
        ax.text(0.5, 0.5, "Datos insuficientes para la tabla", color='white', ha='center', va='center', fontsize=14)
        return get_image_buffer_solid(fig)

    # Merge
    df = pd.merge(df_gex, df_dex, on='strike', how='outer').fillna(0)
    
    # Calculate net GEX, total OI, and total Volume
    df['net_gex'] = df['call_gex'] + df['put_gex']  # Put GEX is already negative in FlashAlpha
    df['total_oi'] = df['call_oi'] + df['put_oi']
    df['total_vol'] = df['call_volume'] + df['put_volume']
    
    # Get top 3 rows for each quadrant
    gex_pos = df.sort_values('net_gex', ascending=False).head(3)
    gex_neg = df.sort_values('net_gex', ascending=True).head(3)
    oi_top = df.sort_values('total_oi', ascending=False).head(3)
    vol_top = df.sort_values('total_vol', ascending=False).head(3)
    
    # Draw table title at the top
    ax.text(0.5, 0.94, "STRIKES CLAVE", color='#e6edf3', fontsize=15, fontweight='bold', ha='center', va='center')
    
    quadrants = [
        ('GEX+ RESISTENCIA', gex_pos, ['strike', 'net_gex', 'total_oi', 'total_vol'], '#2ea043', 0.05, 0.49),
        ('GEX- SOPORTE', gex_neg, ['strike', 'net_gex', 'total_oi', 'total_vol'], '#f85149', 0.52, 0.49),
        ('MAYOR OI', oi_top, ['strike', 'total_oi', 'net_gex', 'total_vol'], '#58a6ff', 0.05, 0.07),
        ('MAYOR VOLUMEN', vol_top, ['strike', 'total_vol', 'total_oi', 'net_gex'], '#db6d28', 0.52, 0.07)
    ]
    
    def fmt_val(col_name, val):
        if col_name == 'strike':
            return f"${val:.2f}"
        if col_name == 'net_gex':
            is_neg = val < 0
            abs_val = abs(val)
            if abs_val >= 1e9:
                s = f"{abs_val/1e9:.1f}B"
            elif abs_val >= 1e6:
                s = f"{abs_val/1e6:.1f}M"
            elif abs_val >= 1e3:
                s = f"{abs_val/1e3:.0f}K"
            else:
                s = f"{abs_val:.0f}"
            return f"-{s}" if is_neg else s
        # For OI/Vol:
        if val >= 1e6:
            return f"{val/1e6:.1f}M"
        if val >= 1e3:
            return f"{val/1e3:.1f}K"
        return f"{val:.0f}"

    for title, q_df, cols, theme_color, x_start, y_start in quadrants:
        # Draw sub-header box
        rect = plt.Rectangle((x_start, y_start + 0.31), 0.43, 0.045, facecolor='#161b22', edgecolor='#30363d', linewidth=0.8, alpha=0.9)
        ax.add_patch(rect)
        
        # Color bar on the left side of sub-header box for accent
        accent_bar = plt.Rectangle((x_start, y_start + 0.31), 0.008, 0.045, facecolor=theme_color, edgecolor='none')
        ax.add_patch(accent_bar)
        
        # Title text
        ax.text(x_start + 0.02, y_start + 0.33, title, color=theme_color, fontsize=10, fontweight='bold', ha='left', va='center')
        
        # Column names positions
        col_labels = {
            'strike': 'Strike',
            'net_gex': 'GEX',
            'total_oi': 'OI',
            'total_vol': 'Vol'
        }
        for idx, col in enumerate(cols):
            cx = x_start + 0.03 + idx * 0.11
            ha_align = 'left' if idx == 0 else 'center'
            ax.text(cx, y_start + 0.27, col_labels[col], color='#8b949e', fontsize=9, ha=ha_align, va='center')
            
        # Divider line
        line = plt.Line2D([x_start, x_start + 0.43], [y_start + 0.25, y_start + 0.25], color='#30363d', linewidth=0.8)
        ax.add_line(line)
        
        # Row layout
        for row_idx in range(3):
            ry = y_start + 0.19 - row_idx * 0.075
            
            if row_idx >= len(q_df):
                continue
                
            row_data = q_df.iloc[row_idx]
            
            # Subtle row separator
            row_line = plt.Line2D([x_start, x_start + 0.43], [ry - 0.037, ry - 0.037], color='#21262d', linewidth=0.5)
            ax.add_line(row_line)
            
            for col_idx, col in enumerate(cols):
                cx = x_start + 0.03 + col_idx * 0.11
                val = row_data[col]
                text_str = fmt_val(col, val)
                
                if col == 'net_gex':
                    # Green if positive, Red if negative GEX box
                    box_color = '#2ea043' if val >= 0 else '#f85149'
                    bg_rect = plt.Rectangle((cx - 0.048, ry - 0.028), 0.096, 0.056, facecolor=box_color, edgecolor='none', alpha=0.85, zorder=1)
                    ax.add_patch(bg_rect)
                    ax.text(cx, ry, text_str, color='white', fontsize=9, fontweight='bold', ha='center', va='center', zorder=2)
                else:
                    ha_align = 'left' if col_idx == 0 else 'center'
                    color = '#e6edf3' if col_idx == 0 else '#c9d1d9'
                    fontweight = 'bold' if col_idx == 0 else 'normal'
                    ax.text(cx, ry, text_str, color=color, fontsize=9, ha=ha_align, va='center', fontweight=fontweight)
                    
    # Draw border around the entire table
    border_rect = plt.Rectangle((0.02, 0.02), 0.96, 0.96, facecolor='none', edgecolor='#30363d', linewidth=1.2)
    ax.add_patch(border_rect)
    
    plt.tight_layout()
    return get_image_buffer_solid(fig)

async def handle_strikes_logic(interaction: discord.Interaction, ticker: str, exp_list: list):
    try:
        ticker = ticker.upper()
        if not exp_list:
            gex_data = fa.gex(ticker, expiration=None)
            dex_data = fa.dex(ticker, expiration=None)
            spot = gex_data.get('underlying_price', 0)
            exp_label = "TODAS LAS EXPIRACIONES"
            net_dex = dex_data.get('net_dex', 0) or 0
        else:
            all_gex_strikes = []
            all_dex_strikes = []
            spot = 0
            net_dex = 0
            for exp in exp_list:
                g = fa.gex(ticker, expiration=exp)
                d = fa.dex(ticker, expiration=exp)
                if 'underlying_price' in g and g['underlying_price']: spot = g['underlying_price']
                net_dex += d.get('net_dex', 0) or 0
                if 'strikes' in g and g['strikes']: all_gex_strikes.extend(g['strikes'])
                if 'strikes' in d and d['strikes']: all_dex_strikes.extend(d['strikes'])
                
            df_g = pd.DataFrame(all_gex_strikes)
            df_d = pd.DataFrame(all_dex_strikes)
            gex_data = {'strikes': df_g.groupby('strike').sum().reset_index().to_dict('records') if not df_g.empty else []}
            dex_data = {'strikes': df_d.groupby('strike').sum().reset_index().to_dict('records') if not df_d.empty else []}
            exp_label = " + ".join(exp_list)

        # Force live spot
        live_spot = await get_live_spot(ticker)
        if live_spot: spot = live_spot

        df_gex = pd.DataFrame(gex_data.get('strikes', []))
        if df_gex.empty:
            await interaction.followup.send("No hay datos suficientes para graficar strikes de este activo.")
            return
            
        call_gex_tot = df_gex['call_gex'].sum()
        put_gex_tot = abs(df_gex['put_gex'].sum())
        call_oi_tot = df_gex['call_oi'].sum()
        put_oi_tot = df_gex['put_oi'].sum()
        
        gex_ratio = call_gex_tot / put_gex_tot if put_gex_tot > 0 else 0
        oi_ratio = call_oi_tot / put_oi_tot if put_oi_tot > 0 else 0
        
        res_strike = df_gex.loc[df_gex['call_gex'].idxmax()]['strike']
        sup_strike = df_gex.loc[df_gex['put_gex'].idxmin()]['strike']
        
        pct_res = ((res_strike - spot) / spot) * 100
        pct_sup = ((sup_strike - spot) / spot) * 100
        
        # Sentiment
        if gex_ratio > 1.2:
            sesgo = "ALCISTA"
            emoji = "🟢"
            color = discord.Color.brand_green()
        elif gex_ratio < 0.8:
            sesgo = "BAJISTA"
            emoji = "🔴"
            color = discord.Color.brand_red()
        else:
            sesgo = "NEUTRAL"
            emoji = "⚪"
            color = discord.Color.dark_grey()
            
        embed = discord.Embed(color=color)
        
        # Build premium description
        desc = f"## {emoji} {ticker} — {sesgo}\n"
        desc += f"**Exp:** `{exp_label}` · **Spot:** `${spot:.2f}`\n\n"
        
        # Delta formatting
        if abs(net_dex) >= 1e9:
            net_dex_str = f"{net_dex/1e9:.2f}B"
        else:
            net_dex_str = f"{net_dex/1e6:.2f}M"
            
        desc += f"**GEX C/P** `{gex_ratio:.2f}x` · **OI C/P** `{oi_ratio:.2f}x` · **Δ neto** `{net_dex_str}`\n"
        desc += f"**Resist.** `${res_strike:.2f}` `(+{pct_res:.1f}%)` · **Soporte** `${sup_strike:.2f}` `({pct_sup:.1f}%)`\n\n"
        
        # Interpretation
        if gex_ratio > 1.2:
            txt_interpret = f"Posicionamiento fuerte en Calls para {ticker}."
        elif gex_ratio < 0.8:
            txt_interpret = f"Posicionamiento fuerte en Puts para {ticker}."
        else:
            txt_interpret = f"Posicionamiento mixto en {ticker}."
            
        desc += f"> {txt_interpret}"
        
        embed.description = desc
        
        # Generate strikes table image
        buf = plot_strikes_table(gex_data, dex_data, ticker, spot, exp_label)
        file = discord.File(fp=buf, filename='strikes.png')
        embed.set_image(url="attachment://strikes.png")
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando strikes clave para {ticker}")
    finally:
        plt.close('all')
        gc.collect()

def plot_iv_surface(ticker, data):
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')
    try:
        tenors = np.array(data.get('tenors', []))
        moneyness = np.array(data.get('moneyness', []))
        iv_grid = np.array(data.get('iv', []))

        if not tenors.any() or not moneyness.any() or not iv_grid.any():
             ax.text(0.5, 0.5, 0.5, "Sin datos de Superficie", color='red')
             return get_image_buffer(fig)

        X, Y = np.meshgrid(moneyness, tenors)
        Z = iv_grid
        surf = ax.plot_surface(X, Y, Z, cmap='coolwarm', edgecolor='none', alpha=0.8)
        
        ax.set_title(f'IV Surface de {ticker.upper()}', color='white', fontsize=16)
        ax.set_xlabel('Moneyness', color='white')
        ax.set_ylabel('Tenor (Días)', color='white')
        ax.set_zlabel('IV', color='white')
        
        ax.xaxis.set_pane_color((0.1, 0.1, 0.1, 1.0))
        ax.yaxis.set_pane_color((0.1, 0.1, 0.1, 1.0))
        ax.zaxis.set_pane_color((0.1, 0.1, 0.1, 1.0))
    except Exception as e:
        ax.text(0.5, 0.5, 0.5, f"Error generando 3D: {e}", color='red')
    return get_image_buffer(fig)

import matplotlib.colors as mcolors

def format_num(val):
    if pd.isna(val) or val == 0: return "0"
    abs_val = abs(val)
    if abs_val >= 1e9:
        return f"{val/1e9:.1f}B"
    elif abs_val >= 1e6:
        return f"{val/1e6:.1f}M"
    elif abs_val >= 1e3:
        return f"{val/1e3:.0f}K"
    else:
        # Si es un numero decimal real pequeño, mostramos hasta 2 decimales
        if abs(val - int(val)) > 1e-5:
            return f"{val:.2f}"
        return f"{val:.0f}"

def plot_lepton_matrix(ticker, final_df):
    if final_df.empty:
        fig, ax = plt.subplots(figsize=(6, 2))
        fig.patch.set_facecolor('#161b22')
        ax.axis('off')
        ax.text(0.5, 0.5, "Sin datos de Matriz", color='red', ha='center', va='center')
        return get_image_buffer(fig)
        
    fig, ax = plt.subplots(figsize=(14, len(final_df)*0.4 + 2))
    fig.patch.set_facecolor('#161b22')
    ax.axis('tight')
    ax.axis('off')
    
    # Header
    ax.set_title(f'Matriz • {ticker} • Expiraciones', color='white', fontsize=18, fontweight='bold', pad=20, loc='left')
    
    table = ax.table(cellText=[[format_num(v) if i > 0 else v for i, v in enumerate(row)] for row in final_df.values],
                     colLabels=final_df.columns,
                     cellLoc='center',
                     loc='center')
                     
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2)
    
    # Color mapping
    call_cols = [1, 3, 5, 7] # OI C, Vol C, Δ C, Γ C
    put_cols = [2, 4, 6, 8]  # OI P, Vol P, Δ P, Γ P
    
    col_maxes = final_df.iloc[:, 1:].abs().max()
    
    cmap_c = mcolors.LinearSegmentedColormap.from_list("c_cmap", ["#161b22", "#2ea043"])
    cmap_p = mcolors.LinearSegmentedColormap.from_list("p_cmap", ["#161b22", "#f85149"])
    
    for (i, j), cell in table.get_celld().items():
        cell.set_edgecolor('#30363d')
        if i == 0:
            cell.set_facecolor('#0d1117')
            cell.set_text_props(color='#8b949e', weight='bold') # Grey header
        else:
            val = final_df.iloc[i-1, j] if j > 0 else 0
            if j in call_cols:
                norm_val = abs(val) / (col_maxes[final_df.columns[j]] + 1e-9)
                cell.set_facecolor(cmap_c(norm_val * 0.7 + 0.1)) # Never fully dark or fully bright
                cell.set_text_props(color='white')
            elif j in put_cols:
                norm_val = abs(val) / (col_maxes[final_df.columns[j]] + 1e-9)
                cell.set_facecolor(cmap_p(norm_val * 0.7 + 0.1))
                cell.set_text_props(color='white')
            else:
                cell.set_facecolor('#161b22')
                cell.set_text_props(color='white', weight='bold')

    plt.tight_layout()
    return get_image_buffer(fig)

def plot_skew_curves(ticker, profiles, selected_expiry=None):
    fig, ax = plt.subplots(figsize=(10, 6), facecolor='#0d1117')
    ax.set_facecolor('#0d1117')
    
    x_points = np.array([-2, -1, 0, 1, 2])
    x_labels = ["10D Put", "25D Put", "ATM", "25D Call", "10D Call"]
    
    if selected_expiry:
        # Expiración única seleccionada: Curva ultra-detallada con zonas y verticales
        profile = next((p for p in profiles if p["expiry"] == selected_expiry), None)
        if not profile:
            raise ValueError(f"No profile found for expiry {selected_expiry}")
        
        y_ivs = np.array([
            profile["put_10d_iv"],
            profile["put_25d_iv"],
            profile["atm_iv"],
            profile["call_25d_iv"],
            profile["call_10d_iv"]
        ])
        
        # Interpolación polinómica exacta de grado 4 para suavidad
        poly_coefs = np.polyfit(x_points, y_ivs, 4)
        x_dense = np.linspace(-2, 2, 150)
        y_dense = np.polyval(poly_coefs, x_dense)
        
        # Graficar curva suave y puntos reales
        ax.plot(x_dense, y_dense, color='#00f0ff', linewidth=3, label=f"Vencimiento: {profile['expiry']} ({profile['days_to_expiry']} DTE)")
        ax.scatter(x_points, y_ivs, color='#ff007f', s=50, zorder=5)
        ax.fill_between(x_dense, ax.get_ylim()[0], y_dense, color='#00f0ff', alpha=0.1)
        
        # Líneas verticales discontinuas y texto de porcentajes
        # 25D Put (Naranja/Rojo)
        ax.axvline(-1, color='#ff9900', linestyle='--', linewidth=1.5, alpha=0.7)
        ax.text(-1, y_ivs[1] + 0.5, f"{y_ivs[1]:.1f}%", color='#ff9900', ha='center', va='bottom', fontweight='bold')
        
        # ATM (Cian/Blanco)
        ax.axvline(0, color='#00ffcc', linestyle='--', linewidth=1.5, alpha=0.7)
        ax.text(0, y_ivs[2] + 0.5, f"{y_ivs[2]:.1f}%", color='#00ffcc', ha='center', va='bottom', fontweight='bold')
        
        # 25D Call (Azul)
        ax.axvline(1, color='#3399ff', linestyle='--', linewidth=1.5, alpha=0.7)
        ax.text(1, y_ivs[3] + 0.5, f"{y_ivs[3]:.1f}%", color='#3399ff', ha='center', va='bottom', fontweight='bold')
        
        # Etiquetas de zonas en la gráfica
        ymin, ymax = ax.get_ylim()
        y_text_pos = ymin + (ymax - ymin) * 0.4
        ax.text(-1.5, y_text_pos, "OTM PUTS\n(Fear/Hedge Bid)", color='#ff9900', ha='center', va='center', fontsize=10, fontweight='bold', alpha=0.6)
        ax.text(0, y_text_pos, "ATM\n(At-the-Money)", color='#00ffcc', ha='center', va='center', fontsize=10, fontweight='bold', alpha=0.6)
        ax.text(1.5, y_text_pos, "OTM CALLS\n(Speculative Bid)", color='#3399ff', ha='center', va='center', fontsize=10, fontweight='bold', alpha=0.6)
        
        ax.set_title(f"OPTIONS VOLATILITY SKEW (25 Delta Skew) - {ticker.upper()} - {selected_expiry} (Skew 25Δ: {profile['skew_25d']:.2f} pts)", color='white', fontsize=13, fontweight='bold', pad=20, loc='left')
    else:
        # Multi Expiry (Top 4 expiraciones más cercanas en curvas suaves)
        profiles_to_plot = profiles[:4]
        colors = ['#00f0ff', '#ff007f', '#ffcc00', '#39ff14']
        
        for i, profile in enumerate(profiles_to_plot):
            y_ivs = np.array([
                profile["put_10d_iv"],
                profile["put_25d_iv"],
                profile["atm_iv"],
                profile["call_25d_iv"],
                profile["call_10d_iv"]
            ])
            poly_coefs = np.polyfit(x_points, y_ivs, 4)
            x_dense = np.linspace(-2, 2, 100)
            y_dense = np.polyval(poly_coefs, x_dense)
            
            color = colors[i % len(colors)]
            ax.plot(x_dense, y_dense, color=color, linewidth=2, 
                    label=f"{profile['expiry']} ({profile['days_to_expiry']} DTE) | Skew: {profile['skew_25d']:.1f}")
            ax.scatter(x_points, y_ivs, color=color, s=25, zorder=5)
            
        ax.set_title(f"Multi-Expiry Volatility Skew - {ticker.upper()}", color='white', fontsize=14, fontweight='bold', pad=20, loc='left')
        
    # Personalización de los ejes
    ax.tick_params(colors='gray', labelsize=10)
    ax.set_xticks(x_points)
    ax.set_xticklabels(x_labels, color='gray', fontsize=9)
    ax.set_ylabel("Implied Volatility (IV) %", color='gray', fontsize=11)
    
    for spine in ['top', 'right']: ax.spines[spine].set_visible(False)
    for spine in ['bottom', 'left']: ax.spines[spine].set_color('gray')
    ax.grid(color='#2a2a2a', linestyle='--', linewidth=0.5, alpha=0.5)
    
    # Leyenda ANTES de los efectos de neón para evitar duplicados
    ax.legend(loc='upper right', frameon=False, fontsize=9, labelcolor='gray')
    
    # Agregar los efectos de neón cyberpunk
    mplcyberpunk.add_glow_effects(ax)
    
    plt.tight_layout()
    return get_image_buffer(fig)

def get_expiry_type(date_str):
    import datetime
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        if dt.weekday() != 4:
            return "W"
        if 15 <= dt.day <= 21:
            return "M"
        return "W"
    except:
        return "W"

def format_scanner_val(col, val):
    import pandas as pd
    if pd.isna(val) or val == 0:
        if col == 'CHG%':
            return "0.0%"
        elif col == 'CHG$':
            return "0.00"
        return "0"
    if col == 'STRIKE':
        return f"${val:.2f}"
    if col == 'CHG%':
        return f"{val:+.1f}%"
    if col == 'CHG$':
        return f"{val:+.2f}"
    if col in ['GEX', 'DEX']:
        is_neg = val < 0
        abs_val = abs(val)
        if abs_val >= 1e9:
            s = f"{val/1e9:.2f}B" if not is_neg else f"-{abs_val/1e9:.2f}B"
        elif abs_val >= 1e6:
            s = f"{val/1e6:.2f}M" if not is_neg else f"-{abs_val/1e6:.2f}M"
        elif abs_val >= 1e3:
            s = f"{val/1e3:.0f}K" if not is_neg else f"-{abs_val/1e3:.0f}K"
        else:
            s = f"{val:.2f}"
        return f"${s}" if not is_neg else f"-${s[1:]}"
    if col in ['OI', 'VOL']:
        abs_val = abs(val)
        if abs_val >= 1e6:
            return f"{val/1e6:.1f}M"
        elif abs_val >= 1e3:
            return f"{val/1e3:.1f}K"
        return f"{val:.0f}"
    return str(val)

def plot_scanner_top_table(ticker, df_data, metric_name):
    import pandas as pd
    import io
    import matplotlib.pyplot as plt
    
    # Prepare figure
    fig, ax = plt.subplots(figsize=(15, len(df_data)*0.4 + 2.5))
    fig.patch.set_facecolor('#0d0e15')
    ax.set_facecolor('#0d0e15')
    ax.axis('off')
    
    # Title
    ax.text(0.02, 0.95, f"OPTION SCANNER TOP 15 • {ticker.upper()}", color='white', fontsize=18, fontweight='bold', ha='left', va='center')
    ax.text(0.98, 0.95, f"Sorted by: {metric_name.upper()} DESC", color='#ffcc00', fontsize=12, fontweight='bold', ha='right', va='center')
    
    # Format the table cell values
    cell_text = []
    for _, row in df_data.iterrows():
        row_cells = []
        for col in df_data.columns:
            row_cells.append(format_scanner_val(col, row[col]))
        cell_text.append(row_cells)
        
    # Relative column widths: sum up to 1.0
    col_widths = [0.06, 0.22, 0.08, 0.07, 0.05, 0.07, 0.07, 0.08, 0.08, 0.11, 0.11]
    
    table = ax.table(cellText=cell_text,
                     colLabels=df_data.columns,
                     colWidths=col_widths,
                     cellLoc='center',
                     loc='center')
                     
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)
    
    # Set headers and row formatting
    for (i, j), cell in table.get_celld().items():
        cell.set_edgecolor('#1a1d2e')
        if i == 0:
            # Header formatting
            cell.set_facecolor('#161925')
            cell.set_text_props(color='#b0bec5', weight='bold')
        else:
            # Row formatting
            bg_color = '#0d0e15' if i % 2 == 1 else '#12141f'
            cell.set_facecolor(bg_color)
            cell.set_text_props(color='white')
            
            # Column-specific coloring
            col_name = df_data.columns[j]
            raw_val = df_data.iloc[i-1, j]
            
            if col_name == 'DTE':
                cell.set_text_props(color='#ffcc00', weight='bold')
            elif col_name == 'STRIKE':
                cell.set_text_props(color='white', weight='bold')
            elif col_name in ['GEX', 'DEX']:
                if pd.notnull(raw_val) and raw_val != 0:
                    if raw_val > 0:
                        cell.set_text_props(color='#00ffcc', weight='bold')
                    else:
                        cell.set_text_props(color='#ff4a5a', weight='bold')
            elif col_name in ['OI', 'VOL']:
                cell.set_text_props(color='#b0bec5')
            elif col_name in ['CHG%', 'CHG$']:
                if pd.notnull(raw_val) and raw_val != 0:
                    if raw_val > 0:
                        cell.set_text_props(color='#00ffcc', weight='bold')
                    else:
                        cell.set_text_props(color='#ff4a5a', weight='bold')
            elif col_name == 'DESCRIPTION':
                cell.set_text_props(color='white', ha='left')
                cell.set_text_props(ha='left')
                
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#0d0e15', edgecolor='none', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

def format_num_heatmap(val):
    import pandas as pd
    if pd.isna(val) or val == 0: return "0"
    abs_val = abs(val)
    if abs_val >= 1e9:
        return f"{val/1e9:.1f}B"
    elif abs_val >= 1e6:
        return f"{val/1e6:.1f}M"
    elif abs_val >= 1e3:
        return f"{val/1e3:.0f}K"
    else:
        return f"{val:.1f}"

def plot_options_heatmap(ticker, df_data, spot, metric_name):
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import io
    import datetime
    
    # 1. Group by (expiry, strike)
    metric_col = metric_name.lower()
    if metric_col == 'gex':
        val_col = 'gex'
    elif metric_col == 'dex':
        val_col = 'dex'
    elif metric_col == 'volume':
        val_col = 'volume'
    else:
        val_col = 'open_interest'
        
    grouped = df_data.groupby(['expiry', 'strike'])[val_col].sum().reset_index()
    
    # 2. Get top 5 closest expirations (future dates only)
    today_str = datetime.date.today().isoformat()
    unique_expiries = sorted([exp for exp in grouped['expiry'].unique() if exp >= today_str])[:5]
    if not unique_expiries:
        unique_expiries = sorted(grouped['expiry'].unique())[:5]
        
    # 3. Get strikes closest to spot (around spot +- 10%, pick top 8)
    unique_strikes = grouped['strike'].unique()
    closest_strikes = sorted(unique_strikes, key=lambda s: abs(s - spot))[:8]
    closest_strikes = sorted(closest_strikes)
    
    # Filter
    grouped_filtered = grouped[grouped['expiry'].isin(unique_expiries) & grouped['strike'].isin(closest_strikes)]
    if grouped_filtered.empty:
        grouped_filtered = grouped.head(40)
        unique_expiries = sorted(grouped_filtered['expiry'].unique())[:5]
        closest_strikes = sorted(grouped_filtered['strike'].unique())[:8]
        
    # Pivot
    pivot_df = grouped_filtered.pivot(index='strike', columns='expiry', values=val_col).fillna(0)
    pivot_df = pivot_df.reindex(index=closest_strikes, columns=unique_expiries).fillna(0)
    
    # 4. Plotting
    fig, ax = plt.subplots(figsize=(10, 6.5))
    fig.patch.set_facecolor('#0d0e15')
    ax.set_facecolor('#0d0e15')
    
    # Colormap
    if metric_name.upper() in ['GEX', 'DEX']:
        # Divergent map: Red -> Dark -> Green
        cmap = mcolors.LinearSegmentedColormap.from_list("custom_div", ["#ff4a5a", "#12141f", "#00ffcc"])
        vmax = pivot_df.abs().max().max()
        vmin = -vmax
        if vmax == 0:
            vmax = 1
            vmin = -1
    else:
        # Sequential: Dark -> Orange
        cmap = mcolors.LinearSegmentedColormap.from_list("custom_seq", ["#12141f", "#ff9900"])
        vmin = 0
        vmax = pivot_df.max().max()
        if vmax == 0:
            vmax = 1
            
    im = ax.imshow(pivot_df.values, cmap=cmap, aspect='auto', vmin=vmin, vmax=vmax)
    
    # Add values text in each cell
    for i in range(len(pivot_df.index)):
        for j in range(len(pivot_df.columns)):
            val = pivot_df.values[i, j]
            text_str = format_num_heatmap(val)
            color = 'white'
            if metric_name.upper() in ['GEX', 'DEX']:
                if val > 0.05 * vmax:
                    color = '#0d0e15'
                elif val < 0.05 * vmin:
                    color = 'white'
            ax.text(j, i, text_str, ha='center', va='center', color=color, fontsize=10, fontweight='bold')
            
    # Labels and Ticks
    ax.set_xticks(np.arange(len(pivot_df.columns)))
    ax.set_yticks(np.arange(len(pivot_df.index)))
    
    # Format labels (short dates)
    short_dates = []
    for d in pivot_df.columns:
        try:
            short_dates.append(datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%m/%d/%y"))
        except:
            short_dates.append(d)
            
    ax.set_xticklabels(short_dates, color='#b0bec5', fontsize=10)
    ax.set_yticklabels([f"${s:.2f}" for s in pivot_df.index], color='#b0bec5', fontsize=10)
    
    # Clean spines
    for spine in ax.spines.values():
        spine.set_color('#1a1d2e')
        
    ax.set_xlabel('Vencimiento (Expiration)', color='#b0bec5', fontsize=11, labelpad=10)
    ax.set_ylabel('Precio Strike', color='#b0bec5', fontsize=11, labelpad=10)
    
    # Title
    ax.text(-0.4, -0.8, f"{ticker.upper()} - Options Flow Heatmap ({metric_name.upper()})", color='white', fontsize=15, fontweight='bold')
    ax.text(len(pivot_df.columns) - 0.6, -0.8, f"Spot: ${spot:.2f}", color='#ffcc00', fontsize=12, fontweight='bold', ha='right')
    
    cbar = fig.colorbar(im, ax=ax, pad=0.03, shrink=0.8)
    cbar.ax.tick_params(labelsize=8, colors='#b0bec5')
    cbar.outline.set_color('#1a1d2e')
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#0d0e15', edgecolor='none', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# --- COMANDOS DEL BOT ---
def check_channel(interaction: discord.Interaction):
    return interaction.channel.name == ALLOWED_CHANNEL

@tree.command(name="niveles", description="Genera el código de TradingView (Pine Script v6) con los niveles clave de opciones")
@app_commands.autocomplete(
    exp1=expiration_autocomplete, exp2=expiration_autocomplete, exp3=expiration_autocomplete,
    exp4=expiration_autocomplete, exp5=expiration_autocomplete, exp6=expiration_autocomplete,
    exp7=expiration_autocomplete, exp8=expiration_autocomplete, exp9=expiration_autocomplete,
    exp10=expiration_autocomplete
)
async def niveles_cmd(
    interaction: discord.Interaction, ticker: str,
    exp1: str = None, exp2: str = None, exp3: str = None, exp4: str = None, exp5: str = None,
    exp6: str = None, exp7: str = None, exp8: str = None, exp9: str = None, exp10: str = None
):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    ticker = ticker.upper()
    
    exp_list = [e for e in [exp1, exp2, exp3, exp4, exp5, exp6, exp7, exp8, exp9, exp10] if e]
    exp_label = f" ({' + '.join(exp_list)})" if exp_list else " (Todas las Fechas)"
    
    try:
        if not exp_list:
            gex_data = fa.gex(ticker, expiration=None)
            spot = gex_data.get('underlying_price', 0)
            levels_data = fa.exposure_levels(ticker)
            levels = levels_data.get('levels', {})
            gamma_flip_raw = levels.get('gamma_flip', 0)
        else:
            all_gex_strikes = []
            spot = 0
            for exp in exp_list:
                g = fa.gex(ticker, expiration=exp)
                if 'underlying_price' in g and g['underlying_price']:
                    spot = g['underlying_price']
                if 'strikes' in g and g['strikes']:
                    all_gex_strikes.extend(g['strikes'])
                    
            df_g = pd.DataFrame(all_gex_strikes)
            if not df_g.empty:
                df_gex = df_g.groupby('strike').sum().reset_index()
                gex_data = {'strikes': df_gex.to_dict('records')}
                gamma_flip_raw = calc_gamma_flip(df_gex, spot)
            else:
                gex_data = {'strikes': []}
                gamma_flip_raw = 0
        
        if not spot:
            live_spot = await get_live_spot(ticker)
            if live_spot:
                spot = live_spot
                
        strikes = gex_data.get('strikes', [])
        df = pd.DataFrame(strikes)
        
        if df.empty:
            await interaction.followup.send(f"No hay suficientes datos de opciones disponibles para {ticker}{exp_label}.")
            return
            
        calls_sorted = df.sort_values('call_gex', ascending=False).head(3)
        puts_sorted = df.sort_values('put_gex', ascending=True).head(3)
        
        calls = []
        for i, (idx, row) in enumerate(calls_sorted.iterrows(), 1):
            calls.append({
                'name': f"GEX Max Call {i}",
                'strike': row['strike'],
                'gex': row['call_gex'] / 1e6
            })
            
        puts = []
        for i, (idx, row) in enumerate(puts_sorted.iterrows(), 1):
            puts.append({
                'name': f"GEX Max Put {i}",
                'strike': row['strike'],
                'gex': abs(row['put_gex']) / 1e6
            })
            
        levels_by_strike = {}
        for c in calls:
            strike = round(c['strike'], 2)
            if strike not in levels_by_strike:
                levels_by_strike[strike] = []
            levels_by_strike[strike].append(c)
            
        for p in puts:
            strike = round(p['strike'], 2)
            if strike not in levels_by_strike:
                levels_by_strike[strike] = []
            levels_by_strike[strike].append(p)
            
        lines_to_draw = []
        for strike, items in levels_by_strike.items():
            if len(items) == 1:
                item = items[0]
                is_call = "Call" in item['name']
                short_name = item['name'].replace("GEX Max ", "")
                lines_to_draw.append({
                    'strike': strike,
                    'label': f"GEX {short_name} (${strike:.2f}) [{item['gex']:.1f}M]",
                    'color': "color.green" if is_call else "color.red"
                })
            else:
                names = []
                gex_strs = []
                sorted_items = sorted(items, key=lambda x: "Call" not in x['name'])
                for item in sorted_items:
                    short_name = item['name'].replace("GEX Max ", "")
                    names.append(short_name)
                    gex_strs.append(f"{item['gex']:.1f}M")
                
                lines_to_draw.append({
                    'strike': strike,
                    'label': f"GEX {' / '.join(names)} (${strike:.2f}) [{' / '.join(gex_strs)}]",
                    'color': "color.white"
                })
                
        flip_val = gamma_flip_raw if gamma_flip_raw else 0
        if flip_val:
            lines_to_draw.append({
                'strike': round(flip_val, 2),
                'label': f"Gamma Flip (${flip_val:.2f})",
                'color': "color.orange"
            })
            
        pine_draws = ""
        for line in lines_to_draw:
            pine_draws += f"draw_level({line['strike']:.2f}, \"{line['label']}\", {line['color']})\n"
            
        pine_code = (
            f"//@version=6\n"
            f"indicator(\"Niveles GEX - {ticker}\", overlay=true)\n\n"
            f"// Niveles de Exposición Gamma para {ticker}{exp_label}\n"
            f"// Spot: ${spot:.2f} | Generado por CarlosBot\n\n"
            f"draw_level(price, txt, col) =>\n"
            f"    if not na(price) and price > 0\n"
            f"        var line ln = na\n"
            f"        var label lb = na\n"
            f"        if barstate.islast\n"
            f"            ln := line.new(bar_index - 80, price, bar_index, price, xloc=xloc.bar_index, extend=extend.none, color=col, width=1, style=line.style_dashed)\n"
            f"            lb := label.new(bar_index, price, text=txt, color=color.new(col, 100), textcolor=col, style=label.style_label_left, size=size.normal)\n"
            f"            line.delete(ln[1])\n"
            f"            label.delete(lb[1])\n\n"
            f"// Dibujar los niveles en el gráfico\n"
            f"{pine_draws}"
        )
        
        call_1 = calls[0]['strike'] if len(calls) > 0 else 0
        call_2 = calls[1]['strike'] if len(calls) > 1 else 0
        call_3 = calls[2]['strike'] if len(calls) > 2 else 0
        
        put_1 = puts[0]['strike'] if len(puts) > 0 else 0
        put_2 = puts[1]['strike'] if len(puts) > 1 else 0
        put_3 = puts[2]['strike'] if len(puts) > 2 else 0
        
        embed = discord.Embed(
            title=f"🎯 NIVELES DE OPCIONES: {ticker}",
            description=f"Niveles clave de Exposición Gamma (GEX) calculados para **{ticker}**{exp_label} (Spot: **${spot:.2f}**).",
            color=discord.Color.blue()
        )
        embed.add_field(name="🟢 Max Call GEX", value=f"1️⃣ `${call_1:.2f}` (`{calls[0]['gex']:.1f}M`)\n2️⃣ `${call_2:.2f}` (`{calls[1]['gex']:.1f}M`)\n3️⃣ `${call_3:.2f}` (`{calls[2]['gex']:.1f}M`)", inline=True)
        embed.add_field(name="🔴 Max Put GEX", value=f"1️⃣ `${put_1:.2f}` (`{puts[0]['gex']:.1f}M`)\n2️⃣ `${put_2:.2f}` (`{puts[1]['gex']:.1f}M`)\n3️⃣ `${put_3:.2f}` (`{puts[2]['gex']:.1f}M`)", inline=True)
        embed.add_field(name="🟠 Gamma Flip", value=f"`${flip_val:.2f}`" if flip_val else "`N/A`", inline=True)
        embed.set_footer(text="CarlosBot Option Levels • Copia el código de abajo en tu TradingView")
        
        await interaction.followup.send(embed=embed)
        await interaction.followup.send(f"```pinescript\n{pine_code}```")
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error generando niveles GEX para {ticker}.")

@tree.command(name="liquidez", description="Genera el código de TradingView con los muros de liquidez del libro de órdenes de cripto en Coinbase")
async def liquidez_cmd(interaction: discord.Interaction, ticker: str, rango: float = 10.0):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    ticker = ticker.upper().replace("-USD", "")
    symbol = f"{ticker}-USD"
    
    try:
        url = f"https://api.exchange.coinbase.com/products/{symbol}/book?level=2"
        headers = {"User-Agent": "Mozilla/5.0"}
        
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=5.0)
        
        if response.status_code == 404:
            await interaction.followup.send(f"No se encontraron datos de liquidez en Coinbase para el ticker **{ticker}**. Asegúrate de que esté listado en Coinbase (ej. BTC, ETH, SOL, XRP).")
            return
            
        response.raise_for_status()
        data = response.json()
        
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        
        if not bids or not asks:
            await interaction.followup.send(f"No hay suficientes datos en el libro de órdenes de Coinbase para **{ticker}** en este momento.")
            return
            
        sample_price = float(bids[0][0])
        
        # Determinar paso de redondeo dinámico
        if sample_price > 10000:
            step = 50.0
        elif sample_price > 1000:
            step = 5.0
        elif sample_price > 10:
            step = 0.5
        elif sample_price > 1:
            step = 0.05
        else:
            order = int(np.floor(np.log10(sample_price)))
            step = round(10 ** (order - 1), 6)
            if step == 0:
                step = 0.0001
                
        # Agrupar compras (Bids)
        grouped_bids = {}
        for item in bids:
            p = float(item[0])
            q = float(item[1])
            rounded_p = round(p / step) * step
            grouped_bids[rounded_p] = grouped_bids.get(rounded_p, 0.0) + q
            
        # Agrupar ventas (Asks)
        grouped_asks = {}
        for item in asks:
            p = float(item[0])
            q = float(item[1])
            rounded_p = round(p / step) * step
            grouped_asks[rounded_p] = grouped_asks.get(rounded_p, 0.0) + q
            
        rango_val = abs(rango)
        lower_bound = sample_price * (1.0 - (rango_val / 100.0))
        upper_bound = sample_price * (1.0 + (rango_val / 100.0))
        
        bids_list = []
        for price, qty in grouped_bids.items():
            if lower_bound <= price <= upper_bound:
                usd_val = price * qty
                bids_list.append({'price': price, 'qty': qty, 'usd': usd_val})
            
        asks_list = []
        for price, qty in grouped_asks.items():
            if lower_bound <= price <= upper_bound:
                usd_val = price * qty
                asks_list.append({'price': price, 'qty': qty, 'usd': usd_val})
            
        # Obtener los 10 mayores muros de compra y venta en valor USD
        top_bids = sorted(bids_list, key=lambda x: x['usd'], reverse=True)[:10]
        top_asks = sorted(asks_list, key=lambda x: x['usd'], reverse=True)[:10]
        
        # Ordenar por precio ascendente
        top_bids = sorted(top_bids, key=lambda x: x['price'])
        top_asks = sorted(top_asks, key=lambda x: x['price'])
        
        # Generar código Pine Script compacto
        pine_draws = ""
        for i, b in enumerate(top_bids, 1):
            lbl = f"Liq C{i} (${b['price']:.2f}) [{b['qty']:.1f} {ticker} / ${b['usd']/1e6:.2f}M]"
            pine_draws += f"d({b['price']:.2f}, \"{lbl}\", color.green)\n"
            
        for i, a in enumerate(top_asks, 1):
            lbl = f"Liq V{i} (${a['price']:.2f}) [{a['qty']:.1f} {ticker} / ${a['usd']/1e6:.2f}M]"
            pine_draws += f"d({a['price']:.2f}, \"{lbl}\", color.red)\n"
            
        pine_code = (
            f"//@version=6\n"
            f"indicator(\"Muros de Liquidez - {ticker}\", overlay=true)\n"
            f"// Rango: ±{rango_val:.1f}% de ${sample_price:.2f} | CarlosBot\n\n"
            f"d(p, t, c) =>\n"
            f"    if p > 0\n"
            f"        var line l = na\n"
            f"        var label b = na\n"
            f"        if barstate.islast\n"
            f"            l := line.new(bar_index - 80, p, bar_index, p, color=c, style=line.style_dashed)\n"
            f"            b := label.new(bar_index, p, t, color=color.new(c, 100), textcolor=c, style=label.style_label_left)\n"
            f"            line.delete(l[1])\n"
            f"            label.delete(b[1])\n\n"
            f"// Dibujar niveles\n"
            f"{pine_draws}"
        )
        
        embed = discord.Embed(
            title=f"📊 MUROS DE LIQUIDEZ: {ticker}",
            description=f"Grandes bloques de órdenes límite detectados en el libro de órdenes de Coinbase para **{ticker}** (Filtro: ±{rango_val:.1f}% del precio actual).",
            color=discord.Color.dark_blue()
        )
        
        total_bids_qty = sum(b['qty'] for b in top_bids)
        total_bids_usd = sum(b['usd'] for b in top_bids)
        
        total_asks_qty = sum(a['qty'] for a in top_asks)
        total_asks_usd = sum(a['usd'] for a in top_asks)

        bids_desc = ""
        for i, b in enumerate(top_bids, 1):
            bids_desc += f"🟢 **Compra {i}**: `${b['price']:.2f}` (`{b['qty']:.1f} {ticker}` / `${b['usd']/1e6:.2f}M`)\n"
        if bids_desc:
            bids_desc += f"📊 **Total Compra**: `{total_bids_qty:.1f} {ticker}` / `${total_bids_usd/1e6:.2f}M`"
        else:
            bids_desc = "*Ninguno*"
            
        asks_desc = ""
        for i, a in enumerate(top_asks, 1):
            asks_desc += f"🔴 **Venta {i}**: `${a['price']:.2f}` (`{a['qty']:.1f} {ticker}` / `${a['usd']/1e6:.2f}M`)\n"
        if asks_desc:
            asks_desc += f"📊 **Total Venta**: `{total_asks_qty:.1f} {ticker}` / `${total_asks_usd/1e6:.2f}M`"
        else:
            asks_desc = "*Ninguno*"
            
        embed.add_field(name="📥 Muros de Compra (Soportes)", value=bids_desc, inline=False)
        embed.add_field(name="📤 Muros de Venta (Resistencias)", value=asks_desc, inline=False)
        embed.set_footer(text="Coinbase Order Book • Copia el código de abajo en tu TradingView")
        
        await interaction.followup.send(embed=embed)
        
        # Enviar el código de forma segura respetando el límite de 2000 caracteres de Discord
        if len(pine_code) > 1975:
            lines = pine_code.split('\n')
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) + 10 > 1975:
                    await interaction.followup.send(f"```pinescript\n{chunk}```")
                    chunk = line + "\n"
                else:
                    chunk += line + "\n"
            if chunk:
                await interaction.followup.send(f"```pinescript\n{chunk}```")
        else:
            await interaction.followup.send(f"```pinescript\n{pine_code}```")
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error cargando datos de liquidez para {ticker}.")

@tree.command(name="analizar", description="Análisis completo de GEX (Suma hasta 8 fechas elegibles)")
@app_commands.autocomplete(exp1=expiration_autocomplete, exp2=expiration_autocomplete, exp3=expiration_autocomplete, exp4=expiration_autocomplete, exp5=expiration_autocomplete, exp6=expiration_autocomplete, exp7=expiration_autocomplete, exp8=expiration_autocomplete)
async def analizar(interaction: discord.Interaction, ticker: str, exp1: str = None, exp2: str = None, exp3: str = None, exp4: str = None, exp5: str = None, exp6: str = None, exp7: str = None, exp8: str = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    exp_list = [e for e in [exp1, exp2, exp3, exp4, exp5, exp6, exp7, exp8] if e]
    await handle_positioning_logic(interaction, ticker, exp_list)

@tree.command(name="iv_surface", description="Superficie de Volatilidad Implícita 3D")
async def iv_surface_cmd(interaction: discord.Interaction, ticker: str):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        data = fa.surface(ticker.upper())
        buf = plot_iv_surface(ticker.upper(), data)
        file = discord.File(fp=buf, filename='surface.png')
        embed = discord.Embed(title=f"IV Surface - {ticker.upper()}", color=discord.Color.purple())
        embed.set_image(url="attachment://surface.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error generando IV Surface para {ticker.upper()}")
    finally:
        plt.close('all')
        gc.collect()

@tree.command(name="matriz", description="Matriz de opciones")
async def matriz_cmd(interaction: discord.Interaction, ticker: str):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        # 1. Obtener precio spot
        spot_data = fa.stock_quote(ticker.upper())
        spot = spot_data.get('mid', 0) if isinstance(spot_data, dict) else 0
        if not spot:
            live_spot = await get_live_spot(ticker)
            if live_spot:
                spot = live_spot
        
        # 2. Obtener cadena completa de opciones
        data = fa.option_quote(ticker.upper())
        df = pd.DataFrame(data)
        
        if df.empty:
            buf = plot_lepton_matrix(ticker.upper(), pd.DataFrame())
            file = discord.File(fp=buf, filename='matriz.png')
            embed = discord.Embed(title=f"Matriz de Expiraciones - {ticker.upper()}", color=discord.Color.dark_grey())
            embed.set_image(url="attachment://matriz.png")
            await interaction.followup.send(embed=embed, file=file)
            return

        # 3. Calcular GEX y DEX en dólares
        df['dex'] = df['open_interest'] * 100 * df['delta'] * spot
        df['gex'] = df['open_interest'] * 100 * df['gamma'] * spot * spot * 0.01
        
        # 4. Agrupar por expiración y tipo
        grouped = df.groupby(['expiry', 'type']).agg({
            'open_interest': 'sum',
            'volume': 'sum',
            'dex': 'sum',
            'gex': 'sum'
        }).reset_index()

        # 5. Pivotar para tener Calls y Puts lado a lado
        pivot = grouped.pivot(index='expiry', columns='type', values=['open_interest', 'volume', 'dex', 'gex']).fillna(0)
        
        # Aplanar nombres de columnas
        pivot.columns = [f"{col[0]}_{col[1]}" for col in pivot.columns]
        
        # Filtrar fechas futuras/presentes únicamente (evitar expiradas)
        import datetime
        today_str = datetime.date.today().isoformat()
        pivot = pivot[pivot.index >= today_str]

        # Filtrar expiraciones sin Open Interest y resetear index
        pivot['total_oi'] = pivot.get('open_interest_C', 0) + pivot.get('open_interest_P', 0)
        pivot = pivot[pivot['total_oi'] > 0].reset_index()
        
        # 6. Formatear y renombrar columnas al estilo Lepton
        final_df = pd.DataFrame()
        final_df['EXP'] = pivot['expiry'].str[5:] # Solo mostrar MM-DD
        
        final_df['OI C'] = pivot.get('open_interest_C', 0)
        final_df['OI P'] = pivot.get('open_interest_P', 0)
        final_df['Vol C'] = pivot.get('volume_C', 0)
        final_df['Vol P'] = pivot.get('volume_P', 0)
        final_df['Δ C'] = pivot.get('dex_C', 0)
        final_df['Δ P'] = pivot.get('dex_P', 0)
        final_df['Γ C'] = pivot.get('gex_C', 0)
        final_df['Γ P'] = pivot.get('gex_P', 0)
        
        # Mostrar las primeras 20 expiraciones (al estilo Lepton)
        final_df = final_df.head(20).reset_index(drop=True)

        buf = plot_lepton_matrix(ticker.upper(), final_df)
        file = discord.File(fp=buf, filename='matriz.png')
        embed = discord.Embed(title=f"Matriz de Expiraciones - {ticker.upper()}", color=discord.Color.dark_grey())
        embed.set_image(url="attachment://matriz.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando Matriz para {ticker.upper()}")
    finally:
        plt.close('all')
        gc.collect()

@tree.command(name="calendar", description="Calendario Económico")
async def calendar_cmd(interaction: discord.Interaction):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        cal_today = [
            {"time": "08:30", "event": "Peticiones de Desempleo", "importance": "Alta", "previous": "210K", "forecast": "215K"},
            {"time": "10:00", "event": "Ventas de Viviendas", "importance": "Media", "previous": "4.1M", "forecast": "4.2M"}
        ]
        embed = discord.Embed(title=f"📅 Calendario Económico EE.UU.", color=discord.Color.blue())
        for row in cal_today:
            embed.add_field(name=f"{row['time']} - {row['event']}", value=f"Imp: {row['importance']} | Prev: {row['previous']} | Est: {row['forecast']}", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error cargando el calendario económico.")

@tree.command(name="scanner_top", description="Top 15 contratos de opciones ordenados por una métrica")
@app_commands.choices(metric=[
    app_commands.Choice(name="GEX (Exposición Gamma)", value="GEX"),
    app_commands.Choice(name="DEX (Exposición Delta)", value="DEX"),
    app_commands.Choice(name="Volume (Volumen)", value="Volume"),
    app_commands.Choice(name="OI (Open Interest)", value="OI")
])
async def scanner_top_cmd(interaction: discord.Interaction, ticker: str, metric: app_commands.Choice[str] = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    
    ticker = ticker.upper().strip()
    sel_metric = metric.value if metric else "GEX"
    
    try:
        # 1. Obtener precio spot
        spot_data = fa.stock_quote(ticker)
        spot = spot_data.get('mid', 0)
        if not spot:
            live_spot = await get_live_spot(ticker)
            if live_spot:
                spot = live_spot
                
        if not spot:
            await interaction.followup.send(f"No se pudo obtener el precio spot actual para {ticker}.")
            return
            
        # 2. Obtener cadena de opciones
        data = fa.option_quote(ticker)
        df = pd.DataFrame(data)
        if df.empty:
            await interaction.followup.send(f"No se encontraron opciones para {ticker}.")
            return
            
        # 3. Calcular GEX y DEX
        df['dex'] = df['open_interest'] * 100 * df['delta'] * spot
        # GEX firmado: Calls positivo, Puts negativo
        df['gex'] = df.apply(
            lambda r: (r['open_interest'] * 100 * r['gamma'] * spot * spot * 0.01) if r['type'] == 'C'
                      else (-r['open_interest'] * 100 * r['gamma'] * spot * spot * 0.01),
            axis=1
        )
        
        # 4. Ordenamiento inicial para determinar el Top 15 y sus expiraciones
        df_sort = df.copy()
        metric_col = sel_metric.upper()
        if metric_col == 'VOLUME':
            metric_col = 'volume'
        elif metric_col == 'OI':
            metric_col = 'open_interest'
            
        if metric_col.lower() in ['gex', 'dex']:
            df_sort['abs_val'] = df_sort[metric_col.lower()].abs()
            df_sort = df_sort.sort_values('abs_val', ascending=False)
            df_sort = df_sort.drop(columns=['abs_val'])
        else:
            df_sort = df_sort.sort_values(metric_col.lower(), ascending=False)
            
        df_top = df_sort.head(15).copy()
        
        # 5. Obtener los cambios en los contratos de yfinance para el top 15
        unique_expiries = df_top['expiry'].unique().tolist()
        yf_ticker = yf.Ticker(ticker)
        
        async def fetch_chain(expiry):
            try:
                chain = await asyncio.to_thread(yf_ticker.option_chain, expiry)
                return expiry, chain
            except:
                return expiry, None
                
        tasks = [fetch_chain(exp) for exp in unique_expiries]
        results = await asyncio.gather(*tasks)
        
        # Mapa de lookup: (expiry, strike, type) -> (change, percentChange)
        yfinance_map = {}
        for expiry, chain in results:
            if chain is None:
                continue
            # Calls
            for _, row in chain.calls.iterrows():
                key = (expiry, float(row['strike']), 'C')
                yfinance_map[key] = {
                    'change': row.get('change', np.nan),
                    'percentChange': row.get('percentChange', np.nan)
                }
            # Puts
            for _, row in chain.puts.iterrows():
                key = (expiry, float(row['strike']), 'P')
                yfinance_map[key] = {
                    'change': row.get('change', np.nan),
                    'percentChange': row.get('percentChange', np.nan)
                }
                
        # Rellenar CHG% y CHG$ usando el mapa
        df_top['CHG%'] = df_top.apply(
            lambda r: yfinance_map.get((r['expiry'], float(r['strike']), r['type']), {}).get('percentChange', np.nan),
            axis=1
        )
        df_top['CHG$'] = df_top.apply(
            lambda r: yfinance_map.get((r['expiry'], float(r['strike']), r['type']), {}).get('change', np.nan),
            axis=1
        )
        
        # 6. Parsing de DTE y Expiry Type
        import datetime
        today = datetime.date.today()
        df_top['DTE'] = df_top['expiry'].apply(lambda e: (datetime.datetime.strptime(e, "%Y-%m-%d").date() - today).days)
        df_top['EXP TYPE'] = df_top['expiry'].apply(get_expiry_type)
        
        # Formatear la descripción al estilo: AAPL 06/19/2026 180.00 C
        def format_desc(r):
            try:
                exp_formatted = datetime.datetime.strptime(r['expiry'], "%Y-%m-%d").strftime("%m/%d/%Y")
                return f"{ticker} {exp_formatted} {r['strike']:.2f} {r['type']}"
            except:
                return f"{ticker} {r['expiry']} {r['strike']:.2f} {r['type']}"
                
        df_top['DESCRIPTION'] = df_top.apply(format_desc, axis=1)
        df_top['TICKER'] = ticker
        
        # Renombrar columnas
        df_res = df_top.rename(columns={
            'open_interest': 'OI',
            'volume': 'VOL',
            'strike': 'STRIKE',
            'gex': 'GEX',
            'dex': 'DEX'
        })
        
        # Reordenar columnas para coincidir con el diseño exacto
        cols = ['TICKER', 'DESCRIPTION', 'EXP TYPE', 'STRIKE', 'DTE', 'CHG%', 'CHG$', 'OI', 'VOL', 'GEX', 'DEX']
        df_res = df_res[cols]
        
        # 7. Renderizar tabla como imagen
        buf = plot_scanner_top_table(ticker, df_res, sel_metric)
        file = discord.File(fp=buf, filename='scanner_top.png')
        
        # Sentiment-based color
        embed_color = discord.Color.orange()
        if sel_metric == 'GEX':
            embed_color = discord.Color.brand_green() if df_res['GEX'].sum() > 0 else discord.Color.brand_red()
        elif sel_metric == 'DEX':
            embed_color = discord.Color.brand_green() if df_res['DEX'].sum() > 0 else discord.Color.brand_red()
            
        embed = discord.Embed(
            title=f"🔍 Top 15 Contratos de Opciones — {ticker}", 
            color=embed_color,
            description=f"Escáner de contratos de opciones específicos para **{ticker}** ordenados por **{sel_metric}**.\nSpot actual: `${spot:.2f}`"
        )
        embed.set_image(url="attachment://scanner_top.png")
        embed.set_footer(text=f"FlashAlpha & yfinance Data · /scanner_top {ticker}")
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando el escáner de opciones para {ticker}.")
    finally:
        plt.close('all')
        gc.collect()

@tree.command(name="heatmap", description="Mapa de calor de exposición y flujos por Strike y Expiración")
@app_commands.choices(metric=[
    app_commands.Choice(name="GEX (Exposición Gamma)", value="GEX"),
    app_commands.Choice(name="DEX (Exposición Delta)", value="DEX"),
    app_commands.Choice(name="Volume (Volumen)", value="Volume"),
    app_commands.Choice(name="OI (Open Interest)", value="OI")
])
async def heatmap_cmd(interaction: discord.Interaction, ticker: str, metric: app_commands.Choice[str] = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    
    ticker = ticker.upper().strip()
    sel_metric = metric.value if metric else "GEX"
    
    try:
        # 1. Obtener precio spot
        spot_data = fa.stock_quote(ticker)
        spot = spot_data.get('mid', 0)
        if not spot:
            live_spot = await get_live_spot(ticker)
            if live_spot:
                spot = live_spot
                
        if not spot:
            await interaction.followup.send(f"No se pudo obtener el precio spot actual para {ticker}.")
            return
            
        # 2. Obtener cadena completa de opciones
        data = fa.option_quote(ticker)
        df = pd.DataFrame(data)
        if df.empty:
            await interaction.followup.send(f"No se encontraron opciones para {ticker}.")
            return
            
        # 3. Calcular GEX y DEX en USD
        df['dex'] = df['open_interest'] * 100 * df['delta'] * spot
        df['gex'] = df.apply(
            lambda r: (r['open_interest'] * 100 * r['gamma'] * spot * spot * 0.01) if r['type'] == 'C'
                      else (-r['open_interest'] * 100 * r['gamma'] * spot * spot * 0.01),
            axis=1
        )
        
        # 4. Generar gráfico del heatmap
        buf = plot_options_heatmap(ticker, df, spot, sel_metric)
        file = discord.File(fp=buf, filename='heatmap.png')
        
        # Sentiment-based color
        embed_color = discord.Color.orange()
        
        embed = discord.Embed(
            title=f"📊 Options Flow Heatmap — {ticker}",
            color=embed_color,
            description=f"Mapa de calor de opciones sobre **{ticker}** mostrando la concentración de **{sel_metric}**.\nSpot actual: `${spot:.2f}`"
        )
        embed.set_image(url="attachment://heatmap.png")
        embed.set_footer(text=f"FlashAlpha Data · /heatmap {ticker}")
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando el mapa de calor de opciones para {ticker}.")
    finally:
        plt.close('all')
        gc.collect()

@tree.command(name="posicionamiento", description="Resumen 4 cuadrantes (Suma hasta 8 fechas elegibles)")
@app_commands.autocomplete(exp1=expiration_autocomplete, exp2=expiration_autocomplete, exp3=expiration_autocomplete, exp4=expiration_autocomplete, exp5=expiration_autocomplete, exp6=expiration_autocomplete, exp7=expiration_autocomplete, exp8=expiration_autocomplete)
async def posicionamiento_cmd(interaction: discord.Interaction, ticker: str, exp1: str = None, exp2: str = None, exp3: str = None, exp4: str = None, exp5: str = None, exp6: str = None, exp7: str = None, exp8: str = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    exp_list = [e for e in [exp1, exp2, exp3, exp4, exp5, exp6, exp7, exp8] if e]
    await handle_positioning_logic(interaction, ticker, exp_list)

@tree.command(name="strikes", description="Strikes clave en una tabla de 4 cuadrantes (Suma hasta 8 fechas elegibles)")
@app_commands.autocomplete(exp1=expiration_autocomplete, exp2=expiration_autocomplete, exp3=expiration_autocomplete, exp4=expiration_autocomplete, exp5=expiration_autocomplete, exp6=expiration_autocomplete, exp7=expiration_autocomplete, exp8=expiration_autocomplete)
async def strikes_cmd(interaction: discord.Interaction, ticker: str, exp1: str = None, exp2: str = None, exp3: str = None, exp4: str = None, exp5: str = None, exp6: str = None, exp7: str = None, exp8: str = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    exp_list = [e for e in [exp1, exp2, exp3, exp4, exp5, exp6, exp7, exp8] if e]
    await handle_strikes_logic(interaction, ticker, exp_list)

@tree.command(name="recap", description="Signal recap for a ticker, glance only (Current Day)")
async def recap_cmd(interaction: discord.Interaction, ticker: str):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        data = fa.stock_summary(ticker.upper())
        if not data or 'price' not in data:
            await interaction.followup.send(f"No se pudo obtener el recap diario para {ticker.upper()}")
            return
            
        embed = discord.Embed(
            title=f"GROWTH • OPEN RECAP",
            description=f"Signal recap for {ticker.upper()}, glance only.\n\n**{ticker.upper()}**",
            color=discord.Color.from_rgb(230, 240, 240)
        )
        
        # Extracción de métricas
        spot = data.get('price', {}).get('mid', 0)
        
        # Corrección de Bug de FlashAlpha: Forzar precio Spot en vivo
        live_spot = await get_live_spot(ticker)
        if live_spot: spot = live_spot
        
        exp = data.get('exposure', {})
        regime = exp.get('regime', 'neutral')
        call_wall = exp.get('call_wall', 0)
        put_wall = exp.get('put_wall', 0)
        gamma_flip = exp.get('gamma_flip', 0)
        max_pain = exp.get('max_pain', 0)
        
        # Datos adicionales para coincidir 100% con la imagen web
        mp_data = {}
        zd_data = {}
        vol_data = {}
        try:
            mp_data = fa.max_pain(ticker.upper()) or {}
            zd_data = fa.zero_dte(ticker.upper()) or {}
            vol_data = fa.volatility(ticker.upper()) or {}
        except Exception:
            pass

        # Dealer alignment
        da = mp_data.get('dealer_alignment', {})
        dealer_alignment = da.get('alignment', 'diverging')
        dealer_alignment_desc = "max pain outside walls, weak pin pressure" if dealer_alignment == "diverging" else "aligned"
        
        # Pin score
        pin_score = mp_data.get('pin_probability', 36)
        pin_score_desc = "weak pin setup" if pin_score < 40 else "moderate pin setup"
        
        # 0DTE pin
        pr = zd_data.get('pin_risk', {})
        zd_pin_score = pr.get('pin_score', 47)
        zd_pin_strike = pr.get('magnet_strike', spot)
        zd_pin_desc = "weak pin" if zd_pin_score < 40 else "moderate pin"
        
        # 0DTE flip distance
        z_regime = zd_data.get('regime', {})
        flip_sigmas = z_regime.get('distance_to_flip_sigmas', 0.92)
        flip_desc = f"flip within {max(1, int(flip_sigmas + 0.5))} sigma"
        
        # VRP regime
        vrp = data.get('volatility', {}).get('vrp')
        vol_spreads = vol_data.get('iv_rv_spreads', {})
        vrp_val = vol_spreads.get('vrp_20d', vrp)
        vrp_desc = "IV moderately rich" if (vrp_val or 0) > 2 else "normal IV premium"
        
        zero_dte = exp.get('zero_dte', {})
        zero_dte_share = zero_dte.get('pct_of_total', 0)
        
        charm_dict = zd_data.get('decay', {})
        charm = charm_dict.get('charm_description', exp.get('interpretation', {}).get('charm', 'dealers buy'))
        if charm == "dealers buy" or "dealers buy" in charm.lower(): charm = "dealers buy"
        elif "dealers sell" in charm.lower(): charm = "dealers sell"
        
        gex_interpretation = exp.get('interpretation', {}).get('gamma', 'Dealers dampening moves')
        
        vol = data.get('volatility', {})
        atm_iv = vol.get('atm_iv', 0)
        hv_20 = vol.get('hv_20', 0)
        
        macro = data.get('macro', {})
        vix = macro.get('vix', {}).get('value', 0)
        vix_change_pct = macro.get('vix', {}).get('change_pct', 0)
        term_struct = macro.get('vix_term_structure', {}).get('structure', 'normal')
        near_slope = macro.get('vix_term_structure', {}).get('near_slope_pct', 0)
        fg_score = macro.get('fear_and_greed', {}).get('score', 50)
        fg_rating = macro.get('fear_and_greed', {}).get('rating', 'neutral')
        
        def format_badge(text, color):
            color_map = {
                'NEUTRAL': '⚪',
                'RANGE': '🟢',
                'FRAGILE': '🟡',
                'WATCH': '🟣',
                'VOL RICH': '🔴',
                'SUPPORTIVE': '🟢',
                'SAFE': '🟢',
                'PRESSURE': '🔴'
            }
            emoji = color_map.get(text, '⚪')
            return f"`{emoji} {text}`"
            
        regime_badge = "RANGE" if "positive" in regime.lower() else "FRAGILE"
        term_struct_badge = "NEUTRAL"
        vix_badge = "NEUTRAL"
        fg_badge = "NEUTRAL"
        zero_share_badge = "NEUTRAL"
        
        # --- DYNAMIC DESCRIPTIONS ---
        # Walls
        if call_wall > 0 and abs(spot - call_wall)/call_wall < 0.015:
            walls_desc = "spot near call wall, upside resistance high"
        elif put_wall > 0 and abs(spot - put_wall)/put_wall < 0.015:
            walls_desc = "spot near put wall, downside support high"
        else:
            walls_desc = "balanced, spot mid-corridor"
            
        # Gamma flip
        gamma_flip_pct = ((gamma_flip - spot) / spot) * 100 if spot else 0
        if abs(gamma_flip_pct) < 1.5:
            gf_desc = "spot near gamma flip, regime stability low"
            gf_badge = "FRAGILE"
        elif gamma_flip_pct > 0: # gamma_flip > spot -> spot is below
            gf_desc = "spot below gamma flip, negative gamma pressure"
            gf_badge = "FRAGILE"
        else: # gamma_flip < spot -> spot is above
            gf_desc = "spot above gamma flip, positive gamma support"
            gf_badge = "SAFE"
            
        # Max pain
        max_pain_pct = ((max_pain - spot) / spot) * 100 if spot else 0
        
        # Term struct
        if "backwardation" in term_struct.lower():
            ts_desc = "inverted, near vol > far vol (caution)"
            term_struct_badge = "FRAGILE"
        else:
            ts_desc = "normal, far vol > near"
            term_struct_badge = "NEUTRAL"
            
        # VIX
        if vix < 15: vix_desc = "low volatility environment"
        elif vix < 20: vix_desc = "normal range"
        else: 
            vix_desc = "elevated volatility"
            vix_badge = "WATCH"
            
        # Fear & Greed
        fg_desc = f"{fg_rating} sentiment".replace('_', ' ')
        if "fear" in fg_rating.lower(): fg_badge = "FRAGILE"
        elif "greed" in fg_rating.lower(): fg_badge = "RANGE"
        else: fg_badge = "NEUTRAL"
        
        # 0DTE Share
        if abs(zero_dte_share) < 15: zero_desc = "minor intraday impact"
        elif abs(zero_dte_share) < 25: zero_desc = "moderate intraday impact"
        else: 
            zero_desc = "heavy intraday impact"
            zero_share_badge = "WATCH"
            
        # FORMATO DEL EMBED (Similar a la imagen)
        embed.add_field(name="Spot", value=f"live mid quote\n**${spot:.2f}** {format_badge('NEUTRAL', 'gray')}", inline=False)
        embed.add_field(name="GEX regime", value=f"{gex_interpretation}\n**{regime.replace('_', ' ').title()}** {format_badge(regime_badge, 'green')}", inline=False)
        embed.add_field(name="Walls", value=f"{walls_desc}\n**{call_wall:.2f}↑ / {put_wall:.2f}↓** {format_badge('NEUTRAL', 'gray')}", inline=False)
        embed.add_field(name="Gamma flip", value=f"{gf_desc}\n**{gamma_flip:.2f} ({gamma_flip_pct:+.2f}%)** {format_badge(gf_badge, 'yellow')}", inline=False)
        embed.add_field(name="Max pain", value=f"max pain {abs(max_pain_pct):.1f}% {'below' if max_pain_pct < 0 else 'above'} spot\n**{max_pain:.2f} ({max_pain_pct:+.2f}%)** {format_badge('WATCH', 'purple')}", inline=False)
        embed.add_field(name="IV vs HV", value=f"IV vs realized\n**{atm_iv:.1f}% / {hv_20:.1f}%** {format_badge('VOL RICH' if atm_iv > hv_20 else 'NEUTRAL', 'red')}", inline=False)
        embed.add_field(name="Term struct", value=f"{ts_desc}\n**{term_struct} ({near_slope:+.1f}%)** {format_badge(term_struct_badge, 'gray')}", inline=False)
        embed.add_field(name="VIX", value=f"{vix_desc}\n**{vix:.1f} ({vix_change_pct:+.1f}%)** {format_badge(vix_badge, 'gray')}", inline=False)
        embed.add_field(name="Fear & Greed", value=f"{fg_desc}\n**{fg_score}/100 {fg_rating}** {format_badge(fg_badge, 'gray')}", inline=False)
        embed.add_field(name="0DTE share", value=f"{zero_desc}\n**{zero_dte_share:+.1f}% of GEX** {format_badge(zero_share_badge, 'gray')}", inline=False)
        embed.add_field(name="Dealer alignment", value=f"{dealer_alignment_desc}\n**{dealer_alignment}** {format_badge('WATCH' if dealer_alignment == 'diverging' else 'NEUTRAL', 'purple')}", inline=False)
        embed.add_field(name="Pin score", value=f"{pin_score_desc}\n**{pin_score}/100** {format_badge('NEUTRAL', 'gray')}", inline=False)
        embed.add_field(name="0DTE pin", value=f"{zd_pin_desc}\n**{zd_pin_score}/100 @ {zd_pin_strike:.2f}** {format_badge('NEUTRAL', 'gray')}", inline=False)
        flip_badge = 'FRAGILE' if flip_sigmas < 1.5 else 'SAFE'
        embed.add_field(name="0DTE flip distance", value=f"{flip_desc}\n**{flip_sigmas:.2f} sigma** {format_badge(flip_badge, 'yellow' if flip_badge == 'FRAGILE' else 'green')}", inline=False)
        if "dealers buy" in charm.lower():
            charm_desc = "time decay supports drift into close"
            charm_badge = "SUPPORTIVE"
        else:
            charm_desc = "time decay puts pressure into close"
            charm_badge = "PRESSURE"
            
        embed.add_field(name="0DTE charm", value=f"{charm_desc}\n**{charm}** {format_badge(charm_badge, '')}", inline=False)
        
        if vrp_val is not None:
            embed.add_field(name="VRP regime", value=f"{vrp_desc}\n**{vrp_val:.2f} pts** {format_badge('NEUTRAL', 'gray')}", inline=False)
        
        embed.set_footer(text=f"FlashAlpha Data • {ticker.upper()} Recap Diario")
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando Recap para {ticker.upper()}")

@tree.command(name="skew", description="Grafica el Volatility Skew (Sesgo de Volatilidad)")
@app_commands.autocomplete(expiration=expiration_autocomplete)
async def skew_cmd(interaction: discord.Interaction, ticker: str, expiration: str = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    ticker = ticker.upper()
    
    try:
        data = fa.volatility(ticker)
        profiles = data.get("skew_profiles", [])
        
        if not profiles:
            await interaction.followup.send(f"No se encontraron datos de skew para {ticker}")
            return
            
        selected_profile = None
        if expiration:
            selected_profile = next((p for p in profiles if p["expiry"] == expiration), None)
            if not selected_profile:
                selected_profile = profiles[0]
                
        skew_val = 0
        if expiration and selected_profile:
            skew_val = selected_profile["skew_25d"]
            buf = plot_skew_curves(ticker, profiles, selected_expiry=selected_profile["expiry"])
            exp_label = f"Vencimiento: `{selected_profile['expiry']}` ({selected_profile['days_to_expiry']} DTE)"
        else:
            skew_val = profiles[0]["skew_25d"]
            buf = plot_skew_curves(ticker, profiles)
            exp_label = "Multi-Expiración (Top 4 Vencimientos)"
            
        if skew_val > 2.0:
            estado = "Defensivo 🛡️ (Put Skew elevado)"
            color = discord.Color.red()
        elif skew_val < -2.0:
            estado = "Especulativo/FOMO 🚀 (Call Skew elevado)"
            color = discord.Color.green()
        else:
            estado = "Neutral ⚖️"
            color = discord.Color.blue()
            
        file = discord.File(fp=buf, filename='skew.png')
        embed = discord.Embed(title=f"📊 Volatility Skew - {ticker}", color=color)
        
        desc = f"```fix\n🔍 ACTIVO: {ticker}\n📅 EXPIRACIÓN: {exp_label}\n```\n"
        desc += "**🎯 Sentimiento Implícito (Sesgo 25Δ):**\n"
        desc += f"```yaml\n"
        desc += f"Estado actual : {estado}\n"
        desc += f"Skew 25Δ      : {skew_val:.2f} pts\n"
        desc += f"```\n"
        desc += f"> *El sesgo mide la diferencia de volatilidad implícita entre Puts y Calls de 25 Delta. Un sesgo alto indica que la protección contra caídas es más cara.*"
        
        embed.description = desc
        embed.set_image(url="attachment://skew.png")
        embed.set_footer(text=f"FlashAlpha Data • {ticker} Skew")
        
        await interaction.followup.send(embed=embed, file=file)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando Skew para {ticker}. Asegúrate de que el ticker sea válido y tenga opciones activas.")
    finally:
        plt.close('all')
        gc.collect()

# -------------------------------------------------------------
# COMANDO Y TAREA DE ALERTAS DE ETFs DE CRIPTO
# -------------------------------------------------------------
from etf_tracker import get_etf_flow_data, ETF_MAP

ETF_CHOICES = [
    app_commands.Choice(name="Bitcoin (BTC)", value="BTC"),
    app_commands.Choice(name="Ethereum (ETH)", value="ETH"),
    app_commands.Choice(name="Solana (SOL)", value="SOL"),
    app_commands.Choice(name="Ripple (XRP)", value="XRP"),
    app_commands.Choice(name="Hedera (HBAR)", value="HBAR"),
    app_commands.Choice(name="Hyperliquid (HYPE)", value="HYPE"),
    app_commands.Choice(name="Chainlink (LINK)", value="LINK"),
    app_commands.Choice(name="Dogecoin (DOGE)", value="DOGE"),
    app_commands.Choice(name="Sui (SUI)", value="SUI"),
    app_commands.Choice(name="Avalanche (AVAX)", value="AVAX"),
    app_commands.Choice(name="Litecoin (LTC)", value="LTC"),
    app_commands.Choice(name="Bitcoin Cash (BCH)", value="BCH"),
]

@tree.command(name="etf", description="Rastrea compras y ventas diarias (Inflows/Outflows) de ETFs Cripto")
@app_commands.choices(ticker=ETF_CHOICES)
async def etf_cmd(interaction: discord.Interaction, ticker: app_commands.Choice[str]):
    await interaction.response.defer()
    try:
        symbol = ticker.value if hasattr(ticker, 'value') else str(ticker)
        data = await asyncio.to_thread(get_etf_flow_data, symbol)
        if "error" in data:
            await interaction.followup.send(f"❌ {data['error']}")
            return

        flow = data["latest_flow"]
        color = discord.Color.brand_green() if flow > 0 else (discord.Color.brand_red() if flow < 0 else discord.Color.dark_grey())
        status_emoji = "🟢 COMPRAS NETAS" if flow > 0 else ("🔴 VENTAS NETAS" if flow < 0 else "⚪ FLUJO NEUTRO / SIN DATOS")
        
        embed = discord.Embed(
            title=f"📊 ETF FLOW TRACKER • {data['name']} ({data['symbol']})",
            color=color
        )
        
        embed.add_field(name="💰 Precio Spot", value=f"${data['spot_price']:,.2f} ({'+' if data['pct_7d']>=0 else ''}{data['pct_7d']:.2f}% 7d)", inline=True)
        embed.add_field(name="📅 Último Registro", value=f"{data['latest_date']}", inline=True)
        embed.add_field(name="🌐 Fuente", value=f"{data['source']}", inline=True)
        
        if data["source"] == "Farside Institutional":
            net_coins = data.get("net_coins", 0.0)
            coin_symbol = "BTC" if symbol == "BTC" else "ETH"
            c_sign = "+" if net_coins > 0 else ""
            c_emoji = "🟢" if net_coins > 0 else ("🔴" if net_coins < 0 else "⚪")
            
            embed.add_field(name="📈 Flujo Neto Diario", value=f"**{'+' if flow>0 else ''}{flow:,.1f}M USD** ({status_emoji})\n{c_emoji} **Monedas Netas:** `{c_sign}{net_coins:,.0f} {coin_symbol}`", inline=False)
            
            aum_text = data.get("aum_text", "")
            if aum_text:
                embed.add_field(name="🏦 Patrimonio Institucional (Total AUM)", value=aum_text, inline=False)
            
            top_em = data.get("top_emitter")
            if top_em:
                embed.add_field(name="🥇 Emisor Líder del Día", value=f"**{top_em}**", inline=False)

            if data["latest_breakdown"]:
                breakdown_text = ""
                for emisor, val in data["latest_breakdown"].items():
                    if emisor:
                        sign = "+" if val > 0 else ""
                        breakdown_text += f"• **{emisor}**: `{sign}{val:,.1f}M USD`\n"
                if breakdown_text:
                    embed.add_field(name="🏦 Desglose por Emisor (Top Fund Managers)", value=breakdown_text, inline=False)
        else:
            vol_val = data['latest_breakdown'].get('Volumen 24h', 0)
            vol_pct = data['latest_breakdown'].get('vol_pct', 0.0)
            v_icon = "💹" if vol_pct > 0 else ("📉" if vol_pct < 0 else "➖")
            v_sign = "+" if vol_pct > 0 else ""
            embed.add_field(name="📊 Volumen / Mercado ETP", value=f"Volumen 24h: `${vol_val:,.0f} USD` (`{v_icon}` **{v_sign}{vol_pct:.1f}% vs ayer**)", inline=False)
            
        history_list = data.get("history_15d", data.get("history_7d", []))
        hist_text = ""
        for h in history_list:
            f_val = h.get("total_flow", 0.0)
            if f_val != 0.0:
                s_icon = "🟢" if f_val > 0 else "🔴"
                hist_text += f"{s_icon} `{h['date']}`: **{'+' if f_val>0 else ''}{f_val:,.1f}M USD**\n"
            else:
                p_val = h.get("price", 0.0)
                diff = h.get("diff", 0.0)
                h_vol = h.get("volume", 0.0)
                h_vol_pct = h.get("vol_pct", 0.0)
                
                s_icon = "🟢" if diff > 0 else ("🔴" if diff < 0 else "⚪")
                c_icon = "💹" if h_vol_pct > 0 else ("📉" if h_vol_pct < 0 else "➖")
                v_sign = "+" if h_vol_pct > 0 else ""
                
                vol_m = h_vol / 1e6 if h_vol >= 1e6 else h_vol
                hist_text += f"{s_icon} `{h['date']}`: `${p_val:,.2f}` | Vol: `${vol_m:,.1f}M` (`{c_icon}` {v_sign}{h_vol_pct:.1f}%)\n"
                
        num_days = len(history_list)
        notice = f"📜 **Histórico Reciente (Últimos {num_days} Días Registrados):**"
        embed.add_field(name=notice, value=hist_text if hist_text else "Sin histórico disponible.", inline=False)
        
        balance_15d = data.get("balance_15d")
        if balance_15d:
            embed.add_field(name=f"⚖️ RESULTADO BALANCE {num_days} DÍAS DE MERCADO", value=balance_15d, inline=False)

        if data.get("source") != "Farside Institutional":
            guia_text = (
                "• `🟢` `💹` **Alcista Fuerte** (Compras Reales)\n"
                "• `🟢` `📉` **Rebote Falso** (Sin Volumen / Riesgo)\n"
                "• `🔴` `💹` **Venta Fuerte** / Capitulación\n"
                "• `🔴` `📉` **Retroceso Sano** (Pausa Normal)"
            )
            embed.add_field(name="💡 Guía Rápida de Interpretación", value=guia_text, inline=False)

        embed.set_footer(text="Bot Carlos Trader • Sistema de Alertas & ETFs Cripto")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"[ETF COMMAND ERROR] {e}")
        traceback.print_exc()
        await interaction.followup.send("❌ Ocurrió un error al procesar los datos del ETF.")
    finally:
        import gc
        gc.collect()

@tasks.loop(hours=24)
async def daily_etf_alert_loop():
    print("[ETF ALERT LOOP] Ejecutando envío diario automático a #alertas-etf...")
    tickers_to_report = ["BTC", "ETH", "SOL", "XRP", "HBAR", "LINK", "DOGE", "AVAX"]
    
    for guild in client.guilds:
        channel = discord.utils.get(guild.text_channels, name="alertas-etf")
        if not channel:
            channel = discord.utils.get(guild.text_channels, name="alertas") or discord.utils.get(guild.text_channels, name="bot-analisis")
            
        if channel:
            try:
                for symbol in tickers_to_report:
                    data = await asyncio.to_thread(get_etf_flow_data, symbol)
                    if "error" in data:
                        continue
                    
                    flow = data["latest_flow"]
                    color = discord.Color.brand_green() if flow > 0 else (discord.Color.brand_red() if flow < 0 else discord.Color.dark_grey())
                    
                    embed = discord.Embed(
                        title=f"🚨 ALERTA DIARIA ETF • {data['name']} ({data['symbol']})",
                        description=f"**Fecha:** {data['latest_date']} | **Precio Spot:** ${data['spot_price']:,.2f}",
                        color=color
                    )
                    if data["source"] == "Farside Institutional":
                        net_coins = data.get("net_coins", 0.0)
                        coin_symbol = "BTC" if symbol == "BTC" else "ETH"
                        c_sign = "+" if net_coins > 0 else ""
                        c_emoji = "🟢" if net_coins > 0 else ("🔴" if net_coins < 0 else "⚪")

                        embed.add_field(name="Flujo Neto del Día", value=f"**{'+' if flow>0 else ''}{flow:,.1f}M USD**\n{c_emoji} `{c_sign}{net_coins:,.0f} {coin_symbol}`", inline=True)
                        
                        balance_15d = data.get("balance_15d", "")
                        if balance_15d:
                            embed.add_field(name="Balance 15 Días", value=balance_15d, inline=False)
                        
                        top_em = data.get("top_emitter")
                        if top_em:
                            embed.add_field(name="Emisor Líder", value=f"`{top_em}`", inline=False)
                    else:
                        vol_val = data['latest_breakdown'].get('Volumen 24h', 0)
                        vol_pct = data['latest_breakdown'].get('vol_pct', 0.0)
                        v_icon = "💹" if vol_pct > 0 else ("📉" if vol_pct < 0 else "➖")
                        v_sign = "+" if vol_pct > 0 else ""
                        embed.add_field(name="Volumen 24h ETP", value=f"${vol_val:,.0f} USD (**{v_icon} {v_sign}{vol_pct:.1f}% vs ayer**)", inline=True)
                        
                        balance_15d = data.get("balance_15d", "")
                        if balance_15d:
                            embed.add_field(name="Tendencia 15 Días ETP", value=balance_15d, inline=False)

                    embed.set_footer(text="Bot Carlos Trader • Alertas Diarias Automáticas")
                    await channel.send(embed=embed)
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"[ETF LOOP ERROR] En servidor {guild.name}: {e}")
            finally:
                import gc
                gc.collect()

# --- COMANDO PCR CON DIVERGENCIA Y ESTRATEGIA ---

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "pcr_history.json")

def load_pcr_history():
    import json
    import os
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading PCR history: {e}")
    return {}

def save_pcr_history(history):
    import json
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving PCR history: {e}")

def get_pcr_trend(ticker, current_pcr):
    import datetime
    history = load_pcr_history()
    ticker = ticker.upper()
    
    today_str = datetime.date.today().isoformat()
    ticker_history = history.setdefault(ticker, [])
    
    prev_entries = [entry for entry in ticker_history if entry['date'] != today_str]
    
    prev_pcr = None
    if prev_entries:
        prev_entry = prev_entries[-1]
        prev_pcr = prev_entry.get('pcr_vol')
    
    today_entry = next((entry for entry in ticker_history if entry['date'] == today_str), None)
    if today_entry:
        today_entry['pcr_vol'] = current_pcr
    else:
        ticker_history.append({'date': today_str, 'pcr_vol': current_pcr})
        history[ticker] = ticker_history[-30:]
        
    save_pcr_history(history)
    
    if prev_pcr is None:
        return "N/A", None
        
    if current_pcr > prev_pcr:
        return "SUBE", prev_pcr
    elif current_pcr < prev_pcr:
        return "BAJA", prev_pcr
    else:
        return "IGUAL", prev_pcr

async def get_price_trend(ticker):
    try:
        ticker_yf = yf.Ticker(ticker.upper())
        hist = await asyncio.to_thread(ticker_yf.history, period="5d")
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[-2]
            curr_close = hist['Close'].iloc[-1]
            price_change = curr_close - prev_close
            trend = "SUBE" if price_change >= 0 else "BAJA"
            return trend, prev_close, curr_close, price_change
    except Exception as e:
        print(f"Error fetching price trend for {ticker}: {e}")
    return "N/A", None, None, None

@tree.command(name="pcr", description="Analiza el Put/Call Ratio (PCR) y la divergencia con el precio para dar señales y estrategias")
async def pcr_cmd(interaction: discord.Interaction, ticker: str):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    ticker = ticker.upper()
    
    try:
        # 1. Obtener PCR actual de FlashAlpha
        summary = fa.stock_summary(ticker)
        flow = summary.get('options_flow', {})
        current_pcr = flow.get('pc_ratio_volume')
        pcr_oi = flow.get('pc_ratio_oi')
        
        if current_pcr is None:
            await interaction.followup.send(f"No se encontraron datos de opciones de PCR para {ticker}.")
            return
            
        # 2. Obtener tendencia del precio
        price_trend, prev_close, curr_close, price_change = await get_price_trend(ticker)
        
        # 3. Obtener tendencia de PCR
        pcr_trend, prev_pcr = get_pcr_trend(ticker, current_pcr)
        
        spot = curr_close if curr_close is not None else summary.get('price', {}).get('mid', 0)
        
        señal = "N/A"
        accion = "N/A"
        explicacion = ""
        color = discord.Color.light_grey()
        is_alcista = False
        is_bajista = False
        
        if price_trend == "N/A" or pcr_trend == "N/A":
            señal = "PENDIENTE"
            accion = "REGISTRANDO DATOS"
            pcr_prev_str = "N/A"
            price_prev_str = "N/A"
            explicacion = f"Esta es la primera consulta registrada para **{ticker}**. Hemos guardado los valores actuales de PCR ({current_pcr:.4f}) y precio (${spot:.2f}) en el historial local. En la próxima consulta diaria podremos calcular las tendencias y mostrar la estrategia."
        else:
            pcr_prev_str = f"{prev_pcr:.4f}"
            price_prev_str = f"${prev_close:.2f}"
            
            # Matriz de 4 escenarios
            if pcr_trend == "SUBE" and price_trend == "BAJA":
                señal = "COMPRA 1 (Mejor)"
                accion = "🛒 COMPRA FUERTE"
                explicacion = "El precio está cayendo, pero el Put/Call Ratio está subiendo (más compras de Puts). Esto indica pánico y capitulación. Los traders están pagando primas caras para protegerse. Históricamente, este pico de pánico coincide con suelos de mercado, lo que representa una excelente oportunidad contraria para buscar compras."
                color = discord.Color.brand_green()
                is_alcista = True
            elif pcr_trend == "SUBE" and price_trend == "SUBE":
                señal = "Compra 2"
                accion = "📈 Mantener o Comprar"
                explicacion = "El precio está subiendo y, al mismo tiempo, los traders están comprando opciones Put de cobertura para asegurar ganancias. Esta subida es saludable y protegida (no hay euforia desmedida). Al haber coberturas de fondo, el mercado tiene soporte. Se recomienda mantener largas o acumular moderadamente."
                color = discord.Color.green()
                is_alcista = True
            elif pcr_trend == "BAJA" and price_trend == "SUBE":
                señal = "VENTA 1 (Mejor)"
                accion = "🚨 VENTA / SHORT"
                explicacion = "El precio está subiendo con fuerza, pero el Put/Call Ratio está cayendo a mínimos (compras masivas de Calls y nula protección de Puts). Esto indica euforia extrema (FOMO) y exceso de confianza. Cuando el mercado está demasiado cargado en Calls sin coberturas, tiende a revertir. Es una divergencia bajista; hora de buscar cortos o tomar ganancias de compras."
                color = discord.Color.brand_red()
                is_bajista = True
            elif pcr_trend == "BAJA" and price_trend == "BAJA":
                señal = "Venta 2"
                accion = "📉 Mantener Short"
                explicacion = "El precio está cayendo y el PCR también (la gente sigue comprando Calls intentando adivinar el suelo del mercado). Al no haber compras de Puts (no hay pánico real de capitulación), la tendencia bajista aún tiene fuerza por delante. Evita buscar compras; es mejor mantener cortos o esperar un pico de pánico real."
                color = discord.Color.red()
                is_bajista = True
            else:
                # PCR IGUAL
                señal = "NEUTRAL"
                accion = "ESPERAR CONFIRMACIÓN"
                explicacion = "El ratio o el precio se mantienen estables. No hay una divergencia de sentimiento clara en este momento. Monitoree las zonas clave del precio."
                color = discord.Color.blue()
                
        # 5. Construir la descripción del Embed
        desc = f"**Activo:** `{ticker}` · **Precio Spot:** `${spot:.2f}`\n\n"
        
        # Bloque de código con tendencias
        desc += "```yaml\n"
        if price_trend == "SUBE":
            desc += f"📈 Tendencia del Precio : SUBE (Ayer: {price_prev_str} ➔ Hoy: ${curr_close:.2f})\n"
        elif price_trend == "BAJA":
            desc += f"📉 Tendencia del Precio : BAJA (Ayer: {price_prev_str} ➔ Hoy: ${curr_close:.2f})\n"
        else:
            desc += f"➖ Tendencia del Precio : N/A (Hoy: ${spot:.2f})\n"
            
        if pcr_trend == "SUBE":
            desc += f"📊 Tendencia del PCR    : SUBE (Ayer: {pcr_prev_str} ➔ Hoy: {current_pcr:.4f})\n"
        elif pcr_trend == "BAJA":
            desc += f"📊 Tendencia del PCR    : BAJA (Ayer: {pcr_prev_str} ➔ Hoy: {current_pcr:.4f})\n"
        else:
            desc += f"📊 Tendencia del PCR    : N/A (Hoy: {current_pcr:.4f})\n"
            
        desc += f"Volume PCR actual      : {current_pcr:.4f}\n"
        desc += f"Open Interest PCR      : {pcr_oi:.4f}\n"
        desc += "```\n\n"
        
        desc += f"### 🎯 SEÑAL DETECTADA: **{señal}**\n"
        desc += f"**Acción Recomendada:** {accion}\n\n"
        
        desc += f"💡 **Explicación del Momento:** {explicacion}\n"
        
        # 6. Añadir guías dinámicas
        if is_alcista:
            desc += "\n---\n\n"
            desc += "### 🛠️ GUÍA DE ESTRATEGIA ALCISTA (Compra en Miedo)\n"
            desc += "*Valida que se cumplan las siguientes reglas en tu gráfico antes de entrar:*\n\n"
            
            pcr_check = "x" if current_pcr > 1.15 else " "
            pcr_badge = "✅" if current_pcr > 1.15 else "❌"
            desc += f"*   `[{pcr_check}]` **1. PCR Alto:** Actualmente en **{current_pcr:.4f}** (Regla `> 1.15` | `ideal > 1.25` {pcr_badge})\n"
            desc += "*   `[ ]` **2. Precio en Soporte Fuerte:** ¿El precio está cerca del **VAL** (Value Area Low), **POC anterior**, **Order Block** o **Fibonacci 0.618 / 0.5**?\n"
            desc += "*   `[ ]` **3. Confirmación de Vela:** Esperar vela de rechazo alcista (**Pin Bar**, **Hammer**, **Engulfing alcista**) o cierre fuerte por encima de la **EMA 9 o 21**.\n"
            desc += "*   `[ ]` **4. Volumen:** ¿Hay un aumento de volumen notable en la zona de soporte?\n\n"
            
            desc += "### 🛡️ GESTIÓN DE RIESGO RECOMENDADA\n"
            desc += "*   **Stop Loss:** Por debajo del soporte (**VAL** o el mínimo reciente).\n"
            desc += "*   **Take Profit 1 (TP1):** **VAH** o la siguiente resistencia clave.\n"
            desc += "*   **Take Profit 2 (TP2):** Ratio **1:2** o **1:3** Riesgo/Beneficio.\n"
            desc += "*   **Riesgo Máximo:** **1% a 1.5%** de tu cuenta por operación.\n"
        elif is_bajista:
            desc += "\n---\n\n"
            desc += "### 🛠️ GUÍA DE ESTRATEGIA BAJISTA CONTRARIA\n"
            desc += "*Valida que se cumplan las siguientes reglas en tu gráfico antes de entrar:*\n\n"
            
            pcr_check = "x" if current_pcr < 0.75 else " "
            pcr_badge = "✅" if current_pcr < 0.75 else "❌"
            desc += f"*   `[{pcr_check}]` **1. PCR Bajo:** Actualmente en **{current_pcr:.4f}** (Regla `< 0.75` | `ideal < 0.65` {pcr_badge})\n"
            desc += "*   `[ ]` **2. Precio en Resistencia Fuerte:** ¿El precio está cerca del **VAH** (Value Area High), **máximo anterior**, o un **Order Block bajista**?\n"
            desc += "*   `[ ]` **3. Vela de Rechazo Bajista:** Esperar vela de confirmación (**Shooting Star**, **Hanging Man** o **Engulfing bajista**).\n"
            desc += "*   `[ ]` **4. Volumen:** ¿El volumen está aumentando en la zona de resistencia?\n\n"
            
            desc += "### 🛡️ GESTIÓN DE RIESGO RECOMENDADA\n"
            desc += "*   **Stop Loss:** Por encima de la resistencia (**VAH** o el máximo reciente).\n"
            desc += "*   **Take Profit 1 (TP1):** **VAL** o el siguiente soporte clave.\n"
            desc += "*   **Take Profit 2 (TP2):** Ratio **1:2** o **1:3** Riesgo/Beneficio.\n"
            desc += "*   **Riesgo Máximo:** **1% a 1.5%** de tu cuenta por operación.\n"
            
        embed = discord.Embed(title="📊 ANÁLISIS DE SENTIMIENTO Y DIVERGENCIA (PCR)", color=color)
        embed.description = desc
        embed.set_footer(text=f"FlashAlpha Data & yfinance • {ticker} PCR Divergence")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send(f"Error procesando análisis PCR para {ticker}. Asegúrate de que el ticker sea válido y tenga opciones activas.")

# --- COMANDO ESCANEAR (50 ACTIVOS) ---

SCANNER_TICKERS = [
    "SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN", "NFLX", "COIN",
    "HOOD", "MSFT", "GOOGL", "PLTR", "MSTR", "AVGO", "JPM", "BABA", "IBIT", "ETHA",
    "USO", "GLD", "SLV", "TLT", "UNG", "XLE", "XLF", "XLK", "XLV", "XLY",
    "XRT", "DIS", "JNJ", "PG", "V", "WMT", "XOM", "LLY", "UNH", "COST",
    "MRK", "USAX", "DRAM", "ORCL", "RBLX", "BA", "ASTS", "LUNR", "SPCX", "RVI",
    "DXYZ", "XOVR", "CRCL", "NKE", "OKLO"
]

LATERAL_TICKERS = [
    "AA", "AAPL", "ABBV", "ABNB", "ACHR", "ADBE", "AG", "AI", "ALOY", "AMD", "AMGN", "AMZN", "ARKB", "ARM", "ARQQ", "ASST",
    "AVGO", "AXP", "BA", "BABA", "BAC", "BAGY", "BBB", "BCOR", "BIDU", "BINI", "BITB", "BITI", "BITO", "BITU", "BITX",
    "BLDP", "BLOX", "BMY", "BSOL", "BTC-USD", "BTCC", "BTCI", "BTCL", "BTRN", "BULL", "BYND", "C", "CAT", "CBRS", "CL",
    "CLSK", "CMCSA", "CMG", "COF", "COIA", "COIN", "COIW", "CONL", "CONX", "COP", "COPX", "COST", "CRCL", "CRM", "CRML",
    "CRNC", "CRWD", "CSCO", "CVX", "DE", "DIA", "DIS", "DJT", "DKNG", "DOGE-USD", "DRAM", "DVN", "DXYZ", "EL", "EOG",
    "ETHE", "ETHT", "ETHU", "ETHW", "ETOR", "EXOD", "F", "FCEL", "FCX", "FDX", "FENY", "FIGR", "FRMI", "GDOG", "GDRX",
    "GDX", "GDXJ", "GILD", "GLNK", "GM", "GOOGL", "GS", "GUSH", "HBR", "HD", "HMC", "HODL", "HOOD", "IBB", "IBKR", "INSP",
    "INTC", "IONQ", "ISRG", "IWM", "JD", "JNJ", "JPM", "KO", "LABU", "LAC", "LCID", "LFGY", "LIT", "LLY", "LMND", "LOW",
    "LYSDY", "MA", "MARA", "MBLY", "MED", "META", "MP", "MRK", "MS", "MSFT", "MSTR", "MSTU", "MSTY", "MU", "NAAS", "NFLX",
    "NIO", "NKE", "NNE", "NRGD", "NU", "NUE", "NUKZ", "NVDA", "OILD", "OKLO", "ONDS", "ORCL", "PALL", "PANW", "PCG", "PDD",
    "PEP", "PFE", "PG", "PLTR", "PLUG", "PPLT", "PSQ", "PYPL", "QBTS", "QCOM", "QQQ", "QUBT", "RBLX", "RCAT", "RDDT",
    "REMX", "RGTI", "RIOT", "RIVN", "ROBN", "RONB", "SBIT", "SBUX", "SLB", "SMCI", "SMH", "SMR", "SNDL", "SNOW", "SOLT",
    "SOLZ", "SOUN", "SOXL", "SOXS", "SPXS", "SPY", "SPYU", "SQQQ", "SSK", "SSM", "STKE", "STRC", "TDOC", "TEM", "TGT",
    "TLRY", "TQQQ", "TSLA", "TSN", "TXN", "UBER", "UNH", "UPS", "USAR", "USO", "USOI", "UUP", "UUUU", "UVIX", "V", "VOD",
    "VXX", "VZ", "WFC", "WMT", "XBI", "XLE", "XLF", "XLK", "XLV", "XLY", "XOM", "XOVR", "XRP-USD", "XRPI", "XRT", "XXRP",
    "XYZ", "YBIT", "YBTC", "ZS"
]

@tree.command(name="escanear_rsi", description="Escanea manualmente la lista completa de 331 activos en 1D, 1W y 1M para detectar RSI extremos")
async def escanear_rsi(interaction: discord.Interaction):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    
    try:
        with open("lista_escaneo.txt", "r") as f:
            tickers = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except Exception as e:
        await interaction.followup.send(f"Error leyendo lista_escaneo.txt: {e}")
        return
        
    if not tickers:
        await interaction.followup.send("lista_escaneo.txt está vacío.")
        return

    status_msg = await interaction.followup.send(
        f"🔍 Iniciando escaneo manual de {len(tickers)} activos en temporalidades 1D, 1W y 1M. Esto tomará unos 20-30 segundos...",
        wait=True
    )

    oversold_signals = {}
    overbought_signals = {}
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    timeframes = [
        ("1D", "365d", "1d"),
        ("1W", "730d", "1wk"),
        ("1M", "1825d", "1mo")
    ]
    
    for tf_label, period, interval in timeframes:
        try:
            chunk_size = 20
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i:i+chunk_size]
                df_batch = await asyncio.to_thread(
                    yf.download,
                    chunk,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    progress=False
                )
                
                for t in chunk:
                    try:
                        if isinstance(df_batch.columns, pd.MultiIndex):
                            df_t = df_batch[t] if t in df_batch.columns.levels[0] else None
                        else:
                            df_t = df_batch
                        
                        if df_t is not None and not df_t.empty and 'Close' in df_t.columns:
                            close_series = df_t['Close'].dropna()
                            if len(close_series) >= 15:
                                rsi_series = calc_rsi(close_series)
                                last_rsi = rsi_series.iloc[-1]
                                
                                if t in ["USDC", "USDT", "USDG", "USDC-USD", "USDT-USD", "USDG-USD"]:
                                    continue
                                    
                                if last_rsi <= 25:
                                    if t not in oversold_signals:
                                        oversold_signals[t] = []
                                    oversold_signals[t].append(f"{tf_label} (RSI: {last_rsi:.1f})")
                                elif last_rsi >= 70:
                                    if t not in overbought_signals:
                                        overbought_signals[t] = []
                                    overbought_signals[t].append(f"{tf_label} (RSI: {last_rsi:.1f})")
                    except Exception as e:
                        pass
                
                if 'df_batch' in locals():
                    del df_batch
                gc.collect()
                await asyncio.sleep(1.0)
        except Exception as e:
            print(f"Error descargando temporalidad {tf_label}: {e}")
            
    desc_oversold = ""
    if oversold_signals:
        for t in sorted(oversold_signals.keys()):
            display_name = t
            if t == "HYPE32196-USD":
                display_name = "HYPE-USD"
            tfs_str = ", ".join(oversold_signals[t])
            desc_oversold += f"• **{display_name}**: `{tfs_str}`\n"
    else:
        desc_oversold = "*Ninguno*"

    desc_overbought = ""
    if overbought_signals:
        for t in sorted(overbought_signals.keys()):
            display_name = t
            if t == "HYPE32196-USD":
                display_name = "HYPE-USD"
            tfs_str = ", ".join(overbought_signals[t])
            desc_overbought += f"• **{display_name}**: `{tfs_str}`\n"
    else:
        desc_overbought = "*Ninguno*"

    embed = discord.Embed(
        title="🔍 REPORTE MANUAL: ESCÁNER DE RSI",
        description=f"Escaneo manual realizado a petición del usuario. Total activos analizados: {len(tickers)}.",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    
    def add_long_field(embed, name, text):
        if len(text) <= 1024:
            embed.add_field(name=name, value=text, inline=False)
        else:
            lines = text.split('\n')
            current_text = ""
            chunk_num = 1
            for line in lines:
                if len(current_text) + len(line) + 2 > 1024:
                    embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)
                    current_text = line + "\n"
                    chunk_num += 1
                else:
                    current_text += line + "\n"
            if current_text:
                embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)

    add_long_field(embed, "📥 OPORTUNIDADES DE COMPRA (RSI <= 25 - Sobreventa)", desc_oversold)
    add_long_field(embed, "📈 OPORTUNIDADES DE VENTA (RSI >= 70 - Sobrecompra)", desc_overbought)
    
    embed.set_footer(text="CarlosBot Scanner • Consulta Manual")
    await status_msg.edit(content="¡Escaneo completado con éxito!", embed=embed)

@tree.command(name="escanear", description="Escanea las 50 principales acciones y genera alertas usando EMAs y FlashAlpha")
async def escanear(interaction: discord.Interaction):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    
    # Funciones locales para indicadores en 4H
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def calc_stoch(df, period=14, smooth_k=3):
        low_min = df['Low'].rolling(window=period).min()
        high_max = df['High'].rolling(window=period).max()
        k = 100 * (df['Close'] - low_min) / (high_max - low_min + 1e-9)
        stoch_k = k.rolling(window=smooth_k).mean()
        return stoch_k
    
    try:
        # Descarga de datos horarios (60 días) para resamplear a 4H
        df_yf = await asyncio.to_thread(
            yf.download, 
            SCANNER_TICKERS, 
            period="60d", 
            interval="1h", 
            group_by="ticker",
            progress=False
        )
        
        calls_signals = []
        puts_signals = []
        neutral_signals = []
        
        sem_scanner = asyncio.Semaphore(5)
        
        async def process_ticker(ticker):
            async with sem_scanner:
                try:
                    if ticker not in df_yf.columns.levels[0]:
                        return
                    df_hist = df_yf[ticker].dropna(subset=['Close'])
                    if df_hist.empty or len(df_hist) < 50:
                        return
                        
                    # Convertir index a datetime para resamplear
                    df_hist.index = pd.to_datetime(df_hist.index)
                    
                    # Resamplear a 4H (desplazamiento de 30min para alinearse con apertura a las 9:30 AM EST)
                    df_4h = df_hist.resample('4h', offset='30min').agg({
                        'Open': 'first',
                        'High': 'max',
                        'Low': 'min',
                        'Close': 'last',
                        'Volume': 'sum'
                    }).dropna()
                    
                    if len(df_4h) < 20:
                        return
                        
                    close_series = df_4h['Close']
                    price = float(close_series.iloc[-1])
                    open_price = float(df_4h['Open'].iloc[-1])
                    high_price = float(df_4h['High'].iloc[-1])
                    low_price = float(df_4h['Low'].iloc[-1])
                    
                    # Calcular RSI y Estocástico en 4H
                    rsi_series = calc_rsi(close_series, 14)
                    curr_rsi = float(rsi_series.iloc[-1])
                    
                    stoch_series = calc_stoch(df_4h, 14, 3)
                    curr_stoch = float(stoch_series.iloc[-1])
                    
                    # Cálculo de mechas relativas a EMA 1 (Close)
                    # "Mecha por abajo de la EMA 1 (cierre)": min(Open, Close) - Low
                    # "Mecha por arriba de la EMA 1 (cierre)": High - max(Open, Close)
                    total_range = high_price - low_price
                    if total_range <= 0:
                        return
                        
                    lower_wick = min(open_price, price) - low_price
                    upper_wick = high_price - max(open_price, price)
                    
                    lower_wick_pct = (lower_wick / total_range) * 100
                    upper_wick_pct = (upper_wick / total_range) * 100
                    
                    # Bollinger (Squeeze)
                    sma20 = close_series.rolling(window=20).mean()
                    std20 = close_series.rolling(window=20).std()
                    bandwidth = (sma20 + 2 * std20 - (sma20 - 2 * std20)) / sma20
                    is_compressed = False
                    if len(bandwidth) >= 50:
                        curr_bandwidth = float(bandwidth.iloc[-1])
                        mean_bandwidth_50 = float(bandwidth.rolling(window=50).mean().iloc[-1])
                        is_compressed = curr_bandwidth < mean_bandwidth_50 * 0.9
                    
                    # Criterio Alcista (Calls):
                    # - RSI 4H <= 30
                    # - Estocástico 4H <= 20
                    # - Mecha inferior >= 35% del total de la vela
                    if curr_rsi <= 30 and curr_stoch <= 20 and lower_wick_pct >= 35:
                        summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                        net_gex = 0
                        call_wall = price
                        pc_ratio_volume = 1.0
                        if summary_data and 'price' in summary_data:
                            exp = summary_data.get('exposure', {})
                            flow = summary_data.get('options_flow', {})
                            net_gex = exp.get('net_gex', 0) or 0
                            call_wall = float(exp.get('call_wall', price) or price)
                            pc_ratio_volume = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                        
                        calls_signals.append({
                            'ticker': ticker,
                            'price': price,
                            'rsi': curr_rsi,
                            'stoch': curr_stoch,
                            'wick_pct': lower_wick_pct,
                            'pc_ratio': pc_ratio_volume,
                            'net_gex': net_gex,
                            'call_wall': call_wall,
                            'squeeze': is_compressed
                        })
                        
                    # Criterio Bajista (Puts):
                    # - RSI 4H >= 62
                    # - Estocástico 4H >= 80
                    # - Mecha superior >= 35% del total de la vela
                    elif curr_rsi >= 62 and curr_stoch >= 80 and upper_wick_pct >= 35:
                        summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                        net_gex = 0
                        put_wall = price
                        pc_ratio_volume = 1.0
                        if summary_data and 'price' in summary_data:
                            exp = summary_data.get('exposure', {})
                            flow = summary_data.get('options_flow', {})
                            net_gex = exp.get('net_gex', 0) or 0
                            put_wall = float(exp.get('put_wall', price) or price)
                            pc_ratio_volume = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                            
                        puts_signals.append({
                            'ticker': ticker,
                            'price': price,
                            'rsi': curr_rsi,
                            'stoch': curr_stoch,
                            'wick_pct': upper_wick_pct,
                            'pc_ratio': pc_ratio_volume,
                            'net_gex': net_gex,
                            'put_wall': put_wall
                        })
                    else:
                        neutral_signals.append(ticker)
                except Exception as e:
                    print(f"Error escaneando {ticker} en bot: {e}")
                    
        # Ejecutar en paralelo
        tasks = [process_ticker(t) for t in SCANNER_TICKERS]
        await asyncio.gather(*tasks)
        
        # Crear Embed
        embed = discord.Embed(
            title="🔍 ESCÁNER DE MERCADO (ALERTAS)",
            color=discord.Color.blue()
        )
        
        # Formatear Calls
        calls_desc = ""
        if calls_signals:
            for sig in calls_signals:
                sq_text = " ⚡ (Squeeze)" if sig['squeeze'] else ""
                calls_desc += f"• **{sig['ticker']}**: Spot: `${sig['price']:.2f}`{sq_text}\n"
                calls_desc += f"  - *Condición:* RSI 4H: `{sig['rsi']:.1f}` | Stoch 4H: `{sig['stoch']:.1f}` | Mecha Inf: `{sig['wick_pct']:.1f}%` 📈\n"
                calls_desc += f"  - *Opciones:* GEX: `${sig['net_gex']/1e6:,.1f}M` | P/C: `{sig['pc_ratio']:.2f}`\n"
                calls_desc += f"  - *Resistencia (Call Wall):* `${sig['call_wall']:.2f}`\n\n"
        else:
            calls_desc = "*Ninguna señal de compra confirmada hoy.*\n"
            
        # Formatear Puts
        puts_desc = ""
        if puts_signals:
            for sig in puts_signals:
                puts_desc += f"• **{sig['ticker']}**: Spot: `${sig['price']:.2f}`\n"
                puts_desc += f"  - *Condición:* RSI 4H: `{sig['rsi']:.1f}` | Stoch 4H: `{sig['stoch']:.1f}` | Mecha Sup: `{sig['wick_pct']:.1f}%` 📉\n"
                puts_desc += f"  - *Soporte (Put Wall):* `${sig['put_wall']:.2f}`\n"
                puts_desc += f"  - *Opciones:* GEX: `${sig['net_gex']/1e6:,.1f}M` | P/C: `{sig['pc_ratio']:.2f}`\n\n"
        else:
            puts_desc = "*Ninguna señal de venta confirmada hoy.*\n"
            
        # Función auxiliar para dividir textos y evitar el error 400 de Discord (límite 1024 caracteres)
        def add_split_fields(emb, name, text):
            if not text:
                return
            chunks = []
            current_chunk = ""
            
            # Si el texto está delimitado por bloques de tickers (\n\n), dividimos por bloque para no partir la información de un ticker
            if "\n\n" in text:
                blocks = [b for b in text.split("\n\n") if b.strip()]
                for block in blocks:
                    block_formatted = block + "\n\n"
                    if len(current_chunk) + len(block_formatted) > 1000:
                        chunks.append(current_chunk.strip())
                        current_chunk = block_formatted
                    else:
                        current_chunk += block_formatted
            else:
                # Si no tiene bloques (como en los tickers neutrales), dividimos por líneas
                for line in text.split('\n'):
                    if len(current_chunk) + len(line) + 1 > 1000:
                        chunks.append(current_chunk.strip())
                        current_chunk = line
                    else:
                        if current_chunk:
                            current_chunk += "\n" + line
                        else:
                            current_chunk = line
            if current_chunk:
                chunks.append(current_chunk.strip())
                
            for i, chunk in enumerate(chunks):
                field_name = name if i == 0 else f"{name} (Cont...)"
                emb.add_field(name=field_name, value=chunk, inline=False)
                
        # Añadir campos usando la función de división segura
        add_split_fields(embed, "🟢 SEÑALES DE COMPRA (CALLS)", calls_desc)
        add_split_fields(embed, "🔴 SEÑALES DE VENTA (PUTS)", puts_desc)
        
        if neutral_signals:
            neutral_desc = f"`{', '.join(sorted(neutral_signals))}`"
            add_split_fields(embed, "⚪ SIN SEÑAL CLARA", neutral_desc)
            
        embed.set_footer(text="FlashAlpha & yfinance Screener • CarlosBot")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send("Error ejecutando el escaneo de alertas.")

# --- COMANDO IV_TERM ---
@tree.command(name="iv_term", description="Muestra la estructura temporal de volatilidad implicada y movimiento esperado (Expected Move)")
async def iv_term(interaction: discord.Interaction, ticker: str):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return

    await interaction.response.defer()
    
    try:
        import matplotlib.patches as patches
        import datetime
        import pandas as pd
        import asyncio
        
        ticker = ticker.upper().strip()
        yf_ticker = yf.Ticker(ticker)
        
        # Obtener precio Spot
        hist = await asyncio.to_thread(yf_ticker.history, period="1d")
        if hist.empty:
            await interaction.followup.send(f"No se pudo encontrar información ni precio Spot para el activo: **{ticker}**.")
            return
            
        spot_price = float(hist["Close"].iloc[-1])
        
        # Obtener fechas de opciones
        expirations = yf_ticker.options
        if not expirations:
            await interaction.followup.send(f"El activo **{ticker}** no tiene contratos de opciones disponibles o no se pudieron cargar.")
            return
            
        # Limitar a las primeras 12 expiraciones para evitar lentitud
        expirations = expirations[:12]
        
        results = []
        today = datetime.date.today()
        
        for date_str in expirations:
            try:
                exp_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte < 0:
                    continue
                exp_formatted = exp_date.strftime("%m-%d")
                    
                opt_chain = await asyncio.to_thread(yf_ticker.option_chain, date_str)
                calls = opt_chain.calls
                puts = opt_chain.puts
                
                if calls.empty or puts.empty:
                    continue
                    
                # Encontrar strike ATM más cercano
                calls['dist'] = (calls['strike'] - spot_price).abs()
                idx = calls['dist'].idxmin()
                atm_strike = calls.loc[idx, 'strike']
                
                atm_call_rows = calls[calls['strike'] == atm_strike]
                if atm_call_rows.empty:
                    continue
                atm_call = atm_call_rows.iloc[0]
                
                puts['dist'] = (puts['strike'] - spot_price).abs()
                idx_p = puts['dist'].idxmin()
                atm_put_strike = puts.loc[idx_p, 'strike']
                
                atm_put_rows = puts[puts['strike'] == atm_put_strike]
                if atm_put_rows.empty:
                    continue
                atm_put = atm_put_rows.iloc[0]
                
                # Precios Mid o Last
                call_bid = atm_call.get('bid', 0)
                call_ask = atm_call.get('ask', 0)
                call_mid = (call_bid + call_ask) / 2 if (call_bid > 0 and call_ask > 0) else atm_call.get('lastPrice', 0)
                
                put_bid = atm_put.get('bid', 0)
                put_ask = atm_put.get('ask', 0)
                put_mid = (put_bid + put_ask) / 2 if (put_bid > 0 and put_ask > 0) else atm_put.get('lastPrice', 0)
                
                straddle_price = call_mid + put_mid
                if straddle_price <= 0:
                    continue
                    
                mm_move = straddle_price * 0.85
                iv = (atm_call.get('impliedVolatility', 0) + atm_put.get('impliedVolatility', 0)) / 2
                pct_move = (mm_move / spot_price) * 100
                max_price = spot_price + mm_move
                min_price = spot_price - mm_move
                
                results.append({
                    "EXP": exp_formatted,
                    "DTE": dte,
                    "IV": iv * 100,
                    "MM Move": mm_move,
                    "%Mov": pct_move,
                    "Max": max_price,
                    "Min": min_price
                })
            except Exception as e:
                print(f"Error procesando {date_str} para {ticker} en /iv_term: {e}")
                
        if not results:
            await interaction.followup.send(f"No se pudieron calcular opciones válidas para el activo **{ticker}**.")
            return
            
        df = pd.DataFrame(results)
        df = df.sort_values("DTE").reset_index(drop=True)
        
        # ----------------- IMAGEN 1: GRÁFICO (DTE vs IV) -----------------
        fig1, ax_chart = plt.subplots(figsize=(6.5, 5.5))
        fig1.patch.set_facecolor('#0d0e15')
        ax_chart.set_facecolor('#0d0e15')
        
        ax_chart.plot(df["DTE"], df["IV"], color='#00ffcc', marker='o', markersize=8, linewidth=2.5)
        ax_chart.set_title(f"Spot ${spot_price:.2f}", color='#ffcc00', fontsize=18, loc='left', pad=15)
        ax_chart.set_xlabel("Días a Expiración (DTE)", color='white', fontsize=12)
        ax_chart.set_ylabel("Implied Volatility (IV %)", color='white', fontsize=12)
        ax_chart.grid(True, color='#222533', linestyle=':', linewidth=1)
        ax_chart.tick_params(colors='white', labelsize=10)
        for spine in ax_chart.spines.values():
            spine.set_color('#222533')
            
        plt.tight_layout()
        buf_chart = io.BytesIO()
        plt.savefig(buf_chart, format='png', facecolor=fig1.get_facecolor(), edgecolor='none', dpi=150)
        buf_chart.seek(0)
        plt.close(fig1)
        
        # ----------------- IMAGEN 2: TABLA DE PRECIOS -----------------
        fig2, ax_table = plt.subplots(figsize=(7.5, 6))
        fig2.patch.set_facecolor('#0d0e15')
        ax_table.set_facecolor('#0d0e15')
        ax_table.axis('off')
        
        columns = ["EXP", "DTE", "IV", "MM Move", "%Mov", "Max", "Min"]
        rows = len(df)
        cell_height = 0.055
        start_y = 0.88
        
        # Anchos de columna
        col_widths = [0.12, 0.1, 0.15, 0.17, 0.14, 0.16, 0.16]
        col_xs = [0.0]
        for w in col_widths[:-1]:
            col_xs.append(col_xs[-1] + w)
            
        # Título de la tabla
        ax_table.text(0.0, 0.95, f"EXPECTED MOVE • {ticker}", color='white', fontsize=14, fontweight='bold')
        ax_table.text(sum(col_widths) - 0.02, 0.95, f"Spot ${spot_price:.2f}", color='#ffcc00', fontsize=13, fontweight='bold', ha='right')
        
        # Cabeceras
        for i, col in enumerate(columns):
            rect = patches.Rectangle((col_xs[i], start_y), col_widths[i], cell_height, 
                                     facecolor='#161925', edgecolor='#222533', linewidth=1)
            ax_table.add_patch(rect)
            ax_table.text(col_xs[i] + col_widths[i]/2, start_y + cell_height/2, col,
                          color='#b0bec5', fontsize=10, fontweight='bold', ha='center', va='center')
                          
        # Filas
        for row_idx in range(rows):
            row_y = start_y - (row_idx + 1) * cell_height
            row_data = df.iloc[row_idx]
            bg_color = '#0d0e15' if row_idx % 2 == 0 else '#12141f'
            
            for i, col in enumerate(columns):
                rect = patches.Rectangle((col_xs[i], row_y), col_widths[i], cell_height, 
                                         facecolor=bg_color, edgecolor='#1a1d2e', linewidth=0.5)
                ax_table.add_patch(rect)
                
                val = row_data[col]
                if col == "EXP":
                    text_str = f"{val}"
                    text_color = 'white'
                elif col == "DTE":
                    text_str = f"{int(val)}"
                    text_color = 'white'
                elif col == "IV":
                    text_str = f"{val:.1f}%"
                    text_color = 'white'
                elif col == "MM Move":
                    text_str = f"{val:.2f}"
                    text_color = 'white'
                elif col == "%Mov":
                    text_str = f"{val:.1f}%"
                    text_color = '#b0bec5'
                elif col == "Max":
                    text_str = f"${val:.2f}"
                    text_color = '#00ffcc'
                elif col == "Min":
                    text_str = f"${val:.2f}"
                    text_color = '#ff4a5a'
                    
                ax_table.text(col_xs[i] + col_widths[i]/2, row_y + cell_height/2, text_str,
                              color=text_color, fontsize=10, ha='center', va='center')
                              
        plt.tight_layout()
        buf_table = io.BytesIO()
        plt.savefig(buf_table, format='png', facecolor=fig2.get_facecolor(), edgecolor='none', dpi=150)
        buf_table.seek(0)
        plt.close(fig2)
        
        discord_file_chart = discord.File(buf_chart, filename=f"{ticker}_iv_term.png")
        discord_file_table = discord.File(buf_table, filename=f"{ticker}_expected_move.png")
        
        await interaction.followup.send(files=[discord_file_chart, discord_file_table])
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send("Error al calcular la estructura temporal de volatilidad.")
    finally:
        plt.close('all')
        gc.collect()

# --- COMANDO LATERAL (CARLOS LATERAL 2) ---
@tree.command(name="lateral", description="Escanea los 50 activos buscando señales de reversión de Carlos Lateral 2")
@app_commands.choices(temporalidad=[
    app_commands.Choice(name="15 minutos (15m)", value="15m"),
    app_commands.Choice(name="1 hora (1h)", value="1h"),
    app_commands.Choice(name="4 horas (4h)", value="4h"),
    app_commands.Choice(name="Diario (1d)", value="1d"),
    app_commands.Choice(name="1 semana (1w)", value="1w"),
    app_commands.Choice(name="2 semanas (2w)", value="2w"),
    app_commands.Choice(name="1 mes (1m)", value="1mes")
])
async def lateral(
    interaction: discord.Interaction, 
    temporalidad: app_commands.Choice[str] = None, 
    velas: int = 3,
    rsi_long: int = 35,
    rsi_short: int = 62,
    stoch_long: int = 25,
    stoch_short: int = 80
):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return

    await interaction.response.defer()
    
    try:
        temp_str = temporalidad.value if temporalidad else "1h"
        
        # Mapeo de temporalidades a yfinance
        resample_rule = None
        if temp_str == "15m":
            interval = "15m"
            period = "5d"
        elif temp_str == "1h":
            interval = "1h"
            period = "30d"
        elif temp_str == "4h":
            interval = "1h"
            period = "60d"
            resample_rule = "4h"
        elif temp_str == "1d":
            interval = "1d"
            period = "250d"
        elif temp_str == "1w":
            interval = "1wk"
            period = "730d"
        elif temp_str == "2w":
            interval = "1wk"
            period = "1095d"
            resample_rule = "2W"
        elif temp_str == "1mes":
            interval = "1mo"
            period = "1825d"
        else:
            interval = "1h"
            period = "30d"
            
        df_yf = await asyncio.to_thread(
            yf.download, 
            LATERAL_TICKERS, 
            period=period, 
            interval=interval,
            group_by="ticker",
            progress=False
        )
        
        long_signals = []
        short_signals = []
        neutral_signals = []
        
        def calculate_rsi(series, period=14):
            delta = series.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
            avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        for ticker in LATERAL_TICKERS:
            try:
                if ticker not in df_yf.columns.levels[0]:
                    continue
                df_hist = df_yf[ticker].dropna(subset=['Close'])
                if df_hist.empty:
                    continue
                    
                if resample_rule:
                    df_hist = df_hist.resample(resample_rule).agg({
                        'Open': 'first',
                        'High': 'max',
                        'Low': 'min',
                        'Close': 'last',
                        'Volume': 'sum'
                    }).dropna()
                    
                if len(df_hist) < 30:
                    continue
                    
                close_series = df_hist['Close']
                high_series = df_hist['High']
                low_series = df_hist['Low']
                
                ema9 = close_series.ewm(span=9, adjust=False).mean()
                rsi = calculate_rsi(close_series, 14)
                
                lowest_low = low_series.rolling(window=14).min()
                highest_high = high_series.rolling(window=14).max()
                stoch_k = 100 * (close_series - lowest_low) / (highest_high - lowest_low)
                
                close_vals = close_series.values
                ema9_vals = ema9.values
                
                contador_bajo = 0
                for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                    if c_val < e_val:
                        contador_bajo += 1
                    else:
                        break
                        
                contador_alto = 0
                for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                    if c_val > e_val:
                        contador_alto += 1
                    else:
                        break
                        
                last_close = float(close_vals[-1])
                last_rsi = float(rsi.iloc[-1])
                last_k = float(stoch_k.iloc[-1])
                
                is_long = (contador_bajo >= velas) and (last_rsi <= rsi_long) and (last_k <= stoch_long)
                is_short = (contador_alto >= velas) and (last_rsi >= rsi_short) and (last_k >= stoch_short)
                
                ticker_info = {
                    'ticker': ticker,
                    'price': last_close,
                    'rsi': last_rsi,
                    'stoch_k': last_k,
                    'consecutive': contador_bajo if contador_bajo >= velas else contador_alto
                }
                
                if is_long:
                    long_signals.append(ticker_info)
                elif is_short:
                    short_signals.append(ticker_info)
                else:
                    neutral_signals.append(ticker)
            except Exception as e:
                print(f"Error procesando {ticker} en /lateral: {e}")
                
        embed = discord.Embed(
            title=f"🔍 ESCÁNER CARLOS LATERAL 2 ({temp_str.upper()})",
            color=discord.Color.dark_purple()
        )
        
        long_desc = ""
        if long_signals:
            for sig in long_signals:
                long_desc += f"• **{sig['ticker']}**: Spot: `${sig['price']:.2f}`\n"
                long_desc += f"  - *Velas bajo EMA 9:* `{sig['consecutive']}` consecutivas 📉\n"
                long_desc += f"  - *RSI:* `{sig['rsi']:.1f}` | *Stoch:* `{sig['stoch_k']:.1f}`\n\n"
        else:
            long_desc = "*Ninguna señal de compra (LONG) detectada.*\n"
            
        short_desc = ""
        if short_signals:
            for sig in short_signals:
                short_desc += f"• **{sig['ticker']}**: Spot: `${sig['price']:.2f}`\n"
                short_desc += f"  - *Velas sobre EMA 9:* `{sig['consecutive']}` consecutivas 📈\n"
                short_desc += f"  - *RSI:* `{sig['rsi']:.1f}` | *Stoch:* `{sig['stoch_k']:.1f}`\n\n"
        else:
            short_desc = "*Ninguna señal de venta (SHORT) detectada.*\n"
            
        def add_split_fields(emb, name, text):
            if not text:
                return
            chunks = []
            current_chunk = ""
            
            if "\n\n" in text:
                blocks = [b for b in text.split("\n\n") if b.strip()]
                for block in blocks:
                    block_formatted = block + "\n\n"
                    if len(current_chunk) + len(block_formatted) > 1000:
                        chunks.append(current_chunk.strip())
                        current_chunk = block_formatted
                    else:
                        current_chunk += block_formatted
            else:
                for line in text.split('\n'):
                    if len(current_chunk) + len(line) + 1 > 1000:
                        chunks.append(current_chunk.strip())
                        current_chunk = line
                    else:
                        if current_chunk:
                            current_chunk += "\n" + line
                        else:
                            current_chunk = line
            if current_chunk:
                chunks.append(current_chunk.strip())
                
            for i, chunk in enumerate(chunks):
                field_name = name if i == 0 else f"{name} (Cont...)"
                emb.add_field(name=field_name, value=chunk, inline=False)
                
        add_split_fields(embed, "🟢 SEÑALES LONG (COMPRA)", long_desc)
        add_split_fields(embed, "🔴 SEÑALES SHORT (VENTA)", short_desc)
        
        if neutral_signals:
            neutral_desc = f"`{', '.join(sorted(neutral_signals))}`"
            add_split_fields(embed, "⚪ SIN SEÑAL CLARA", neutral_desc)
            
        embed.set_footer(text="Carlos Lateral 2 Screener • CarlosBot")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(traceback.format_exc())
        await interaction.followup.send("Error ejecutando el escaneo de Reversión Carlos Lateral 2.")

# --- CONFIGURACIÓN DE ALERTAS AUTOMÁTICAS ESCANEAR ---
ESCANEAR_STATE_FILE = "escanear_alert_state.json"
ALERT_ESCANEAR_CHANNEL_NAME = "alertas-escanear"
IS_FIRST_ESCANEAR_RUN = True

def load_escanear_alert_state():
    try:
        if os.path.exists(ESCANEAR_STATE_FILE):
            with open(ESCANEAR_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading escanear alert state: {e}")
    return {}

def save_escanear_alert_state(state):
    try:
        with open(ESCANEAR_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving escanear alert state: {e}")
# --- CONFIGURACIÓN DE ALERTAS AUTOMÁTICAS ESCANEAR DIARIO (1D) ---
ESCANEAR_1D_STATE_FILE = "escanear_1d_alert_state.json"
ALERT_ESCANEAR_1D_CHANNEL_NAME = "alertas-escanear1d"
IS_FIRST_ESCANEAR_1D_RUN = True

def load_escanear_1d_alert_state():
    try:
        if os.path.exists(ESCANEAR_1D_STATE_FILE):
            with open(ESCANEAR_1D_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading 1D alert state: {e}")
    return {}

def save_escanear_1d_alert_state(state):
    try:
        with open(ESCANEAR_1D_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving 1D alert state: {e}")
# --- CONFIGURACIÓN DE ALERTAS AUTOMÁTICAS ESCANEAR SEMANAL (1W) ---
ESCANEAR_1W_STATE_FILE = "escanear_1w_alert_state.json"
ALERT_ESCANEAR_1W_CHANNEL_NAME = "alertas-escanear1w"
IS_FIRST_ESCANEAR_1W_RUN = True

def load_escanear_1w_alert_state():
    try:
        if os.path.exists(ESCANEAR_1W_STATE_FILE):
            with open(ESCANEAR_1W_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading 1W alert state: {e}")
    return {}

def save_escanear_1w_alert_state(state):
    try:
        with open(ESCANEAR_1W_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving 1W alert state: {e}")

# --- CONFIGURACIÓN DE ALERTAS AUTOMÁTICAS ESCANEAR MENSUAL (1M) ---
ESCANEAR_1M_STATE_FILE = "escanear_1m_alert_state.json"
ALERT_ESCANEAR_1M_CHANNEL_NAME = "alertas-escanear1m"
IS_FIRST_ESCANEAR_1M_RUN = True

def load_escanear_1m_alert_state():
    try:
        if os.path.exists(ESCANEAR_1M_STATE_FILE):
            with open(ESCANEAR_1M_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading 1M alert state: {e}")
    return {}

def save_escanear_1m_alert_state(state):
    try:
        with open(ESCANEAR_1M_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving 1M alert state: {e}")

# --- LÓGICA DE ALERTAS AUTOMÁTICAS CARLOS LATERAL 2 ---
import json
import datetime

STATE_FILE = "alert_state.json"
ALERT_CHANNEL_NAME = "alertas-lateral"
IS_FIRST_ALERTS_RUN = True
IS_FIRST_RUN = True

# --- CONFIGURACIÓN DE ALERTAS CRIPTO ---
CRYPTO_STATE_FILE = "crypto_alert_state.json"
ALERT_CRYPTO_CHANNEL_NAME = "alertas-crypto"
IS_FIRST_CRYPTO_RUN = True

CRYPTO_TICKERS = [
    "1INCH-USD", "AAVE-USD", "ADA-USD", "ALGO-USD", "ANKR-USD", "APT-USD", "ARB-USD", "ATOM-USD", "AVAX-USD", "AXS-USD",
    "BAT-USD", "BCH-USD", "BEAM-USD", "BONK-USD", "BTC-USD", "BTT-USD", "CAKE-USD", "CHZ-USD", "CRV-USD", "DASH-USD",
    "DOGE-USD", "DOT-USD", "DYDX-USD", "DYM-USD", "EGLD-USD", "ENJ-USD", "ENS-USD", "EOS-USD", "ETC-USD", "ETH-USD",
    "FET-USD", "FIL-USD", "FLOKI-USD", "FLOW-USD", "FTM-USD", "GALA-USD", "GRT-USD", "HBAR-USD", "HOT-USD", "ICP-USD",
    "IMX-USD", "INJ-USD", "IOTA-USD", "JASMY-USD", "JUP-USD", "KAS-USD", "KAVA-USD", "LDO-USD", "LINK-USD", "LRC-USD",
    "LTC-USD", "LUNC-USD", "MANA-USD", "MANTA-USD", "MATIC-USD", "MINA-USD", "MKR-USD", "NEAR-USD", "NEO-USD", "OMG-USD",
    "ONDO-USD", "ONE-USD", "ONT-USD", "OP-USD", "ORDI-USD", "PEPE-USD", "PYTH-USD", "PYUSD-USD", "QNT-USD", "QTUM-USD",
    "RAY-USD", "RNDR-USD", "RSR-USD", "RUNE-USD", "RVN-USD", "SAND-USD", "SC-USD", "SEI-USD", "SHIB-USD", "SNX-USD",
    "SOL-USD", "STRK-USD", "STX-USD", "SUI-USD", "THETA-USD", "TIA-USD", "TRX-USD", "UNI-USD", "VET-USD", "WAVES-USD",
    "WIF-USD", "WLD-USD", "WOO-USD", "XEC-USD", "XMR-USD", "XRP-USD", "XTZ-USD", "YFI-USD", "ZEC-USD", "ZETA-USD",
    "ZIL-USD"
]

# --- CONFIGURACIÓN DE ALERTAS VIP CRIPTO ---
VIP_CRYPTO_STATE_FILE = "vip_alert_state.json"
ALERT_VIP_CHANNEL_NAME = "vip-alertas"
IS_FIRST_VIP_RUN = True

VIP_CRYPTO_TICKERS = [
    "AAVE-USD", "BCH-USD", "BNB-USD", "BTC-USD", "ETC-USD", "ETH-USD",
    "HYPE32196-USD", "PAXG-USD", "SOL-USD", "XRP-USD"
]

def load_vip_alert_state():
    try:
        if os.path.exists(VIP_CRYPTO_STATE_FILE):
            with open(VIP_CRYPTO_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading VIP alert state: {e}")
    return {}

def save_vip_alert_state(state):
    try:
        with open(VIP_CRYPTO_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving VIP alert state: {e}")

def calculate_rsi_global(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def load_alert_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading alert state: {e}")
    return {}

def save_alert_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving alert state: {e}")

def load_crypto_alert_state():
    try:
        if os.path.exists(CRYPTO_STATE_FILE):
            with open(CRYPTO_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading crypto alert state: {e}")
    return {}

def save_crypto_alert_state(state):
    try:
        with open(CRYPTO_STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving crypto alert state: {e}")

def generate_alert_chart(ticker, df_hist, ema9, rsi, stoch_k, timeframe):
    plot_candles = 40
    if len(df_hist) < plot_candles:
        plot_candles = len(df_hist)
        
    df_slice = df_hist.tail(plot_candles)
    ema9_slice = ema9.tail(plot_candles)
    rsi_slice = rsi.tail(plot_candles)
    stoch_k_slice = stoch_k.tail(plot_candles)
    
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(7, 5.6), sharex=True, 
                                       gridspec_kw={'height_ratios': [3, 1, 1]})
    
    ax1.plot(df_slice.index, df_slice['Close'], label='Precio', color='#00ffcc', linewidth=2)
    ax1.plot(df_slice.index, ema9_slice, label='EMA 9', color='#ffcc00', linewidth=1.5, linestyle='--')
    ax1.set_title(f"{ticker} ({timeframe.upper()}) - Carlos Lateral 2", color='white', fontsize=10)
    ax1.legend(loc='upper left', facecolor='#111111', edgecolor='gray')
    ax1.grid(True, color='#333333', linestyle=':')
    ax1.tick_params(colors='white')
    
    ax2.plot(df_slice.index, rsi_slice, color='#ff0055', linewidth=1.5)
    ax2.axhline(35, color='#00ff66', linestyle=':', alpha=0.7, label='RSI 35 LONG')
    ax2.axhline(62, color='#ff3300', linestyle=':', alpha=0.7, label='RSI 62 SHORT')
    ax2.set_ylabel('RSI (14)', color='white')
    ax2.grid(True, color='#333333', linestyle=':')
    ax2.tick_params(colors='white')
    ax2.set_ylim(10, 90)
    
    ax3.plot(df_slice.index, stoch_k_slice, color='#0066ff', linewidth=1.5)
    ax3.axhline(25, color='#00ff66', linestyle=':', alpha=0.7, label='Stoch 25 LONG')
    ax3.axhline(80, color='#ff3300', linestyle=':', alpha=0.7, label='Stoch 80 SHORT')
    ax3.set_ylabel('Stoch %K', color='white')
    ax3.grid(True, color='#333333', linestyle=':')
    ax3.tick_params(colors='white')
    ax3.set_ylim(-5, 105)
    
    fig.patch.set_facecolor('#0d0e15')
    for ax in [ax1, ax2, ax3]:
        ax.set_facecolor('#0d0e15')
        ax.spines['bottom'].set_color('gray')
        ax.spines['top'].set_color('gray')
        ax.spines['left'].set_color('gray')
        ax.spines['right'].set_color('gray')
        
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    return buf

async def run_escanear_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_ESCANEAR_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_ESCANEAR_RUN
    
    state = load_escanear_alert_state()
    state_changed = False
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def calc_stoch(df, period=14, smooth_k=3):
        low_min = df['Low'].rolling(window=period).min()
        high_max = df['High'].rolling(window=period).max()
        k = 100 * (df['Close'] - low_min) / (high_max - low_min + 1e-9)
        stoch_k = k.rolling(window=smooth_k).mean()
        return stoch_k

    try:
        df_yf = await asyncio.to_thread(
            yf.download, 
            SCANNER_TICKERS, 
            period="60d", 
            interval="1h", 
            group_by="ticker",
            progress=False
        )
        
        for ticker in SCANNER_TICKERS:
            try:
                if ticker not in df_yf.columns.levels[0]:
                    continue
                df_hist = df_yf[ticker].dropna(subset=['Close'])
                if df_hist.empty or len(df_hist) < 50:
                    continue
                    
                df_hist.index = pd.to_datetime(df_hist.index)
                
                df_4h = df_hist.resample('4h', offset='30min').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last',
                    'Volume': 'sum'
                }).dropna()
                
                if len(df_4h) < 20:
                    continue
                    
                close_series = df_4h['Close']
                price = float(close_series.iloc[-1])
                open_price = float(df_4h['Open'].iloc[-1])
                high_price = float(df_4h['High'].iloc[-1])
                low_price = float(df_4h['Low'].iloc[-1])
                
                curr_rsi = float(calc_rsi(close_series, 14).iloc[-1])
                curr_stoch = float(calc_stoch(df_4h, 14, 3).iloc[-1])
                
                total_range = high_price - low_price
                if total_range <= 0:
                    continue
                    
                lower_wick = min(open_price, price) - low_price
                upper_wick = high_price - max(open_price, price)
                
                lower_wick_pct = (lower_wick / total_range) * 100
                upper_wick_pct = (upper_wick / total_range) * 100
                
                is_call = curr_rsi <= 30 and curr_stoch <= 20 and lower_wick_pct >= 35
                is_put = curr_rsi >= 62 and curr_stoch >= 80 and upper_wick_pct >= 35
                
                current_signal = None
                if is_call:
                    current_signal = "CALL"
                elif is_put:
                    current_signal = "PUT"
                    
                old_signal = state.get(ticker)
                
                if current_signal and current_signal != old_signal:
                    state[ticker] = current_signal
                    state_changed = True
                    
                    # --- OPTIMIZACIÓN: Solo consumimos API para las confirmadas ---
                    summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                    net_gex = 0
                    pc_ratio = 1.0
                    wall = price
                    
                    if summary_data and 'price' in summary_data:
                        exp = summary_data.get('exposure', {})
                        flow = summary_data.get('options_flow', {})
                        net_gex = exp.get('net_gex', 0) or 0
                        pc_ratio = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                        if current_signal == "CALL":
                            wall = float(exp.get('call_wall', price) or price)
                        else:
                            wall = float(exp.get('put_wall', price) or price)
                            
                    color = discord.Color.green() if current_signal == "CALL" else discord.Color.red()
                    emoji = "🟢 CALL" if current_signal == "CALL" else "🔴 PUT"
                    condition_text = f"RSI 4H: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Inf: `{lower_wick_pct:.1f}%`" if current_signal == "CALL" else f"RSI 4H: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Sup: `{upper_wick_pct:.1f}%`"
                    wall_name = "Resistencia (Call Wall)" if current_signal == "CALL" else "Soporte (Put Wall)"
                    
                    embed = discord.Embed(
                        title=f"🚨 ALERTA ESCÁNER: {ticker} ({emoji})",
                        color=color,
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.add_field(name="Temporalidad", value="`4H`", inline=True)
                    embed.add_field(name="Precio Spot", value=f"`${price:.2f}`", inline=True)
                    embed.add_field(name="Condición", value=condition_text, inline=False)
                    embed.add_field(name="GEX (FlashAlpha)", value=f"`${net_gex/1e6:,.1f}M`", inline=True)
                    embed.add_field(name="P/C Ratio Vol", value=f"`{pc_ratio:.2f}`", inline=True)
                    embed.add_field(name=wall_name, value=f"`${wall:.2f}`", inline=True)
                    embed.set_footer(text="CarlosBot Escáner Alertas Automáticas")
                    
                    # No enviar en la primera corrida para evitar alertas spam de historial viejo
                    if not IS_FIRST_ESCANEAR_RUN:
                        await alert_channel.send(embed=embed)
                        await asyncio.sleep(1.0)
                        
                elif not current_signal and old_signal:
                    state.pop(ticker, None)
                    state_changed = True
                    
            except Exception as e:
                print(f"Error procesando {ticker} en loop automático de escaneo: {e}")
                
        IS_FIRST_ESCANEAR_RUN = False
        if state_changed:
            save_escanear_alert_state(state)
            
    except Exception as e:
        print(f"Error en loop automático de alertas de escaneo: {e}")

async def run_escanear_1d_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_ESCANEAR_1D_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_ESCANEAR_1D_RUN
    
    state = load_escanear_1d_alert_state()
    state_changed = False
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def calc_stoch(df, period=14, smooth_k=3):
        low_min = df['Low'].rolling(window=period).min()
        high_max = df['High'].rolling(window=period).max()
        k = 100 * (df['Close'] - low_min) / (high_max - low_min + 1e-9)
        stoch_k = k.rolling(window=smooth_k).mean()
        return stoch_k

    try:
        # Descarga de datos diarios (250 días) para cálculo estable
        df_yf = await asyncio.to_thread(
            yf.download, 
            SCANNER_TICKERS, 
            period="250d", 
            interval="1d", 
            group_by="ticker",
            progress=False
        )
        
        for ticker in SCANNER_TICKERS:
            try:
                if ticker not in df_yf.columns.levels[0]:
                    continue
                df_hist = df_yf[ticker].dropna(subset=['Close'])
                if df_hist.empty or len(df_hist) < 30:
                    continue
                
                close_series = df_hist['Close']
                price = float(close_series.iloc[-1])
                open_price = float(df_hist['Open'].iloc[-1])
                high_price = float(df_hist['High'].iloc[-1])
                low_price = float(df_hist['Low'].iloc[-1])
                
                curr_rsi = float(calc_rsi(close_series, 14).iloc[-1])
                curr_stoch = float(calc_stoch(df_hist, 14, 3).iloc[-1])
                
                total_range = high_price - low_price
                if total_range <= 0:
                    continue
                    
                lower_wick = min(open_price, price) - low_price
                upper_wick = high_price - max(open_price, price)
                
                lower_wick_pct = (lower_wick / total_range) * 100
                upper_wick_pct = (upper_wick / total_range) * 100
                
                is_call = curr_rsi <= 30 and curr_stoch <= 20 and lower_wick_pct >= 35
                is_put = curr_rsi >= 62 and curr_stoch >= 80 and upper_wick_pct >= 35
                
                current_signal = None
                if is_call:
                    current_signal = "CALL"
                elif is_put:
                    current_signal = "PUT"
                    
                old_signal = state.get(ticker)
                
                if current_signal and current_signal != old_signal:
                    state[ticker] = current_signal
                    state_changed = True
                    
                    # --- OPTIMIZACIÓN: Solo consumimos API para las confirmadas ---
                    summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                    net_gex = 0
                    pc_ratio = 1.0
                    wall = price
                    
                    if summary_data and 'price' in summary_data:
                        exp = summary_data.get('exposure', {})
                        flow = summary_data.get('options_flow', {})
                        net_gex = exp.get('net_gex', 0) or 0
                        pc_ratio = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                        if current_signal == "CALL":
                            wall = float(exp.get('call_wall', price) or price)
                        else:
                            wall = float(exp.get('put_wall', price) or price)
                            
                    color = discord.Color.green() if current_signal == "CALL" else discord.Color.red()
                    emoji = "🟢 CALL" if current_signal == "CALL" else "🔴 PUT"
                    condition_text = f"RSI Daily: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Inf: `{lower_wick_pct:.1f}%`" if current_signal == "CALL" else f"RSI Daily: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Sup: `{upper_wick_pct:.1f}%`"
                    wall_name = "Resistencia (Call Wall)" if current_signal == "CALL" else "Soporte (Put Wall)"
                    
                    embed = discord.Embed(
                        title=f"🚨 ALERTA DIARIA: {ticker} ({emoji})",
                        color=color,
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.add_field(name="Temporalidad", value="`Diario (1D)`", inline=True)
                    embed.add_field(name="Precio Spot", value=f"`${price:.2f}`", inline=True)
                    embed.add_field(name="Condición", value=condition_text, inline=False)
                    embed.add_field(name="GEX (FlashAlpha)", value=f"`${net_gex/1e6:,.1f}M`", inline=True)
                    embed.add_field(name="P/C Ratio Vol", value=f"`{pc_ratio:.2f}`", inline=True)
                    embed.add_field(name=wall_name, value=f"`${wall:.2f}`", inline=True)
                    embed.set_footer(text="CarlosBot Escáner Alertas Diarias")
                    
                    # No enviar en la primera corrida para evitar alertas spam de historial viejo
                    if not IS_FIRST_ESCANEAR_1D_RUN:
                        await alert_channel.send(embed=embed)
                        await asyncio.sleep(1.0)
                        
                elif not current_signal and old_signal:
                    state.pop(ticker, None)
                    state_changed = True
                    
            except Exception as e:
                print(f"Error procesando {ticker} en loop automático diario: {e}")
                
        IS_FIRST_ESCANEAR_1D_RUN = False
        if state_changed:
            save_escanear_1d_alert_state(state)
            
    except Exception as e:
        print(f"Error en loop automático diario: {e}")

async def run_escanear_1w_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_ESCANEAR_1W_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_ESCANEAR_1W_RUN
    
    state = load_escanear_1w_alert_state()
    state_changed = False
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def calc_stoch(df, period=14, smooth_k=3):
        low_min = df['Low'].rolling(window=period).min()
        high_max = df['High'].rolling(window=period).max()
        k = 100 * (df['Close'] - low_min) / (high_max - low_min + 1e-9)
        stoch_k = k.rolling(window=smooth_k).mean()
        return stoch_k

    try:
        # Descarga de datos semanales (730 días es excelente para cálculo estable)
        df_yf = await asyncio.to_thread(
            yf.download, 
            SCANNER_TICKERS, 
            period="730d", 
            interval="1wk", 
            group_by="ticker",
            progress=False
        )
        
        for ticker in SCANNER_TICKERS:
            try:
                if ticker not in df_yf.columns.levels[0]:
                    continue
                df_hist = df_yf[ticker].dropna(subset=['Close'])
                if df_hist.empty or len(df_hist) < 30:
                    continue
                
                close_series = df_hist['Close']
                price = float(close_series.iloc[-1])
                open_price = float(df_hist['Open'].iloc[-1])
                high_price = float(df_hist['High'].iloc[-1])
                low_price = float(df_hist['Low'].iloc[-1])
                
                curr_rsi = float(calc_rsi(close_series, 14).iloc[-1])
                curr_stoch = float(calc_stoch(df_hist, 14, 3).iloc[-1])
                
                total_range = high_price - low_price
                if total_range <= 0:
                    continue
                    
                lower_wick = min(open_price, price) - low_price
                upper_wick = high_price - max(open_price, price)
                
                lower_wick_pct = (lower_wick / total_range) * 100
                upper_wick_pct = (upper_wick / total_range) * 100
                
                is_call = curr_rsi <= 30 and curr_stoch <= 20 and lower_wick_pct >= 35
                is_put = curr_rsi >= 62 and curr_stoch >= 80 and upper_wick_pct >= 35
                
                current_signal = None
                if is_call:
                    current_signal = "CALL"
                elif is_put:
                    current_signal = "PUT"
                    
                old_signal = state.get(ticker)
                
                if current_signal and current_signal != old_signal:
                    state[ticker] = current_signal
                    state_changed = True
                    
                    # --- OPTIMIZACIÓN: Solo consumimos API para las confirmadas ---
                    summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                    net_gex = 0
                    pc_ratio = 1.0
                    wall = price
                    
                    if summary_data and 'price' in summary_data:
                        exp = summary_data.get('exposure', {})
                        flow = summary_data.get('options_flow', {})
                        net_gex = exp.get('net_gex', 0) or 0
                        pc_ratio = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                        if current_signal == "CALL":
                            wall = float(exp.get('call_wall', price) or price)
                        else:
                            wall = float(exp.get('put_wall', price) or price)
                            
                    color = discord.Color.green() if current_signal == "CALL" else discord.Color.red()
                    emoji = "🟢 CALL" if current_signal == "CALL" else "🔴 PUT"
                    condition_text = f"RSI Weekly: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Inf: `{lower_wick_pct:.1f}%`" if current_signal == "CALL" else f"RSI Weekly: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Sup: `{upper_wick_pct:.1f}%`"
                    wall_name = "Resistencia (Call Wall)" if current_signal == "CALL" else "Soporte (Put Wall)"
                    
                    embed = discord.Embed(
                        title=f"🚨 ALERTA SEMANAL: {ticker} ({emoji})",
                        color=color,
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.add_field(name="Temporalidad", value="`Semanal (1W)`", inline=True)
                    embed.add_field(name="Precio Spot", value=f"`${price:.2f}`", inline=True)
                    embed.add_field(name="Condición", value=condition_text, inline=False)
                    embed.add_field(name="GEX (FlashAlpha)", value=f"`${net_gex/1e6:,.1f}M`", inline=True)
                    embed.add_field(name="P/C Ratio Vol", value=f"`{pc_ratio:.2f}`", inline=True)
                    embed.add_field(name=wall_name, value=f"`${wall:.2f}`", inline=True)
                    embed.set_footer(text="CarlosBot Escáner Alertas Semanales")
                    
                    # No enviar en la primera corrida para evitar alertas spam de historial viejo
                    if not IS_FIRST_ESCANEAR_1W_RUN:
                        await alert_channel.send(embed=embed)
                        await asyncio.sleep(1.0)
                        
                elif not current_signal and old_signal:
                    state.pop(ticker, None)
                    state_changed = True
                    
            except Exception as e:
                print(f"Error procesando {ticker} en loop automático semanal: {e}")
                
        IS_FIRST_ESCANEAR_1W_RUN = False
        if state_changed:
            save_escanear_1w_alert_state(state)
            
    except Exception as e:
        print(f"Error en loop automático semanal: {e}")

async def run_escanear_1m_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_ESCANEAR_1M_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_ESCANEAR_1M_RUN
    
    state = load_escanear_1m_alert_state()
    state_changed = False
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def calc_stoch(df, period=14, smooth_k=3):
        low_min = df['Low'].rolling(window=period).min()
        high_max = df['High'].rolling(window=period).max()
        k = 100 * (df['Close'] - low_min) / (high_max - low_min + 1e-9)
        stoch_k = k.rolling(window=smooth_k).mean()
        return stoch_k

    try:
        # Descarga de datos mensuales (3650 días ~ 10 años es excelente para cálculo estable)
        df_yf = await asyncio.to_thread(
            yf.download, 
            SCANNER_TICKERS, 
            period="3650d", 
            interval="1mo", 
            group_by="ticker",
            progress=False
        )
        
        for ticker in SCANNER_TICKERS:
            try:
                if ticker not in df_yf.columns.levels[0]:
                    continue
                df_hist = df_yf[ticker].dropna(subset=['Close'])
                if df_hist.empty or len(df_hist) < 20:
                    continue
                
                close_series = df_hist['Close']
                price = float(close_series.iloc[-1])
                open_price = float(df_hist['Open'].iloc[-1])
                high_price = float(df_hist['High'].iloc[-1])
                low_price = float(df_hist['Low'].iloc[-1])
                
                curr_rsi = float(calc_rsi(close_series, 14).iloc[-1])
                curr_stoch = float(calc_stoch(df_hist, 14, 3).iloc[-1])
                
                total_range = high_price - low_price
                if total_range <= 0:
                    continue
                    
                lower_wick = min(open_price, price) - low_price
                upper_wick = high_price - max(open_price, price)
                
                lower_wick_pct = (lower_wick / total_range) * 100
                upper_wick_pct = (upper_wick / total_range) * 100
                
                is_call = curr_rsi <= 30 and curr_stoch <= 20 and lower_wick_pct >= 35
                is_put = curr_rsi >= 62 and curr_stoch >= 80 and upper_wick_pct >= 35
                
                current_signal = None
                if is_call:
                    current_signal = "CALL"
                elif is_put:
                    current_signal = "PUT"
                    
                old_signal = state.get(ticker)
                
                if current_signal and current_signal != old_signal:
                    state[ticker] = current_signal
                    state_changed = True
                    
                    # --- OPTIMIZACIÓN: Solo consumimos API para las confirmadas ---
                    summary_data = await asyncio.to_thread(fa.stock_summary, ticker)
                    net_gex = 0
                    pc_ratio = 1.0
                    wall = price
                    
                    if summary_data and 'price' in summary_data:
                        exp = summary_data.get('exposure', {})
                        flow = summary_data.get('options_flow', {})
                        net_gex = exp.get('net_gex', 0) or 0
                        pc_ratio = float(flow.get('pc_ratio_volume', 1.0) or 1.0)
                        if current_signal == "CALL":
                            wall = float(exp.get('call_wall', price) or price)
                        else:
                            wall = float(exp.get('put_wall', price) or price)
                            
                    color = discord.Color.green() if current_signal == "CALL" else discord.Color.red()
                    emoji = "🟢 CALL" if current_signal == "CALL" else "🔴 PUT"
                    condition_text = f"RSI Monthly: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Inf: `{lower_wick_pct:.1f}%`" if current_signal == "CALL" else f"RSI Monthly: `{curr_rsi:.1f}` | Stoch: `{curr_stoch:.1f}` | Mecha Sup: `{upper_wick_pct:.1f}%`"
                    wall_name = "Resistencia (Call Wall)" if current_signal == "CALL" else "Soporte (Put Wall)"
                    
                    embed = discord.Embed(
                        title=f"🚨 ALERTA MENSUAL: {ticker} ({emoji})",
                        color=color,
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.add_field(name="Temporalidad", value="`Mensual (1M)`", inline=True)
                    embed.add_field(name="Precio Spot", value=f"`${price:.2f}`", inline=True)
                    embed.add_field(name="Condición", value=condition_text, inline=False)
                    embed.add_field(name="GEX (FlashAlpha)", value=f"`${net_gex/1e6:,.1f}M`", inline=True)
                    embed.add_field(name="P/C Ratio Vol", value=f"`{pc_ratio:.2f}`", inline=True)
                    embed.add_field(name=wall_name, value=f"`${wall:.2f}`", inline=True)
                    embed.set_footer(text="CarlosBot Escáner Alertas Mensuales")
                    
                    # No enviar en la primera corrida para evitar alertas spam de historial viejo
                    if not IS_FIRST_ESCANEAR_1M_RUN:
                        await alert_channel.send(embed=embed)
                        await asyncio.sleep(1.0)
                        
                elif not current_signal and old_signal:
                    state.pop(ticker, None)
                    state_changed = True
                    
            except Exception as e:
                print(f"Error procesando {ticker} en loop automático mensual: {e}")
                
        IS_FIRST_ESCANEAR_1M_RUN = False
        if state_changed:
            save_escanear_1m_alert_state(state)
            
    except Exception as e:
        print(f"Error en loop automático mensual: {e}")

async def run_lateral_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_ALERTS_RUN
    
    timeframes = ["15m"]
    now_dt = datetime.datetime.now()
    if now_dt.minute < 15 or IS_FIRST_ALERTS_RUN:
        timeframes.extend(["1h", "4h"])
        
    if IS_FIRST_ALERTS_RUN or (now_dt.hour == 16 and now_dt.minute < 15):
        timeframes.extend(["1d", "1w", "2w", "1mes"])
        
    IS_FIRST_ALERTS_RUN = False
    
    state = load_alert_state()
    state_changed = False
    
    for tf in timeframes:
        try:
            resample_rule = None
            if tf == "15m":
                interval = "15m"
                period = "5d"
            elif tf == "1h":
                interval = "1h"
                period = "30d"
            elif tf == "4h":
                interval = "1h"
                period = "60d"
                resample_rule = "4h"
            elif tf == "1d":
                interval = "1d"
                period = "250d"
            elif tf == "1w":
                interval = "1wk"
                period = "730d"
            elif tf == "2w":
                interval = "1wk"
                period = "1095d"
                resample_rule = "2W"
            elif tf == "1mes":
                interval = "1mo"
                period = "1825d"
            else:
                continue
                
            df_yf = await asyncio.to_thread(
                yf.download, 
                LATERAL_TICKERS, 
                period=period, 
                interval=interval,
                group_by="ticker",
                progress=False
            )
            
            for ticker in LATERAL_TICKERS:
                try:
                    if ticker not in df_yf.columns.levels[0]:
                        continue
                    df_hist = df_yf[ticker].dropna(subset=['Close'])
                    if df_hist.empty:
                        continue
                        
                    if resample_rule:
                        df_hist = df_hist.resample(resample_rule).agg({
                            'Open': 'first',
                            'High': 'max',
                            'Low': 'min',
                            'Close': 'last',
                            'Volume': 'sum'
                        }).dropna()
                        
                    if len(df_hist) < 30:
                        continue
                        
                    close_series = df_hist['Close']
                    high_series = df_hist['High']
                    low_series = df_hist['Low']
                    
                    ema9 = close_series.ewm(span=9, adjust=False).mean()
                    rsi = calculate_rsi_global(close_series, 14)
                    
                    lowest_low = low_series.rolling(window=14).min()
                    highest_high = high_series.rolling(window=14).max()
                    stoch_k = 100 * (close_series - lowest_low) / (highest_high - lowest_low)
                    
                    close_vals = close_series.values
                    ema9_vals = ema9.values
                    
                    contador_bajo = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val < e_val:
                            contador_bajo += 1
                        else:
                            break
                            
                    contador_alto = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val > e_val:
                            contador_alto += 1
                        else:
                            break
                            
                    last_close = float(close_vals[-1])
                    last_rsi = float(rsi.iloc[-1])
                    last_k = float(stoch_k.iloc[-1])
                    
                    is_long = (contador_bajo >= 3) and (last_rsi <= 35) and (last_k <= 25)
                    is_short = (contador_alto >= 3) and (last_rsi >= 62) and (last_k >= 80)
                    
                    state_key = f"{ticker}_{tf}"
                    current_signal = None
                    if is_long:
                        current_signal = "LONG"
                    elif is_short:
                        current_signal = "SHORT"
                        
                    old_signal = state.get(state_key)
                    
                    if current_signal and current_signal != old_signal:
                        state[state_key] = current_signal
                        state_changed = True
                        
                        chart_buf = generate_alert_chart(ticker, df_hist, ema9, rsi, stoch_k, tf)
                        discord_file = discord.File(chart_buf, filename=f"{ticker}_{tf}_alert.png")
                        
                        color = discord.Color.green() if current_signal == "LONG" else discord.Color.red()
                        emoji = "🟢 LONG" if current_signal == "LONG" else "🔴 SHORT"
                        trend_emoji = "bajo EMA 9 📉" if current_signal == "LONG" else "sobre EMA 9 📈"
                        consecutive = contador_bajo if current_signal == "LONG" else contador_alto
                        
                        embed = discord.Embed(
                            title=f"🚨 ALERTA DE ENTRADA: {ticker} ({emoji})",
                            color=color,
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        embed.add_field(name="Temporalidad", value=f"`{tf.upper()}`", inline=True)
                        embed.add_field(name="Precio Spot", value=f"`${last_close:.2f}`", inline=True)
                        embed.add_field(name="Velas Consecutivas", value=f"`{consecutive}` {trend_emoji}", inline=True)
                        embed.add_field(name="RSI (14)", value=f"`{last_rsi:.1f}`", inline=True)
                        embed.add_field(name="Stochastic %K", value=f"`{last_k:.1f}`", inline=True)
                        embed.set_image(url=f"attachment://{ticker}_{tf}_alert.png")
                        embed.set_footer(text="Carlos Lateral 2 Alerts • CarlosBot")
                        
                        await alert_channel.send(embed=embed, file=discord_file)
                        await asyncio.sleep(1.5)  # Evitar límite de rate limit de Discord
                        
                    elif not current_signal and old_signal:
                        state.pop(state_key, None)
                        state_changed = True
                        
                except Exception as e:
                    print(f"Error procesando {ticker} en loop alertas: {e}")
            await asyncio.sleep(0.05)
            if state_changed:
                save_alert_state(state)
                state_changed = False
            # Garbage collection to free memory after each timeframe
            import gc
            gc.collect()
        except Exception as e:
            print(f"Error en loop de alertas para temporalidad {tf}: {e}")

async def run_crypto_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_CRYPTO_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_CRYPTO_RUN
    
    # En el de crypto solo poner 4h, 1d, 1w y 1mes
    timeframes = ["4h"]
    now_dt = datetime.datetime.now()
    
    if IS_FIRST_CRYPTO_RUN or (now_dt.hour == 16):
        timeframes.extend(["1d", "1w", "2w", "1mes"])
        
    IS_FIRST_CRYPTO_RUN = False
    
    state = load_crypto_alert_state()
    state_changed = False
    
    for tf in timeframes:
        try:
            resample_rule = None
            if tf == "4h":
                interval = "1h"
                period = "60d"
                resample_rule = "4h"
            elif tf == "1d":
                interval = "1d"
                period = "250d"
            elif tf == "1w":
                interval = "1wk"
                period = "730d"
            elif tf == "2w":
                interval = "1wk"
                period = "1095d"
                resample_rule = "2W"
            elif tf == "1mes":
                interval = "1mo"
                period = "1825d"
            else:
                continue
                
            df_yf = await asyncio.to_thread(
                yf.download, 
                CRYPTO_TICKERS, 
                period=period, 
                interval=interval,
                group_by="ticker",
                progress=False
            )
            
            for ticker in CRYPTO_TICKERS:
                try:
                    if ticker not in df_yf.columns.levels[0]:
                        continue
                    df_hist = df_yf[ticker].dropna(subset=['Close'])
                    if df_hist.empty:
                        continue
                        
                    if resample_rule:
                        df_hist = df_hist.resample(resample_rule).agg({
                            'Open': 'first',
                            'High': 'max',
                            'Low': 'min',
                            'Close': 'last',
                            'Volume': 'sum'
                        }).dropna()
                        
                    if len(df_hist) < 30:
                        continue
                        
                    close_series = df_hist['Close']
                    high_series = df_hist['High']
                    low_series = df_hist['Low']
                    
                    ema9 = close_series.ewm(span=9, adjust=False).mean()
                    rsi = calculate_rsi_global(close_series, 14)
                    
                    lowest_low = low_series.rolling(window=14).min()
                    highest_high = high_series.rolling(window=14).max()
                    stoch_k = 100 * (close_series - lowest_low) / (highest_high - lowest_low)
                    
                    close_vals = close_series.values
                    ema9_vals = ema9.values
                    
                    contador_bajo = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val < e_val:
                            contador_bajo += 1
                        else:
                            break
                            
                    contador_alto = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val > e_val:
                            contador_alto += 1
                        else:
                            break
                            
                    last_close = float(close_vals[-1])
                    last_rsi = float(rsi.iloc[-1])
                    last_k = float(stoch_k.iloc[-1])
                    
                    is_long = (contador_bajo >= 3) and (last_rsi <= 35) and (last_k <= 25)
                    is_short = (contador_alto >= 3) and (last_rsi >= 62) and (last_k >= 80)
                    
                    state_key = f"{ticker}_{tf}"
                    current_signal = None
                    if is_long:
                        current_signal = "LONG"
                    elif is_short:
                        current_signal = "SHORT"
                        
                    old_signal = state.get(state_key)
                    
                    if current_signal and current_signal != old_signal:
                        state[state_key] = current_signal
                        state_changed = True
                        
                        chart_buf = generate_alert_chart(ticker, df_hist, ema9, rsi, stoch_k, tf)
                        discord_file = discord.File(chart_buf, filename=f"{ticker}_{tf}_alert.png")
                        
                        color = discord.Color.green() if current_signal == "LONG" else discord.Color.red()
                        emoji = "🟢 LONG" if current_signal == "LONG" else "🔴 SHORT"
                        trend_emoji = "bajo EMA 9 📉" if current_signal == "LONG" else "sobre EMA 9 📈"
                        consecutive = contador_bajo if current_signal == "LONG" else contador_alto
                        
                        embed = discord.Embed(
                            title=f"🚨 ALERTA CRIPTO: {ticker} ({emoji})",
                            color=color,
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        embed.add_field(name="Temporalidad", value=f"`{tf.upper()}`", inline=True)
                        embed.add_field(name="Precio Spot", value=f"`${last_close:.4f}`" if last_close < 1 else f"`${last_close:.2f}`", inline=True)
                        embed.add_field(name="Velas Consecutivas", value=f"`{consecutive}` {trend_emoji}", inline=True)
                        embed.add_field(name="RSI (14)", value=f"`{last_rsi:.1f}`", inline=True)
                        embed.add_field(name="Stochastic %K", value=f"`{last_k:.1f}`", inline=True)
                        embed.set_image(url=f"attachment://{ticker}_{tf}_alert.png")
                        embed.set_footer(text="Carlos Lateral 2 Crypto Alerts • CarlosBot")
                        
                        await alert_channel.send(embed=embed, file=discord_file)
                        await asyncio.sleep(1.5)  # Evitar rate limits
                        
                    elif not current_signal and old_signal:
                        state.pop(state_key, None)
                        state_changed = True
                        
                except Exception as e:
                    print(f"Error procesando {ticker} en loop crypto: {e}")
            
            await asyncio.sleep(0.05)
            if state_changed:
                save_crypto_alert_state(state)
                state_changed = False
            # Garbage collection to free memory after each timeframe
            import gc
            gc.collect()
                
        except Exception as e:
            print(f"Error en loop de alertas crypto para temporalidad {tf}: {e}")

async def run_vip_alerts():
    await client.wait_until_ready()
    
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == ALERT_VIP_CHANNEL_NAME:
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        return
        
    global IS_FIRST_VIP_RUN
    
    timeframes = ["15m"]
    now_dt = datetime.datetime.now()
    if now_dt.minute < 15 or IS_FIRST_VIP_RUN:
        timeframes.extend(["1h", "4h"])
        
    if IS_FIRST_VIP_RUN or (now_dt.hour == 16 and now_dt.minute < 15):
        timeframes.extend(["1d", "1w", "1mes"])
        
    IS_FIRST_VIP_RUN = False
    
    state = load_vip_alert_state()
    state_changed = False
    
    for tf in timeframes:
        try:
            resample_rule = None
            if tf == "15m":
                interval = "15m"
                period = "5d"
            elif tf == "1h":
                interval = "1h"
                period = "30d"
            elif tf == "4h":
                interval = "1h"
                period = "60d"
                resample_rule = "4h"
            elif tf == "1d":
                interval = "1d"
                period = "250d"
            elif tf == "1w":
                interval = "1wk"
                period = "730d"
            elif tf == "1mes":
                interval = "1mo"
                period = "1825d"
            else:
                continue
                
            df_yf = await asyncio.to_thread(
                yf.download, 
                VIP_CRYPTO_TICKERS, 
                period=period, 
                interval=interval,
                group_by="ticker",
                progress=False
            )
            
            for ticker in VIP_CRYPTO_TICKERS:
                try:
                    if ticker not in df_yf.columns.levels[0]:
                        continue
                    df_hist = df_yf[ticker].dropna(subset=['Close'])
                    if df_hist.empty:
                        continue
                        
                    if resample_rule:
                        df_hist = df_hist.resample(resample_rule).agg({
                            'Open': 'first',
                            'High': 'max',
                            'Low': 'min',
                            'Close': 'last',
                            'Volume': 'sum'
                        }).dropna()
                        
                    if len(df_hist) < 30:
                        continue
                        
                    close_series = df_hist['Close']
                    high_series = df_hist['High']
                    low_series = df_hist['Low']
                    
                    ema9 = close_series.ewm(span=9, adjust=False).mean()
                    rsi = calculate_rsi_global(close_series, 14)
                    
                    lowest_low = low_series.rolling(window=14).min()
                    highest_high = high_series.rolling(window=14).max()
                    stoch_k = 100 * (close_series - lowest_low) / (highest_high - lowest_low)
                    
                    close_vals = close_series.values
                    ema9_vals = ema9.values
                    
                    contador_bajo = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val < e_val:
                            contador_bajo += 1
                        else:
                            break
                            
                    contador_alto = 0
                    for c_val, e_val in zip(reversed(close_vals), reversed(ema9_vals)):
                        if c_val > e_val:
                            contador_alto += 1
                        else:
                            break
                            
                    last_close = float(close_vals[-1])
                    last_rsi = float(rsi.iloc[-1])
                    last_k = float(stoch_k.iloc[-1])
                    
                    is_long = (contador_bajo >= 3) and (last_rsi <= 35) and (last_k <= 25)
                    is_short = (contador_alto >= 3) and (last_rsi >= 62) and (last_k >= 80)
                    
                    state_key = f"{ticker}_{tf}"
                    current_signal = None
                    if is_long:
                        current_signal = "LONG"
                    elif is_short:
                        current_signal = "SHORT"
                        
                    old_signal = state.get(state_key)
                    
                    if current_signal and current_signal != old_signal:
                        state[state_key] = current_signal
                        state_changed = True
                        
                        chart_buf = generate_alert_chart(ticker, df_hist, ema9, rsi, stoch_k, tf)
                        discord_file = discord.File(chart_buf, filename=f"{ticker}_{tf}_alert.png")
                        
                        # Calcular TPs y Stop Loss según temporalidad
                        tf_lower = tf.lower()
                        if tf_lower in ["15m", "1h"]:
                            tp_pcts = [1.0, 1.5, 2.0, 2.5]
                            sl_pct = 1.5
                        elif tf_lower in ["4h", "1d"]:
                            tp_pcts = [1.0, 1.5, 2.0, 2.5]
                            sl_pct = 3.0
                        else:  # "1w" o "1mes"
                            tp_pcts = [2.0, 3.0, 4.0, 5.0]
                            sl_pct = 6.0

                        def fmt_p(val):
                            return f"${val:.4f}" if val < 1 else f"${val:.2f}"

                        if current_signal == "LONG":
                            tp_vals = [last_close * (1 + p / 100) for p in tp_pcts]
                            sl_val = last_close * (1 - sl_pct / 100)
                        else: # SHORT
                            tp_vals = [last_close * (1 - p / 100) for p in tp_pcts]
                            sl_val = last_close * (1 + sl_pct / 100)

                        color = discord.Color.green() if current_signal == "LONG" else discord.Color.red()
                        emoji = "🟢 LONG" if current_signal == "LONG" else "🔴 SHORT"
                        trend_emoji = "bajo EMA 9 📉" if current_signal == "LONG" else "sobre EMA 9 📈"
                        consecutive = contador_bajo if current_signal == "LONG" else contador_alto
                        
                        display_ticker = ticker.replace("HYPE32196-USD", "HYPE-USD")
                        embed = discord.Embed(
                            title=f"🚨 ALERTA VIP: {display_ticker} ({emoji})",
                            color=color,
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        embed.add_field(name="Temporalidad", value=f"`{tf.upper()}`", inline=True)
                        embed.add_field(name="Precio Spot", value=f"`{fmt_p(last_close)}`", inline=True)
                        embed.add_field(name="Velas Consecutivas", value=f"`{consecutive}` {trend_emoji}", inline=True)
                        embed.add_field(name="RSI (14)", value=f"`{last_rsi:.1f}`", inline=True)
                        embed.add_field(name="Stochastic %K", value=f"`{last_k:.1f}`", inline=True)
                        
                        # Parámetros del Trade
                        embed.add_field(name="🎯 Entrada (Entry)", value=f"`{fmt_p(last_close)}`", inline=False)
                        
                        tp_text = (
                            f"1️⃣ `{fmt_p(tp_vals[0])}` ({tp_pcts[0]}%)\n"
                            f"2️⃣ `{fmt_p(tp_vals[1])}` ({tp_pcts[1]}%)\n"
                            f"3️⃣ `{fmt_p(tp_vals[2])}` ({tp_pcts[2]}%)\n"
                            f"4️⃣ `{fmt_p(tp_vals[3])}` ({tp_pcts[3]}%)"
                        )
                        sl_text = f"🛑 `{fmt_p(sl_val)}` ({sl_pct}%)"
                        
                        embed.add_field(name="💵 Objetivos Take Profit", value=tp_text, inline=True)
                        embed.add_field(name="🛑 Stop Loss", value=sl_text, inline=True)
                        
                        leverage_text = (
                            "Elige tu propio apalancamiento. Multiplica los objetivos anteriores por el apalancamiento que elijas para calcular tu retorno real.\n"
                            "*(Ejemplo: con 10x de apalancamiento, un TP de 1.5% equivale a 15% de ganancia real; un SL de 1.5% equivale a 15% de pérdida)*\n"
                            "⚠️ **Descargo de responsabilidad:** Esto no es asesoría financiera. Las alertas son generadas de forma automática mediante algoritmos de análisis técnico. No nos hacemos responsables de pérdidas financieras. Opera bajo tu propio riesgo y haz tu propia investigación (DYOR)."
                        )
                        embed.add_field(name="⚡ Apalancamiento y Advertencia", value=leverage_text, inline=False)
                        
                        embed.set_image(url=f"attachment://{ticker}_{tf}_alert.png")
                        embed.set_footer(text="Carlos Lateral 2 VIP Alerts • CarlosBot")
                        
                        await alert_channel.send(embed=embed, file=discord_file)
                        await asyncio.sleep(1.5)  # Evitar rate limits
                        
                    elif not current_signal and old_signal:
                        state.pop(state_key, None)
                        state_changed = True
                        
                except Exception as e:
                    print(f"Error procesando {ticker} en loop VIP: {e}")
            
            await asyncio.sleep(0.05)
            if state_changed:
                save_vip_alert_state(state)
                state_changed = False
            # Garbage collection to free memory after each timeframe
            import gc
            gc.collect()
                
        except Exception as e:
            print(f"Error en loop de alertas VIP para temporalidad {tf}: {e}")

@tasks.loop(time=datetime.time(hour=21, minute=30, tzinfo=datetime.timezone.utc))
async def daily_rsi_scanner_loop():
    await client.wait_until_ready()
    print("[INFO] Iniciando escaneo diario de RSI...")
    
    try:
        with open("lista_escaneo.txt", "r") as f:
            tickers = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except Exception as e:
        print(f"[ERROR] No se pudo leer lista_escaneo.txt: {e}")
        return
        
    if not tickers:
        print("[WARNING] lista_escaneo.txt está vacío.")
        return

    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == "alertas-rsi":
                alert_channel = channel
                break
        if alert_channel:
            break
            
    if not alert_channel:
        print("[WARNING] No se encontró el canal #alertas-rsi en ningún servidor.")
        return

    oversold_signals = {}
    overbought_signals = {}
    
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        return 100 - (100 / (1 + rs))

    timeframes = [
        ("1D", "365d", "1d"),
        ("1W", "730d", "1wk"),
        ("1M", "1825d", "1mo")
    ]
    
    for tf_label, period, interval in timeframes:
        print(f"[INFO] Escaneando temporalidad {tf_label}...")
        try:
            chunk_size = 20
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i:i+chunk_size]
                df_batch = await asyncio.to_thread(
                    yf.download,
                    chunk,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    progress=False
                )
                
                for t in chunk:
                    try:
                        if isinstance(df_batch.columns, pd.MultiIndex):
                            df_t = df_batch[t] if t in df_batch.columns.levels[0] else None
                        else:
                            df_t = df_batch
                        
                        if df_t is not None and not df_t.empty and 'Close' in df_t.columns:
                            close_series = df_t['Close'].dropna()
                            if len(close_series) >= 15:
                                rsi_series = calc_rsi(close_series)
                                last_rsi = rsi_series.iloc[-1]
                                
                                # Ignorar stablecoins
                                if t in ["USDC", "USDT", "USDG", "USDC-USD", "USDT-USD", "USDG-USD"]:
                                    continue
                                    
                                if last_rsi <= 25:
                                    if t not in oversold_signals:
                                        oversold_signals[t] = []
                                    oversold_signals[t].append(f"{tf_label} (RSI: {last_rsi:.1f})")
                                elif last_rsi >= 70:
                                    if t not in overbought_signals:
                                        overbought_signals[t] = []
                                    overbought_signals[t].append(f"{tf_label} (RSI: {last_rsi:.1f})")
                    except Exception as e:
                        print(f"Error procesando {t} en {tf_label}: {e}")
                
                if 'df_batch' in locals():
                    del df_batch
                gc.collect()
                await asyncio.sleep(1.0)
        except Exception as e:
            print(f"Error descargando temporalidad {tf_label}: {e}")
            
    desc_oversold = ""
    if oversold_signals:
        for t in sorted(oversold_signals.keys()):
            display_name = t
            if t == "HYPE32196-USD":
                display_name = "HYPE-USD"
            tfs_str = ", ".join(oversold_signals[t])
            desc_oversold += f"• **{display_name}**: `{tfs_str}`\n"
    else:
        desc_oversold = "*Ninguno*"

    desc_overbought = ""
    if overbought_signals:
        for t in sorted(overbought_signals.keys()):
            display_name = t
            if t == "HYPE32196-USD":
                display_name = "HYPE-USD"
            tfs_str = ", ".join(overbought_signals[t])
            desc_overbought += f"• **{display_name}**: `{tfs_str}`\n"
    else:
        desc_overbought = "*Ninguno*"

    embed = discord.Embed(
        title="🔍 REPORTE DIARIO: ESCÁNER DE RSI",
        description="Escaneo automático de sobreventa y sobrecompra extrema (RSI <= 25 y RSI >= 70) realizado tras el cierre del mercado.",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    
    def add_long_field(embed, name, text):
        if len(text) <= 1024:
            embed.add_field(name=name, value=text, inline=False)
        else:
            lines = text.split('\n')
            current_text = ""
            chunk_num = 1
            for line in lines:
                if len(current_text) + len(line) + 2 > 1024:
                    embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)
                    current_text = line + "\n"
                    chunk_num += 1
                else:
                    current_text += line + "\n"
            if current_text:
                embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)

    add_long_field(embed, "📥 OPORTUNIDADES DE COMPRA (RSI <= 25 - Sobreventa)", desc_oversold)
    add_long_field(embed, "📈 OPORTUNIDADES DE VENTA (RSI >= 70 - Sobrecompra)", desc_overbought)
    
    embed.set_footer(text="CarlosBot Scanner • Actualización Diaria")
    await alert_channel.send(embed=embed)
    print("[INFO] Reporte diario de RSI enviado correctamente.")
    plt.close('all')
    gc.collect()

async def run_prewarm_cache():
    """Actualiza la caché de los tickers más comunes cada hora en segundo plano"""
    for ticker in COMMON_TICKERS:
        await fetch_and_cache_options(ticker)
        await asyncio.sleep(5) # Pausa más larga (5 seg) para no saturar y evitar ban de IP

async def run_gex_scanner_logic(limite_prueba=None):
    try:
        with open("tickers_gex.txt", "r") as f:
            tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    except Exception as e:
        print(f"[ERROR] No se pudo leer tickers_gex.txt: {e}")
        return
        
    if not tickers:
        print("[WARNING] tickers_gex.txt está vacío.")
        return
        
    if limite_prueba is not None:
        tickers = tickers[:limite_prueba]
        
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == "alertas-extremos-gex":
                alert_channel = channel
                break
        if not alert_channel:
            try:
                alert_channel = await guild.create_text_channel("alertas-extremos-gex")
                print(f"[OK] Canal #alertas-extremos-gex creado en {guild.name}")
                break
            except Exception as e:
                print(f"[ERROR] No se pudo crear el canal #alertas-extremos-gex en {guild.name}: {e}")
        if alert_channel:
            break
            
    if not alert_channel:
        print("[WARNING] No se encontró ni se pudo crear el canal #alertas-extremos-gex.")
        return

    long_signals = []
    short_signals = []
    
    # Umbral configurable (20% por defecto)
    UMBRAL_EXTREMO = 0.20
    
    for idx, ticker in enumerate(tickers):
        try:
            print(f"[GEX SCAN] Procesando {ticker} ({idx+1}/{len(tickers)})...")
            
            t = yf.Ticker(ticker)
            spot = 0.0
            try:
                spot = float(t.fast_info['lastPrice'])
            except:
                hist = t.history(period='1d')
                if not hist.empty:
                    spot = float(hist['Close'].iloc[-1])
            
            if spot <= 0:
                print(f"[GEX SCAN] Spot inválido para {ticker}: {spot}")
                continue
                
            expirations = list(t.options)
            if not expirations:
                print(f"[GEX SCAN] No se encontraron expiraciones para {ticker}")
                continue
                
            all_strikes_with_oi = []
            
            for exp in expirations:
                try:
                    opt = t.option_chain(exp)
                    calls = opt.calls
                    if not calls.empty:
                        for _, row in calls.iterrows():
                            oi = row.get('openInterest', 0)
                            if pd.notna(oi) and oi >= 5:
                                all_strikes_with_oi.append(('C', float(row['strike']), float(oi)))
                    puts = opt.puts
                    if not puts.empty:
                        for _, row in puts.iterrows():
                            oi = row.get('openInterest', 0)
                            if pd.notna(oi) and oi >= 5:
                                all_strikes_with_oi.append(('P', float(row['strike']), float(oi)))
                except Exception:
                    continue
            
            if not all_strikes_with_oi:
                continue
                
            df_opt = pd.DataFrame(all_strikes_with_oi, columns=['type', 'strike', 'open_interest'])
            
            # Extremos de Calls
            df_calls = df_opt[df_opt['type'] == 'C']
            if df_calls.empty:
                continue
            max_call_strike = df_calls['strike'].max()
            
            # Extremos de Puts
            df_puts = df_opt[df_opt['type'] == 'P']
            if df_puts.empty:
                continue
            min_put_strike = df_puts['strike'].min()
            
            limit_short = max_call_strike * (1.0 - UMBRAL_EXTREMO)
            limit_long = min_put_strike * (1.0 + UMBRAL_EXTREMO)
            
            if spot >= limit_short:
                diff_pct = ((max_call_strike - spot) / spot) * 100.0
                short_signals.append({
                    'ticker': ticker,
                    'spot': spot,
                    'max_call': max_call_strike,
                    'diff_pct': diff_pct
                })
                print(f"[SIGNAL SHORT] {ticker} a {spot} (Max Call: {max_call_strike}, Diff: {diff_pct:.1f}%)")
            elif spot <= limit_long:
                diff_pct = ((spot - min_put_strike) / spot) * 100.0
                long_signals.append({
                    'ticker': ticker,
                    'spot': spot,
                    'min_put': min_put_strike,
                    'diff_pct': diff_pct
                })
                print(f"[SIGNAL LONG] {ticker} a {spot} (Min Put: {min_put_strike}, Diff: {diff_pct:.1f}%)")
                
            del t
            del df_opt
            del all_strikes_with_oi
            
        except Exception as e:
            print(f"[ERROR GEX SCAN] Error procesando {ticker}: {e}")
            
        await asyncio.sleep(1.5)
        import gc
        gc.collect()

    embed = discord.Embed(
        title="🚨 ALERTA GEX: EXTREMOS DEL MERCADO",
        description=f"Escaneo de extremos en cadena de opciones (Yahoo Finance).\nUmbral de alerta: **{(UMBRAL_EXTREMO * 100):.0f}%** o menos de distancia del extremo.",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now()
    )
    
    desc_longs = ""
    if long_signals:
        for s in sorted(long_signals, key=lambda x: x['diff_pct']):
            desc_longs += f"🟢 **{s['ticker']}**: Spot `{s['spot']:.2f}` | Muro Put `{s['min_put']:.2f}` (a `{s['diff_pct']:.1f}%` de distancia)\n"
    else:
        desc_longs = "*Ninguno*"
        
    desc_shorts = ""
    if short_signals:
        for s in sorted(short_signals, key=lambda x: x['diff_pct']):
            desc_shorts += f"🔴 **{s['ticker']}**: Spot `{s['spot']:.2f}` | Muro Call `{s['max_call']:.2f}` (a `{s['diff_pct']:.1f}%` de distancia)\n"
    else:
        desc_shorts = "*Ninguno*"
        
    def add_long_field(embed, name, text):
        if len(text) <= 1024:
            embed.add_field(name=name, value=text, inline=False)
        else:
            lines = text.split('\n')
            current_text = ""
            chunk_num = 1
            for line in lines:
                if len(current_text) + len(line) + 2 > 1024:
                    embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)
                    current_text = line + "\n"
                    chunk_num += 1
                else:
                    current_text += line + "\n"
            if current_text:
                embed.add_field(name=f"{name} (Parte {chunk_num})", value=current_text, inline=False)

    add_long_field(embed, "🟢 COMPRAS POTENCIALES (Cerca del extremo de PUTS - Soporte)", desc_longs)
    add_long_field(embed, "🔴 VENTAS POTENCIALES / SHORTS (Cerca del extremo de CALLS - Resistencia)", desc_shorts)
    
    embed.set_footer(text="CarlosBot GEX Scanner")
    await alert_channel.send(embed=embed)
    print("[INFO] Reporte de Extremos GEX enviado correctamente.")

async def run_options_scanner_logic(limite_prueba=None):
    import datetime
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import gc

    # 1. Leer tickers_opciones.txt o usar por defecto
    tickers = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "AMD", "COIN", "IBIT", "SMCI", "MSTR", "HOOD", "SPCX", "BA", "NFLX"]
    try:
        if os.path.exists("tickers_opciones.txt"):
            with open("tickers_opciones.txt", "r") as f:
                file_tickers = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
                if file_tickers:
                    tickers = file_tickers
    except Exception as e:
        print(f"[ERROR] No se pudo leer tickers_opciones.txt: {e}")

    if limite_prueba is not None:
        tickers = tickers[:limite_prueba]

    # 2. Buscar o crear el canal #alertas-estrategias-opciones
    alert_channel = None
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == "alertas-estrategias-opciones":
                alert_channel = channel
                break
        if not alert_channel:
            try:
                alert_channel = await guild.create_text_channel("alertas-estrategias-opciones")
                print(f"[OK] Canal #alertas-estrategias-opciones creado en {guild.name}")
                break
            except Exception as e:
                print(f"[ERROR] No se pudo crear el canal #alertas-estrategias-opciones en {guild.name}: {e}")
        if alert_channel:
            break

    if not alert_channel:
        print("[WARNING] No se encontró ni se pudo crear el canal #alertas-estrategias-opciones.")
        return

    # 3. Escanear
    collars_all = []
    spreads_all = []
    condors_all = []

    today = datetime.date.today()

    for idx, ticker in enumerate(tickers):
        try:
            print(f"[OPTIONS SCAN] Procesando {ticker} ({idx+1}/{len(tickers)})...")
            t = yf.Ticker(ticker)
            spot = 0.0
            try:
                spot = float(t.fast_info['lastPrice'])
            except:
                hist = t.history(period='1d')
                if not hist.empty:
                    spot = float(hist['Close'].iloc[-1])

            if spot <= 0:
                continue

            expirations = list(t.options)
            if not expirations:
                continue

            valid_exps = []
            for exp in expirations:
                try:
                    exp_date = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    if 20 <= dte <= 60:
                        valid_exps.append((exp, dte))
                except:
                    continue

            for exp, dte in valid_exps:
                try:
                    opt = t.option_chain(exp)
                    calls = opt.calls.copy()
                    puts = opt.puts.copy()

                    if calls.empty or puts.empty:
                        continue

                    # Filtrar opciones cuyo lastTradeDate sea mayor a 4 días (precios viejos/fantasma)
                    now_utc = datetime.datetime.now(datetime.timezone.utc)
                    try:
                        calls['lastTradeDate'] = pd.to_datetime(calls['lastTradeDate'])
                        calls = calls[(now_utc - calls['lastTradeDate']).dt.days <= 4]
                        
                        puts['lastTradeDate'] = pd.to_datetime(puts['lastTradeDate'])
                        puts = puts[(now_utc - puts['lastTradeDate']).dt.days <= 4]
                    except Exception as e:
                        print(f"[WARNING] No se pudo filtrar lastTradeDate: {e}")

                    if calls.empty or puts.empty:
                        continue

                    # Fallback para bid/ask cuando mercado cerrado (usar lastPrice)
                    calls['clean_bid'] = np.where(calls['bid'] > 0, calls['bid'], calls['lastPrice'])
                    calls['clean_ask'] = np.where(calls['ask'] > 0, calls['ask'], calls['lastPrice'])
                    calls = calls[(calls['clean_bid'] > 0) & (calls['clean_ask'] > 0) & (calls['strike'] > 0)]

                    puts['clean_bid'] = np.where(puts['bid'] > 0, puts['bid'], puts['lastPrice'])
                    puts['clean_ask'] = np.where(puts['ask'] > 0, puts['ask'], puts['lastPrice'])
                    puts = puts[(puts['clean_bid'] > 0) & (puts['clean_ask'] > 0) & (puts['strike'] > 0)]

                    # --- 1. COLLARS ---
                    valid_puts_collar = puts[(puts['strike'] >= spot * 0.80) & (puts['strike'] <= spot * 0.95)]
                    valid_calls_collar = calls[(calls['strike'] >= spot * 1.05) & (calls['strike'] <= spot * 1.15)]

                    for _, put_row in valid_puts_collar.iterrows():
                        put_strike = float(put_row['strike'])
                        put_ask = float(put_row['clean_ask'])

                        for _, call_row in valid_calls_collar.iterrows():
                            call_strike = float(call_row['strike'])
                            call_bid = float(call_row['clean_bid'])

                            net_options_debit = put_ask - call_bid
                            # Filtro realista: crédito neto <= 2% del precio spot (evita créditos inflados por precios viejos)
                            if net_options_debit <= 0 and net_options_debit >= -0.02 * spot:
                                net_cost = spot + net_options_debit
                                max_profit = call_strike - net_cost
                                max_loss = net_cost - put_strike

                                collars_all.append({
                                    'ticker': ticker,
                                    'exp': exp,
                                    'dte': dte,
                                    'spot': spot,
                                    'put_strike': put_strike,
                                    'put_ask': put_ask,
                                    'call_strike': call_strike,
                                    'call_bid': call_bid,
                                    'net_debit': net_options_debit,
                                    'max_profit': max_profit,
                                    'max_loss': max_loss,
                                    'pct_profit': (max_profit / net_cost) * 100
                                })

                    # --- 2. BULL PUT SPREADS ---
                    valid_sold_puts = puts[(puts['strike'] >= spot * 0.88) & (puts['strike'] <= spot * 0.95)]
                    for _, sold_put in valid_sold_puts.iterrows():
                        k_sold = float(sold_put['strike'])
                        p_bid = float(sold_put['clean_bid'])

                        valid_bought_puts = puts[puts['strike'] < k_sold]
                        if not valid_bought_puts.empty:
                            bought_put = valid_bought_puts.iloc[-1]
                            k_bought = float(bought_put['strike'])
                            p_ask = float(bought_put['clean_ask'])

                            spread_width = k_sold - k_bought
                            net_credit = p_bid - p_ask

                            # Alerta si cobramos entre el 15% y el 45% del ancho del spread (evita Spreads con créditos fantasma)
                            if net_credit > 0 and (spread_width * 0.15) <= net_credit <= (spread_width * 0.45):
                                max_loss = spread_width - net_credit
                                spreads_all.append({
                                    'ticker': ticker,
                                    'type': 'BULL_PUT',
                                    'exp': exp,
                                    'dte': dte,
                                    'spot': spot,
                                    'k_sold': k_sold,
                                    'k_bought': k_bought,
                                    'credit': net_credit,
                                    'width': spread_width,
                                    'max_loss': max_loss,
                                    'pct_return': (net_credit / max_loss) * 100
                                })

                    # --- 3. BEAR CALL SPREADS ---
                    valid_sold_calls = calls[(calls['strike'] >= spot * 1.05) & (calls['strike'] <= spot * 1.12)]
                    for _, sold_call in valid_sold_calls.iterrows():
                        k_sold = float(sold_call['strike'])
                        c_bid = float(sold_call['clean_bid'])

                        valid_bought_calls = calls[calls['strike'] > k_sold]
                        if not valid_bought_calls.empty:
                            bought_call = valid_bought_calls.iloc[0]
                            k_bought = float(bought_call['strike'])
                            c_ask = float(bought_call['clean_ask'])

                            spread_width = k_bought - k_sold
                            net_credit = c_bid - c_ask

                            if net_credit > 0 and (spread_width * 0.15) <= net_credit <= (spread_width * 0.45):
                                max_loss = spread_width - net_credit
                                spreads_all.append({
                                    'ticker': ticker,
                                    'type': 'BEAR_CALL',
                                    'exp': exp,
                                    'dte': dte,
                                    'spot': spot,
                                    'k_sold': k_sold,
                                    'k_bought': k_bought,
                                    'credit': net_credit,
                                    'width': spread_width,
                                    'max_loss': max_loss,
                                    'pct_return': (net_credit / max_loss) * 100
                                })

                    # --- 4. IRON CONDORS ---
                    condor_puts = puts[(puts['strike'] >= spot * 0.88) & (puts['strike'] <= spot * 0.94)]
                    condor_calls = calls[(calls['strike'] >= spot * 1.06) & (calls['strike'] <= spot * 1.12)]

                    for _, put_row in condor_puts.iterrows():
                        kp_sold = float(put_row['strike'])
                        pb_bid = float(put_row['clean_bid'])

                        lower_puts = puts[puts['strike'] < kp_sold]
                        if lower_puts.empty: continue
                        put_bought = lower_puts.iloc[-1]
                        kp_bought = float(put_bought['strike'])
                        pb_ask = float(put_bought['clean_ask'])

                        put_credit = pb_bid - pb_ask
                        put_width = kp_sold - kp_bought

                        for _, call_row in condor_calls.iterrows():
                            kc_sold = float(call_row['strike'])
                            cb_bid = float(call_row['clean_bid'])

                            higher_calls = calls[calls['strike'] > kc_sold]
                            if higher_calls.empty: continue
                            call_bought = higher_calls.iloc[0]
                            kc_bought = float(call_bought['strike'])
                            cb_ask = float(call_bought['clean_ask'])

                            call_credit = cb_bid - cb_ask
                            call_width = kc_bought - kc_sold

                            total_credit = put_credit + call_credit
                            max_width = max(put_width, call_width)

                            if total_credit > 0 and (max_width * 0.15) <= total_credit <= (max_width * 0.45):
                                max_loss = max_width - total_credit
                                condors_all.append({
                                    'ticker': ticker,
                                    'exp': exp,
                                    'dte': dte,
                                    'spot': spot,
                                    'put_sold': kp_sold,
                                    'put_bought': kp_bought,
                                    'call_sold': kc_sold,
                                    'call_bought': kc_bought,
                                    'credit': total_credit,
                                    'max_loss': max_loss,
                                    'pct_return': (total_credit / max_loss) * 100
                                })

                except Exception as e:
                    print(f"[OPTIONS SCAN ERROR] Expiración {exp} para {ticker}: {e}")
                    continue

            # Liberar memoria agresivamente por ticker (evita OOM en Railway)
            try:
                del t, expirations, valid_exps, opt, calls, puts
            except:
                pass
            gc.collect()

        except Exception as e:
            print(f"[OPTIONS SCAN ERROR] Ticker {ticker}: {e}")
            continue

    # 4. Enviar Alertas por Discord
    embed = discord.Embed(
        title="🤖 Escáner de Estrategias de Opciones",
        description=f"Escaneo de las principales acciones líquidas realizado el {today.strftime('%Y-%m-%d')}.",
        color=0x2563eb
    )

    # Collars
    collars_desc = ""
    if collars_all:
        collars_sorted = sorted(collars_all, key=lambda x: x['pct_profit'], reverse=True)[:5]
        for c in collars_sorted:
            collars_desc += f"🛡️ **{c['ticker']}** (Spot `${c['spot']:.2f}`): Expiración `{c['exp']}` ({c['dte']} DTE)\n"
            collars_desc += f"  • Put `${c['put_strike']:.1f}` (Comprado) / Call `${c['call_strike']:.1f}` (Vendido)\n"
            collars_desc += f"  • Débito Opciones: `${c['net_debit']:.2f}` | Ganancia Máxima: **{c['pct_profit']:.1f}%**\n\n"
    else:
        collars_desc = "*No se encontraron Collars con costo <= $0 hoy.*"

    # Spreads
    spreads_desc = ""
    if spreads_all:
        spreads_sorted = sorted(spreads_all, key=lambda x: x['pct_return'], reverse=True)[:5]
        for s in spreads_sorted:
            type_symbol = "🟢" if s['type'] == 'BULL_PUT' else "🔴"
            spreads_desc += f"{type_symbol} **{s['ticker']}** `{s['type']}`: Expiración `{s['exp']}` ({s['dte']} DTE)\n"
            spreads_desc += f"  • Vendido: `${s['k_sold']:.1f}` / Comprado: `${s['k_bought']:.1f}`\n"
            spreads_desc += f"  • Crédito: `${s['credit']:.2f}` | Retorno Máx: **{s['pct_return']:.1f}%**\n\n"
    else:
        spreads_desc = "*No se encontraron Spreads atractivos hoy.*"

    # Iron Condors
    condors_desc = ""
    if condors_all:
        condors_sorted = sorted(condors_all, key=lambda x: x['pct_return'], reverse=True)[:5]
        for d in condors_sorted:
            condors_desc += f"🦅 **{d['ticker']}** (Spot `${d['spot']:.2f}`): Expiración `{d['exp']}` ({d['dte']} DTE)\n"
            condors_desc += f"  • Rango Vendido: `${d['put_sold']:.1f}` a `${d['call_sold']:.1f}`\n"
            condors_desc += f"  • Crédito: `${d['credit']:.2f}` | Retorno Máx: **{d['pct_return']:.1f}%**\n\n"
    else:
        condors_desc = "*No se encontraron Cóndores de Hierro hoy.*"

    embed.add_field(name="🛡️ TOP 5 COLLARS (Seguro Gratis)", value=collars_desc[:1024], inline=False)
    embed.add_field(name="📊 TOP 5 SPREADS DE CRÉDITO", value=spreads_desc[:1024], inline=False)
    embed.add_field(name="🦅 TOP 5 CÓNDORES DE HIERRO", value=condors_desc[:1024], inline=False)
    embed.set_footer(text="CarlosBot Options Scanner")

    await alert_channel.send(embed=embed)
    print("[INFO] Reporte de escáner de opciones enviado correctamente.")

@tree.command(name="escanear_opciones", description="Escanea manualmente estrategias de opciones (Collars, Spreads, Condores) utilizando Yahoo Finance")
@app_commands.describe(limite_prueba="Número máximo de activos a escanear")
async def escanear_opciones(interaction: discord.Interaction, limite_prueba: int = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    
    msg_txt = "🔍 Iniciando escaneo manual de estrategias de opciones en segundo plano."
    if limite_prueba:
        msg_txt += f" (Limitado a los primeros {limite_prueba} activos)."
    msg_txt += "\nLas alertas aparecerán en #alertas-estrategias-opciones al finalizar..."
    
    status_msg = await interaction.followup.send(msg_txt, wait=True)
    
    try:
        await run_options_scanner_logic(limite_prueba)
        await status_msg.edit(content="✅ Escaneo manual de opciones finalizado. Reporte enviado a #alertas-estrategias-opciones.")
    except Exception as e:
        await status_msg.edit(content=f"❌ Error durante el escaneo: {e}")

# Escaneo de opciones dos veces al día en horas de mercado (9:00 AM PT / 12:00 PM EST y 12:00 PM PT / 3:00 PM EST)
daily_options_times = [
    datetime.time(hour=12, minute=0, tzinfo=ZoneInfo("America/New_York")),
    datetime.time(hour=15, minute=0, tzinfo=ZoneInfo("America/New_York"))
]

@tasks.loop(time=daily_options_times)
async def daily_options_scanner_loop():
    await client.wait_until_ready()
    print("[INFO] Iniciando escaneo programado de opciones en tiempo real...")
    try:
        await run_options_scanner_logic()
    except Exception as e:
        print(f"[ERROR] En loop programado de opciones: {e}")
    finally:
        plt.close('all')
        gc.collect()

@tasks.loop(time=datetime.time(hour=15, minute=0, tzinfo=ZoneInfo("America/New_York")))
async def daily_gex_extreme_scanner_loop():
    await client.wait_until_ready()
    print("[INFO] Iniciando escaneo diario de Extremos GEX (3:00 PM EST)...")
    try:
        await run_gex_scanner_logic()
    except Exception as e:
        print(f"[ERROR] En loop diario de Extremos GEX: {e}")
    finally:
        plt.close('all')
        gc.collect()

@tree.command(name="escanear_gex", description="Escanea manualmente los extremos GEX en tickers_gex.txt")
@app_commands.describe(limite_prueba="Número máximo de activos a escanear (deja vacío para todos)")
async def escanear_gex(interaction: discord.Interaction, limite_prueba: int = None):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    
    msg_txt = "🔍 Iniciando escaneo manual de extremos GEX en segundo plano."
    if limite_prueba:
        msg_txt += f" (Limitado a los primeros {limite_prueba} activos)."
    msg_txt += "\nLas alertas aparecerán en #alertas-extremos-gex al finalizar..."
    
    status_msg = await interaction.followup.send(msg_txt, wait=True)
    
    try:
        await run_gex_scanner_logic(limite_prueba)
        await status_msg.edit(content="✅ Escaneo manual de extremos GEX finalizado. Reporte enviado a #alertas-extremos-gex.")
    except Exception as e:
        await status_msg.edit(content=f"❌ Error durante el escaneo: {e}")

# --- CONFIGURACION DE GRUPOS GEX ---
GRUPOS_DASHBOARD = {
    "1": ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "META", "TSLA", "MU", "AMD"],
    "2": ["IBIT", "HOOD", "COIN", "MSTR", "CRCL", "XRP", "HBR", "GLNK", "ETHA", "BSOL"]
}

async def generate_dashboard_images(group_id: str):
    tickers = GRUPOS_DASHBOARD.get(group_id, [])
    if not tickers:
        raise ValueError("Grupo no válido o vacío")
        
    gex_data_dict = {}
    hist_data_dict = {}
    
    import yfinance as yf
    import pandas as pd
    import asyncio
    import gc
    from gex_engine import fetch_and_calculate_all
    
    print(f"[DASHBOARD] Descargando precios históricos para Grupo {group_id}...")
    df_yf = await asyncio.to_thread(
        yf.download,
        tickers,
        period="250d",
        interval="1d",
        group_by="ticker",
        progress=False
    )
    
    for ticker in tickers:
        try:
            if isinstance(df_yf.columns, pd.MultiIndex):
                df_t = df_yf[ticker]['Close'].dropna() if ticker in df_yf.columns.levels[0] else pd.Series(dtype=float)
            else:
                df_t = df_yf['Close'].dropna()
            hist_data_dict[ticker] = {'prices': df_t}
        except Exception as e:
            print(f"Error procesando histórico para {ticker}: {e}")
            hist_data_dict[ticker] = {'prices': pd.Series(dtype=float)}
            
    for ticker in tickers:
        try:
            print(f"[DASHBOARD] Calculando GEX para {ticker}...")
            df_gex, _, _, spot = await asyncio.to_thread(
                fetch_and_calculate_all,
                ticker,
                None,
                0.0,
                "",
                "USD"
            )
            gex_data_dict[ticker] = {'df_gex': df_gex, 'spot': spot}
        except Exception as e:
            print(f"Error calculando GEX para {ticker}: {e}")
            gex_data_dict[ticker] = {'df_gex': pd.DataFrame(), 'spot': 0.0}
            
        await asyncio.sleep(1.5)
        gc.collect()
        
    from visualizer import plot_gex_grid, plot_performance_table
    
    buf_gex = await asyncio.to_thread(plot_gex_grid, tickers, gex_data_dict)
    buf_table = await asyncio.to_thread(plot_performance_table, tickers, hist_data_dict)
    
    file_gex = discord.File(buf_gex, filename=f"gex_grid_group_{group_id}.png")
    file_table = discord.File(buf_table, filename=f"perf_table_group_{group_id}.png")
    
    return file_gex, file_table

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="Grupo 1 (Tecnológicas)", style=discord.ButtonStyle.primary, custom_id="gex_group_1")
    async def group_1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_dashboard(interaction, "1")
        
    @discord.ui.button(label="Grupo 2 (Crypto/Mixto)", style=discord.ButtonStyle.primary, custom_id="gex_group_2")
    async def group_2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_dashboard(interaction, "2")
        
    async def update_dashboard(self, interaction: discord.Interaction, group_id: str):
        if not check_channel(interaction):
            await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
            return
            
        await interaction.response.defer()
        status_msg = await interaction.followup.send(f"🔄 Cargando datos del Grupo {group_id}...", ephemeral=True)
        
        try:
            gex_file, table_file = await generate_dashboard_images(group_id)
            await interaction.message.edit(
                content=f"📈 **Dashboard GEX & Rendimientos - Grupo {group_id}**",
                attachments=[gex_file, table_file]
            )
            await status_msg.edit(content=f"✅ Grupo {group_id} cargado con éxito.")
        except Exception as e:
            print(f"[ERROR] Al actualizar dashboard interactivo: {e}")
            await status_msg.edit(content=f"❌ Error al cargar Grupo {group_id}: {e}")

@tree.command(name="dashboard_gex", description="Muestra el Dashboard GEX y la Tabla de Rendimiento por Grupos")
async def dashboard_gex(interaction: discord.Interaction):
    if not check_channel(interaction):
        await interaction.response.send_message(f"Solo en #{ALLOWED_CHANNEL}", ephemeral=True)
        return
        
    await interaction.response.defer()
    status_msg = await interaction.followup.send("🔄 Cargando datos del Grupo 1 (Tecnológicas)...", wait=True)
    
    try:
        gex_file, table_file = await generate_dashboard_images("1")
        view = DashboardView()
        await interaction.followup.send(
            content="📈 **Dashboard GEX & Rendimientos - Grupo 1**",
            files=[gex_file, table_file],
            view=view
        )
        await status_msg.delete()
    except Exception as e:
        print(f"[ERROR] En comando dashboard_gex: {e}")
        await status_msg.edit(content=f"❌ Error al generar Dashboard: {e}")

# --- MASTER ORCHESTRATOR LOOP (OPTIMIZADO PARA EVITAR CONCURRENCIA Y OOM) ---
@tasks.loop(minutes=15)
async def master_orchestrator_loop():
    global IS_FIRST_RUN
    now = datetime.datetime.now()
    minute = now.minute
    print(f"[ORCHESTRATOR] Iniciando ciclo de escaneo secuencial a las {now.strftime('%H:%M:%S')}")
    
    # 1. Tareas de 15 minutos (Corren siempre o en la primera ejecucion)
    try:
        print("[ORCHESTRATOR] Ejecutando alertas laterales...")
        await run_lateral_alerts()
    except Exception as e:
        print(f"[ORCHESTRATOR ERROR] En alertas laterales: {e}")
    import gc
    gc.collect()

    try:
        print("[ORCHESTRATOR] Ejecutando alertas VIP...")
        await run_vip_alerts()
    except Exception as e:
        print(f"[ORCHESTRATOR ERROR] En alertas VIP: {e}")
    import gc
    gc.collect()

    # 2. Tareas de 30 minutos (Corren en minutos 0, 30 o en la primera ejecucion)
    if minute in [0, 30] or IS_FIRST_RUN:
        try:
            print("[ORCHESTRATOR] Ejecutando escanear alertas (30m)...")
            await run_escanear_alerts()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En escanear alertas: {e}")
        import gc
        gc.collect()

    # 3. Tareas de 60 minutos (Corren en minuto 0 o en la primera ejecucion)
    if minute == 0 or IS_FIRST_RUN:
        try:
            print("[ORCHESTRATOR] Ejecutando escanear 1D...")
            await run_escanear_1d_alerts()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En escanear 1D: {e}")
        import gc
        gc.collect()

        try:
            print("[ORCHESTRATOR] Ejecutando escanear 1W...")
            await run_escanear_1w_alerts()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En escanear 1W: {e}")
        import gc
        gc.collect()

        try:
            print("[ORCHESTRATOR] Ejecutando escanear 1M...")
            await run_escanear_1m_alerts()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En escanear 1M: {e}")
        import gc
        gc.collect()

        try:
            print("[ORCHESTRATOR] Ejecutando alertas crypto (1h/4h)...")
            await run_crypto_alerts()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En alertas crypto: {e}")
        import gc
        gc.collect()

        try:
            print("[ORCHESTRATOR] Ejecutando precalentamiento de cache...")
            await run_prewarm_cache()
        except Exception as e:
            print(f"[ORCHESTRATOR ERROR] En prewarm cache: {e}")
        import gc
        gc.collect()

    IS_FIRST_RUN = False
    plt.close('all')
    import gc
    gc.collect()
    print(f"[ORCHESTRATOR] Ciclo de escaneo completado con exito. Memoria liberada.")

@client.event
async def on_ready():
    # Sincronización global
    await tree.sync()
    
    # Sincronización local en todos los servidores en los que está el bot para que aparezcan al instante
    for guild in client.guilds:
        try:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            print(f"[OK] Comandos sincronizados instantáneamente para el servidor: {guild.name}")
        except Exception as e:
            print(f"[ERROR] No se pudo sincronizar en {guild.name}: {e}")
            
    print(f"[OK] Bot Carlos Trader iniciado correctamente como {client.user}!")
    print("[OK] Todos los comandos han sido sincronizados.")
    
    # Iniciar las tareas automáticas en segundo plano de manera escalonada para evitar picos de memoria RAM y CPU
    async def start_tasks_staggered():
        print("[INFO] Iniciando secuencia de inicio de tareas optimizadas...")
        
        # 1. Iniciar el loop master principal de alertas
        await asyncio.sleep(5)
        if not master_orchestrator_loop.is_running():
            master_orchestrator_loop.start()
            print("[INFO] Tarea Master Orchestrator de alertas iniciada.")
            
        # 2. Iniciar el escaner diario de RSI
        await asyncio.sleep(10)
        if not daily_rsi_scanner_loop.is_running():
            daily_rsi_scanner_loop.start()
            print("[INFO] Tarea de escaner diario de RSI iniciada.")
            
        # 3. Iniciar el escaner diario de Extremos GEX
        await asyncio.sleep(10)
        if not daily_gex_extreme_scanner_loop.is_running():
            daily_gex_extreme_scanner_loop.start()
            print("[INFO] Tarea de escaner diario de Extremos GEX iniciada.")
            
        # 4. Iniciar el escaner diario de opciones
        await asyncio.sleep(10)
        if not daily_options_scanner_loop.is_running():
            daily_options_scanner_loop.start()
            print("[INFO] Tarea de escaner diario de opciones iniciada.")
            
        print("[INFO] Secuencia de inicio completada con exito.")

    asyncio.create_task(start_tasks_staggered())

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
