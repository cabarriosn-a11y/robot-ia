"""
╔══════════════════════════════════════════════════════════════════╗
║     ROBOT IA OTC v6.1 — ESTRATEGIA GANADORA DEL BACKTEST      ║
║                                                                 ║
║  Estrategia principal (233 trades, 56.7% WR):                 ║
║  Extremo_20 + Pin_bar + Precio_lejos_EMA                      ║
║                                                                 ║
║  Estrategia secundaria (43 trades, 62.8% WR):                 ║
║  RSI_giro + BB_toque + Precio_lejos_EMA                       ║
║                                                                 ║
║  v6.1 cambios:                                                 ║
║  + Fix KeyError 'underlying' de IQ Option                     ║
║  + Historial persistente en Google Sheets                     ║
║  + Notificacion Telegram en errores criticos                  ║
╚══════════════════════════════════════════════════════════════════╝

VARIABLES DE ENTORNO en Railway:
    IQ_EMAIL             → tu email de IQ Option
    IQ_PASSWORD          → tu contraseña de IQ Option
    GROQ_KEY             → tu API key de Groq
    TELEGRAM_TOKEN       → token de tu bot de Telegram
    TELEGRAM_CHAT        → tu chat ID de Telegram
    SHEET_ID             → ID de tu Google Sheet
    GOOGLE_CREDENTIALS   → contenido JSON de la cuenta de servicio (una linea)
"""

import os
import sys
import io
import time
import csv
import json
import logging
import re
from datetime import datetime, date
from collections import deque, defaultdict

import pandas as pd

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("[ERROR] pip install git+https://github.com/iqoptionapi/iqoptionapi.git")
    sys.exit(1)

try:
    from groq import Groq
except ImportError:
    print("[ERROR] pip install groq")
    sys.exit(1)

try:
    import gspread
    from google.oauth2.service_account import Credentials
    SHEETS_DISPONIBLE = True
except ImportError:
    print("[WARN] gspread no instalado — usando solo CSV local")
    SHEETS_DISPONIBLE = False

import urllib.request
import urllib.parse

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    'iq_email':    os.environ.get('IQ_EMAIL',    ''),
    'iq_password': os.environ.get('IQ_PASSWORD', ''),
    'iq_modo':     'PRACTICE',
    'groq_key':    os.environ.get('GROQ_KEY', ''),
    'min_confianza': 8,

    'telegram_token':   os.environ.get('TELEGRAM_TOKEN', ''),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT',  ''),

    'sheet_id':          os.environ.get('SHEET_ID', ''),
    'google_credentials': os.environ.get('GOOGLE_CREDENTIALS', ''),

    # GBPUSD eliminado por backtest (49.7% WR)
    'pares_otc': [
        'EURUSD-OTC',
        'EURGBP-OTC',
        'EURJPY-OTC',
        'AUDCAD-OTC',
    ],

    'monto_por_trade':    1,
    'max_perdidas_dia':   5,
    'max_racha_loss_par': 2,

    'dist_ema_pct':       0.06,
    'lookback_extremo':   20,
    'pin_ratio_max':      0.35,
    'dist_ema_sec':       0.05,
    'rsi_giro_nivel':     38,
    'rsi_giro_nivel_put': 62,

    'candles_cantidad': 100,
    'candle_size':      60,
    'sleep_scan':       60,
}

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('robot_ia_v6.log', encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def tg(msg: str):
    token   = CONFIG['telegram_token']
    chat_id = CONFIG['telegram_chat_id']
    if not token or not chat_id:
        return
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=data), timeout=10
        )
    except Exception as e:
        log.warning(f"[TG] Error: {e}")

def tg_inicio(balance: float):
    tg(
        f"<b>🤖 ROBOT IA OTC v6.1 ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Modo: <b>{CONFIG['iq_modo']}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"Estrategia: <b>Ganadora del Backtest</b>\n"
        f"Principal: Extremo20 + Pin bar + Precio lejos EMA\n"
        f"Secundaria: RSI giro + BB toque + Precio lejos EMA\n"
        f"Historial: <b>Google Sheets ✅</b>\n"
        f"Opera: <b>24/7</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>233 trades historicos — WR 56.7%</i>"
    )

def tg_entrada(par, direccion, confianza, estrategia, razon,
               precio, expiracion, modelo, stats):
    emoji = "📈" if direccion == "call" else "📉"
    total = stats['wins'] + stats['losses']
    wr    = round(stats['wins'] / total * 100, 1) if total > 0 else 0
    tg(
        f"<b>{emoji} OPERACION — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>{'CALL ☝️' if direccion=='call' else 'PUT 👇'}</b>\n"
        f"Precio: <b>{precio}</b>\n"
        f"Estrategia: <b>{estrategia}</b>\n"
        f"Monto: ${CONFIG['monto_por_trade']} | Exp: <b>{expiracion} min</b>\n"
        f"🧠 Confianza IA: <b>{confianza}/10</b>\n"
        f"🤖 Modelo: <i>{modelo}</i>\n"
        f"📋 <i>{razon}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Hoy: <b>{stats['wins']}W / {stats['losses']}L</b>"
        + (f" | WR: {wr}%" if total > 0 else "")
    )

def tg_resultado(par, direccion, resultado, ganancia, balance, wins, losses):
    emoji = "✅" if resultado == 'win' else "❌"
    signo = f"+${ganancia:.2f}" if ganancia > 0 else f"-${abs(ganancia):.2f}"
    total = wins + losses
    wr    = round(wins / total * 100, 1) if total > 0 else 0
    barra = ("🟢" * wins) + ("🔴" * losses)
    tg(
        f"<b>{emoji} {'WIN' if resultado=='win' else 'LOSS'} — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'CALL' if direccion=='call' else 'PUT'}: <b>{signo}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ {wins}W  ❌ {losses}L  |  WR: <b>{wr}%</b>\n"
        f"{barra}"
    )

def tg_resumen_diario(stats: dict):
    total = stats['wins'] + stats['losses']
    wr    = round(stats['wins'] / total * 100, 1) if total > 0 else 0
    tg(
        f"<b>📊 RESUMEN DEL DIA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Trades: {total}\n"
        f"✅ Wins:   <b>{stats['wins']}</b>\n"
        f"❌ Losses: <b>{stats['losses']}</b>\n"
        f"📈 WR:     <b>{wr}%</b>\n"
        f"💰 P&L:    <b>${stats['pnl']:+.2f}</b>"
    )

def tg_reactivado(balance: float):
    tg(f"<b>🟢 BOT REACTIVADO</b>\nNuevo dia\nBalance: <b>${balance:.2f}</b>")

def tg_pausa_practica(losses: int):
    tg(f"<b>⚠️ {losses} perdidas hoy</b>\nModo PRACTICE — continua operando para aprender.")

def tg_stop_real(losses: int):
    tg(f"<b>⏸️ BOT EN PAUSA</b>\nLimite de {losses} perdidas.\n<b>Se reactiva manana 🔄</b>")

# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE SHEETS — HISTORIAL PERSISTENTE
# ─────────────────────────────────────────────────────────────────────────────
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
SHEET_HEADERS = [
    'fecha','hora','par','direccion','monto','confianza',
    'estrategia','expiracion','razon','resultado','ganancia','balance'
]
CSV_FILE = 'trades_ia.csv'

_sheet = None

def conectar_sheet():
    """Retorna la hoja de trades. None si falla."""
    if not SHEETS_DISPONIBLE:
        return None
    try:
        creds_json = CONFIG['google_credentials']
        sheet_id   = CONFIG['sheet_id']
        if not creds_json or not sheet_id:
            log.warning("[SHEET] Sin credenciales — usando CSV local")
            return None
        creds  = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        client = gspread.authorize(creds)
        sh     = client.open_by_key(sheet_id)
        try:
            hoja = sh.worksheet('Trades')
        except gspread.WorksheetNotFound:
            hoja = sh.add_worksheet(title='Trades', rows=5000, cols=12)
            hoja.append_row(SHEET_HEADERS)
            log.info("[SHEET] Hoja 'Trades' creada con headers")
        log.info("[SHEET] ✅ Conectado a Google Sheets")
        return hoja
    except Exception as e:
        log.error(f"[SHEET] Error conectando: {e}")
        return None

def get_sheet():
    global _sheet
    if _sheet is None:
        _sheet = conectar_sheet()
    return _sheet

def guardar_trade(par, direccion, confianza, estrategia, razon,
                  expiracion, resultado, ganancia, balance):
    """Guarda en Google Sheets Y en CSV local como backup."""
    fila = [
        date.today().isoformat(),
        datetime.now().strftime('%H:%M:%S'),
        par, direccion, CONFIG['monto_por_trade'],
        confianza, estrategia, expiracion,
        str(razon)[:80], resultado,
        round(ganancia, 2), round(balance, 2)
    ]
    # 1. Google Sheets (persistente)
    try:
        hoja = get_sheet()
        if hoja:
            hoja.append_row(fila)
            log.info("[SHEET] ✅ Trade guardado en Google Sheets")
    except Exception as e:
        log.warning(f"[SHEET] Error guardando: {e}")
        global _sheet
        _sheet = None  # resetear para reconectar proxima vez

    # 2. CSV local (backup)
    try:
        existe = os.path.exists(CSV_FILE)
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if not existe:
                w.writerow(SHEET_HEADERS)
            w.writerow(fila)
    except Exception as e:
        log.warning(f"[CSV] Error guardando local: {e}")

def leer_stats_hoy_desde_sheet() -> dict:
    """Recupera wins/losses/pnl del dia actual desde Google Sheets."""
    stats = {'total':0,'wins':0,'losses':0,'pnl':0.0}
    try:
        hoja = get_sheet()
        if not hoja:
            return _leer_stats_csv()
        registros = hoja.get_all_records()
        if not registros:
            return stats
        hoy = date.today().isoformat()
        trades_hoy = [r for r in registros if str(r.get('fecha','')) == hoy]
        for t in trades_hoy:
            if t.get('resultado') == 'win':
                stats['wins'] += 1
                stats['pnl']  += float(t.get('ganancia', 0))
            elif t.get('resultado') == 'loss':
                stats['losses'] += 1
                stats['pnl']   += float(t.get('ganancia', 0))
        stats['total'] = stats['wins'] + stats['losses']
        stats['pnl']   = round(stats['pnl'], 2)
        log.info(f"[SHEET] Stats hoy: {stats['wins']}W/{stats['losses']}L "
                 f"P&L ${stats['pnl']:+.2f}")
        return stats
    except Exception as e:
        log.warning(f"[SHEET] Error leyendo stats: {e} — usando CSV")
        return _leer_stats_csv()

def _leer_stats_csv() -> dict:
    stats = {'total':0,'wins':0,'losses':0,'pnl':0.0}
    if not os.path.exists(CSV_FILE):
        return stats
    try:
        df  = pd.read_csv(CSV_FILE)
        hoy = df[df['fecha'] == date.today().isoformat()]
        if hoy.empty: return stats
        stats['wins']   = len(hoy[hoy['resultado'] == 'win'])
        stats['losses'] = len(hoy[hoy['resultado'] == 'loss'])
        stats['total']  = stats['wins'] + stats['losses']
        stats['pnl']    = round(hoy['ganancia'].sum(), 2)
        return stats
    except:
        return stats

def leer_memoria() -> str:
    """Lee historial desde Sheets o CSV para el prompt de la IA."""
    try:
        hoja = get_sheet()
        if not hoja:
            return _leer_memoria_csv()
        registros = hoja.get_all_records()
        if not registros:
            return "Sin historial."
        ultimos = registros[-30:]
        total   = len(ultimos)
        wins    = sum(1 for r in ultimos if r.get('resultado') == 'win')
        losses  = total - wins
        wr      = round(wins / total * 100, 1) if total > 0 else 0
        resumen = ""
        pares   = set(r['par'] for r in ultimos)
        for par in pares:
            sub = [r for r in ultimos if r['par'] == par]
            pw  = sum(1 for r in sub if r.get('resultado') == 'win')
            pl  = len(sub) - pw
            pwr = round(pw / len(sub) * 100) if sub else 0
            resumen += f"{par}:{pw}W/{pl}L({pwr}%) "
        u5 = " ".join(
            f"{r['par']} {str(r.get('direccion','')).upper()}={r.get('resultado','')}"
            for r in registros[-5:]
        )
        return f"T:{total} {wins}W/{losses}L WR:{wr}% | {resumen}| U5:{u5}"
    except:
        return _leer_memoria_csv()

def leer_racha_par(par: str) -> int:
    """Racha de perdidas del par desde Sheets o CSV."""
    try:
        hoja = get_sheet()
        if not hoja:
            return _leer_racha_csv(par)
        registros  = hoja.get_all_records()
        trades_par = [r for r in registros if r.get('par') == par][-10:]
        if not trades_par: return 0
        racha = 0
        for r in reversed(trades_par):
            if r.get('resultado') == 'loss': racha += 1
            else: break
        return racha
    except:
        return _leer_racha_csv(par)

def _leer_memoria_csv() -> str:
    if not os.path.exists(CSV_FILE): return "Sin historial."
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty: return "Sin historial."
        u  = df.tail(30)
        w  = len(u[u['resultado']=='win'])
        l  = len(u) - w
        wr = round(w/len(u)*100,1) if len(u)>0 else 0
        return f"T:{len(u)} {w}W/{l}L WR:{wr}%"
    except: return "Error historial."

def _leer_racha_csv(par: str) -> int:
    if not os.path.exists(CSV_FILE): return 0
    try:
        df = pd.read_csv(CSV_FILE)
        dp = df[df['par']==par].tail(10)
        if dp.empty: return 0
        racha = 0
        for r in reversed(dp['resultado'].tolist()):
            if r == 'loss': racha += 1
            else: break
        return racha
    except: return 0

# ─────────────────────────────────────────────────────────────────────────────
# CALCULAR INDICADORES
# ─────────────────────────────────────────────────────────────────────────────
def calcular_indicadores(df: pd.DataFrame) -> dict:
    c = df['close']

    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(span=14, adjust=False).mean()
    rsi_s = 100 - (100 / (1 + gain / loss))
    rsi   = float(rsi_s.iloc[-1])
    rsi_p = float(rsi_s.iloc[-2])

    ema9  = float(c.ewm(span=9,  adjust=False).mean().iloc[-1])
    ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    ml  = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ms  = ml.ewm(span=9, adjust=False).mean()
    mh  = float((ml - ms).iloc[-1])
    mhp = float((ml - ms).iloc[-2])

    sma   = c.rolling(20).mean()
    std   = c.rolling(20).std()
    bb_up = float((sma + 2*std).iloc[-1])
    bb_dn = float((sma - 2*std).iloc[-1])

    hl    = df['high'] - df['low']
    atr   = float(hl.rolling(14).mean().iloc[-1])
    atr_r = round(atr / float(c.iloc[-1]) * 100, 4)

    va    = df['volume'].rolling(20).mean()
    vr    = float((df['volume'] / va).iloc[-1])

    precio = float(c.iloc[-1])

    max20  = float(df['high'].rolling(20).max().iloc[-1])
    min20  = float(df['low'].rolling(20).min().iloc[-1])
    en_max = precio >= max20 * 0.998
    en_min = precio <= min20 * 1.002
    extremo_dir = 'put' if en_max else 'call' if en_min else None

    cuerpo  = abs(float(df['close'].iloc[-1]) - float(df['open'].iloc[-1]))
    rango   = float(df['high'].iloc[-1]) - float(df['low'].iloc[-1])
    ratio   = cuerpo / (rango + 1e-10)
    es_pin  = ratio < CONFIG['pin_ratio_max']
    vela_verde = float(df['close'].iloc[-1]) >= float(df['open'].iloc[-1])
    pin_dir = 'call' if (es_pin and vela_verde) else 'put' if (es_pin and not vela_verde) else None

    dist_ema21 = (precio - ema21) / precio * 100
    ema_dir = 'call' if dist_ema21 < -CONFIG['dist_ema_pct'] else \
              'put'  if dist_ema21 >  CONFIG['dist_ema_pct'] else None

    rsi_giro_dir = None
    if rsi < CONFIG['rsi_giro_nivel']     and rsi > rsi_p: rsi_giro_dir = 'call'
    if rsi > CONFIG['rsi_giro_nivel_put'] and rsi < rsi_p: rsi_giro_dir = 'put'

    bb_dir = None
    if precio <= bb_dn: bb_dir = 'call'
    if precio >= bb_up: bb_dir = 'put'

    cambio3 = (precio - float(c.iloc[-4])) / float(c.iloc[-4]) * 100
    mom_dir = 'call' if cambio3 < -0.15 else 'put' if cambio3 > 0.15 else None

    v1 = float(df['close'].iloc[-1]) >= float(df['open'].iloc[-1])
    v2 = float(df['close'].iloc[-2]) >= float(df['open'].iloc[-2])
    v2_dir = 'call' if (not v1 and not v2) else 'put' if (v1 and v2) else None

    # Estrategia 1 — Principal (233 trades, 56.7% WR)
    est1_dirs   = [extremo_dir, pin_dir, ema_dir]
    est1_validas = [d for d in est1_dirs if d is not None]
    est1_senal  = None
    if len(est1_validas) == 3 and len(set(est1_validas)) == 1:
        est1_senal = est1_validas[0]

    # Estrategia 2 — Secundaria (43 trades, 62.8% WR)
    est2_dirs   = [rsi_giro_dir, bb_dir, ema_dir]
    est2_validas = [d for d in est2_dirs if d is not None]
    est2_senal  = None
    if len(est2_validas) == 3 and len(set(est2_validas)) == 1:
        est2_senal = est2_validas[0]

    # Estrategia 3 — Bonus (38 trades, 57.9% WR)
    vela_grande = cuerpo > float((df['close'] - df['open']).abs().rolling(20).mean().iloc[-1]) * 1.5
    vg_dir = 'call' if (vela_grande and not vela_verde) else \
             'put'  if (vela_grande and vela_verde)     else None
    rsi_ext_dir = None
    if rsi < 25: rsi_ext_dir = 'call'
    if rsi > 75: rsi_ext_dir = 'put'
    est3_dirs   = [rsi_ext_dir, vg_dir, mom_dir]
    est3_validas = [d for d in est3_dirs if d is not None]
    est3_senal  = None
    if len(est3_validas) == 3 and len(set(est3_validas)) == 1:
        est3_senal = est3_validas[0]

    senal_final = None
    estrategia  = None
    if est1_senal:
        senal_final = est1_senal
        estrategia  = 'Principal(Extremo+Pin+EMA)'
    elif est2_senal:
        senal_final = est2_senal
        estrategia  = 'Secundaria(RSI+BB+EMA)'
    elif est3_senal:
        senal_final = est3_senal
        estrategia  = 'Bonus(RSIext+VelaGrande+Mom)'

    ultimas = ['V' if df['close'].iloc[i]>=df['open'].iloc[i] else 'R' for i in range(-5,0)]

    return {
        'precio':        round(precio, 6),
        'rsi':           round(rsi, 2),
        'rsi_estado':    'SC' if rsi>70 else 'SV' if rsi<30 else 'N',
        'rsi_giro':      rsi_giro_dir,
        'ema_tendencia': 'ALC' if ema9>ema21>ema50 else 'BAJ' if ema9<ema21<ema50 else 'MIX',
        'ema21':         round(ema21, 6),
        'dist_ema21':    round(dist_ema21, 4),
        'macd_hist':     round(mh, 8),
        'macd_sube':     mh > mhp,
        'bb_superior':   round(bb_up, 6),
        'bb_inferior':   round(bb_dn, 6),
        'bb_dir':        bb_dir,
        'atr_rel_pct':   atr_r,
        'volumen_rel':   round(vr, 2),
        'ultimas_5':     ultimas,
        'es_pin':        es_pin,
        'pin_dir':       pin_dir,
        'en_max':        en_max,
        'en_min':        en_min,
        'extremo_dir':   extremo_dir,
        'ema_dir':       ema_dir,
        'mom_dir':       mom_dir,
        'cambio3':       round(cambio3, 4),
        'senal':         senal_final,
        'estrategia':    estrategia,
        'est1_senal':    est1_senal,
        'est2_senal':    est2_senal,
        'est3_senal':    est3_senal,
    }

# ─────────────────────────────────────────────────────────────────────────────
# JSON ROBUSTO
# ─────────────────────────────────────────────────────────────────────────────
def parsear_json(texto: str):
    texto = texto.replace('```json','').replace('```','').strip()
    try: return json.loads(texto)
    except: pass
    try:
        m = re.search(r'\{[^{}]+\}', texto, re.DOTALL)
        if m: return json.loads(m.group())
    except: pass
    try:
        if '{' in texto and '}' in texto:
            return json.loads(texto[texto.index('{'):texto.rindex('}')+1])
    except: pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# CEREBRO IA v6.1
# ─────────────────────────────────────────────────────────────────────────────
class CerebroIA:

    MODELOS = [
        'llama3-70b-8192',
        'llama-3.1-8b-instant',
        'gemma2-9b-it',
        'mixtral-8x7b-32768',
    ]

    def __init__(self):
        self.client        = Groq(api_key=CONFIG['groq_key'])
        self.historial     = deque(maxlen=20)
        self.modelo_idx    = 0
        self.modelo_actual = self.MODELOS[0]
        log.info(f"[IA] Groq v6.1 | {self.modelo_actual}")

    def siguiente_modelo(self):
        self.modelo_idx    = (self.modelo_idx+1) % len(self.MODELOS)
        self.modelo_actual = self.MODELOS[self.modelo_idx]
        log.warning(f"[IA] Rotando → {self.modelo_actual}")
        tg(f"⚡ <b>Rotacion modelo</b>\nAhora: <b>{self.modelo_actual}</b>")

    def analizar(self, par, ind, stats, racha_loss) -> dict:
        memoria    = leer_memoria()
        hora       = datetime.now().hour
        senal_bt   = ind['senal']
        estrategia = ind['estrategia']

        prompt = f"""Trader experto OTC IQ Option. Valida señal del backtest.

PAR:{par} H:{hora}h
ESTRATEGIA:{estrategia}
SEÑAL_BACKTEST:{senal_bt}
SKIP:racha>={racha_loss}(max2)

RSI:{ind['rsi']}({ind['rsi_estado']}) RSI_GIRO:{ind['rsi_giro']}
EMA:{ind['ema_tendencia']} DIST_EMA21:{ind['dist_ema21']}%
MACD:{'U' if ind['macd_sube'] else 'D'}{ind['macd_hist']}
BB_DIR:{ind['bb_dir']} PIN:{ind['es_pin']}({ind['pin_dir']})
EXTREMO_20:MAX={ind['en_max']} MIN={ind['en_min']}
CAMBIO3:{ind['cambio3']}% VOL:{ind['volumen_rel']}x
V5:{''.join(ind['ultimas_5'])}

HOY:{stats['wins']}W/{stats['losses']}L ${stats['pnl']:+.2f}
MEM:{memoria}

La señal viene del backtest con 56.7% WR historico.
Tu rol: confirmar si el contexto actual apoya la señal o es mejor skip.
EXP:1m=fuerte,2m=normal,3m=moderado,5m=conservador

SOLO JSON: {{"decision":"{senal_bt}","confianza":8,"expiracion":2,"razon":"max 10 palabras"}}"""

        for _ in range(len(self.MODELOS)):
            try:
                resp = self.client.chat.completions.create(
                    model=self.modelo_actual,
                    messages=[
                        {"role":"system","content":"Solo respondes JSON valido. Sin texto adicional."},
                        {"role":"user","content":prompt}
                    ],
                    max_tokens=100, temperature=0.1,
                )
                texto = resp.choices[0].message.content.strip()
                data  = parsear_json(texto)

                if data is None:
                    return {'decision': senal_bt, 'confianza': 8,
                            'expiracion': 2, 'razon': 'señal backtest',
                            'modelo': self.modelo_actual}

                data['decision'] = str(data.get('decision', senal_bt)).lower().strip()
                if data['decision'] not in ['call','put','skip']:
                    data['decision'] = senal_bt
                if not (1 <= data.get('confianza',0) <= 10):
                    data['confianza'] = 8
                exp = data.get('expiracion', 2)
                data['expiracion'] = exp if exp in [1,2,3,5] else 2
                data['modelo']     = self.modelo_actual

                log.info(f"[IA] {par} → {data['decision'].upper()} "
                         f"({data['confianza']}/10) exp:{data['expiracion']}min "
                         f"[{self.modelo_actual}]")
                return data

            except Exception as e:
                err = str(e)
                if any(x in err for x in ['429','400','rate_limit','decommissioned','tokens']):
                    log.warning(f"[IA] Rotando: {err[:60]}")
                    self.siguiente_modelo()
                    time.sleep(2)
                    continue
                log.error(f"[IA] Error: {e}")
                return {'decision': senal_bt, 'confianza': 8,
                        'expiracion': 2, 'razon': 'fallback backtest',
                        'modelo': self.modelo_actual}

        log.error("[IA] Todos los modelos agotados")
        tg("🔴 <b>Modelos agotados</b> — se reactivaran mañana.")
        return {'decision':'skip','confianza':0,'expiracion':2,
                'razon':'Sin modelos','modelo':'ninguno'}

    def registrar(self, par, decision, resultado=None):
        entry = {'par':par,'decision':decision,'hora':datetime.now().strftime('%H:%M')}
        if resultado: entry['resultado'] = resultado
        self.historial.append(entry)


# ─────────────────────────────────────────────────────────────────────────────
# BOT PRINCIPAL v6.1
# ─────────────────────────────────────────────────────────────────────────────
class RobotIAOTC:

    def __init__(self):
        self.api       = None
        self.ia        = CerebroIA()
        self.stats     = leer_stats_hoy_desde_sheet()
        self.stats_par = defaultdict(lambda: {'wins':0,'losses':0})
        self.operando  = False
        self._dia      = date.today()

    def conectar(self) -> bool:
        for i in range(1, 4):
            log.info(f"[BOT] Conectando ({i}/3)...")
            try:
                self.api = IQ_Option(CONFIG['iq_email'], CONFIG['iq_password'])
                ok, r    = self.api.connect()
                if ok:
                    self.api.change_balance(CONFIG['iq_modo'])
                    bal = self.api.get_balance()
                    log.info(f"[OK] {CONFIG['iq_modo']} | ${bal:.2f}")
                    tg_inicio(bal)
                    return True
                log.warning(f"[BOT] Fallo: {r}")
                time.sleep(5)
            except Exception as e:
                log.error(f"[BOT] Error: {e}")
                time.sleep(5)
        return False

    def get_candles(self, par):
        try:
            raw = self.api.get_candles(
                par, CONFIG['candle_size'],
                CONFIG['candles_cantidad'], time.time()
            )
            if not raw: return None
            df = pd.DataFrame(raw)
            df.rename(columns={'min':'low','max':'high'}, inplace=True)
            for col in ['open','close','high','low','volume']:
                if col not in df.columns: df[col] = 0.0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            return df if len(df) >= 50 else None
        except Exception as e:
            log.error(f"[BOT] Error candles {par}: {e}")
            return None

    def par_disponible(self, par) -> bool:
        try:
            activos = self.api.get_all_open_time()
            for tipo in ['turbo','binary']:
                if activos.get(tipo,{}).get(par,{}).get('open',False):
                    return True
            return False
        except:
            return False

    def ejecutar(self, par, direccion, expiracion=2) -> tuple:
        """Resultado por diferencia de balance. Fix KeyError 'underlying'."""
        try:
            balance_antes = self.api.get_balance()
            log.info(f"[TRADE] Balance antes: ${balance_antes:.2f}")

            ok       = False
            order_id = None

            # Intento 1
            try:
                ok, order_id = self.api.buy(
                    CONFIG['monto_por_trade'], par, direccion, expiracion
                )
            except KeyError as e:
                log.warning(f"[TRADE] KeyError '{e}' en {par} — reintentando en 5s...")
                tg(f"⚠️ <b>Error IQ Option</b>\nKeyError: {e}\nPar: {par}\nReintentando...")
                time.sleep(5)
                # Intento 2
                try:
                    ok, order_id = self.api.buy(
                        CONFIG['monto_por_trade'], par, direccion, expiracion
                    )
                except Exception as e2:
                    log.error(f"[TRADE] Segundo intento fallido: {e2}")
                    tg(f"🔴 <b>Trade cancelado</b>\n{par} — IQ Option no responde\n<i>{str(e2)[:60]}</i>")
                    return None, 0

            if not ok:
                log.error(f"[TRADE] No se pudo abrir en {par} — skip")
                return None, 0

            log.info(f"[TRADE] #{order_id} | {direccion.upper()} {par} {expiracion}min")
            time.sleep(expiracion * 60 + 15)

            balance_despues = balance_antes
            for i in range(5):
                try:
                    b = self.api.get_balance()
                    if b is not None and abs(b - balance_antes) > 0.01:
                        balance_despues = b
                        break
                    log.info(f"[TRADE] Balance sin cambio {i+1}/5...")
                    time.sleep(3)
                except:
                    time.sleep(3)

            if balance_despues == balance_antes:
                try: balance_despues = self.api.get_balance() or balance_antes
                except: pass

            dif = balance_despues - balance_antes
            log.info(f"[TRADE] ${balance_antes:.2f}→${balance_despues:.2f} diff:${dif:+.2f}")

            if dif > 0.01:    return 'win',  round(dif, 2)
            elif dif < -0.01: return 'loss', round(dif, 2)
            else:             return 'loss', -CONFIG['monto_por_trade']

        except Exception as e:
            log.error(f"[TRADE] Error: {e}")
            return None, 0

    def reset_dia(self):
        if date.today() != self._dia:
            tg_resumen_diario(self.stats)
            self.stats     = {'total':0,'wins':0,'losses':0,'pnl':0.0}
            self.stats_par = defaultdict(lambda: {'wins':0,'losses':0})
            self._dia      = date.today()
            self.ia.modelo_idx    = 0
            self.ia.modelo_actual = self.ia.MODELOS[0]
            log.info("[BOT] Nuevo dia — stats reseteadas")
            try: tg_reactivado(self.api.get_balance())
            except: tg_reactivado(0.0)

    def puede_operar(self) -> tuple:
        self.reset_dia()
        if CONFIG['iq_modo'] == 'PRACTICE':
            return True, "OK"
        if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
            return False, "Limite perdidas"
        return True, "OK"

    def ciclo(self):
        if self.operando: return

        ok, razon = self.puede_operar()
        if not ok:
            log.info(f"[BOT] Pausado: {razon}")
            return

        candidatos = []

        for par in CONFIG['pares_otc']:
            if not self.par_disponible(par):
                log.info(f"[SKIP] {par} no disponible")
                continue

            racha = leer_racha_par(par)
            if racha >= CONFIG['max_racha_loss_par']:
                log.info(f"[SKIP] {par} — racha {racha} perdidas")
                continue

            df = self.get_candles(par)
            if df is None: continue

            try:
                ind = calcular_indicadores(df)
            except Exception as e:
                log.warning(f"[SKIP] {par} error indicadores: {e}")
                continue

            if ind['senal'] is None:
                log.info(f"[SKIP] {par} — sin señal de backtest")
                continue

            log.info(f"[OK] {par} — {ind['estrategia']} → {ind['senal'].upper()} "
                     f"— consultando {self.ia.modelo_actual}...")

            decision = self.ia.analizar(par, ind, self.stats, racha)

            if decision['decision'] == 'skip':
                log.info(f"[IA] {par} — IA dice skip")
                continue
            if decision['confianza'] < CONFIG['min_confianza']:
                log.info(f"[IA] {par} confianza baja ({decision['confianza']})")
                continue

            candidatos.append({
                'par':        par,
                'direccion':  decision['decision'],
                'confianza':  decision['confianza'],
                'expiracion': decision['expiracion'],
                'estrategia': ind['estrategia'],
                'razon':      decision['razon'],
                'precio':     ind['precio'],
                'modelo':     decision.get('modelo', self.ia.modelo_actual),
            })
            time.sleep(1)

        if not candidatos:
            log.info("[BOT] Sin señales validas este ciclo")
            return

        mejor = max(candidatos, key=lambda x: x['confianza'])
        log.info(f"[BOT] ✅ {mejor['par']} {mejor['direccion'].upper()} "
                 f"conf:{mejor['confianza']}/10 exp:{mejor['expiracion']}min "
                 f"[{mejor['estrategia']}]")

        tg_entrada(
            mejor['par'], mejor['direccion'], mejor['confianza'],
            mejor['estrategia'], mejor['razon'],
            mejor['precio'], mejor['expiracion'],
            mejor['modelo'], self.stats
        )

        self.operando = True
        try:
            resultado, ganancia = self.ejecutar(
                mejor['par'], mejor['direccion'], mejor['expiracion']
            )

            if resultado in ('win','loss'):
                self.stats['total'] += 1
                self.stats['pnl']   += ganancia
                if resultado == 'win':
                    self.stats['wins'] += 1
                    self.stats_par[mejor['par']]['wins'] += 1
                else:
                    self.stats['losses'] += 1
                    self.stats_par[mejor['par']]['losses'] += 1

                balance = self.api.get_balance()
                tg_resultado(mejor['par'], mejor['direccion'],
                             resultado, ganancia, balance,
                             self.stats['wins'], self.stats['losses'])
                self.ia.registrar(mejor['par'], mejor['direccion'], resultado)
                guardar_trade(
                    mejor['par'], mejor['direccion'],
                    mejor['confianza'], mejor['estrategia'],
                    mejor['razon'], mejor['expiracion'],
                    resultado, ganancia, balance
                )
                log.info(f"[STATS] {self.stats['wins']}W/{self.stats['losses']}L "
                         f"P&L ${self.stats['pnl']:+.2f} | ${balance:.2f}")

                if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
                    if CONFIG['iq_modo'] == 'PRACTICE':
                        tg_pausa_practica(self.stats['losses'])
                    else:
                        tg_stop_real(self.stats['losses'])
        finally:
            self.operando = False

    def run(self):
        log.info("[START] Robot IA OTC v6.1 — Estrategia ganadora + Google Sheets")
        if not CONFIG['iq_email'] or not CONFIG['iq_password']:
            log.error("[ERROR] Falta IQ_EMAIL o IQ_PASSWORD"); return
        if not CONFIG['groq_key']:
            log.error("[ERROR] Falta GROQ_KEY"); return
        if not self.conectar():
            log.error("[ERROR] No se pudo conectar"); return

        ciclo_n = 0
        while True:
            ciclo_n += 1
            log.info(f"\n{'─'*50}")
            log.info(f"[CICLO {ciclo_n}] {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | "
                     f"{self.stats['wins']}W/{self.stats['losses']}L | "
                     f"P&L ${self.stats['pnl']:+.2f} | {self.ia.modelo_actual}")
            try:
                if not self.api.check_connect():
                    self.conectar()
            except:
                self.conectar()
            try:
                self.ciclo()
            except Exception as e:
                log.error(f"[BOT] Error: {e}")
            log.info(f"[WAIT] {CONFIG['sleep_scan']}s...")
            time.sleep(CONFIG['sleep_scan'])
# ─────────────────────────────────────────────────────────────────────────────
# SERVIDOR HTTP MINIMO — requerido por Render para no hacer timeout
# ─────────────────────────────────────────────────────────────────────────────
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass  # silenciar logs del servidor

def iniciar_servidor():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# En if __name__ == '__main__':
if __name__ == '__main__':
    # Arrancar servidor HTTP en hilo separado
    t = threading.Thread(target=iniciar_servidor, daemon=True)
    t.start()
    log.info(f"[HTTP] Health check en puerto {os.environ.get('PORT', 8080)}")
    # Arrancar bot
    bot = RobotIAOTC()
    bot.run()
if __name__ == '__main__':
    bot = RobotIAOTC()
    bot.run()
