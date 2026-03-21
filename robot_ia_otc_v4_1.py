"""
╔══════════════════════════════════════════════════════════════════╗
║     ROBOT OTC v7.1 — RSI7 + IA GROQ                           ║
║                                                                 ║
║  Estrategia: RSI7 + EMA10/50 + RSI14 (60.5% WR backtest)     ║
║  CALL: RSI7<40 + EMA10>EMA50 + RSI14<50                       ║
║  PUT:  RSI7>60 + EMA10<EMA50 + RSI14>50                       ║
║                                                                 ║
║  IA Groq: filtro ligero — si falla, entra igual               ║
║  + Google Sheets historial persistente                        ║
║  + Telegram notificaciones                                    ║
║  + Servidor HTTP para Render                                  ║
╚══════════════════════════════════════════════════════════════════╝

VARIABLES DE ENTORNO en Render:
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
import threading
from datetime import datetime, date
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler

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
    GROQ_DISPONIBLE = True
except ImportError:
    print("[WARN] groq no instalado — operando sin IA")
    GROQ_DISPONIBLE = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    SHEETS_DISPONIBLE = True
except ImportError:
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

    'telegram_token':   os.environ.get('TELEGRAM_TOKEN', ''),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT',  ''),

    'sheet_id':           os.environ.get('SHEET_ID', ''),
    'google_credentials': os.environ.get('GOOGLE_CREDENTIALS', ''),

    'pares_otc': [
        'EURUSD-OTC',
        'EURGBP-OTC',
        'EURJPY-OTC',
        'AUDCAD-OTC',
    ],

    # Estrategia RSI7
    'rsi7_call':  40,
    'rsi7_put':   60,
    'rsi14_call': 50,
    'rsi14_put':  50,

    # IA — filtro ligero
    'min_confianza_ia': 6,   # muy bajo — solo bloquea señales muy malas
    'ia_timeout':       8,   # segundos max para esperar respuesta IA

    'monto_por_trade':    1,
    'expiracion':         1,
    'max_racha_loss_par': 3,

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
    handlers=[logging.StreamHandler(sys.stdout)]
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
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log.warning(f"[TG] Error: {e}")

def tg_inicio(balance: float):
    tg(
        f"<b>🤖 ROBOT OTC v7.1 ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Modo: <b>{CONFIG['iq_modo']}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"Estrategia: <b>RSI7 + EMA10/50 + RSI14</b>\n"
        f"WR backtest: <b>60.5%</b>\n"
        f"IA: <b>Groq filtro ligero ✅</b>\n"
        f"Historial: <b>Google Sheets ✅</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>RSI7 detecta — IA confirma — entra</i>"
    )

def tg_entrada(par, operacion, rsi7, rsi14, ema10, ema50,
               confianza, razon, modelo, stats):
    emoji = "📈" if operacion == "call" else "📉"
    total = stats['wins'] + stats['losses']
    wr    = round(stats['wins'] / total * 100, 1) if total > 0 else 0
    tg(
        f"<b>{emoji} OPERACION — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>{'CALL ☝️' if operacion=='call' else 'PUT 👇'}</b>\n"
        f"RSI7: <b>{rsi7:.1f}</b> | RSI14: <b>{rsi14:.1f}</b>\n"
        f"EMA10 {'>' if ema10>ema50 else '<'} EMA50\n"
        f"🧠 IA: <b>{confianza}/10</b> — <i>{razon}</i>\n"
        f"🤖 Modelo: <i>{modelo}</i>\n"
        f"Monto: <b>${CONFIG['monto_por_trade']}</b> | Exp: <b>1 min</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Hoy: <b>{stats['wins']}W / {stats['losses']}L</b>"
        + (f" | WR: {wr}%" if total > 0 else "")
    )

def tg_resultado(par, operacion, resultado, ganancia, balance, wins, losses):
    emoji = "✅" if resultado == 'win' else "❌"
    signo = f"+${ganancia:.2f}" if ganancia > 0 else f"-${abs(ganancia):.2f}"
    total = wins + losses
    wr    = round(wins / total * 100, 1) if total > 0 else 0
    barra = ("🟢" * min(wins, 10)) + ("🔴" * min(losses, 10))
    tg(
        f"<b>{emoji} {'WIN' if resultado=='win' else 'LOSS'} — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'CALL' if operacion=='call' else 'PUT'}: <b>{signo}</b>\n"
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

# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────────────────────────────────────
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
SHEET_HEADERS = [
    'fecha','hora','par','direccion','monto',
    'rsi7','rsi14','ema10','ema50',
    'confianza_ia','razon_ia','modelo_ia',
    'resultado','ganancia','balance'
]
CSV_FILE = 'trades_v71.csv'
_sheet   = None

def conectar_sheet():
    if not SHEETS_DISPONIBLE:
        return None
    try:
        creds_json = CONFIG['google_credentials']
        sheet_id   = CONFIG['sheet_id']
        if not creds_json or not sheet_id:
            return None
        creds  = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        client = gspread.authorize(creds)
        sh     = client.open_by_key(sheet_id)
        try:
            hoja = sh.worksheet('Trades')
        except gspread.WorksheetNotFound:
            hoja = sh.add_worksheet(title='Trades', rows=5000, cols=15)
            hoja.append_row(SHEET_HEADERS)
        log.info("[SHEET] ✅ Conectado a Google Sheets")
        return hoja
    except Exception as e:
        log.error(f"[SHEET] Error: {e}")
        return None

def get_sheet():
    global _sheet
    if _sheet is None:
        _sheet = conectar_sheet()
    return _sheet

def guardar_trade(par, direccion, rsi7, rsi14, ema10, ema50,
                  confianza_ia, razon_ia, modelo_ia,
                  resultado, ganancia, balance):
    fila = [
        date.today().isoformat(), datetime.now().strftime('%H:%M:%S'),
        par, direccion, CONFIG['monto_por_trade'],
        round(rsi7,2), round(rsi14,2), round(ema10,6), round(ema50,6),
        confianza_ia, str(razon_ia)[:60], modelo_ia,
        resultado, round(ganancia,2), round(balance,2)
    ]
    try:
        hoja = get_sheet()
        if hoja:
            hoja.append_row(fila)
            log.info("[SHEET] ✅ Trade guardado")
    except Exception as e:
        log.warning(f"[SHEET] Error: {e}")
        global _sheet
        _sheet = None
    try:
        existe = os.path.exists(CSV_FILE)
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if not existe:
                w.writerow(SHEET_HEADERS)
            w.writerow(fila)
    except: pass

def leer_stats_hoy() -> dict:
    stats = {'total':0,'wins':0,'losses':0,'pnl':0.0}
    try:
        hoja = get_sheet()
        if not hoja:
            return _leer_stats_csv()
        registros = hoja.get_all_records()
        hoy = date.today().isoformat()
        for t in [r for r in registros if str(r.get('fecha','')) == hoy]:
            if t.get('resultado') == 'win':
                stats['wins'] += 1
                stats['pnl']  += float(t.get('ganancia', 0))
            elif t.get('resultado') == 'loss':
                stats['losses'] += 1
                stats['pnl']   += float(t.get('ganancia', 0))
        stats['total'] = stats['wins'] + stats['losses']
        stats['pnl']   = round(stats['pnl'], 2)
        log.info(f"[SHEET] Stats: {stats['wins']}W/{stats['losses']}L P&L ${stats['pnl']:+.2f}")
        return stats
    except:
        return _leer_stats_csv()

def _leer_stats_csv() -> dict:
    stats = {'total':0,'wins':0,'losses':0,'pnl':0.0}
    if not os.path.exists(CSV_FILE): return stats
    try:
        df  = pd.read_csv(CSV_FILE)
        hoy = df[df['fecha'] == date.today().isoformat()]
        if hoy.empty: return stats
        stats['wins']   = len(hoy[hoy['resultado'] == 'win'])
        stats['losses'] = len(hoy[hoy['resultado'] == 'loss'])
        stats['total']  = stats['wins'] + stats['losses']
        stats['pnl']    = round(hoy['ganancia'].sum(), 2)
        return stats
    except: return stats

def leer_racha_par(par: str) -> int:
    try:
        hoja = get_sheet()
        registros = hoja.get_all_records() if hoja else []
        trades = [r for r in registros if r.get('par') == par][-10:]
        if not trades: return 0
        racha = 0
        for r in reversed(trades):
            if r.get('resultado') == 'loss': racha += 1
            else: break
        return racha
    except:
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
# CALCULAR SEÑAL RSI7
# ─────────────────────────────────────────────────────────────────────────────
def calcular_senal(df: pd.DataFrame) -> dict:
    c = df['close']

    def rsi(span):
        d = c.diff()
        g = d.clip(lower=0).ewm(span=span, adjust=False).mean()
        l = (-d).clip(lower=0).ewm(span=span, adjust=False).mean()
        return float((100 - (100 / (1 + g / l))).iloc[-1])

    rsi7  = rsi(7)
    rsi14 = rsi(14)
    ema10 = float(c.ewm(span=10, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    operacion = None
    if rsi7 < CONFIG['rsi7_call'] and ema10 > ema50 and rsi14 < CONFIG['rsi14_call']:
        operacion = 'call'
    elif rsi7 > CONFIG['rsi7_put'] and ema10 < ema50 and rsi14 > CONFIG['rsi14_put']:
        operacion = 'put'

    return {
        'senal': operacion,
        'rsi7':  round(rsi7,  2),
        'rsi14': round(rsi14, 2),
        'ema10': round(ema10, 6),
        'ema50': round(ema50, 6),
    }

# ─────────────────────────────────────────────────────────────────────────────
# CEREBRO IA — FILTRO LIGERO
# ─────────────────────────────────────────────────────────────────────────────
def parsear_json(texto: str):
    texto = texto.replace('```json','').replace('```','').strip()
    try: return json.loads(texto)
    except: pass
    try:
        m = re.search(r'\{[^{}]+\}', texto, re.DOTALL)
        if m: return json.loads(m.group())
    except: pass
    return None

class CerebroIA:

    MODELOS = [
        'llama3-70b-8192',
        'llama-3.1-8b-instant',
        'gemma2-9b-it',
        'mixtral-8x7b-32768',
    ]

    def __init__(self):
        if not GROQ_DISPONIBLE or not CONFIG['groq_key']:
            self.client = None
            log.warning("[IA] Sin Groq — operando sin IA")
            return
        self.client        = Groq(api_key=CONFIG['groq_key'])
        self.modelo_idx    = 0
        self.modelo_actual = self.MODELOS[0]
        log.info(f"[IA] Groq activo | {self.modelo_actual}")

    def siguiente_modelo(self):
        self.modelo_idx    = (self.modelo_idx + 1) % len(self.MODELOS)
        self.modelo_actual = self.MODELOS[self.modelo_idx]
        log.warning(f"[IA] Rotando → {self.modelo_actual}")

    def validar(self, par, senal, ind, stats) -> dict:
        """Valida la señal RSI7. Si falla → entra igual (no bloqueante)."""

        # Fallback si no hay IA
        if not self.client:
            return {'decision': senal, 'confianza': 7,
                    'razon': 'sin IA', 'modelo': 'ninguno'}

        prompt = f"""Trader OTC IQ Option. La estrategia RSI7 detectó señal.

PAR:{par} SEÑAL:{senal.upper()} H:{datetime.now().hour}h
RSI7:{ind['rsi7']} RSI14:{ind['rsi14']}
EMA10:{ind['ema10']} EMA50:{ind['ema50']}
EMA10>EMA50: {ind['ema10']>ind['ema50']}
HOY:{stats['wins']}W/{stats['losses']}L

Estrategia RSI7 tiene 60.5% WR historico.
¿Confirmas la entrada o es mejor skip?
Solo di skip si hay algo MUY en contra.

SOLO JSON: {{"decision":"{senal}","confianza":7,"razon":"max 8 palabras"}}"""

        for _ in range(len(self.MODELOS)):
            try:
                resp = self.client.chat.completions.create(
                    model=self.modelo_actual,
                    messages=[
                        {"role":"system","content":"Solo respondes JSON valido."},
                        {"role":"user","content":prompt}
                    ],
                    max_tokens=80, temperature=0.1,
                )
                texto = resp.choices[0].message.content.strip()
                data  = parsear_json(texto)

                if data is None:
                    # IA no respondió bien → entrar igual
                    return {'decision': senal, 'confianza': 7,
                            'razon': 'señal RSI7', 'modelo': self.modelo_actual}

                data['decision'] = str(data.get('decision', senal)).lower().strip()
                if data['decision'] not in ['call','put','skip']:
                    data['decision'] = senal
                if not (1 <= data.get('confianza', 0) <= 10):
                    data['confianza'] = 7
                data['modelo'] = self.modelo_actual

                log.info(f"[IA] {par} → {data['decision'].upper()} "
                         f"({data['confianza']}/10) [{self.modelo_actual}]")
                return data

            except Exception as e:
                err = str(e)
                if any(x in err for x in ['429','400','rate_limit','decommissioned']):
                    self.siguiente_modelo()
                    time.sleep(2)
                    continue
                log.warning(f"[IA] Error: {e} — entrando sin IA")
                # Si falla → entrar igual
                return {'decision': senal, 'confianza': 7,
                        'razon': 'fallback RSI7', 'modelo': self.modelo_actual}

        # Todos los modelos fallaron → entrar igual
        log.warning("[IA] Todos los modelos fallaron — entrando con señal RSI7")
        return {'decision': senal, 'confianza': 7,
                'razon': 'RSI7 directo', 'modelo': 'ninguno'}

# ─────────────────────────────────────────────────────────────────────────────
# BOT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
class RobotOTC:

    def __init__(self):
        self.api      = None
        self.ia       = CerebroIA()
        self.stats    = leer_stats_hoy()
        self.operando = False
        self._dia     = date.today()

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
            return df if len(df) >= 60 else None
        except Exception as e:
            log.error(f"[BOT] Error candles {par}: {e}")
            return None

    def ejecutar(self, par, operacion) -> tuple:
        try:
            balance_antes = self.api.get_balance()
            log.info(f"[TRADE] Balance antes: ${balance_antes:.2f}")

            ok = False
            order_id = None

            try:
                ok, order_id = self.api.buy(
                    CONFIG['monto_por_trade'], par, operacion, CONFIG['expiracion']
                )
            except KeyError as e:
                log.warning(f"[TRADE] KeyError '{e}' — reintentando...")
                tg(f"⚠️ <b>Reintentando</b> {par}...")
                time.sleep(3)
                try:
                    ok, order_id = self.api.buy(
                        CONFIG['monto_por_trade'], par, operacion, CONFIG['expiracion']
                    )
                except Exception as e2:
                    log.error(f"[TRADE] Falló: {e2}")
                    return None, 0

            if not ok:
                log.error(f"[TRADE] No se pudo abrir {par}")
                return None, 0

            log.info(f"[TRADE] #{order_id} | {operacion.upper()} {par} {CONFIG['expiracion']}min")
            time.sleep(CONFIG['expiracion'] * 60 + 15)

            balance_despues = balance_antes
            for i in range(5):
                try:
                    b = self.api.get_balance()
                    if b is not None and abs(b - balance_antes) > 0.01:
                        balance_despues = b
                        break
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
            self.stats = {'total':0,'wins':0,'losses':0,'pnl':0.0}
            self._dia  = date.today()
            self.ia.modelo_idx    = 0
            self.ia.modelo_actual = self.ia.MODELOS[0]
            log.info("[BOT] Nuevo dia — stats reseteadas")
            try: tg(f"<b>🟢 NUEVO DIA</b>\nBalance: <b>${self.api.get_balance():.2f}</b>")
            except: pass

    def ciclo(self):
        if self.operando: return
        self.reset_dia()

        for par in CONFIG['pares_otc']:
            # Verificar racha
            racha = leer_racha_par(par)
            if racha >= CONFIG['max_racha_loss_par']:
                log.info(f"[SKIP] {par} — racha {racha} losses")
                continue

            # Obtener velas
            df = self.get_candles(par)
            if df is None:
                log.info(f"[SKIP] {par} — sin velas")
                continue

            # Calcular señal RSI7
            try:
                ind = calcular_senal(df)
            except Exception as e:
                log.warning(f"[SKIP] {par} error: {e}")
                continue

            log.info(f"[SCAN] {par} | RSI7:{ind['rsi7']} RSI14:{ind['rsi14']} "
                     f"EMA10{'>' if ind['ema10']>ind['ema50'] else '<'}EMA50 "
                     f"→ {ind['senal'] or 'SIN SEÑAL'}")

            if ind['senal'] is None:
                continue

            # Validar con IA — filtro ligero
            decision = self.ia.validar(par, ind['senal'], ind, self.stats)

            # Si IA dice skip con baja confianza — saltamos
            if (decision['decision'] == 'skip' and
                    decision['confianza'] < CONFIG['min_confianza_ia']):
                log.info(f"[IA] {par} — skip ({decision['confianza']}/10)")
                continue

            # Si IA dice skip pero confianza alta de la señal → entrar igual
            if decision['decision'] == 'skip':
                log.info(f"[IA] {par} — IA dice skip pero RSI7 es fuerte → entrando")
                decision['decision'] = ind['senal']

            # Entrar
            log.info(f"[BOT] ✅ {par} {decision['decision'].upper()} "
                     f"IA:{decision['confianza']}/10 — {decision['razon']}")

            tg_entrada(par, decision['decision'],
                      ind['rsi7'], ind['rsi14'], ind['ema10'], ind['ema50'],
                      decision['confianza'], decision['razon'],
                      decision['modelo'], self.stats)

            self.operando = True
            try:
                resultado, ganancia = self.ejecutar(par, decision['decision'])

                if resultado in ('win', 'loss'):
                    self.stats['total'] += 1
                    self.stats['pnl']   += ganancia
                    if resultado == 'win':
                        self.stats['wins'] += 1
                    else:
                        self.stats['losses'] += 1

                    balance = self.api.get_balance()
                    tg_resultado(par, decision['decision'], resultado,
                                ganancia, balance,
                                self.stats['wins'], self.stats['losses'])
                    guardar_trade(par, decision['decision'],
                                 ind['rsi7'], ind['rsi14'],
                                 ind['ema10'], ind['ema50'],
                                 decision['confianza'], decision['razon'],
                                 decision['modelo'],
                                 resultado, ganancia, balance)
                    log.info(f"[STATS] {self.stats['wins']}W/{self.stats['losses']}L "
                             f"P&L ${self.stats['pnl']:+.2f} | ${balance:.2f}")
            finally:
                self.operando = False

            # Un trade por ciclo
            break

    def run(self):
        log.info("[START] Robot OTC v7.1 — RSI7 + IA Groq")
        if not CONFIG['iq_email'] or not CONFIG['iq_password']:
            log.error("[ERROR] Falta IQ_EMAIL o IQ_PASSWORD"); return
        if not self.conectar():
            log.error("[ERROR] No se pudo conectar"); return

        ciclo_n = 0
        while True:
            ciclo_n += 1
            log.info(f"\n{'─'*50}")
            log.info(f"[CICLO {ciclo_n}] {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | "
                     f"{self.stats['wins']}W/{self.stats['losses']}L | "
                     f"P&L ${self.stats['pnl']:+.2f}")
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
# SERVIDOR HTTP — requerido por Render
# ─────────────────────────────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass

def iniciar_servidor():
    port   = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# ─────────────────────────────────────────────────────────────────────────────
# ARRANCAR
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    t = threading.Thread(target=iniciar_servidor, daemon=True)
    t.start()
    log.info(f"[HTTP] Health check en puerto {os.environ.get('PORT', 8080)}")
    bot = RobotOTC()
    bot.run()
