"""
╔══════════════════════════════════════════════════════════════════╗
║         ROBOT IA OTC - Groq (GRATIS) como cerebro              ║
║         IQ Option OTC + Groq/Llama + Telegram                  ║
║         Opera solo y te notifica cada trade                    ║
╚══════════════════════════════════════════════════════════════════╝

VARIABLES DE ENTORNO en Railway:
    IQ_EMAIL         → tu email de IQ Option
    IQ_PASSWORD      → tu contraseña de IQ Option
    GROQ_KEY         → tu API key de Groq (console.groq.com)
    TELEGRAM_TOKEN   → token de tu bot de Telegram
    TELEGRAM_CHAT    → tu chat ID de Telegram
"""

import os
import sys
import io
import time
import csv
import json
import logging
import threading
from datetime import datetime, date
from collections import deque

import pandas as pd

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("[ERROR] Instala: pip install git+https://github.com/iqoptionapi/iqoptionapi.git")
    sys.exit(1)

try:
    from groq import Groq
except ImportError:
    print("[ERROR] Instala: pip install groq")
    sys.exit(1)

import urllib.request
import urllib.parse

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # IQ Option
    'iq_email':    os.environ.get('IQ_EMAIL',    ''),
    'iq_password': os.environ.get('IQ_PASSWORD', ''),
    'iq_modo':     'PRACTICE',   # <-- cambia a 'REAL' cuando estés listo

    # Groq IA (GRATIS)
    'groq_key':      os.environ.get('GROQ_KEY', ''),
    'groq_model':    'llama-3.3-70b-versatile',
    'min_confianza': 7,          # IA debe dar >= 7/10 para operar

    # Telegram
    'telegram_token':   os.environ.get('TELEGRAM_TOKEN', ''),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT',  ''),

    # Pares OTC a analizar
    'pares_otc': [
        'EURUSD-OTC',
        'EURGBP-OTC',
        'GBPUSD-OTC',
        'EURJPY-OTC',
        'AUDCAD-OTC',
        'BTCUSD-OTC',
    ],

    # Gestión de riesgo
    'monto_por_trade':    1,     # USD por operación
    'expiracion_min':     5,     # minutos
    'max_perdidas_dia':   5,     # para automáticamente si pierde 5 seguidas
    'max_trades_dia':     20,
    'pausa_entre_trades': 30,    # segundos entre trades

    # Técnico
    'candles_cantidad': 50,
    'candle_size':      60,      # 60s = velas de 1 minuto
    'sleep_scan':       30,      # segundos entre escaneos
}

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('robot_ia_otc.log', encoding='utf-8'),
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
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log.warning(f"[TG] Error: {e}")

def tg_inicio(balance):
    tg(
        f"<b>🤖 ROBOT IA OTC ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Modo: <b>{CONFIG['iq_modo']}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"IA: Groq / Llama 3.3 70B (gratis)\n"
        f"Monto/trade: ${CONFIG['monto_por_trade']}\n"
        f"Confianza mínima: {CONFIG['min_confianza']}/10\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Operando solo. Te aviso en cada trade.</i>"
    )

def tg_entrada(par, direccion, confianza, razon, precio):
    emoji = "📈" if direccion == "call" else "📉"
    tg(
        f"<b>{emoji} NUEVA OPERACION — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Dirección: <b>{'CALL (SUBE) ☝️' if direccion=='call' else 'PUT (BAJA) 👇'}</b>\n"
        f"Precio entrada: <b>{precio}</b>\n"
        f"Monto: <b>${CONFIG['monto_por_trade']}</b>\n"
        f"Expiración: {CONFIG['expiracion_min']} min\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Confianza IA: <b>{confianza}/10</b>\n"
        f"📋 Razón: <i>{razon}</i>"
    )

def tg_resultado(par, direccion, resultado, ganancia, balance, wins, losses):
    emoji = "✅" if resultado == 'win' else "❌"
    signo = f"+${ganancia:.2f}" if ganancia > 0 else f"-${abs(ganancia):.2f}"
    wr    = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0
    tg(
        f"<b>{emoji} RESULTADO — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'CALL' if direccion=='call' else 'PUT'}: <b>{signo}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Hoy: {wins}W / {losses}L | WR: {wr}%"
    )

def tg_stop(razon):
    tg(f"<b>⛔ BOT DETENIDO</b>\n<i>{razon}</i>")

# ─────────────────────────────────────────────────────────────────────────────
# INDICADORES
# ─────────────────────────────────────────────────────────────────────────────
def calcular_indicadores(df: pd.DataFrame) -> dict:
    c = df['close']

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(span=14, adjust=False).mean()
    rsi   = float((100 - (100 / (1 + gain / loss))).iloc[-1])

    # EMAs
    ema9  = float(c.ewm(span=9,  adjust=False).mean().iloc[-1])
    ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    # MACD
    ml    = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ms    = ml.ewm(span=9, adjust=False).mean()
    mh    = float((ml - ms).iloc[-1])
    mhp   = float((ml - ms).iloc[-2])

    # Bollinger
    sma   = c.rolling(20).mean()
    std   = c.rolling(20).std()
    bb_up = float((sma + 2 * std).iloc[-1])
    bb_dn = float((sma - 2 * std).iloc[-1])

    # Volumen
    va    = df['volume'].rolling(20).mean()
    vr    = float((df['volume'] / va).iloc[-1])

    precio = float(c.iloc[-1])
    cambio = round((precio - float(c.iloc[-2])) / float(c.iloc[-2]) * 100, 4)
    ultimas = ['VERDE' if df['close'].iloc[i] >= df['open'].iloc[i] else 'ROJA'
               for i in range(-5, 0)]

    return {
        'precio':          round(precio, 6),
        'cambio_pct':      cambio,
        'rsi':             round(rsi, 2),
        'rsi_estado':      'SOBRECOMPRADO' if rsi > 70 else 'SOBREVENDIDO' if rsi < 30 else 'NEUTRO',
        'ema_tendencia':   'ALCISTA' if ema9 > ema21 > ema50 else 'BAJISTA' if ema9 < ema21 < ema50 else 'MIXTA',
        'ema9':            round(ema9, 6),
        'ema21':           round(ema21, 6),
        'macd_hist':       round(mh, 8),
        'macd_sube':       mh > mhp,
        'bb_superior':     round(bb_up, 6),
        'bb_inferior':     round(bb_dn, 6),
        'precio_vs_bb':    'ARRIBA_BB' if precio > bb_up else 'ABAJO_BB' if precio < bb_dn else 'DENTRO_BB',
        'volumen_rel':     round(vr, 2),
        'ultimas_5_velas': ultimas,
    }

# ─────────────────────────────────────────────────────────────────────────────
# CEREBRO IA — GROQ
# ─────────────────────────────────────────────────────────────────────────────
class CerebroIA:

    def __init__(self):
        self.client   = Groq(api_key=CONFIG['groq_key'])
        self.historial = deque(maxlen=10)
        log.info("[IA] Groq/Llama inicializado correctamente")

    def analizar(self, par: str, ind: dict, stats: dict) -> dict:
        hist_txt = ""
        if self.historial:
            hist_txt = "\nÚLTIMAS OPERACIONES:\n"
            for h in list(self.historial)[-3:]:
                hist_txt += f"- {h['par']} {h['decision'].upper()}: {h.get('resultado','pendiente')}\n"

        prompt = f"""Eres un trader experto en opciones binarias OTC de IQ Option.
Analiza estos datos técnicos y decide si operar.

PAR: {par} | EXPIRACIÓN: {CONFIG['expiracion_min']} minutos

INDICADORES:
- Precio: {ind['precio']} ({ind['cambio_pct']:+.4f}%)
- RSI(14): {ind['rsi']} → {ind['rsi_estado']}
- Tendencia EMA: {ind['ema_tendencia']} (9:{ind['ema9']} / 21:{ind['ema21']})
- MACD Histograma: {ind['macd_hist']} ({'SUBIENDO' if ind['macd_sube'] else 'BAJANDO'})
- Bollinger: Superior {ind['bb_superior']} | Inferior {ind['bb_inferior']}
- Precio vs BB: {ind['precio_vs_bb']}
- Volumen relativo: {ind['volumen_rel']}x
- Últimas 5 velas: {' → '.join(ind['ultimas_5_velas'])}

STATS HOY: {stats['total']} trades | {stats['wins']}W {stats['losses']}L | P&L ${stats['pnl']:+.2f}
{hist_txt}

REGLAS:
1. Solo opera con mínimo 3-4 indicadores alineados en la misma dirección
2. Si no hay señal clara responde skip
3. Sé conservador, no operar también es una buena decisión

Responde SOLO este JSON sin markdown ni texto extra:
{{"decision": "call", "confianza": 8, "razon": "explicacion corta de max 15 palabras"}}

decision puede ser: call, put o skip
confianza: número del 1 al 10"""

        try:
            resp = self.client.chat.completions.create(
                model=CONFIG['groq_model'],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.1,   # baja temperatura = más consistente
            )
            texto = resp.choices[0].message.content.strip()
            texto = texto.replace('```json','').replace('```','').strip()

            resultado = json.loads(texto)
            resultado['decision'] = resultado['decision'].lower().strip()

            if resultado['decision'] not in ['call', 'put', 'skip']:
                resultado['decision'] = 'skip'
            if not (1 <= resultado.get('confianza', 0) <= 10):
                resultado['confianza'] = 5

            log.info(f"[IA] {par} → {resultado['decision'].upper()} "
                     f"({resultado['confianza']}/10) | {resultado.get('razon','')[:60]}")
            return resultado

        except json.JSONDecodeError:
            log.warning(f"[IA] Error parseando JSON. Respuesta: {texto[:100]}")
            return {'decision': 'skip', 'confianza': 0, 'razon': 'Error JSON'}
        except Exception as e:
            log.error(f"[IA] Error Groq: {e}")
            return {'decision': 'skip', 'confianza': 0, 'razon': str(e)}

    def registrar(self, par, decision, resultado=None):
        entry = {'par': par, 'decision': decision, 'hora': datetime.now().strftime('%H:%M')}
        if resultado:
            entry['resultado'] = resultado
        self.historial.append(entry)

# ─────────────────────────────────────────────────────────────────────────────
# CSV LOG
# ─────────────────────────────────────────────────────────────────────────────
CSV_FILE = 'trades_ia.csv'

def guardar_csv(par, direccion, confianza, razon, resultado, ganancia, balance):
    existe = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(['fecha','hora','par','direccion','monto','confianza',
                        'razon','resultado','ganancia','balance'])
        w.writerow([
            date.today().isoformat(),
            datetime.now().strftime('%H:%M:%S'),
            par, direccion,
            CONFIG['monto_por_trade'],
            confianza, razon[:80],
            resultado, round(ganancia, 2), round(balance, 2)
        ])

# ─────────────────────────────────────────────────────────────────────────────
# BOT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
class RobotIAOTC:

    def __init__(self):
        self.api      = None
        self.ia       = CerebroIA()
        self.stats    = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
        self.operando = False
        self._dia     = date.today()

    # ── Conexión ──────────────────────────────────────────────────────────────
    def conectar(self) -> bool:
        for intento in range(1, 4):
            log.info(f"[BOT] Conectando a IQ Option (intento {intento}/3)...")
            try:
                self.api = IQ_Option(CONFIG['iq_email'], CONFIG['iq_password'])
                ok, razon = self.api.connect()
                if ok:
                    self.api.change_balance(CONFIG['iq_modo'])
                    bal = self.api.get_balance()
                    log.info(f"[OK] Conectado | {CONFIG['iq_modo']} | Balance: ${bal:.2f}")
                    tg_inicio(bal)
                    return True
                log.warning(f"[BOT] Fallo: {razon}")
                time.sleep(5)
            except Exception as e:
                log.error(f"[BOT] Error: {e}")
                time.sleep(5)
        return False

    # ── Datos ─────────────────────────────────────────────────────────────────
    def get_candles(self, par):
        try:
            raw = self.api.get_candles(par, CONFIG['candle_size'],
                                       CONFIG['candles_cantidad'], time.time())
            if not raw:
                return None
            df = pd.DataFrame(raw)
            df.rename(columns={'min': 'low', 'max': 'high'}, inplace=True)
            for col in ['open', 'close', 'high', 'low', 'volume']:
                if col not in df.columns:
                    df[col] = 0.0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            return df if len(df) >= 30 else None
        except Exception as e:
            log.error(f"[BOT] Error candles {par}: {e}")
            return None

    def par_disponible(self, par) -> bool:
        try:
            activos = self.api.get_all_open_time()
            for tipo in ['turbo', 'binary']:
                if activos.get(tipo, {}).get(par, {}).get('open', False):
                    return True
            return False
        except:
            return False

    # ── Ejecutar trade ────────────────────────────────────────────────────────
    def ejecutar(self, par, direccion) -> tuple:
        try:
            ok, order_id = self.api.buy(
                CONFIG['monto_por_trade'], par,
                direccion, CONFIG['expiracion_min']
            )
            if not ok:
                log.error(f"[TRADE] No se pudo abrir orden en {par}")
                return None, 0

            log.info(f"[TRADE] Orden #{order_id} abierta — esperando resultado...")
            time.sleep(CONFIG['expiracion_min'] * 60 + 15)

            data = self.api.check_win_v4(order_id)
            if data is None:
                time.sleep(10)
                data = self.api.check_win_v4(order_id)

            if data is not None:
                ganancia  = float(data)
                resultado = 'win' if ganancia > 0 else 'loss'
                log.info(f"[TRADE] {resultado.upper()} | ${ganancia:+.2f}")
                return resultado, ganancia

            return 'unknown', 0
        except Exception as e:
            log.error(f"[TRADE] Error: {e}")
            return None, 0

    # ── Límites diarios ───────────────────────────────────────────────────────
    def reset_dia(self):
        if date.today() != self._dia:
            self.stats = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
            self._dia  = date.today()

    def puede_operar(self) -> tuple:
        self.reset_dia()
        if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
            return False, f"Límite pérdidas ({self.stats['losses']})"
        if self.stats['total'] >= CONFIG['max_trades_dia']:
            return False, f"Límite trades ({self.stats['total']})"
        return True, "OK"

    # ── Ciclo principal ───────────────────────────────────────────────────────
    def ciclo(self):
        if self.operando:
            return

        ok, razon = self.puede_operar()
        if not ok:
            log.info(f"[BOT] Pausado: {razon}")
            return

        candidatos = []

        for par in CONFIG['pares_otc']:
            if not self.par_disponible(par):
                log.info(f"[SKIP] {par} no disponible")
                continue

            df = self.get_candles(par)
            if df is None:
                continue

            try:
                ind = calcular_indicadores(df)
            except Exception as e:
                log.warning(f"[SKIP] {par} error indicadores: {e}")
                continue

            decision = self.ia.analizar(par, ind, self.stats)

            if decision['decision'] == 'skip':
                continue
            if decision['confianza'] < CONFIG['min_confianza']:
                log.info(f"[IA] {par} confianza insuficiente ({decision['confianza']}/10)")
                continue

            candidatos.append({
                'par':       par,
                'direccion': decision['decision'],
                'confianza': decision['confianza'],
                'razon':     decision['razon'],
                'precio':    ind['precio'],
            })
            time.sleep(1)

        if not candidatos:
            log.info("[BOT] Sin señales este ciclo")
            return

        # El mejor candidato gana
        mejor = max(candidatos, key=lambda x: x['confianza'])
        log.info(f"[BOT] Mejor: {mejor['par']} {mejor['direccion'].upper()} "
                 f"confianza {mejor['confianza']}/10")

        tg_entrada(mejor['par'], mejor['direccion'],
                   mejor['confianza'], mejor['razon'], mejor['precio'])

        self.operando = True
        try:
            resultado, ganancia = self.ejecutar(mejor['par'], mejor['direccion'])

            if resultado in ('win', 'loss'):
                self.stats['total'] += 1
                self.stats['pnl']   += ganancia
                if resultado == 'win': self.stats['wins']   += 1
                else:                  self.stats['losses'] += 1

                balance = self.api.get_balance()
                tg_resultado(mejor['par'], mejor['direccion'],
                             resultado, ganancia, balance,
                             self.stats['wins'], self.stats['losses'])

                self.ia.registrar(mejor['par'], mejor['direccion'], resultado)
                guardar_csv(mejor['par'], mejor['direccion'],
                            mejor['confianza'], mejor['razon'],
                            resultado, ganancia, balance)

                log.info(f"[STATS] {self.stats['wins']}W/{self.stats['losses']}L "
                         f"| P&L ${self.stats['pnl']:+.2f} | Balance ${balance:.2f}")

                if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
                    tg_stop(f"Se alcanzó el límite de {CONFIG['max_perdidas_dia']} pérdidas por día. Bot pausado hasta mañana.")
        finally:
            self.operando = False

    # ── Run ───────────────────────────────────────────────────────────────────
    def run(self):
        log.info("[START] Iniciando Robot IA OTC con Groq...")

        if not CONFIG['iq_email'] or not CONFIG['iq_password']:
            log.error("[ERROR] Faltan IQ_EMAIL o IQ_PASSWORD")
            return
        if not CONFIG['groq_key']:
            log.error("[ERROR] Falta GROQ_KEY")
            return

        if not self.conectar():
            log.error("[ERROR] No se pudo conectar a IQ Option")
            return

        ciclo_num = 0
        while True:
            ciclo_num += 1
            log.info(f"\n{'─'*45}")
            log.info(f"[CICLO {ciclo_num}] {datetime.now().strftime('%d/%m %H:%M:%S')} | "
                     f"{self.stats['wins']}W/{self.stats['losses']}L | "
                     f"P&L ${self.stats['pnl']:+.2f}")

            # Verificar conexión
            try:
                if not self.api.check_connect():
                    log.warning("[BOT] Desconectado, reconectando...")
                    self.conectar()
            except:
                self.conectar()

            try:
                self.ciclo()
            except Exception as e:
                log.error(f"[BOT] Error en ciclo: {e}")

            log.info(f"[WAIT] Próximo scan en {CONFIG['sleep_scan']}s...")
            time.sleep(CONFIG['sleep_scan'])

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    bot = RobotIAOTC()
    bot.run()
