"""
╔══════════════════════════════════════════════════════════════════╗
║     ROBOT IA OTC v4.1 - MODELOS ESTABLES + JSON ROBUSTO       ║
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
    'iq_email':    os.environ.get('IQ_EMAIL',    ''),
    'iq_password': os.environ.get('IQ_PASSWORD', ''),
    'iq_modo':     'PRACTICE',
    'groq_key':    os.environ.get('GROQ_KEY', ''),
    'min_confianza': 8,
    'telegram_token':   os.environ.get('TELEGRAM_TOKEN', ''),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT',  ''),
    'pares_otc': [
        'EURUSD-OTC',
        'EURGBP-OTC',
        'GBPUSD-OTC',
        'EURJPY-OTC',
        'AUDCAD-OTC',
        'BTCUSD-OTC',
    ],
    'monto_por_trade':    1,
    'max_perdidas_dia':   5,
    'max_trades_dia':     9999,
    'max_racha_loss_par': 2,
    'nivel_tolerancia_pct': 0.002,
    'nivel_cerca_pct':      0.15,
    'nivel_medio_pct':      0.40,
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
        logging.FileHandler('robot_ia_v4.log', encoding='utf-8'),
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
        f"<b>🤖 ROBOT IA OTC v4.1 ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Modo: <b>{CONFIG['iq_modo']}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"IA: Groq con <b>4 modelos en rotación</b>\n"
        f"Confianza mínima: <b>{CONFIG['min_confianza']}/10</b>\n"
        f"Monto/trade: <b>${CONFIG['monto_por_trade']}</b>\n"
        f"Expiración: <b>decidida por la IA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Opera solo en niveles clave reales.</i>"
    )

def tg_entrada(par, direccion, confianza, razon, precio,
               nivel, expiracion, modelo, stats):
    emoji = "📈" if direccion == "call" else "📉"
    total = stats['wins'] + stats['losses']
    wr    = round(stats['wins'] / total * 100, 1) if total > 0 else 0
    tg(
        f"<b>{emoji} OPERACION — {par}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>{'CALL ☝️' if direccion=='call' else 'PUT 👇'}</b>\n"
        f"Precio: <b>{precio}</b>\n"
        f"Nivel: <b>{nivel}</b>\n"
        f"Monto: ${CONFIG['monto_por_trade']} | "
        f"Exp: <b>{expiracion} min</b> ← IA decidió\n"
        f"🧠 Confianza: <b>{confianza}/10</b>\n"
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
        f"<b>📊 RESUMEN DEL DÍA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Trades: {total}\n"
        f"✅ Wins:   <b>{stats['wins']}</b>\n"
        f"❌ Losses: <b>{stats['losses']}</b>\n"
        f"📈 WR:     <b>{wr}%</b>\n"
        f"💰 P&L:    <b>${stats['pnl']:+.2f}</b>"
    )

def tg_reactivado(balance: float):
    tg(
        f"<b>🟢 BOT REACTIVADO</b>\n"
        f"Nuevo día — stats reseteadas\n"
        f"Balance: <b>${balance:.2f}</b>"
    )

def tg_pausa_practica(losses: int):
    tg(
        f"<b>⚠️ {losses} pérdidas hoy</b>\n"
        f"Modo PRACTICE — continúa operando para aprender."
    )

def tg_stop_real(losses: int):
    tg(
        f"<b>⏸️ BOT EN PAUSA</b>\n"
        f"Límite de {losses} pérdidas.\n"
        f"<b>Se reactiva mañana 🔄</b>"
    )

# ─────────────────────────────────────────────────────────────────────────────
# MEMORIA DE TRADES
# ─────────────────────────────────────────────────────────────────────────────
CSV_FILE = 'trades_ia.csv'

def leer_memoria() -> str:
    if not os.path.exists(CSV_FILE):
        return "Sin historial."
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            return "Sin historial."
        ultimos = df.tail(30)
        total   = len(ultimos)
        wins    = len(ultimos[ultimos['resultado'] == 'win'])
        losses  = len(ultimos[ultimos['resultado'] == 'loss'])
        wr      = round(wins / total * 100, 1) if total > 0 else 0
        resumen_par = ""
        for par in ultimos['par'].unique():
            sub = ultimos[ultimos['par'] == par]
            pw  = len(sub[sub['resultado'] == 'win'])
            pl  = len(sub[sub['resultado'] == 'loss'])
            pwr = round(pw / len(sub) * 100) if len(sub) > 0 else 0
            resumen_par += f"{par}:{pw}W/{pl}L({pwr}%) "
        ultimos5 = ""
        for _, row in df.tail(5).iterrows():
            exp_txt = f"{row['expiracion']}m" \
                      if 'expiracion' in df.columns else ""
            ultimos5 += f"{row['par']} {str(row['direccion']).upper()}{exp_txt}={row['resultado']} "
        return f"T:{total} {wins}W/{losses}L WR:{wr}% | PARES:{resumen_par}| U5:{ultimos5}"
    except Exception as e:
        log.warning(f"[MEM] Error: {e}")
        return "Error historial."

def leer_racha_par(par: str) -> int:
    if not os.path.exists(CSV_FILE):
        return 0
    try:
        df     = pd.read_csv(CSV_FILE)
        df_par = df[df['par'] == par].tail(10)
        if df_par.empty:
            return 0
        racha = 0
        for resultado in reversed(df_par['resultado'].tolist()):
            if resultado == 'loss':
                racha += 1
            else:
                break
        return racha
    except:
        return 0

def leer_wr_hora(hora: int) -> float:
    if not os.path.exists(CSV_FILE):
        return 50.0
    try:
        df = pd.read_csv(CSV_FILE)
        if 'hora' not in df.columns:
            return 50.0
        df['hora_int'] = df['hora'].apply(lambda x: int(str(x).split(':')[0]))
        sub = df[df['hora_int'] == hora]
        if len(sub) < 3:
            return 50.0
        return round(len(sub[sub['resultado'] == 'win']) / len(sub) * 100, 1)
    except:
        return 50.0

def guardar_csv(par, direccion, confianza, razon,
                expiracion, resultado, ganancia, balance):
    existe = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(['fecha', 'hora', 'par', 'direccion', 'monto',
                        'confianza', 'expiracion', 'razon',
                        'resultado', 'ganancia', 'balance'])
        w.writerow([
            date.today().isoformat(),
            datetime.now().strftime('%H:%M:%S'),
            par, direccion,
            CONFIG['monto_por_trade'],
            confianza, expiracion,
            str(razon)[:80],
            resultado, round(ganancia, 2), round(balance, 2)
        ])

# ─────────────────────────────────────────────────────────────────────────────
# DETECCION DE SOPORTE Y RESISTENCIA
# ─────────────────────────────────────────────────────────────────────────────
def detectar_niveles(df: pd.DataFrame) -> dict:
    highs      = df['high'].values
    lows       = df['low'].values
    precio     = float(df['close'].iloc[-1])
    tolerancia = precio * CONFIG['nivel_tolerancia_pct']

    resistencias = []
    soportes     = []

    for i in range(2, len(highs) - 2):
        if highs[i] >= max(highs[max(0, i-2):i]) and \
           highs[i] >= max(highs[i+1:min(len(highs), i+3)]):
            if sum(1 for h in highs if abs(h - highs[i]) < tolerancia) >= 2:
                resistencias.append(round(highs[i], 6))

    for i in range(2, len(lows) - 2):
        if lows[i] <= min(lows[max(0, i-2):i]) and \
           lows[i] <= min(lows[i+1:min(len(lows), i+3)]):
            if sum(1 for l in lows if abs(l - lows[i]) < tolerancia) >= 2:
                soportes.append(round(lows[i], 6))

    def filtrar(niveles, tol):
        if not niveles:
            return []
        niveles   = sorted(set(niveles))
        filtrados = [niveles[0]]
        for n in niveles[1:]:
            if abs(n - filtrados[-1]) > tol:
                filtrados.append(n)
        return filtrados

    resistencias = [r for r in filtrar(resistencias, tolerancia * 2) if r > precio]
    soportes     = [s for s in filtrar(soportes, tolerancia * 2)     if s < precio]

    res_cercana = min(resistencias, key=lambda x: abs(x - precio)) if resistencias else None
    sop_cercano = max(soportes,     key=lambda x: abs(x - precio)) if soportes     else None

    dist_res = round(abs(precio - res_cercana) / precio * 100, 4) if res_cercana else 999
    dist_sop = round(abs(precio - sop_cercano) / precio * 100, 4) if sop_cercano else 999

    cerca = CONFIG['nivel_cerca_pct']
    medio = CONFIG['nivel_medio_pct']

    if dist_res <= cerca:    contexto = "PRECIO_EN_RESISTENCIA"
    elif dist_sop <= cerca:  contexto = "PRECIO_EN_SOPORTE"
    elif dist_res <= medio:  contexto = "PRECIO_CERCA_RESISTENCIA"
    elif dist_sop <= medio:  contexto = "PRECIO_CERCA_SOPORTE"
    else:                    contexto = "PRECIO_EN_ZONA_MEDIA"

    return {
        'resistencia_cercana':  res_cercana,
        'soporte_cercano':      sop_cercano,
        'dist_resistencia_pct': dist_res,
        'dist_soporte_pct':     dist_sop,
        'contexto_nivel':       contexto,
        'total_resistencias':   len(resistencias),
        'total_soportes':       len(soportes),
    }

# ─────────────────────────────────────────────────────────────────────────────
# INDICADORES TECNICOS
# ─────────────────────────────────────────────────────────────────────────────
def calcular_indicadores(df: pd.DataFrame) -> dict:
    c = df['close']

    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(span=14, adjust=False).mean()
    rsi   = float((100 - (100 / (1 + gain / loss))).iloc[-1])

    ema9  = float(c.ewm(span=9,  adjust=False).mean().iloc[-1])
    ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    ml  = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ms  = ml.ewm(span=9, adjust=False).mean()
    mh  = float((ml - ms).iloc[-1])
    mhp = float((ml - ms).iloc[-2])

    sma   = c.rolling(20).mean()
    std   = c.rolling(20).std()
    bb_up = float((sma + 2 * std).iloc[-1])
    bb_dn = float((sma - 2 * std).iloc[-1])
    bb_md = float(sma.iloc[-1])

    hl      = df['high'] - df['low']
    atr     = float(hl.rolling(14).mean().iloc[-1])
    atr_rel = round(atr / float(c.iloc[-1]) * 100, 4)

    va = df['volume'].rolling(20).mean()
    vr = float((df['volume'] / va).iloc[-1])

    precio = float(c.iloc[-1])
    cambio = round((precio - float(c.iloc[-2])) / float(c.iloc[-2]) * 100, 4)

    rsi_s  = 100 - (100 / (1 + gain / loss))
    p_sube = float(c.iloc[-1]) > float(c.iloc[-5])
    r_sube = float(rsi_s.iloc[-1]) > float(rsi_s.iloc[-5])
    diverg = 'BAJ' if p_sube and not r_sube else \
             'ALC' if not p_sube and r_sube else 'N'

    ultimas = ['V' if df['close'].iloc[i] >= df['open'].iloc[i]
               else 'R' for i in range(-5, 0)]

    conf_call = sum([rsi < 35, ema9 > ema21, mh > mhp, precio < bb_dn, vr > 1.3])
    conf_put  = sum([rsi > 65, ema9 < ema21, mh < mhp, precio > bb_up, vr > 1.3])

    niveles = detectar_niveles(df)

    return {
        'precio':            round(precio, 6),
        'cambio_pct':        cambio,
        'rsi':               round(rsi, 2),
        'rsi_estado':        'SC' if rsi > 70 else 'SV' if rsi < 30 else 'N',
        'divergencia_rsi':   diverg,
        'ema_tendencia':     'ALC' if ema9 > ema21 > ema50 else
                             'BAJ' if ema9 < ema21 < ema50 else 'MIX',
        'ema9':              round(ema9, 6),
        'ema21':             round(ema21, 6),
        'macd_hist':         round(mh, 8),
        'macd_sube':         mh > mhp,
        'bb_superior':       round(bb_up, 6),
        'bb_inferior':       round(bb_dn, 6),
        'bb_medio':          round(bb_md, 6),
        'precio_vs_bb':      'ARR' if precio > bb_up else
                             'ABA' if precio < bb_dn else 'DEN',
        'atr_rel_pct':       atr_rel,
        'volumen_rel':       round(vr, 2),
        'ultimas_5_velas':   ultimas,
        'confluencias_call': conf_call,
        'confluencias_put':  conf_put,
        **niveles,
    }

# ─────────────────────────────────────────────────────────────────────────────
# PARSEAR JSON ROBUSTO — extrae JSON aunque la IA meta texto extra
# ─────────────────────────────────────────────────────────────────────────────
def parsear_json_ia(texto: str) -> dict | None:
    """
    Intenta extraer el JSON de la respuesta aunque la IA
    haya agregado texto, markdown o explicaciones extra.
    """
    # Limpiar markdown
    texto = texto.replace('```json', '').replace('```', '').strip()

    # Intento 1 — parsear directo
    try:
        return json.loads(texto)
    except:
        pass

    # Intento 2 — extraer con regex el primer JSON completo
    try:
        match = re.search(r'\{[^{}]+\}', texto, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass

    # Intento 3 — extraer entre primera { y última }
    try:
        if '{' in texto and '}' in texto:
            start = texto.index('{')
            end   = texto.rindex('}') + 1
            return json.loads(texto[start:end])
    except:
        pass

    return None

# ─────────────────────────────────────────────────────────────────────────────
# CEREBRO IA v4.1 — MODELOS ESTABLES + JSON ROBUSTO
# ─────────────────────────────────────────────────────────────────────────────
class CerebroIA:

    MODELOS = [
        'llama3-70b-8192',       # estable, nunca eliminado
        'llama-3.1-8b-instant',  # rápido, 1M tokens/día
        'gemma2-9b-it',          # respaldo
        'mixtral-8x7b-32768',    # extra respaldo
    ]

    def __init__(self):
        self.client        = Groq(api_key=CONFIG['groq_key'])
        self.historial     = deque(maxlen=20)
        self.modelo_idx    = 0
        self.modelo_actual = self.MODELOS[0]
        log.info(f"[IA] Groq iniciado | {self.modelo_actual}")

    def siguiente_modelo(self):
        self.modelo_idx    = (self.modelo_idx + 1) % len(self.MODELOS)
        self.modelo_actual = self.MODELOS[self.modelo_idx]
        log.warning(f"[IA] ⚡ Rotando → {self.modelo_actual}")
        tg(
            f"⚡ <b>Rotación de modelo IA</b>\n"
            f"Ahora usando: <b>{self.modelo_actual}</b>"
        )

    def analizar(self, par: str, ind: dict, stats: dict,
                 racha_loss: int, wr_hora: float) -> dict:

        memoria = leer_memoria()
        hora    = datetime.now().hour

        ctx = ind['contexto_nivel']
        if ctx == 'PRECIO_EN_RESISTENCIA':
            nivel_desc = f"EN_RES:{ind['resistencia_cercana']}({ind['dist_resistencia_pct']}%)->PUT"
        elif ctx == 'PRECIO_EN_SOPORTE':
            nivel_desc = f"EN_SOP:{ind['soporte_cercano']}({ind['dist_soporte_pct']}%)->CALL"
        elif ctx == 'PRECIO_CERCA_RESISTENCIA':
            nivel_desc = f"CERCA_RES:{ind['resistencia_cercana']}({ind['dist_resistencia_pct']}%)"
        elif ctx == 'PRECIO_CERCA_SOPORTE':
            nivel_desc = f"CERCA_SOP:{ind['soporte_cercano']}({ind['dist_soporte_pct']}%)"
        else:
            nivel_desc = "ZONA_MEDIA"

        prompt = f"""Eres un trader experto OTC IQ Option. WR objetivo >65%.

PAR:{par} H:{hora}h
NIVEL:{ctx}|{nivel_desc}
SKIP_SI:ZONA_MEDIA|racha>={racha_loss}(max2)|WR_hora={wr_hora}%<45%

RSI:{ind['rsi']}({ind['rsi_estado']}) DIV:{ind['divergencia_rsi']}
EMA:{ind['ema_tendencia']}(9:{ind['ema9']}/21:{ind['ema21']})
MACD:{'U' if ind['macd_sube'] else 'D'}{ind['macd_hist']}
BB:{ind['precio_vs_bb']}(H:{ind['bb_superior']} L:{ind['bb_inferior']})
VOL:{ind['volumen_rel']}x ATR:{ind['atr_rel_pct']}%
V5:{''.join(ind['ultimas_5_velas'])} CC:{ind['confluencias_call']} CP:{ind['confluencias_put']}

HOY:{stats['wins']}W/{stats['losses']}L ${stats['pnl']:+.2f}
MEM:{memoria}

EXP:1m=fuerte_rapida,2m=corta,3m=moderada,5m=todo_alineado

INSTRUCCION CRITICA: Responde UNICAMENTE con el siguiente JSON.
NO escribas texto antes ni despues. NO uses markdown. SOLO el JSON:
{{"decision":"call","confianza":8,"expiracion":2,"razon":"max 10 palabras"}}"""

        for _ in range(len(self.MODELOS)):
            try:
                resp = self.client.chat.completions.create(
                    model=self.modelo_actual,
                    messages=[
                        {
                            "role": "system",
                            "content": "Eres un asistente de trading. SOLO respondes con JSON válido. Nunca escribes texto adicional."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    max_tokens=100,
                    temperature=0.1,
                )

                texto = resp.choices[0].message.content.strip()

                # Usar parseador robusto
                data = parsear_json_ia(texto)

                if data is None:
                    log.warning(f"[IA] No se pudo extraer JSON: {texto[:80]}")
                    return {'decision': 'skip', 'confianza': 0,
                            'expiracion': 5, 'razon': 'No JSON',
                            'modelo': self.modelo_actual}

                data['decision'] = str(data.get('decision', 'skip')).lower().strip()
                if data['decision'] not in ['call', 'put', 'skip']:
                    data['decision'] = 'skip'
                if not (1 <= data.get('confianza', 0) <= 10):
                    data['confianza'] = 5

                exp = data.get('expiracion', 5)
                data['expiracion'] = exp if exp in [1, 2, 3, 5] else 5
                data['modelo']     = self.modelo_actual

                log.info(f"[IA] {par} → {data['decision'].upper()} "
                         f"({data['confianza']}/10) "
                         f"exp:{data['expiracion']}min "
                         f"[{self.modelo_actual}] | "
                         f"{data.get('razon','')[:50]}")
                return data

            except Exception as e:
                err = str(e)
                if any(x in err for x in ['429', '400', 'rate_limit',
                                           'decommissioned', 'tokens']):
                    log.warning(f"[IA] Error modelo {self.modelo_actual}: {err[:60]}")
                    self.siguiente_modelo()
                    time.sleep(2)
                    continue
                else:
                    log.error(f"[IA] Error inesperado: {e}")
                    return {'decision': 'skip', 'confianza': 0,
                            'expiracion': 5, 'razon': str(e)[:50],
                            'modelo': self.modelo_actual}

        log.error("[IA] Todos los modelos agotados")
        tg(f"🔴 <b>Todos los modelos agotados</b>\nSe reactivarán mañana.")
        return {'decision': 'skip', 'confianza': 0,
                'expiracion': 5, 'razon': 'Sin modelos',
                'modelo': 'ninguno'}

    def registrar(self, par, decision, expiracion=5, resultado=None):
        entry = {'par': par, 'decision': decision,
                 'expiracion': expiracion,
                 'hora': datetime.now().strftime('%H:%M')}
        if resultado:
            entry['resultado'] = resultado
        self.historial.append(entry)

# ─────────────────────────────────────────────────────────────────────────────
# BOT PRINCIPAL v4.1
# ─────────────────────────────────────────────────────────────────────────────
class RobotIAOTC:

    def __init__(self):
        self.api       = None
        self.ia        = CerebroIA()
        self.stats     = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
        self.stats_par = defaultdict(lambda: {'wins': 0, 'losses': 0})
        self.operando  = False
        self._dia      = date.today()

    def conectar(self) -> bool:
        for intento in range(1, 4):
            log.info(f"[BOT] Conectando (intento {intento}/3)...")
            try:
                self.api = IQ_Option(CONFIG['iq_email'], CONFIG['iq_password'])
                ok, razon = self.api.connect()
                if ok:
                    self.api.change_balance(CONFIG['iq_modo'])
                    bal = self.api.get_balance()
                    log.info(f"[OK] Conectado | {CONFIG['iq_modo']} | ${bal:.2f}")
                    tg_inicio(bal)
                    return True
                log.warning(f"[BOT] Fallo: {razon}")
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
            if not raw:
                return None
            df = pd.DataFrame(raw)
            df.rename(columns={'min': 'low', 'max': 'high'}, inplace=True)
            for col in ['open', 'close', 'high', 'low', 'volume']:
                if col not in df.columns:
                    df[col] = 0.0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            return df if len(df) >= 50 else None
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

    def ejecutar(self, par, direccion, expiracion=5) -> tuple:
        try:
            ok, order_id = self.api.buy(
                CONFIG['monto_por_trade'], par,
                direccion, expiracion
            )
            if not ok:
                log.error(f"[TRADE] No se pudo abrir orden en {par}")
                return None, 0

            log.info(f"[TRADE] #{order_id} | {direccion.upper()} {par} {expiracion}min")
            time.sleep(expiracion * 60 + 10)

            for intento in range(10):
                try:
                    data = self.api.check_win_v4(order_id)
                    if data is not None:
                        ganancia  = float(data)
                        resultado = 'win' if ganancia > 0 else 'loss'
                        log.info(f"[TRADE] {resultado.upper()} | ${ganancia:+.2f}")
                        return resultado, ganancia
                    log.info(f"[TRADE] Pendiente {intento+1}/10...")
                    time.sleep(3)
                except Exception as e:
                    log.warning(f"[TRADE] Error check {intento+1}: {e}")
                    time.sleep(3)

            log.warning(f"[TRADE] Sin resultado — esperando 60s más #{order_id}...")
            time.sleep(60)
            for intento in range(5):
                try:
                    data = self.api.check_win_v4(order_id)
                    if data is not None:
                        ganancia  = float(data)
                        resultado = 'win' if ganancia > 0 else 'loss'
                        log.info(f"[TRADE] Tardío: {resultado.upper()} | ${ganancia:+.2f}")
                        return resultado, ganancia
                    log.info(f"[TRADE] Reintento tardío {intento+1}/5...")
                    time.sleep(10)
                except Exception as e:
                    log.warning(f"[TRADE] Error tardío {intento+1}: {e}")
                    time.sleep(10)

            log.warning(f"[TRADE] Sin resultado definitivo #{order_id}")
            tg(f"⚠️ <b>Sin resultado — {par}</b>\n#{order_id} — no se contabiliza.")
            return 'unknown', 0

        except Exception as e:
            log.error(f"[TRADE] Error: {e}")
            return None, 0

    def reset_dia(self):
        if date.today() != self._dia:
            tg_resumen_diario(self.stats)
            self.stats     = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
            self.stats_par = defaultdict(lambda: {'wins': 0, 'losses': 0})
            self._dia      = date.today()
            self.ia.modelo_idx    = 0
            self.ia.modelo_actual = self.ia.MODELOS[0]
            log.info("[BOT] Nuevo día — stats y modelos reseteados")
            try:
                tg_reactivado(self.api.get_balance())
            except:
                tg_reactivado(0.0)

    def puede_operar(self) -> tuple:
        self.reset_dia()
        if CONFIG['iq_modo'] == 'PRACTICE':
            return True, "OK"
        if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
            return False, f"Límite pérdidas ({self.stats['losses']})"
        if self.stats['total'] >= CONFIG['max_trades_dia']:
            return False, f"Límite trades ({self.stats['total']})"
        return True, "OK"

    def ciclo(self):
        if self.operando:
            return

        ok, razon = self.puede_operar()
        if not ok:
            log.info(f"[BOT] Pausado: {razon}")
            return

        hora_actual = datetime.now().hour
        candidatos  = []

        for par in CONFIG['pares_otc']:

            if not self.par_disponible(par):
                log.info(f"[SKIP] {par} no disponible")
                continue

            racha_loss = leer_racha_par(par)
            if racha_loss >= CONFIG['max_racha_loss_par']:
                log.info(f"[SKIP] {par} — racha {racha_loss} pérdidas")
                continue

            wr_hora = leer_wr_hora(hora_actual)

            df = self.get_candles(par)
            if df is None:
                continue

            try:
                ind = calcular_indicadores(df)
            except Exception as e:
                log.warning(f"[SKIP] {par} error: {e}")
                continue

            if ind['contexto_nivel'] == 'PRECIO_EN_ZONA_MEDIA':
                log.info(f"[SKIP] {par} — zona media")
                continue

            if max(ind['confluencias_call'], ind['confluencias_put']) < 2:
                log.info(f"[SKIP] {par} — confluencias insuficientes")
                continue

            log.info(f"[OK] {par} — {ind['contexto_nivel']} "
                     f"— consultando {self.ia.modelo_actual}...")

            decision = self.ia.analizar(par, ind, self.stats, racha_loss, wr_hora)

            if decision['decision'] == 'skip':
                continue
            if decision['confianza'] < CONFIG['min_confianza']:
                log.info(f"[IA] {par} confianza baja ({decision['confianza']})")
                continue

            nivel_txt = (f"Resistencia {ind['resistencia_cercana']}"
                         if 'RESISTENCIA' in ind['contexto_nivel']
                         else f"Soporte {ind['soporte_cercano']}")

            candidatos.append({
                'par':        par,
                'direccion':  decision['decision'],
                'confianza':  decision['confianza'],
                'expiracion': decision['expiracion'],
                'razon':      decision['razon'],
                'precio':     ind['precio'],
                'nivel':      nivel_txt,
                'modelo':     decision.get('modelo', self.ia.modelo_actual),
            })
            time.sleep(1)

        if not candidatos:
            log.info("[BOT] Sin señales válidas este ciclo")
            return

        mejor = max(candidatos, key=lambda x: x['confianza'])
        log.info(f"[BOT] ✅ {mejor['par']} {mejor['direccion'].upper()} | "
                 f"conf:{mejor['confianza']}/10 | exp:{mejor['expiracion']}min | "
                 f"{mejor['nivel']} [{mejor['modelo']}]")

        tg_entrada(
            mejor['par'], mejor['direccion'],
            mejor['confianza'], mejor['razon'],
            mejor['precio'], mejor['nivel'],
            mejor['expiracion'], mejor['modelo'],
            self.stats
        )

        self.operando = True
        try:
            resultado, ganancia = self.ejecutar(
                mejor['par'], mejor['direccion'], mejor['expiracion']
            )

            if resultado == 'unknown':
                log.warning(f"[TRADE] No contabilizado: {mejor['par']}")

            elif resultado in ('win', 'loss'):
                self.stats['total'] += 1
                self.stats['pnl']   += ganancia
                if resultado == 'win':
                    self.stats['wins'] += 1
                    self.stats_par[mejor['par']]['wins'] += 1
                else:
                    self.stats['losses'] += 1
                    self.stats_par[mejor['par']]['losses'] += 1

                balance = self.api.get_balance()
                tg_resultado(
                    mejor['par'], mejor['direccion'],
                    resultado, ganancia, balance,
                    self.stats['wins'], self.stats['losses']
                )
                self.ia.registrar(
                    mejor['par'], mejor['direccion'],
                    mejor['expiracion'], resultado
                )
                guardar_csv(
                    mejor['par'], mejor['direccion'],
                    mejor['confianza'], mejor['razon'],
                    mejor['expiracion'], resultado, ganancia, balance
                )
                log.info(f"[STATS] {self.stats['wins']}W/{self.stats['losses']}L | "
                         f"P&L ${self.stats['pnl']:+.2f} | ${balance:.2f}")

                if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
                    if CONFIG['iq_modo'] == 'PRACTICE':
                        tg_pausa_practica(self.stats['losses'])
                    else:
                        tg_stop_real(self.stats['losses'])
        finally:
            self.operando = False

    def run(self):
        log.info("[START] Robot IA OTC v4.1...")

        if not CONFIG['iq_email'] or not CONFIG['iq_password']:
            log.error("[ERROR] Falta IQ_EMAIL o IQ_PASSWORD")
            return
        if not CONFIG['groq_key']:
            log.error("[ERROR] Falta GROQ_KEY")
            return

        if not self.conectar():
            log.error("[ERROR] No se pudo conectar")
            return

        ciclo_n = 0
        while True:
            ciclo_n += 1
            log.info(f"\n{'─'*50}")
            log.info(f"[CICLO {ciclo_n}] "
                     f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | "
                     f"{self.stats['wins']}W/{self.stats['losses']}L | "
                     f"P&L ${self.stats['pnl']:+.2f} | "
                     f"{self.ia.modelo_actual}")

            try:
                if not self.api.check_connect():
                    log.warning("[BOT] Desconectado. Reconectando...")
                    self.conectar()
            except:
                log.warning("[BOT] Error conexión. Reconectando...")
                self.conectar()

            try:
                self.ciclo()
            except Exception as e:
                log.error(f"[BOT] Error: {e}")

            log.info(f"[WAIT] {CONFIG['sleep_scan']}s...")
            time.sleep(CONFIG['sleep_scan'])

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    bot = RobotIAOTC()
    bot.run()
