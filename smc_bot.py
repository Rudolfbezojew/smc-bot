# Discord-Signal-Bot (laeuft kostenlos ueber GitHub Actions)
# Strategie 1: SMC v11.4 Sweep (15m)  –  Strategie 2: v17 PBD Range-Ausbruch (4H)
# Gleiche Regeln wie die TradingView-Indikatoren.
import json, os, sys, time, csv, urllib.request, urllib.error
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ================= EINSTELLUNGEN (wie im Indikator) =================
COINS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
SW_LEN   = 5        # Swing-Laenge
SL_BUF   = 0.1      # SL-Puffer hinter Sweep (ATR)
CHOCH_MAX = 16      # max. Kerzen Sweep -> CHoCH
USE_1H   = True
USE_4H   = True
USE_REV  = True     # Trendwende-Modul
REV_AGE  = 24
MAX_HOLD = 32
LVL_LIFE = 480
TPS      = [1.0, 2.0, 3.0]
WEIGHTS  = [40, 40, 20]
BE_AFTER1 = True
TR_AFTER2 = True
LIQ_MIN  = 1.0
COST_PCT = 0.07
MAX_DIST_R = 0.3    # Hinweis "auslassen", wenn Kurs schon so weit (in R) vom Entry weg ist
DIGITS   = {"BTCUSDT": 2, "ETHUSDT": 2, "SOLUSDT": 2, "XRPUSDT": 4}
TZ       = ZoneInfo("Europe/Berlin")
M15      = 15 * 60 * 1000
# PBD 4H (v17)
PBD_ON   = True
PBD_MIN_H, PBD_MAX_H = 1.0, 6.0   # Range-Hoehe in ATR
PBD_EDGE = 0.15                   # Kanten-Beruehrung: aeussere 15 %
PBD_MIN_CR = 3                    # Mitte mind. 3x gekreuzt
PBD_CRV  = 2.0
PBD_HOLD = 96
H4       = 4 * 3600 * 1000

# ================= DATEN =================
def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "smc-bot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def fetch_binance(base, sym, interval, total):
    out, end = [], None
    while len(out) < total:
        url = f"{base}/api/v3/klines?symbol={sym}&interval={interval}&limit=1000"
        if end is not None:
            url += f"&endTime={end}"
        rows = http_json(url)
        if not rows:
            break
        out = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows] + out
        end = int(rows[0][0]) - 1
        if len(rows) < 1000:
            break
    seen, res = set(), []
    for b in out:
        if b[0] not in seen:
            seen.add(b[0]); res.append(b)
    res.sort(key=lambda x: x[0])
    return res[-total:]

KRAKEN = {"BTCUSDT": "XBTUSDT", "ETHUSDT": "ETHUSDT", "SOLUSDT": "SOLUSDT", "XRPUSDT": "XRPUSDT"}
KR_INT = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}

def fetch_kraken(sym, interval, total):
    d = http_json(f"https://api.kraken.com/0/public/OHLC?pair={KRAKEN[sym]}&interval={KR_INT[interval]}")
    if d.get("error"):
        raise RuntimeError(str(d["error"]))
    key = [k for k in d["result"] if k != "last"][0]
    return [[int(r[0]) * 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in d["result"][key]][-total:]

def fetch(sym, interval, total):
    errs = []
    for base in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            return fetch_binance(base, sym, interval, total), "Binance"
        except Exception as e:
            errs.append(f"{base}: {e}")
    try:
        return fetch_kraken(sym, interval, total), "Kraken"
    except Exception as e:
        errs.append(f"kraken: {e}")
    raise RuntimeError("; ".join(errs))

IV_MS = {"15m": M15, "1h": 3600000, "4h": 14400000, "1d": 86400000}

def closed(bars, interval, now_ms):
    return [b for b in bars if b[0] + IV_MS[interval] <= now_ms]

# ================= HOEHERE ZEITEBENEN =================
def pivot_hi(h, i, L, R):
    k = i - R
    if k - L < 0:
        return None
    c = h[k]
    if all(c > h[j] for j in range(k - L, k)) and all(c >= h[j] for j in range(k + 1, i + 1)):
        return c
    return None

def pivot_lo(l, i, L, R):
    k = i - R
    if k - L < 0:
        return None
    c = l[k]
    if all(c < l[j] for j in range(k - L, k)) and all(c <= l[j] for j in range(k + 1, i + 1)):
        return c
    return None

def struct_1h(b1):
    # Pine f_struct(): Trend nach Bruch des letzten 1H-Pivots (3/3)
    h = [b[2] for b in b1]; l = [b[3] for b in b1]; c = [b[4] for b in b1]
    gHi = gLo = None; t = 0; res = {}
    for i in range(len(b1)):
        p1 = pivot_hi(h, i, 3, 3); p2 = pivot_lo(l, i, 3, 3)
        if p1 is not None: gHi = p1
        if p2 is not None: gLo = p2
        if gHi is not None and c[i] > gHi: t = 1; gHi = None
        if gLo is not None and c[i] < gLo: t = -1; gLo = None
        res[b1[i][0]] = t
    return res

def ema_up_4h(b4, length=200):
    res = {}; ema = None; alpha = 2 / (length + 1); buf = []
    for b in b4:
        c = b[4]
        if ema is None:
            buf.append(c)
            if len(buf) == length:
                ema = sum(buf) / length
        else:
            ema = alpha * c + (1 - alpha) * ema
        res[b[0]] = None if ema is None else (1 if c > ema else 0)
    return res

# ================= SIMULATION (Bar fuer Bar wie Pine) =================
def simulate(sym, b15, b1, b4, bd):
    o = [b[1] for b in b15]; h = [b[2] for b in b15]; l = [b[3] for b in b15]; c = [b[4] for b in b15]
    T = [b[0] for b in b15]; n = len(b15)
    tr1 = struct_1h(b1); up4 = ema_up_4h(b4)
    day_hl = {b[0]: (b[2], b[3]) for b in bd}
    # ATR (RMA 14)
    atr = [None] * n; trs = []
    for i in range(n):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i < 14:
            trs.append(tr)
            if i == 13: atr[i] = sum(trs) / 14
        else:
            atr[i] = (atr[i - 1] * 13 + tr) / 14
    wsum = max(sum(WEIGHTS), 1); W = [w / wsum for w in WEIGHTS]
    lv = []            # [preis, seite, bar, istPD]
    phs, pls = [], []  # (bar, preis)
    S = dict(act=False); Lg = dict(act=False)
    tr_ = None; freeBar = 0; ev = []; d = DIGITS.get(sym, 4)
    fmt = lambda x: f"{x:.{d}f}"

    def add_lvl(p, s, b, pd):
        lv.append([p, s, b, pd])
        if len(lv) > 150: lv.pop(0)

    for i in range(n):
        a = atr[i]
        # --- Swings ---
        if i >= 2 * SW_LEN:
            ph = pivot_hi(h, i, SW_LEN, SW_LEN); pl = pivot_lo(l, i, SW_LEN, SW_LEN); pb = i - SW_LEN
            if ph is not None:
                phs.append((pb, ph)); phs[:] = phs[-30:]; add_lvl(ph, 1, pb, False)
            if pl is not None:
                pls.append((pb, pl)); pls[:] = pls[-30:]; add_lvl(pl, -1, pb, False)
        # --- Vortageshoch/-tief ---
        dkey = T[i] // 86400000 * 86400000
        if i > 0 and dkey != T[i - 1] // 86400000 * 86400000 and (dkey - 86400000) in day_hl:
            pdh, pdl = day_hl[dkey - 86400000]
            add_lvl(pdh, 1, i, True); add_lvl(pdl, -1, i, True)
        # --- Trade-Verwaltung ---
        if tr_ is not None and i > tr_["bar"]:
            t = tr_; sd = t["side"]; r = None
            hit = lambda lvl: h[i] >= lvl if sd == 1 else l[i] <= lvl
            hitSL = l[i] <= t["stop"] if sd == 1 else h[i] >= t["stop"]
            if hitSL:
                r = t["got"] + t["rem"] * (t["stop"] - t["e"]) / t["risk"] * sd
            else:
                for k in range(3):
                    if t["k"] == k and t["rem"] > 0.0001 and hit(t["e"] + sd * t["risk"] * TPS[k]):
                        t["got"] += W[k] * TPS[k]; t["rem"] -= W[k]; t["k"] = k + 1
                        if t["rem"] > 0.0001 and k < 2:
                            msg = f"TP{k+1} erreicht: {t['name']}\n{W[k]*100:.0f}% schliessen"
                            if k == 0 and BE_AFTER1:
                                t["stop"] = t["e"]; msg += f"\nStop auf Einstand setzen: {fmt(t['e'])}"
                            if k == 1 and TR_AFTER2:
                                t["stop"] = t["e"] + sd * t["risk"] * TPS[0]; msg += f"\nStop auf TP1 setzen: {fmt(t['stop'])}"
                            ev.append(dict(t=T[i], kind=f"tp{k+1}", msg=msg))
                if t["rem"] <= 0.0001:
                    r = t["got"]
                elif i - t["bar"] >= MAX_HOLD - 1:
                    r = t["got"] + t["rem"] * (c[i] - t["e"]) / t["risk"] * sd
            if r is not None:
                net = r - COST_PCT / 100 * t["e"] / t["risk"]
                why = ("Stop-Loss" if t["k"] == 0 else "Stop (Einstand/TP1)") if hitSL else \
                      (("TP3 erreicht" if t["k"] >= 3 else "Ziel erreicht") if t["rem"] <= 0.0001 else "Zeitstopp - Rest schliessen")
                ev.append(dict(t=T[i], kind="end", msg=f"Trade beendet: {t['name']}\n{why}\nErgebnis: {net:+.2f}R",
                               row=dict(sym=sym, side="LONG" if sd == 1 else "SHORT", wende=int(t["rev"]),
                                        entry_time=T[t["bar"]] + M15, exit_time=T[i] + M15, entry=round(t["e"], d + 2), sl=round(t["sl"], d + 2),
                                        reason=why, r=round(net, 3))))
                tr_ = None; freeBar = i + 1
        # --- Sweep-Erkennung ---
        swHi = swLo = False; swHiP = swLoP = None; swHiB = swLoB = None; swHiK = swLoK = False
        for j in range(len(lv) - 1, -1, -1):
            p, s, b, pd = lv[j]
            if i - b >= LVL_LIFE:
                lv.pop(j)
            elif i - b >= 3 and ((s == 1 and h[i] > p) or (s == -1 and l[i] < p)):
                if s == 1:
                    swHi = True
                    if swHiP is None or p > swHiP: swHiP, swHiB, swHiK = p, b, pd
                else:
                    swLo = True
                    if swLoP is None or p < swLoP: swLoP, swLoB, swLoK = p, b, pd
                lv.pop(j)
        if swHi: S = dict(act=True, ok=False, lvl=swHiP, sb=i, ext=h[i], born=swHiB, pd=swHiK, bos=None)
        if swLo: Lg = dict(act=True, ok=False, lvl=swLoP, sb=i, ext=l[i], born=swLoB, pd=swLoK, bos=None)
        shortSig = longSig = False
        if S["act"]:
            S["ext"] = max(S["ext"], h[i])
            if not S["ok"]:
                if c[i] < S["lvl"]:
                    S["ok"] = True
                    S["bos"] = next((p for b, p in reversed(pls) if b < S["sb"]), None)
                    if S["bos"] is None: S["act"] = False
                elif i - S["sb"] >= 2:
                    S["act"] = False
            else:
                if i - S["sb"] > CHOCH_MAX: S["act"] = False
                elif c[i] < S["bos"]: shortSig = True; S["act"] = False
        if Lg["act"]:
            Lg["ext"] = min(Lg["ext"], l[i])
            if not Lg["ok"]:
                if c[i] > Lg["lvl"]:
                    Lg["ok"] = True
                    Lg["bos"] = next((p for b, p in reversed(phs) if b < Lg["sb"]), None)
                    if Lg["bos"] is None: Lg["act"] = False
                elif i - Lg["sb"] >= 2:
                    Lg["act"] = False
            else:
                if i - Lg["sb"] > CHOCH_MAX: Lg["act"] = False
                elif c[i] > Lg["bos"]: longSig = True; Lg["act"] = False
        # --- Signal ---
        side = 0
        if longSig and not shortSig: side, X = 1, Lg
        if shortSig and not longSig: side, X = -1, S
        if side != 0 and tr_ is None and i >= freeBar and a is not None:
            t1 = tr1.get(T[i] // 3600000 * 3600000 - 3600000, 0)
            u4 = up4.get(T[i] // 14400000 * 14400000 - 14400000)
            age = X["sb"] - X["born"]
            slP = X["ext"] - SL_BUF * a if side == 1 else X["ext"] + SL_BUF * a
            risk = abs(c[i] - slP)
            trOk = (not USE_1H or t1 == side) and (not USE_4H or (u4 is not None and u4 == (1 if side == 1 else 0)))
            revOk = USE_REV and not trOk and t1 != side and u4 is not None and u4 == (0 if side == 1 else 1) and (age >= REV_AGE or X["pd"])
            if (trOk or revOk) and risk >= 0.2 * a:
                rev = revOk and not trOk
                dirTxt = ("WENDE " if rev else "") + ("BUY" if side == 1 else "SELL")
                name = ("WENDE " if rev else "") + ("LONG " if side == 1 else "SHORT ") + sym
                tr_ = dict(side=side, e=c[i], sl=slP, stop=slP, risk=risk, k=0, rem=1.0, got=0.0, bar=i, rev=rev, name=name)
                liq = None
                for p, s, b, pd in lv:
                    if s == side and (p > c[i] if side == 1 else p < c[i]) and abs(p - c[i]) / risk >= LIQ_MIN:
                        if liq is None or abs(p - c[i]) < abs(liq - c[i]): liq = p
                tps = "\n".join(f"TP{k+1}: {fmt(c[i] + side * risk * TPS[k])} ({W[k]*100:.0f}%)" for k in range(3))
                liqTxt = "-" if liq is None else f"{fmt(liq)} ({abs(liq - c[i]) / risk:.1f}R)"
                ev.append(dict(t=T[i], kind="entry", side=side, e=c[i], risk=risk,
                               msg=f"**{dirTxt} {sym} (15m)**\nEntry: {fmt(c[i])}\nSL: {fmt(slP)}\n{tps}\nLiq-Ziel: {liqTxt}"))
    return ev, tr_, (tr1.get(T[-1] // 3600000 * 3600000 - 3600000, 0), up4.get(T[-1] // 14400000 * 14400000 - 14400000))

# ================= PBD 4H (v17) =================
def atr_rma(h, l, c, n=14):
    out = [None] * len(c); trs = []
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i < n:
            trs.append(tr)
            if i == n - 1: out[i] = sum(trs) / n
        else:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out

def pbd_zone(h, l, c, end, aRef):
    # groesste gueltige Range (40 -> 8 Kerzen), letzte Kerze = end
    for W in range(40, 7, -4):
        st = end - W + 1
        if st < 0 or aRef is None:
            continue
        hi = max(h[st:end + 1]); lo = min(l[st:end + 1]); H = hi - lo
        if not (PBD_MIN_H * aRef <= H <= PBD_MAX_H * aRef):
            continue
        mid = (hi + lo) / 2; cr = 0; prev = 0
        for k in range(st, end + 1):
            sg = 1 if c[k] > mid else -1 if c[k] < mid else 0
            if sg != 0:
                if prev != 0 and sg != prev: cr += 1
                prev = sg
        if cr < PBD_MIN_CR:
            continue
        tt = tb = 0; lt = lb = -100
        for pos, k in enumerate(range(st, end + 1)):
            if h[k] >= hi - PBD_EDGE * H:
                if pos - lt > 2: tt += 1
                lt = pos
            if l[k] <= lo + PBD_EDGE * H:
                if pos - lb > 2: tb += 1
                lb = pos
        if tt >= 2 and tb >= 2:
            return W, hi, lo
    return None

def simulate_pbd(sym, b4):
    o = [b[1] for b in b4]; h = [b[2] for b in b4]; l = [b[3] for b in b4]; c = [b[4] for b in b4]
    T = [b[0] for b in b4]; n = len(b4); atr = atr_rma(h, l, c)
    d = DIGITS.get(sym, 4); fmt = lambda x: f"{x:.{d}f}"
    tr_ = None; freeBar = 0; ev = []
    for i in range(n):
        # Ausstieg
        if tr_ is not None and i > tr_["bar"]:
            t = tr_; sd = t["side"]
            hitS = l[i] <= t["sl"] if sd == 1 else h[i] >= t["sl"]
            hitT = h[i] >= t["tp"] if sd == 1 else l[i] <= t["tp"]
            if hitS or hitT or i - t["bar"] >= PBD_HOLD - 1:
                px = (min(o[i], t["sl"]) if sd == 1 else max(o[i], t["sl"])) if hitS else t["tp"] if hitT else c[i]
                net = (px - t["e"]) / t["risk"] * sd - COST_PCT / 100 * t["e"] / t["risk"]
                why = "Stop-Loss" if hitS else "Ziel erreicht" if hitT else "Zeitstopp - schliessen"
                ev.append(dict(t=T[i], kind="end", msg=f"Trade beendet: PBD {'LONG' if sd == 1 else 'SHORT'} {sym} (4H)\n{why}\nErgebnis: {net:+.2f}R",
                               row=dict(sym=sym, side="LONG" if sd == 1 else "SHORT", wende=0, entry_time=T[t["bar"]] + H4,
                                        exit_time=T[i] + H4, entry=round(t["e"], d + 2), sl=round(t["sl"], d + 2), reason=why, r=round(net, 3))))
                tr_ = None; freeBar = i + 1
        # Signal: i-1 = Ausbruchskerze, i = Bestaetigung
        if i < 60 or atr[i] is None:
            continue
        z = pbd_zone(h, l, c, i - 2, atr[i - 1])
        if z is None:
            continue
        W, hi, lo = z; H = hi - lo
        brk = 1 if c[i - 1] > hi else -1 if c[i - 1] < lo else 0
        if brk == 0:
            continue
        bodyU = abs(c[i] - o[i]); topT = max(o[i - 1], c[i - 1]); botT = min(o[i - 1], c[i - 1])
        inside = botT <= min(o[i], c[i]) and max(o[i], c[i]) <= topT
        wickOut = h[i] - max(o[i], c[i]) if brk == 1 else min(o[i], c[i]) - l[i]
        reject = inside and wickOut >= 2 * max(bodyU, 1e-12)
        outside = c[i] > hi if brk == 1 else c[i] < lo
        if not outside or reject or tr_ is not None or i < freeBar:
            continue
        z0 = i - W - 1
        imp = c[z0] - c[z0 - 10]
        kind = "P" if imp >= 3 * atr[z0] else "b" if imp <= -3 * atr[z0] else "D"
        sl = lo - 0.1 * atr[i] if brk == 1 else hi + 0.1 * atr[i]
        risk = abs(c[i] - sl); tp = c[i] + brk * PBD_CRV * risk
        if risk <= 0:
            continue
        tr_ = dict(side=brk, e=c[i], sl=sl, tp=tp, risk=risk, bar=i)
        ev.append(dict(t=T[i], kind="entry", side=brk, e=c[i], risk=risk,
                       msg=f"**PBD {'BUY' if brk == 1 else 'SELL'} {sym} (4H)**\nForm: {kind} · Range {fmt(lo)} – {fmt(hi)}\nEntry: {fmt(c[i])}\nStop: {fmt(sl)} (hinter der Range)\nZiel: {fmt(tp)} (CRV 1:{PBD_CRV:.0f})"))
    return ev, tr_

# ================= DISCORD / ZUSTAND / JOURNAL =================
def send(msg):
    hook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    print("----\n" + msg)
    if not hook:
        return
    data = json.dumps({"content": msg[:1990]}).encode()
    for _ in range(3):
        req = urllib.request.Request(hook, data=data, headers={"Content-Type": "application/json", "User-Agent": "smc-bot"})
        try:
            urllib.request.urlopen(req, timeout=20); time.sleep(1); return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read().decode()).get("retry_after", 2)) + 0.5); continue
            print("Discord-Fehler", e.code, e.read()[:200]); return

def hm(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%d.%m. %H:%M")

JOURNAL = "journal.csv"
FIELDS = ["strat", "sym", "side", "wende", "entry_time", "exit_time", "entry", "sl", "reason", "r"]

def journal_add(row):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new: w.writeheader()
        r = dict(row); r["entry_time"] = hm(r["entry_time"]); r["exit_time"] = hm(r["exit_time"])
        w.writerow(r)

def journal_stats(strat, sym=None):
    if not os.path.exists(JOURNAL):
        return 0, 0, 0.0
    rows = [r for r in csv.DictReader(open(JOURNAL)) if r.get("strat") == strat]
    if sym: rows = [r for r in rows if r["sym"] == sym]
    rs = [float(r["r"]) for r in rows]
    return len(rs), sum(1 for x in rs if x > 0), sum(rs)

def trend_txt(t1, u4):
    return f"1H {'▲' if t1 == 1 else '▼' if t1 == -1 else '–'} 4H {'▲' if u4 == 1 else '▼' if u4 == 0 else '–'}"

def main():
    now = int(time.time() * 1000)
    state = json.load(open("state.json")) if os.path.exists("state.json") else {}
    last = state.setdefault("last", {})
    first = state.setdefault("start", {})
    start_lines = []
    for sym in COINS:
        try:
            raw15, src = fetch(sym, "15m", 1500)
            b15 = closed(raw15, "15m", now)
            b1 = closed(fetch(sym, "1h", 1000)[0], "1h", now)
            b4 = closed(fetch(sym, "4h", 1000)[0], "4h", now)
            bd = closed(fetch(sym, "1d", 60)[0], "1d", now)
        except Exception as e:
            print(sym, "Datenfehler:", e); continue
        if len(b15) < 300 or len(b4) < 100:
            print(sym, "zu wenig Daten"); continue
        price = raw15[-1][4]
        d = DIGITS.get(sym, 4)
        ev11, open11, (t1, u4) = simulate(sym, b15, b1, b4, bd)
        runs = [("v11", "v11.4 15m", ev11, open11, b15[-1][0], M15)]
        if PBD_ON:
            evp, openp = simulate_pbd(sym, b4)
            runs.append(("pbd", "PBD 4H", evp, openp, b4[-1][0], H4))
        for strat, label, ev, open_tr, newest, iv in runs:
            key = f"{strat}:{sym}"
            if key not in last:
                last[key] = newest; first[key] = newest
                stat = "wartet" if open_tr is None else f"im Trade (Entry {open_tr['e']}) – nicht nachtraden"
                start_lines.append(f"{label} {sym}: {stat}" + (f", {trend_txt(t1, u4)} [{src}]" if strat == "v11" else ""))
                continue
            for e in ev:
                if e["t"] <= last[key]:
                    continue
                close_ms = e["t"] + iv
                delay = max(0, (now - close_ms) // 60000)
                msg = e["msg"]
                if e["kind"] == "entry":
                    dist = (price - e["e"]) / e["risk"] * e["side"]
                    msg += f"\nKerze schloss {hm(close_ms)} (vor {delay} Min.) · Kurs jetzt {price:.{d}f} ({dist:+.2f}R)"
                    if abs(dist) > MAX_DIST_R:
                        msg += f"\nACHTUNG: Kurs schon mehr als {MAX_DIST_R}R vom Entry weg – auslassen"
                else:
                    msg += f"\n({hm(close_ms)}, vor {delay} Min.)"
                if e["kind"] == "end" and e["row"]["entry_time"] - iv <= first.get(key, 0):
                    msg += "\n(Trade lief schon vor dem Bot-Start – zaehlt nicht im Journal)"
                elif e["kind"] == "end":
                    row = dict(e["row"]); row["strat"] = strat
                    journal_add(row)
                    n, w, sm = journal_stats(strat, sym); N, Wn, Sm = journal_stats(strat)
                    msg += f"\nLive {label} {sym}: {n} Trades, {sm:+.1f}R · alle Coins: {N} Trades, {Wn}/{N} Gewinner, {Sm:+.1f}R"
                send(msg)
            last[key] = newest
    if start_lines:
        send("**Signal-Bot gestartet** – SMC v11.4 (15m) + PBD (4H). Ab jetzt kommen alle neuen Signale.\n" + "\n".join(start_lines))
    json.dump(state, open("state.json", "w"), indent=1)

if __name__ == "__main__":
    main()
