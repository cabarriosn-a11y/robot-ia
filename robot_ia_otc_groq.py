"""
╔══════════════════════════════════════════════════════════════════╗
║     ROBOT IA OTC v4.0 - COMPLETO                               ║
║                                                                 ║
║  ✅ Opera en IQ Option OTC automáticamente                     ║
║  ✅ Avisa por Telegram cada operación                          ║
║  ✅ Muestra W/L y barra 🟢🔴 después de cada trade            ║
║  ✅ Memoria de 30 trades — aprende de sus errores              ║
║  ✅ Detecta soporte y resistencia reales                       ║
║  ✅ No opera en zona media del precio                          ║
║  ✅ Bloquea par con 2 pérdidas seguidas                        ║
║  ✅ Filtro de horas por win rate histórico                     ║
║  ✅ Confianza mínima 8/10 para operar                          ║
║  ✅ La IA decide el tiempo de expiración (1/2/3/5 min)         ║
║  ✅ Máx 5 pérdidas/día → para solo                            ║
║  ✅ Máx 15 trades/día                                          ║
║  ✅ Guarda todo en CSV                                         ║
║  ✅ Resumen diario automático por Telegram                     ║
║  ✅ Reconexión automática si se cae                            ║
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
    # IQ Option
    'iq_email':    os.environ.get('IQ_EMAIL',    ''),
    'iq_password': os.environ.get('IQ_PASSWORD', ''),
    'iq_modo':     'PRACTICE',   # cambia a 'REAL' cuando estés listo

    # Groq IA (GRATIS)
    'groq_key':   os.environ.get('GROQ_KEY', ''),
    'groq_model': 'llama-3.3-70b-versatile',

    # Confianza mínima para operar
    'min_confianza': 8,

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
    'max_perdidas_dia':   5,     # para automáticamente si pierde 5 en el día
    'max_trades_dia':     15,    # máximo trades por día

    # Racha perdedora por par
    'max_racha_loss_par': 2,     # bloquea par si pierde 2 seguidas

    # Soporte / Resistencia
    'nivel_tolerancia_pct': 0.002,  # 0.2% margen para agrupar niveles
    'nivel_cerca_pct':      0.15,   # 0.15% = precio "en el nivel"
    'nivel_medio_pct':      0.40,   # más de 0.40% = zona media → skip

    # Técnico
    'candles_cantidad': 100,     # velas para calcular indicadores y niveles
    'candle_size':      60,      # tamaño de vela en segundos (60 = 1 minuto)
    'sleep_scan':       45,      # segundos entre escaneos
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
    """Envía mensaje a Telegram."""
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
        f"<b>🤖 ROBOT IA OTC v4.0 ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Modo: <b>{CONFIG['iq_modo']}</b>\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"IA: Groq / Llama 3.3 70B (gratis)\n"
        f"Confianza mínima: <b>{CONFIG['min_confianza']}/10</b>\n"
        f"Monto/trade: <b>${CONFIG['monto_por_trade']}</b>\n"
        f"Expiración: <b>decidida por la IA</b> (1/2/3/5 min)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Opera solo en niveles clave reales.</i>\n"
        f"<i>Te aviso en cada operación.</i>"
    )

def tg_entrada(par, direccion, confianza, razon, precio, nivel, expiracion, stats):
    """Notifica entrada de operación con stats actuales."""
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
        f"📋 <i>{razon}</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Hoy: <b>{stats['wins']}W / {stats['losses']}L</b>"
        + (f" | WR: {wr}%" if total > 0 else "")
    )

def tg_resultado(par, direccion, resultado, ganancia, balance, wins, losses):
    """Notifica resultado con barra visual W/L."""
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
    """Resumen automático al cambiar el día."""
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

def tg_stop(razon: str):
    tg(f"<b>⛔ BOT DETENIDO</b>\n<i>{razon}</i>")

# ─────────────────────────────────────────────────────────────────────────────
# MEMORIA DE TRADES — Lee el CSV para contexto de la IA
# ─────────────────────────────────────────────────────────────────────────────
CSV_FILE = 'trades_ia.csv'

def leer_memoria() -> str:
    """Lee últimos 30 trades y genera resumen para la IA."""
    if not os.path.exists(CSV_FILE):
        return "Sin historial previo."
    try:
        df = pd.read_csv(CSV_FILE)
        if df.empty:
            return "Sin historial previo."

        ultimos = df.tail(30)
        total   = len(ultimos)
        wins    = len(ultimos[ultimos['resultado'] == 'win'])
        losses  = len(ultimos[ultimos['resultado'] == 'loss'])
        wr      = round(wins / total * 100, 1) if total > 0 else 0

        # Win rate por par
        resumen_par = ""
        for par in ultimos['par'].unique():
            sub = ultimos[ultimos['par'] == par]
            pw  = len(sub[sub['resultado'] == 'win'])
            pl  = len(sub[sub['resultado'] == 'loss'])
            pwr = round(pw / len(sub) * 100) if len(sub) > 0 else 0
            resumen_par += f"  {par}: {pw}W/{pl}L ({pwr}%)\n"

        # Últimos 5 trades
        ultimos5 = ""
        for _, row in df.tail(5).iterrows():
            exp_txt = f" {row['expiracion']}min" if 'expiracion' in df.columns else ""
            ultimos5 += f"  {row['par']} {str(row['direccion']).upper()}{exp_txt} → {row['resultado']}\n"

        return (
            f"HISTORIAL ({total} trades): {wins}W/{losses}L WR:{wr}%\n"
            f"POR PAR:\n{resumen_par}"
            f"ÚLTIMOS 5:\n{ultimos5}"
        )
    except Exception as e:
        log.warning(f"[MEM] Error leyendo memoria: {e}")
        return "Error leyendo historial."

def leer_racha_par(par: str) -> int:
    """Cuántas pérdidas seguidas tiene el par."""
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
    """Win rate histórico de una hora específica."""
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
        wins = len(sub[sub['resultado'] == 'win'])
        return round(wins / len(sub) * 100, 1)
    except:
        return 50.0

def guardar_csv(par, direccion, confianza, razon, expiracion, resultado, ganancia, balance):
    """Guarda el trade en CSV."""
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
    """
    Detecta soportes y resistencias reales buscando
    máximos/mínimos locales tocados 2+ veces.
    """
    highs      = df['high'].values
    lows       = df['low'].values
    precio     = float(df['close'].iloc[-1])
    tolerancia = precio * CONFIG['nivel_tolerancia_pct']

    resistencias = []
    soportes     = []

    # Máximos locales repetidos → resistencias
    for i in range(2, len(highs) - 2):
        if highs[i] >= max(highs[max(0, i-2):i]) and \
           highs[i] >= max(highs[i+1:min(len(highs), i+3)]):
            tocado = sum(1 for h in highs if abs(h - highs[i]) < tolerancia)
            if tocado >= 2:
                resistencias.append(round(highs[i], 6))

    # Mínimos locales repetidos → soportes
    for i in range(2, len(lows) - 2):
        if lows[i] <= min(lows[max(0, i-2):i]) and \
           lows[i] <= min(lows[i+1:min(len(lows), i+3)]):
            tocado = sum(1 for l in lows if abs(l - lows[i]) < tolerancia)
            if tocado >= 2:
                soportes.append(round(lows[i], 6))

    # Eliminar duplicados cercanos
    def filtrar(niveles, tol):
        if not niveles:
            return []
        niveles = sorted(set(niveles))
        filtrados = [niveles[0]]
        for n in niveles[1:]:
            if abs(n - filtrados[-1]) > tol:
                filtrados.append(n)
        return filtrados

    resistencias = filtrar(resistencias, tolerancia * 2)
    soportes     = filtrar(soportes,     tolerancia * 2)

    # Solo niveles relevantes respecto al precio actual
    resistencias = [r for r in resistencias if r > precio]
    soportes     = [s for s in soportes     if s < precio]

    # Nivel más cercano
    res_cercana = min(resistencias, key=lambda x: abs(x - precio)) \
                  if resistencias else None
    sop_cercano = max(soportes,     key=lambda x: abs(x - precio)) \
                  if soportes else None

    dist_res = round(abs(precio - res_cercana) / precio * 100, 4) \
               if res_cercana else 999
    dist_sop = round(abs(precio - sop_cercano) / precio * 100, 4) \
               if sop_cercano else 999

    cerca = CONFIG['nivel_cerca_pct']
    medio = CONFIG['nivel_medio_pct']

    if dist_res <= cerca:
        contexto = "PRECIO_EN_RESISTENCIA"
    elif dist_sop <= cerca:
        contexto = "PRECIO_EN_SOPORTE"
    elif dist_res <= medio:
        contexto = "PRECIO_CERCA_RESISTENCIA"
    elif dist_sop <= medio:
        contexto = "PRECIO_CERCA_SOPORTE"
    else:
        contexto = "PRECIO_EN_ZONA_MEDIA"

    return {
        'resistencia_cercana':   res_cercana,
        'soporte_cercano':       sop_cercano,
        'dist_resistencia_pct':  dist_res,
        'dist_soporte_pct':      dist_sop,
        'contexto_nivel':        contexto,
        'total_resistencias':    len(resistencias),
        'total_soportes':        len(soportes),
    }

# ─────────────────────────────────────────────────────────────────────────────
# INDICADORES TECNICOS
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
    ml  = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    ms  = ml.ewm(span=9,  adjust=False).mean()
    mh  = float((ml - ms).iloc[-1])
    mhp = float((ml - ms).iloc[-2])

    # Bollinger Bands
    sma   = c.rolling(20).mean()
    std   = c.rolling(20).std()
    bb_up = float((sma + 2 * std).iloc[-1])
    bb_dn = float((sma - 2 * std).iloc[-1])
    bb_md = float(sma.iloc[-1])

    # ATR — volatilidad
    hl      = df['high'] - df['low']
    atr     = float(hl.rolling(14).mean().iloc[-1])
    atr_rel = round(atr / float(c.iloc[-1]) * 100, 4)

    # Volumen relativo
    va = df['volume'].rolling(20).mean()
    vr = float((df['volume'] / va).iloc[-1])

    precio = float(c.iloc[-1])
    cambio = round((precio - float(c.iloc[-2])) / float(c.iloc[-2]) * 100, 4)

    # Divergencia RSI
    rsi_s  = 100 - (100 / (1 + gain / loss))
    p_sube = float(c.iloc[-1]) > float(c.iloc[-5])
    r_sube = float(rsi_s.iloc[-1]) > float(rsi_s.iloc[-5])
    diverg = 'BAJISTA' if p_sube and not r_sube else \
             'ALCISTA'  if not p_sube and r_sube else 'NINGUNA'

    # Últimas 5 velas
    ultimas = ['VERDE' if df['close'].iloc[i] >= df['open'].iloc[i] else 'ROJA'
               for i in range(-5, 0)]

    # Confluencias previas
    conf_call = sum([rsi < 35, ema9 > ema21, mh > mhp, precio < bb_dn, vr > 1.3])
    conf_put  = sum([rsi > 65, ema9 < ema21, mh < mhp, precio > bb_up, vr > 1.3])

    # Niveles de soporte/resistencia
    niveles = detectar_niveles(df)

    return {
        'precio':            round(precio, 6),
        'cambio_pct':        cambio,
        'rsi':               round(rsi, 2),
        'rsi_estado':        'SOBRECOMPRADO' if rsi > 70 else
                             'SOBREVENDIDO'  if rsi < 30 else 'NEUTRO',
        'divergencia_rsi':   diverg,
        'ema_tendencia':     'ALCISTA' if ema9 > ema21 > ema50 else
                             'BAJISTA' if ema9 < ema21 < ema50 else 'MIXTA',
        'ema9':              round(ema9, 6),
        'ema21':             round(ema21, 6),
        'macd_hist':         round(mh, 8),
        'macd_sube':         mh > mhp,
        'bb_superior':       round(bb_up, 6),
        'bb_inferior':       round(bb_dn, 6),
        'bb_medio':          round(bb_md, 6),
        'precio_vs_bb':      'ARRIBA_BB' if precio > bb_up else
                             'ABAJO_BB'  if precio < bb_dn else 'DENTRO_BB',
        'atr_rel_pct':       atr_rel,
        'volumen_rel':       round(vr, 2),
        'ultimas_5_velas':   ultimas,
        'confluencias_call': conf_call,
        'confluencias_put':  conf_put,
        **niveles,
    }

# ─────────────────────────────────────────────────────────────────────────────
# CEREBRO IA v4 — CON EXPIRACION DINAMICA
# ─────────────────────────────────────────────────────────────────────────────
class CerebroIA:

    def __init__(self):
        self.client    = Groq(api_key=CONFIG['groq_key'])
        self.historial = deque(maxlen=20)
        log.info("[IA] Groq/Llama v4 inicializado — expiración dinámica activada")

    def analizar(self, par: str, ind: dict, stats: dict,
                 racha_loss: int, wr_hora: float) -> dict:

        memoria = leer_memoria()
        hora    = datetime.now().hour

        # Descripción del nivel para el prompt
        ctx = ind['contexto_nivel']
        if ctx == 'PRECIO_EN_RESISTENCIA':
            nivel_desc = (f"PRECIO TOCANDO RESISTENCIA en {ind['resistencia_cercana']} "
                          f"(a {ind['dist_resistencia_pct']}%) → señal PUT")
        elif ctx == 'PRECIO_EN_SOPORTE':
            nivel_desc = (f"PRECIO TOCANDO SOPORTE en {ind['soporte_cercano']} "
                          f"(a {ind['dist_soporte_pct']}%) → señal CALL")
        elif ctx == 'PRECIO_CERCA_RESISTENCIA':
            nivel_desc = (f"Precio acercándose a resistencia {ind['resistencia_cercana']} "
                          f"(a {ind['dist_resistencia_pct']}%)")
        elif ctx == 'PRECIO_CERCA_SOPORTE':
            nivel_desc = (f"Precio acercándose a soporte {ind['soporte_cercano']} "
                          f"(a {ind['dist_soporte_pct']}%)")
        else:
            nivel_desc = "Precio en zona media sin nivel clave cercano"

        prompt = f"""Eres un trader experto en opciones binarias OTC de IQ Option.
Eres conservador y disciplinado. Tu objetivo es un win rate mayor al 65%.
Es mejor NO operar que entrar con señal débil.

PAR: {par} | HORA: {hora}:00

NIVELES CLAVE (más importante):
- Contexto: {ctx}
- {nivel_desc}
- Total resistencias detectadas: {ind['total_resistencias']}
- Total soportes detectados:     {ind['total_soportes']}
REGLA: Si contexto es PRECIO_EN_ZONA_MEDIA → skip obligatorio.

INDICADORES TÉCNICOS:
- Precio: {ind['precio']} ({ind['cambio_pct']:+.4f}%)
- RSI(14): {ind['rsi']} → {ind['rsi_estado']} | Divergencia: {ind['divergencia_rsi']}
- Tendencia EMA: {ind['ema_tendencia']} (9:{ind['ema9']} / 21:{ind['ema21']})
- MACD Histograma: {ind['macd_hist']} ({'SUBIENDO' if ind['macd_sube'] else 'BAJANDO'})
- Bollinger: {ind['precio_vs_bb']} (sup:{ind['bb_superior']} inf:{ind['bb_inferior']})
- ATR relativo: {ind['atr_rel_pct']}% | Volumen: {ind['volumen_rel']}x
- Últimas 5 velas: {' → '.join(ind['ultimas_5_velas'])}
- Confluencias CALL: {ind['confluencias_call']}/5
- Confluencias PUT:  {ind['confluencias_put']}/5

CONTEXTO:
- Racha pérdidas seguidas en {par}: {racha_loss} (si >= 2 → skip)
- Win rate histórico a las {hora}:00 = {wr_hora}% (si < 45% → skip)
- Trades hoy: {stats['total']} | {stats['wins']}W {stats['losses']}L | P&L ${stats['pnl']:+.2f}

MEMORIA DE TRADES RECIENTES:
{memoria}

REGLAS ESTRICTAS:
1. PRECIO_EN_ZONA_MEDIA = skip siempre
2. Racha pérdidas >= 2 en este par = skip siempre
3. Win rate hora < 45% = skip siempre
4. Necesitas nivel clave + mínimo 2 indicadores confirmando
5. Confianza 9-10: nivel + RSI + MACD + EMA todos alineados
6. Confianza 8: nivel + 2 indicadores alineados
7. Si ATR > 0.2% el mercado está muy volátil → baja confianza

TIEMPO DE EXPIRACIÓN — decide según la fuerza de la señal:
- 1 min: señal muy fuerte y rápida (RSI extremo + vela grande tocando nivel exacto)
- 2 min: tendencia clara pero corta, puede revertir pronto
- 3 min: señal moderada que necesita un poco más de tiempo
- 5 min: tendencia fuerte con múltiples indicadores alineados

Responde SOLO este JSON sin markdown ni texto extra:
{{"decision": "call", "confianza": 8, "expiracion": 2, "razon": "max 15 palabras"}}

decision: "call", "put" o "skip"
confianza: número del 1 al 10
expiracion: 1, 2, 3 o 5"""

        try:
            resp = self.client.chat.completions.create(
                model=CONFIG['groq_model'],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.1,
            )
            texto = resp.choices[0].message.content.strip()
            texto = texto.replace('```json', '').replace('```', '').strip()

            # Extraer solo el JSON si hay texto extra
            if '{' in texto:
                texto = texto[texto.index('{'):texto.rindex('}')+1]

            resultado = json.loads(texto)
            resultado['decision'] = resultado['decision'].lower().strip()

            # Validar decision
            if resultado['decision'] not in ['call', 'put', 'skip']:
                resultado['decision'] = 'skip'

            # Validar confianza
            if not (1 <= resultado.get('confianza', 0) <= 10):
                resultado['confianza'] = 5

            # Validar expiración — si la IA manda algo raro, default 5
            exp = resultado.get('expiracion', 5)
            if exp not in [1, 2, 3, 5]:
                exp = 5
            resultado['expiracion'] = exp

            log.info(f"[IA] {par} → {resultado['decision'].upper()} "
                     f"({resultado['confianza']}/10) "
                     f"exp:{resultado['expiracion']}min | "
                     f"{resultado.get('razon', '')[:60]}")
            return resultado

        except json.JSONDecodeError:
            log.warning(f"[IA] Error JSON. Texto: {texto[:100]}")
            return {'decision': 'skip', 'confianza': 0,
                    'expiracion': 5, 'razon': 'Error JSON'}
        except Exception as e:
            log.error(f"[IA] Error Groq: {e}")
            return {'decision': 'skip', 'confianza': 0,
                    'expiracion': 5, 'razon': str(e)}

    def registrar(self, par, decision, expiracion=5, resultado=None):
        entry = {
            'par':        par,
            'decision':   decision,
            'expiracion': expiracion,
            'hora':       datetime.now().strftime('%H:%M'),
        }
        if resultado:
            entry['resultado'] = resultado
        self.historial.append(entry)

# ─────────────────────────────────────────────────────────────────────────────
# BOT PRINCIPAL v4
# ─────────────────────────────────────────────────────────────────────────────
class RobotIAOTC:

    def __init__(self):
        self.api       = None
        self.ia        = CerebroIA()
        self.stats     = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
        self.stats_par = defaultdict(lambda: {'wins': 0, 'losses': 0})
        self.operando  = False
        self._dia      = date.today()

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
                    log.info(f"[OK] Conectado | {CONFIG['iq_modo']} | ${bal:.2f}")
                    tg_inicio(bal)
                    return True
                log.warning(f"[BOT] Fallo conexión: {razon}")
                time.sleep(5)
            except Exception as e:
                log.error(f"[BOT] Error conectando: {e}")
                time.sleep(5)
        return False

    # ── Datos ─────────────────────────────────────────────────────────────────
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

    # ── Ejecutar trade ────────────────────────────────────────────────────────
    def ejecutar(self, par, direccion, expiracion=5) -> tuple:
        try:
            ok, order_id = self.api.buy(
                CONFIG['monto_por_trade'], par,
                direccion, expiracion
            )
            if not ok:
                log.error(f"[TRADE] No se pudo abrir orden en {par}")
                return None, 0

            log.info(f"[TRADE] Orden #{order_id} | {expiracion}min | esperando...")
            time.sleep(expiracion * 60 + 10)

            # Reintentar verificación hasta 10 veces
            for intento in range(10):
                try:
                    data = self.api.check_win_v4(order_id)
                    if data is not None:
                        ganancia  = float(data)
                        resultado = 'win' if ganancia > 0 else 'loss'
                        log.info(f"[TRADE] {resultado.upper()} | ${ganancia:+.2f}")
                        return resultado, ganancia
                    log.info(f"[TRADE] Pendiente, reintento {intento+1}/10...")
                    time.sleep(3)
                except Exception as e:
                    log.warning(f"[TRADE] Error check {intento+1}: {e}")
                    time.sleep(3)

            log.warning(f"[TRADE] Sin resultado para #{order_id}")
            tg(f"⚠️ Sin resultado verificado para {par} orden #{order_id}")
            return 'unknown', 0

        except Exception as e:
            log.error(f"[TRADE] Error ejecutando: {e}")
            return None, 0

    # ── Control diario ────────────────────────────────────────────────────────
    def reset_dia(self):
        if date.today() != self._dia:
            tg_resumen_diario(self.stats)
            self.stats     = {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
            self.stats_par = defaultdict(lambda: {'wins': 0, 'losses': 0})
            self._dia      = date.today()
            log.info("[BOT] Nuevo día — stats reseteadas")

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

        hora_actual = datetime.now().hour
        candidatos  = []

        for par in CONFIG['pares_otc']:

            # 1. ¿Par disponible en IQ Option?
            if not self.par_disponible(par):
                log.info(f"[SKIP] {par} no disponible")
                continue

            # 2. ¿Racha de pérdidas?
            racha_loss = leer_racha_par(par)
            if racha_loss >= CONFIG['max_racha_loss_par']:
                log.info(f"[SKIP] {par} — racha {racha_loss} pérdidas seguidas")
                continue

            # 3. Win rate histórico de esta hora
            wr_hora = leer_wr_hora(hora_actual)

            # 4. Obtener velas
            df = self.get_candles(par)
            if df is None:
                continue

            # 5. Calcular indicadores + niveles
            try:
                ind = calcular_indicadores(df)
            except Exception as e:
                log.warning(f"[SKIP] {par} error indicadores: {e}")
                continue

            # 6. Filtro zona media — skip directo sin gastar llamada a la IA
            if ind['contexto_nivel'] == 'PRECIO_EN_ZONA_MEDIA':
                log.info(f"[SKIP] {par} — zona media "
                         f"(res:{ind['dist_resistencia_pct']}% "
                         f"sop:{ind['dist_soporte_pct']}%)")
                continue

            # 7. Filtro confluencias previas mínimas
            max_conf = max(ind['confluencias_call'], ind['confluencias_put'])
            if max_conf < 2:
                log.info(f"[SKIP] {par} — solo {max_conf}/5 confluencias previas")
                continue

            log.info(f"[OK] {par} — {ind['contexto_nivel']} — consultando IA...")

            # 8. Consultar a la IA
            decision = self.ia.analizar(par, ind, self.stats, racha_loss, wr_hora)

            if decision['decision'] == 'skip':
                log.info(f"[IA] {par} → SKIP")
                continue

            if decision['confianza'] < CONFIG['min_confianza']:
                log.info(f"[IA] {par} confianza insuficiente "
                         f"({decision['confianza']}/{CONFIG['min_confianza']})")
                continue

            # Descripción del nivel para Telegram
            if 'RESISTENCIA' in ind['contexto_nivel']:
                nivel_txt = f"Resistencia {ind['resistencia_cercana']}"
            else:
                nivel_txt = f"Soporte {ind['soporte_cercano']}"

            candidatos.append({
                'par':        par,
                'direccion':  decision['decision'],
                'confianza':  decision['confianza'],
                'expiracion': decision['expiracion'],
                'razon':      decision['razon'],
                'precio':     ind['precio'],
                'nivel':      nivel_txt,
            })
            time.sleep(1)  # pausa entre consultas a Groq

        if not candidatos:
            log.info("[BOT] Sin señales válidas en niveles clave este ciclo")
            return

        # Elegir el candidato con mayor confianza
        mejor = max(candidatos, key=lambda x: x['confianza'])
        log.info(f"[BOT] ✅ Operando: {mejor['par']} "
                 f"{mejor['direccion'].upper()} | "
                 f"conf:{mejor['confianza']}/10 | "
                 f"exp:{mejor['expiracion']}min | "
                 f"{mejor['nivel']}")

        # Notificar entrada
        tg_entrada(
            mejor['par'], mejor['direccion'],
            mejor['confianza'], mejor['razon'],
            mejor['precio'], mejor['nivel'],
            mejor['expiracion'], self.stats
        )

        self.operando = True
        try:
            resultado, ganancia = self.ejecutar(
                mejor['par'],
                mejor['direccion'],
                mejor['expiracion']
            )

            if resultado in ('win', 'loss'):
                # Actualizar stats
                self.stats['total'] += 1
                self.stats['pnl']   += ganancia
                if resultado == 'win':
                    self.stats['wins'] += 1
                    self.stats_par[mejor['par']]['wins'] += 1
                else:
                    self.stats['losses'] += 1
                    self.stats_par[mejor['par']]['losses'] += 1

                balance = self.api.get_balance()

                # Notificar resultado con barra W/L
                tg_resultado(
                    mejor['par'], mejor['direccion'],
                    resultado, ganancia, balance,
                    self.stats['wins'], self.stats['losses']
                )

                # Registrar en memoria IA y CSV
                self.ia.registrar(
                    mejor['par'], mejor['direccion'],
                    mejor['expiracion'], resultado
                )
                guardar_csv(
                    mejor['par'], mejor['direccion'],
                    mejor['confianza'], mejor['razon'],
                    mejor['expiracion'], resultado,
                    ganancia, balance
                )

                log.info(f"[STATS] {self.stats['wins']}W/{self.stats['losses']}L "
                         f"| P&L ${self.stats['pnl']:+.2f} "
                         f"| Balance ${balance:.2f}")

                # Parar si se alcanza límite de pérdidas
                # En PRACTICE no para — sigue operando para educar a la IA
                if self.stats['losses'] >= CONFIG['max_perdidas_dia']:
                    if CONFIG['iq_modo'] == 'REAL':
                       tg_stop(
                         f"Límite de {CONFIG['max_perdidas_dia']} pérdidas "
                         f"diarias alcanzado. Reanuda mañana."
                       )
                    else:
                        tg(
                            f"⚠️ {CONFIG['max_perdidas_dia']} pérdidas alcanzadas "
                            f"— modo PRACTICE, continúa operando para aprender."
                        )
        finally:
            self.operando = False

    # ── Run ───────────────────────────────────────────────────────────────────
    def run(self):
        log.info("[START] Robot IA OTC v4.0 iniciando...")

        if not CONFIG['iq_email'] or not CONFIG['iq_password']:
            log.error("[ERROR] Falta IQ_EMAIL o IQ_PASSWORD en variables de entorno")
            return
        if not CONFIG['groq_key']:
            log.error("[ERROR] Falta GROQ_KEY en variables de entorno")
            return

        if not self.conectar():
            log.error("[ERROR] No se pudo conectar a IQ Option")
            return

        ciclo_n = 0
        while True:
            ciclo_n += 1
            log.info(f"\n{'─'*50}")
            log.info(f"[CICLO {ciclo_n}] "
                     f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | "
                     f"{self.stats['wins']}W/{self.stats['losses']}L | "
                     f"P&L ${self.stats['pnl']:+.2f}")

            # Verificar conexión y reconectar si es necesario
            try:
                if not self.api.check_connect():
                    log.warning("[BOT] Desconectado. Reconectando...")
                    self.conectar()
            except:
                log.warning("[BOT] Error verificando conexión. Reconectando...")
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
