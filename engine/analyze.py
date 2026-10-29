"""
Motore di analisi calcistica - gratuito, con quote reali.

Fonte dati: football-data.co.uk (nessuna chiave richiesta).
  - fixtures.csv / new_league_fixtures.csv: partite in programma con le quote
    di piu' bookmaker (colonne "Avg" = media tra bookmaker).
  - mmz4281/<stagione>/<codice>.csv: risultati della stagione in corso.

Cosa fa ad ogni esecuzione:
  1. Scarica i risultati della stagione e calcola la forma recente di ogni squadra.
  2. Stima le probabilita' di ogni partita con un modello di Poisson (gol fatti e
     subiti, casa/trasferta) e le mescola per meta' con le probabilita' implicite
     nelle quote. Il valore atteso (EV) e' stima x quota - 1.
  3. Genera i segnali (statistici e "value") e le schedine consigliate (X1..X5).
  4. Registra ogni segnale pubblicato e, a partita finita, verifica se e' uscito:
     e' il "tabellone di verifica" che dice se il sistema funziona davvero.

Il registro vive dentro docs/data.json (chiave "history"), cosi' il workflow non
ha bisogno di toccare altri file.
"""

import csv
import io
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import requests

FIXTURES_MAIN_URL = "https://www.football-data.co.uk/fixtures.csv"
FIXTURES_EXTRA_URL = "https://www.football-data.co.uk/new_league_fixtures.csv"
HIST_BASE = "https://www.football-data.co.uk/mmz4281"

MAIN_LEAGUES = {
    "E0": "Premier League",
    "E1": "Championship",
    "E2": "League One",
    "E3": "League Two",
    "EC": "National League",
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "I1": "Serie A",
    "I2": "Serie B",
    "SP1": "LaLiga",
    "SP2": "LaLiga 2",
    "F1": "Ligue 1",
    "F2": "Ligue 2",
    "N1": "Eredivisie",
    "B1": "Belgio - Pro League",
}

# Quote disponibili ma nessuno storico gratuito: mostriamo solo il favorito di mercato.
EXTRA_LEAGUES = {
    ("Ireland", "Premier Division"): "Irlanda - Premier Division",
    ("Norway", "Eliteserien"): "Norvegia - Eliteserien",
    ("Sweden", "Allsvenskan"): "Svezia - Allsvenskan",
}

OUTPUT_PATH = "docs/data.json"

# --- Parametri dei segnali statistici (forma recente) ---
N_RECENT_MATCHES = 8
MIN_FORM_MATCHES = 4          # sotto questa soglia niente segnali statistici

# --- Parametri del modello ---
SHRINK_K = 6.0                # partite "fittizie" a media di campionato (stabilizza le forze)
MARKET_WEIGHT = 0.5           # peso del mercato nella stima finale
MIN_LEAGUE_MATCHES = 30       # sotto questa soglia il modello non parte
VALUE_MIN_EV = 0.03           # valore atteso minimo per chiamarlo "value"
VALUE_MIN_P = 0.20            # niente quote lunghe: stima minima per un value bet

# --- Puntata consigliata: Kelly frazionato con tetto ---
KELLY_FRACTION = 0.25
KELLY_CAP_SINGLE = 0.02
KELLY_CAP_MULTI = 0.01

# --- Schedine consigliate ---
SLIP_WINDOW_DAYS = 2          # partite di oggi e dei 2 giorni successivi
SLIP_MIN_EV = -0.04           # accetta al massimo un piccolo EV negativo (poi lo dichiara)
SLIP_MIN_P = 0.45

# --- Registro ---
HISTORY_KEEP_DAYS = 400
PENDING_VOID_AFTER_DAYS = 10  # partita non trovata nei risultati dopo 10 giorni: annullata


def current_season_code(today=None):
    """Es. ottobre 2026 -> '2627' (stagione 2026-27)."""
    today = today or datetime.now(timezone.utc).date()
    start_year = today.year if today.month >= 7 else today.year - 1
    return f"{str(start_year)[2:]}{str(start_year + 1)[2:]}"


# ----------------------------------------------------------------------------
# Utilita'
# ----------------------------------------------------------------------------

def fetch_csv(url):
    resp = requests.get(url, timeout=30, headers={"User-Agent": "SchedinaAnalisi/2.0"})
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def num(value):
    """Quote e valori positivi: ritorna None se vuoto o <= 0."""
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def goals(value):
    """Gol: lo zero e' un valore valido (num() lo scarterebbe)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_day(text):
    text = (text or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def devig(odds):
    """Probabilita' implicite normalizzate (tolto il margine del bookmaker)."""
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    return [x / total for x in inv]


# ----------------------------------------------------------------------------
# Risultati e forma recente
# ----------------------------------------------------------------------------

def load_league_results(code, season_code):
    try:
        rows = fetch_csv(f"{HIST_BASE}/{season_code}/{code}.csv")
    except Exception as exc:  # noqa: BLE001
        print(f"  storico non disponibile per {code}: {exc}")
        return []
    results = []
    for row in rows:
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        fh, fa = goals(row.get("FTHG")), goals(row.get("FTAG"))
        day = parse_day(row.get("Date"))
        if not home or not away or fh is None or fa is None or day is None:
            continue
        results.append({"home": home, "away": away, "day": day, "fh": fh, "fa": fa})
    return results


def form_stats(results):
    by_team = {}
    for m in sorted(results, key=lambda r: r["day"]):
        by_team.setdefault(m["home"], []).append((m["fh"], m["fa"]))
        by_team.setdefault(m["away"], []).append((m["fa"], m["fh"]))

    stats = {}
    for team, matches in by_team.items():
        recent = matches[-N_RECENT_MATCHES:]
        n = len(recent)
        over25 = sum(1 for a, b in recent if a + b > 2)
        btts = sum(1 for a, b in recent if a > 0 and b > 0)
        points = sum(3 if a > b else 1 if a == b else 0 for a, b in recent)
        stats[team] = {
            "n": n,
            "over25_rate": round(over25 / n, 2),
            "btts_rate": round(btts / n, 2),
            "ppg": round(points / n, 2),
        }
    return stats


# ----------------------------------------------------------------------------
# Modello di Poisson
# ----------------------------------------------------------------------------

NEUTRAL = {"att_h": 1.0, "def_h": 1.0, "att_a": 1.0, "def_a": 1.0}


def league_model(results):
    """Forze di attacco/difesa di ogni squadra, in casa e in trasferta."""
    if len(results) < MIN_LEAGUE_MATCHES:
        return None
    n = len(results)
    mu_h = sum(m["fh"] for m in results) / n
    mu_a = sum(m["fa"] for m in results) / n
    if mu_h <= 0 or mu_a <= 0:
        return None

    acc = {}
    for m in results:
        h = acc.setdefault(m["home"], dict(hg=0, hf=0, ha=0, ag=0, af=0, aa=0))
        h["hg"] += 1
        h["hf"] += m["fh"]
        h["ha"] += m["fa"]
        a = acc.setdefault(m["away"], dict(hg=0, hf=0, ha=0, ag=0, af=0, aa=0))
        a["ag"] += 1
        a["af"] += m["fa"]
        a["aa"] += m["fh"]

    k = SHRINK_K
    teams = {}
    for name, t in acc.items():
        teams[name] = {
            "att_h": (t["hf"] + k * mu_h) / ((t["hg"] + k) * mu_h),
            "def_h": (t["ha"] + k * mu_a) / ((t["hg"] + k) * mu_a),
            "att_a": (t["af"] + k * mu_a) / ((t["ag"] + k) * mu_a),
            "def_a": (t["aa"] + k * mu_h) / ((t["ag"] + k) * mu_h),
        }
    return {"mu_h": mu_h, "mu_a": mu_a, "teams": teams}


def expected_goals(model, home, away):
    th = model["teams"].get(home, NEUTRAL)
    ta = model["teams"].get(away, NEUTRAL)
    lam_h = model["mu_h"] * th["att_h"] * ta["def_a"]
    lam_a = model["mu_a"] * ta["att_a"] * th["def_h"]
    return lam_h, lam_a


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def outcome_probs(lam_h, lam_a, max_goals=10):
    ph = [poisson_pmf(i, lam_h) for i in range(max_goals + 1)]
    pa = [poisson_pmf(i, lam_a) for i in range(max_goals + 1)]
    p_h = p_d = p_a = p_over = p_btts = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            if i > j:
                p_h += p
            elif i == j:
                p_d += p
            else:
                p_a += p
            if i + j > 2:
                p_over += p
            if i > 0 and j > 0:
                p_btts += p
    tot = p_h + p_d + p_a
    return {
        "H": p_h / tot, "D": p_d / tot, "A": p_a / tot,
        "O25": p_over / tot, "U25": 1 - p_over / tot, "BTTS": p_btts / tot,
    }


def kelly_fraction(p, odds, cap):
    """Kelly frazionato: 0 se il valore atteso non e' positivo."""
    if not odds or odds <= 1 or p <= 0:
        return 0.0
    f = (p * odds - 1) / (odds - 1)
    if f <= 0:
        return 0.0
    return round(min(f * KELLY_FRACTION, cap), 4)


# ----------------------------------------------------------------------------
# Segnali
# ----------------------------------------------------------------------------

LABELS = {
    "H": ("1X2", "1 (vittoria {home})"),
    "D": ("1X2", "X (pareggio)"),
    "A": ("1X2", "2 (vittoria {away})"),
    "O25": ("Over/Under", "Over 2.5"),
    "U25": ("Over/Under", "Under 2.5"),
    "BTTS": ("GG/NG", "Gol (GG)"),
}
CONF_DOWN = {"Alta": "Media", "Media": "Bassa", "Bassa": "Bassa"}


def outcome_for_key(key, fh, fa):
    if key == "H":
        return fh > fa
    if key == "D":
        return fh == fa
    if key == "A":
        return fa > fh
    if key == "O25":
        return fh + fa > 2
    if key == "U25":
        return fh + fa <= 2
    if key == "BTTS":
        return fh > 0 and fa > 0
    raise ValueError(key)


def analyze_fixture(row, code, league_name, form, model, played, today):
    home, away = row.get("HomeTeam"), row.get("AwayTeam")
    day = parse_day(row.get("Date"))
    if not home or not away or day is None or day < today:
        return []
    if (home, away, day) in played:      # gia' giocata: i segnali sarebbero fuori tempo
        return []

    odds = {
        "H": num(row.get("AvgH")), "D": num(row.get("AvgD")), "A": num(row.get("AvgA")),
        "O25": num(row.get("Avg>2.5")), "U25": num(row.get("Avg<2.5")),
    }

    # 1) segnali statistici dalla forma recente
    stat = {}   # key -> (confidenza, motivo)
    hs, as_ = form.get(home), form.get(away)
    if hs and as_ and hs["n"] >= MIN_FORM_MATCHES and as_["n"] >= MIN_FORM_MATCHES:
        gap = hs["ppg"] - as_["ppg"]
        if gap >= 1.2 and odds["H"]:
            stat["H"] = ("Alta" if gap >= 1.8 else "Media",
                         f"Forma nettamente a favore del {home}: media punti {hs['ppg']} vs {as_['ppg']} "
                         f"(ultime {hs['n']}/{as_['n']} partite).")
        elif gap <= -1.2 and odds["A"]:
            stat["A"] = ("Alta" if gap <= -1.8 else "Media",
                         f"Forma nettamente a favore del {away}: media punti {as_['ppg']} vs {hs['ppg']} "
                         f"(ultime {as_['n']}/{hs['n']} partite).")
        over = (hs["over25_rate"] + as_["over25_rate"]) / 2
        if over >= 0.65 and odds["O25"]:
            stat["O25"] = ("Alta" if over >= 0.75 else "Media",
                           f"Over 2.5 nel {round(over * 100)}% delle ultime partite delle due squadre.")
        elif over <= 0.30 and odds["U25"]:
            stat["U25"] = ("Alta" if over <= 0.20 else "Media",
                           f"Solo il {round(over * 100)}% delle ultime partite delle due squadre sopra i 2.5 gol.")
        btts = (hs["btts_rate"] + as_["btts_rate"]) / 2
        if btts >= 0.70:
            stat["BTTS"] = ("Alta" if btts >= 0.80 else "Media",
                            f"Entrambe a segno nel {round(btts * 100)}% delle ultime partite.")

    # 2) stime del modello, mescolate col mercato
    est = {}    # key -> dict(p, pm, pk, ev)
    if model:
        lam_h, lam_a = expected_goals(model, home, away)
        pm = outcome_probs(lam_h, lam_a)
        pk = {}
        if odds["H"] and odds["D"] and odds["A"]:
            pk["H"], pk["D"], pk["A"] = devig([odds["H"], odds["D"], odds["A"]])
        if odds["O25"] and odds["U25"]:
            pk["O25"], pk["U25"] = devig([odds["O25"], odds["U25"]])
        for key in ("H", "D", "A", "O25", "U25"):
            if key in pk:
                p = (1 - MARKET_WEIGHT) * pm[key] + MARKET_WEIGHT * pk[key]
                est[key] = {"p": p, "pm": pm[key], "pk": pk[key], "ev": p * odds[key] - 1}
        est["BTTS"] = {"p": pm["BTTS"], "pm": pm["BTTS"], "pk": None, "ev": None}

    # 3) elenco finale: statistici + value (il pareggio non viene mai proposto)
    keys = set(stat) | {
        k for k, e in est.items()
        if k not in ("D", "BTTS") and e["ev"] >= VALUE_MIN_EV and e["p"] >= VALUE_MIN_P
    }

    signals = []
    for key in sorted(keys):
        mercato, pron_tpl = LABELS[key]
        e = est.get(key)
        is_value = bool(e and e["ev"] is not None and e["ev"] >= VALUE_MIN_EV and e["p"] >= VALUE_MIN_P)
        in_stat = key in stat

        parts = []
        if in_stat:
            parts.append(stat[key][1])
        if e:
            if e["pk"] is not None:
                parts.append(
                    f"Stima {e['p']:.0%} (modello {e['pm']:.0%}, mercato {e['pk']:.0%}) · "
                    f"quota {odds[key]:.2f} → valore atteso {e['ev']:+.0%}."
                )
            else:
                parts.append(f"Il modello stima {e['pm']:.0%} di probabilita' (quota non disponibile).")

        if in_stat and is_value:
            conf, tipo = "Alta", "entrambi"
        elif is_value:
            conf = "Alta" if (e["ev"] >= 0.10 and e["p"] >= 0.35) else "Media"
            tipo = "value"
        else:
            conf, tipo = stat[key][0], "statistico"
            if e and e["ev"] is not None and e["ev"] < -0.08:
                conf = CONF_DOWN[conf]
                parts.append("Attenzione: il mercato paga meno di quanto la stima suggerisce.")

        quota = odds.get(key)
        cap = KELLY_CAP_SINGLE
        signals.append({
            "id": f"{day.isoformat()}|{home}|{away}|{key}",
            "partita": f"{home} - {away}", "home": home, "away": away,
            "campionato": league_name, "lg": code,
            "data": row.get("Date", ""), "day": day.isoformat(),
            "mercato": mercato, "key": key,
            "pronostico": pron_tpl.format(home=home, away=away),
            "quota": quota,
            "p": round(e["p"], 3) if e else None,
            "pm": round(e["pm"], 3) if e else None,
            "pk": round(e["pk"], 3) if e and e["pk"] is not None else None,
            "ev": round(e["ev"], 3) if e and e["ev"] is not None else None,
            "kelly": kelly_fraction(e["p"], quota, cap) if e and e["ev"] is not None and quota else 0.0,
            "value": is_value, "tipo": tipo, "confidenza": conf,
            "nota": " ".join(parts),
        })
    return signals


def analyze_extra_leagues(today):
    signals = []
    for row in fetch_csv(FIXTURES_EXTRA_URL):
        key = (row.get("Country"), row.get("League"))
        if key not in EXTRA_LEAGUES:
            continue
        home, away = row.get("Home"), row.get("Away")
        day = parse_day(row.get("Date"))
        avg_h, avg_d, avg_a = num(row.get("AvgH")), num(row.get("AvgD")), num(row.get("AvgA"))
        if not (home and away and avg_h and avg_d and avg_a) or (day and day < today):
            continue
        options = {f"1 (vittoria {home})": avg_h, "X (pareggio)": avg_d, f"2 (vittoria {away})": avg_a}
        favorite = min(options, key=options.get)    # quota piu' bassa = favorita
        signals.append({
            "id": f"extra|{row.get('Date', '')}|{home}|{away}",
            "partita": f"{home} - {away}", "home": home, "away": away,
            "campionato": EXTRA_LEAGUES[key], "lg": None,
            "data": row.get("Date", ""), "day": day.isoformat() if day else None,
            "mercato": "1X2", "key": None, "pronostico": f"Favorita di mercato: {favorite}",
            "quota": options[favorite], "p": None, "pm": None, "pk": None, "ev": None,
            "kelly": 0.0, "value": False, "tipo": "mercato", "confidenza": "Bassa",
            "nota": "Solo quadro di mercato: per questo campionato non esiste uno storico gratuito.",
        })
    return signals


# ----------------------------------------------------------------------------
# Schedine consigliate
# ----------------------------------------------------------------------------

def build_slips(signals, today):
    horizon = today + timedelta(days=SLIP_WINDOW_DAYS)
    pool = []
    for s in signals:
        if not (s.get("key") and s.get("quota") and s.get("p") is not None and s.get("ev") is not None):
            continue
        day = parse_day(s["day"])
        if day is None or not (today <= day <= horizon):
            continue
        if s["ev"] < SLIP_MIN_EV or s["p"] < SLIP_MIN_P or s["confidenza"] not in ("Alta", "Media"):
            continue
        pool.append(s)

    pool.sort(key=lambda s: s["ev"] + 0.25 * s["p"], reverse=True)
    picked, seen = [], set()
    for s in pool:
        match = (s["home"], s["away"], s["day"])
        if match in seen:            # una sola selezione per partita
            continue
        seen.add(match)
        picked.append(s)

    slips = []
    for n in range(1, 6):
        if len(picked) < n:
            break
        legs = picked[:n]
        quota = math.prod(l["quota"] for l in legs)
        p = math.prod(l["p"] for l in legs)
        ev = p * quota - 1
        cap = KELLY_CAP_SINGLE if n == 1 else KELLY_CAP_MULTI
        slips.append({
            "id": f"{today.isoformat()}|X{n}",
            "tipo": "Singola" if n == 1 else f"X{n}",
            "n": n, "day": today.isoformat(),
            "quota": round(quota, 2), "p": round(p, 3), "ev": round(ev, 3),
            "kelly": kelly_fraction(p, quota, cap),
            "value": ev > 0,
            "legs": [{k: l[k] for k in ("id", "partita", "campionato", "data", "mercato",
                                        "pronostico", "quota", "p", "confidenza")} for l in legs],
            "nota": ("Valore atteso positivo secondo la stima." if ev > 0 else
                     "Nessun valore atteso positivo: e' un'indicazione statistica, non una puntata consigliata."),
        })
    return slips


# ----------------------------------------------------------------------------
# Registro dei segnali e verifica degli esiti
# ----------------------------------------------------------------------------

def load_previous_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f).get("history") or {}
    except (OSError, ValueError):
        hist = {}
    return {"signals": hist.get("signals", []), "slips": hist.get("slips", [])}


def update_history(history, signals, slips, results_by_league, today):
    played = {}
    for code, results in results_by_league.items():
        idx = played.setdefault(code, {})
        for m in results:
            idx[(m["home"], m["away"], m["day"])] = (m["fh"], m["fa"])

    # 1) verifica le giocate in attesa
    for entry in history["signals"]:
        if entry["status"] != "P":
            continue
        day = parse_day(entry["day"])
        if day is None or day > today:
            continue
        result = played.get(entry["lg"], {}).get((entry["home"], entry["away"], day))
        if result:
            entry["score"] = f"{result[0]}-{result[1]}"
            entry["status"] = "W" if outcome_for_key(entry["key"], *result) else "L"
        elif (today - day).days > PENDING_VOID_AFTER_DAYS:
            entry["status"] = "V"

    # 2) registra i segnali nuovi (la prima quota pubblicata resta quella valida)
    known = {e["id"] for e in history["signals"]}
    for s in signals:
        if not s.get("key") or not s.get("lg") or s["id"] in known:
            continue
        history["signals"].append({
            "id": s["id"], "day": s["day"], "lg": s["lg"], "campionato": s["campionato"],
            "home": s["home"], "away": s["away"], "key": s["key"],
            "mercato": s["mercato"], "pronostico": s["pronostico"],
            "quota": s["quota"], "p": s["p"], "tipo": s["tipo"], "conf": s["confidenza"],
            "status": "P", "score": None,
        })
        known.add(s["id"])

    # 3) schedine: una per tipo al giorno (la prima del giorno), poi verifica
    slip_ids = {e["id"] for e in history["slips"]}
    for sl in slips:
        if sl["id"] in slip_ids:
            continue
        history["slips"].append({
            "id": sl["id"], "day": sl["day"], "tipo": sl["tipo"], "quota": sl["quota"],
            "p": sl["p"], "legs": [l["id"] for l in sl["legs"]], "status": "P",
        })
    by_id = {e["id"]: e for e in history["signals"]}
    for sl in history["slips"]:
        if sl["status"] != "P":
            continue
        legs = [by_id.get(i) for i in sl["legs"]]
        if any(l is None for l in legs):
            continue
        states = [l["status"] for l in legs]
        if "L" in states:
            sl["status"] = "L"
        elif all(st == "W" for st in states):
            sl["status"] = "W"
        elif "P" not in states:       # nessuna persa, ma almeno una annullata
            sl["status"] = "V"

    # 4) pulizia
    limit = today - timedelta(days=HISTORY_KEEP_DAYS)
    history["signals"] = [e for e in history["signals"] if (parse_day(e["day"]) or today) >= limit]
    history["slips"] = [e for e in history["slips"] if (parse_day(e["day"]) or today) >= limit]
    return history


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    today = datetime.now(timezone.utc).date()
    season = current_season_code(today)

    print("Scarico i risultati della stagione...")
    results_by_league = {}
    for code, name in MAIN_LEAGUES.items():
        print(f"  {name}")
        results_by_league[code] = load_league_results(code, season)
        time.sleep(1.0)

    print("Scarico le partite in programma...")
    fixtures = fetch_csv(FIXTURES_MAIN_URL)

    signals = []
    for code, name in MAIN_LEAGUES.items():
        results = results_by_league[code]
        form = form_stats(results)
        model = league_model(results)
        played = {(m["home"], m["away"], m["day"]) for m in results}
        for row in fixtures:
            if row.get("Div") == code:
                signals.extend(analyze_fixture(row, code, name, form, model, played, today))

    try:
        signals.extend(analyze_extra_leagues(today))
    except Exception as exc:  # noqa: BLE001
        print(f"  Errore campionati extra: {exc}")

    slips = build_slips(signals, today)
    history = update_history(load_previous_history(OUTPUT_PATH), signals, slips, results_by_league, today)

    output = {
        "schema": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_note": (
            "Quote reali gratuite da football-data.co.uk: Premier League, Championship, League One, "
            "League Two, National League, Bundesliga, 2.Bundesliga, Serie A, Serie B, LaLiga, LaLiga 2, "
            "Ligue 1, Ligue 2, Eredivisie, Belgio, Irlanda, Norvegia, Svezia. Non coperti gratuitamente: "
            "Serie C, Eerste Divisie (Olanda 2)."
        ),
        "odds_note": (
            "Le quote sono la media tra piu' bookmaker, aggiornate dalla fonte il venerdi' per il turno "
            "del weekend e il martedi' per gli infrasettimanali. Per Irlanda, Norvegia e Svezia mostriamo "
            "solo il favorito di mercato."
        ),
        "model_note": (
            "Le probabilita' sono stimate con un modello di Poisson sui gol della stagione in corso e "
            "mescolate al 50% con le probabilita' implicite nelle quote (senza margine). Il valore atteso "
            "(EV) e' stima x quota - 1: e' positivo solo se la stima supera il prezzo di mercato. "
            "La puntata consigliata e' un quarto di Kelly con tetto, ed e' zero quando l'EV non e' positivo."
        ),
        "signals": signals,
        "slips": slips,
        "history": history,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    resolved = sum(1 for e in history["signals"] if e["status"] in ("W", "L"))
    print(f"\nSegnali: {len(signals)} · schedine: {len(slips)} · registro: "
          f"{len(history['signals'])} giocate ({resolved} verificate)")


if __name__ == "__main__":
    main()
