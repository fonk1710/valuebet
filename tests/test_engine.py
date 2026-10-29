"""
Test del motore con dati sintetici (nessuna rete).
Esegui dalla radice del repository:  python tests/test_engine.py
"""

import json
import math
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import analyze as A  # noqa: E402

TODAY = datetime.now(timezone.utc).date()
SEASON = A.current_season_code(TODAY)


def fmt(d):
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------- utilita'
def test_parsers():
    assert A.goals("0") == 0 and A.goals("3") == 3 and A.goals("") is None and A.goals(None) is None
    assert A.num("0") is None and A.num("2.5") == 2.5 and A.num("") is None
    assert A.parse_day("29/10/2026").isoformat() == "2026-10-29"
    assert A.parse_day("29/10/26").isoformat() == "2026-10-29"
    assert A.parse_day("2026-10-29").isoformat() == "2026-10-29"
    assert A.parse_day("boh") is None and A.parse_day("") is None
    assert A.current_season_code(datetime(2026, 10, 29).date()) == "2627"
    assert A.current_season_code(datetime(2027, 3, 1).date()) == "2627"
    assert A.current_season_code(datetime(2026, 7, 1).date()) == "2627"
    assert A.current_season_code(datetime(2026, 6, 30).date()) == "2526"
    p = A.devig([2.0, 3.5, 4.0])
    assert abs(sum(p) - 1) < 1e-9
    print("ok parser")


def test_poisson():
    pr = A.outcome_probs(1.4, 1.4)
    assert abs(pr["H"] + pr["D"] + pr["A"] - 1) < 1e-9
    assert abs(pr["H"] - pr["A"]) < 1e-9, "squadre uguali: casa e trasferta simmetriche"
    assert abs(pr["O25"] + pr["U25"] - 1) < 1e-9
    forte = A.outcome_probs(2.6, 0.7)
    assert forte["H"] > 0.7 and forte["O25"] > 0.5
    assert 0 < forte["BTTS"] < 1
    # lambda alte -> piu' Over
    assert A.outcome_probs(2.0, 2.0)["O25"] > A.outcome_probs(0.8, 0.8)["O25"]
    print("ok poisson")


def test_kelly():
    assert A.kelly_fraction(0.5, 1.9, 0.02) == 0.0           # EV negativo: nessuna puntata
    assert A.kelly_fraction(0.6, 2.0, 0.02) == 0.02          # limitato dal tetto
    k = A.kelly_fraction(0.52, 2.0, 0.10)                    # f=0.04 -> quarto di Kelly = 0.01
    assert abs(k - 0.01) < 1e-9
    assert A.kelly_fraction(0.5, None, 0.02) == 0.0
    print("ok kelly")


def test_outcomes():
    assert A.outcome_for_key("H", 2, 1) and not A.outcome_for_key("H", 1, 1)
    assert A.outcome_for_key("A", 0, 1) and A.outcome_for_key("D", 0, 0)
    assert A.outcome_for_key("O25", 2, 1) and not A.outcome_for_key("O25", 1, 1)
    assert A.outcome_for_key("U25", 1, 1) and not A.outcome_for_key("U25", 2, 1)
    assert A.outcome_for_key("BTTS", 1, 1) and not A.outcome_for_key("BTTS", 2, 0)
    print("ok esiti")


# ------------------------------------------------- campionato sintetico
TEAMS = {  # nome: (attacco, difesa) - difesa piu' alta = subisce di piu'
    "Forti": (2.0, 0.6), "Medi A": (1.2, 1.0), "Medi B": (1.0, 1.0), "Medi C": (1.0, 1.1),
    "Deboli A": (0.7, 1.4), "Deboli B": (0.6, 1.5), "Gol Facili": (1.8, 1.7), "Muro": (0.6, 0.6),
}


def make_results(seed=7, days_back=70):
    rnd = random.Random(seed)
    names = list(TEAMS)
    rows, d = [], TODAY - timedelta(days=days_back)
    pairs = [(h, a) for h in names for a in names if h != a]
    rnd.shuffle(pairs)
    for i, (h, a) in enumerate(pairs):
        day = d + timedelta(days=i * (days_back - 2) // len(pairs))
        lam_h = 1.5 * TEAMS[h][0] * TEAMS[a][1]
        lam_a = 1.1 * TEAMS[a][0] * TEAMS[h][1]
        fh = sum(rnd.random() < lam_h / 12 for _ in range(12))   # ~ Poisson
        fa = sum(rnd.random() < lam_a / 12 for _ in range(12))
        rows.append({"Date": fmt(day), "HomeTeam": h, "AwayTeam": a, "FTHG": str(fh), "FTAG": str(fa)})
    return rows


def raw_rows_to_results(rows):
    return [{"home": r["HomeTeam"], "away": r["AwayTeam"], "day": A.parse_day(r["Date"]),
             "fh": int(r["FTHG"]), "fa": int(r["FTAG"])} for r in rows]


def test_model_and_form():
    results = raw_rows_to_results(make_results())
    assert len(results) >= A.MIN_LEAGUE_MATCHES
    model = A.league_model(results)
    assert model and model["mu_h"] > 0 and model["mu_a"] > 0
    assert A.league_model(results[:10]) is None, "pochi dati: il modello non parte"
    t = model["teams"]
    assert t["Forti"]["att_h"] > t["Deboli B"]["att_h"], "i forti attaccano di piu'"
    assert t["Muro"]["def_h"] < t["Gol Facili"]["def_h"], "il muro subisce meno"
    lh, la = A.expected_goals(model, "Forti", "Deboli B")
    lh2, la2 = A.expected_goals(model, "Deboli B", "Forti")
    assert lh > la and la2 > lh2
    assert A.expected_goals(model, "Sconosciuta", "Forti")[0] > 0     # squadra mai vista: neutra
    form = A.form_stats(results)
    assert form["Forti"]["n"] == A.N_RECENT_MATCHES and form["Forti"]["ppg"] > form["Deboli B"]["ppg"]
    # regressione: gli zeri contano (prima le partite con 0 gol venivano scartate)
    zero = [{"home": "A", "away": "B", "day": TODAY, "fh": 0, "fa": 0}] * 5
    assert A.form_stats(zero)["A"]["n"] == 5 and A.form_stats(zero)["A"]["ppg"] == 1.0
    print("ok modello e forma")


def fixture(home, away, day, h=2.0, d=3.4, a=3.8, over=1.9, under=1.9, div="E0"):
    return {"Div": div, "Date": fmt(day), "HomeTeam": home, "AwayTeam": away,
            "AvgH": str(h), "AvgD": str(d), "AvgA": str(a),
            "Avg>2.5": str(over), "Avg<2.5": str(under)}


def test_analyze_fixture():
    results = raw_rows_to_results(make_results())
    form, model = A.form_stats(results), A.league_model(results)
    played = {(m["home"], m["away"], m["day"]) for m in results}
    tomorrow = TODAY + timedelta(days=1)

    # partita passata e partita gia' giocata: niente
    assert A.analyze_fixture(fixture("Forti", "Deboli B", TODAY - timedelta(days=1)), "E0", "L", form, model, played, TODAY) == []
    some = results[0]
    assert A.analyze_fixture(fixture(some["home"], some["away"], some["day"]), "E0", "L", form, model, played, TODAY) == []
    assert A.analyze_fixture({"Div": "E0", "Date": "", "HomeTeam": "A", "AwayTeam": "B"}, "E0", "L", form, model, played, TODAY) == []

    # quota troppo alta per la squadra forte: deve uscire come value
    sigs = A.analyze_fixture(fixture("Forti", "Deboli B", tomorrow, h=1.9, d=4.0, a=6.5), "E0", "Lega", form, model, played, TODAY)
    keys = {s["key"]: s for s in sigs}
    assert "H" in keys, f"atteso un segnale 1 per la forte, trovati {list(keys)}"
    s = keys["H"]
    for f in ("id", "partita", "campionato", "data", "day", "mercato", "pronostico", "quota", "p", "ev",
              "kelly", "value", "tipo", "confidenza", "nota", "key", "lg", "home", "away"):
        assert f in s, f
    assert s["mercato"] == "1X2" and s["pronostico"].startswith("1 (")
    assert s["day"] == tomorrow.isoformat() and s["id"].endswith("|Forti|Deboli B|H")
    assert math.isclose(s["ev"], s["p"] * s["quota"] - 1, abs_tol=0.002)
    assert 0 <= s["kelly"] <= A.KELLY_CAP_SINGLE
    assert "D" not in keys, "il pareggio non si propone mai"

    # quota bassa e allineata: nessun value (p mescolata col mercato)
    sigs2 = A.analyze_fixture(fixture("Medi A", "Medi B", tomorrow, h=2.4, d=3.2, a=3.0, over=2.0, under=1.85),
                              "E0", "Lega", form, model, played, TODAY)
    assert all(not x["value"] or x["ev"] >= A.VALUE_MIN_EV for x in sigs2)

    # senza modello (campionato nuovo) ma con forma: solo segnali statistici, senza p
    sigs3 = A.analyze_fixture(fixture("Forti", "Deboli B", tomorrow), "E0", "Lega", form, None, played, TODAY)
    assert all(x["p"] is None and x["kelly"] == 0.0 and x["tipo"] == "statistico" for x in sigs3)
    print("ok analyze_fixture:", sorted(keys))


def test_slips():
    def sig(i, p, quota, ev=None, conf="Media", day=None, home=None):
        ev = p * quota - 1 if ev is None else ev
        return {"id": f"s{i}", "partita": f"H{i} - A{i}", "home": home or f"H{i}", "away": f"A{i}",
                "campionato": "L", "data": "x", "day": (day or TODAY).isoformat(), "mercato": "1X2",
                "pronostico": "1", "quota": quota, "p": p, "ev": ev, "confidenza": conf, "key": "H"}
    pool = [sig(1, 0.62, 1.70), sig(2, 0.55, 2.0), sig(3, 0.50, 2.1), sig(4, 0.48, 2.2), sig(5, 0.47, 2.15, conf="Alta"),
            sig(6, 0.30, 4.0),                                   # p troppo bassa
            sig(7, 0.60, 1.5, conf="Bassa"),                     # confidenza bassa
            sig(8, 0.60, 1.4, ev=-0.20),                         # EV troppo negativo
            sig(9, 0.60, 2.0, day=TODAY + timedelta(days=9)),    # fuori finestra
            sig(10, 0.99, 2.0, home="H1")]                       # stessa partita della #1 con score migliore
    pool[-1]["away"] = "A1"
    slips = A.build_slips(pool, TODAY)
    assert [s["n"] for s in slips] == [1, 2, 3, 4, 5], [s["n"] for s in slips]
    assert slips[0]["tipo"] == "Singola" and slips[1]["tipo"] == "X2" and slips[4]["tipo"] == "X5"
    ids = [l["id"] for l in slips[4]["legs"]]
    assert len(set(ids)) == 5 and "s6" not in ids and "s7" not in ids and "s8" not in ids and "s9" not in ids
    matches = {(l["partita"]) for l in slips[4]["legs"]}
    assert len(matches) == 5, "una sola selezione per partita"
    x3 = slips[2]
    q = math.prod(l["quota"] for l in x3["legs"])
    p = math.prod(l["p"] for l in x3["legs"])
    assert math.isclose(x3["quota"], round(q, 2)) and math.isclose(x3["ev"], round(p * q - 1, 3), abs_tol=0.002)
    assert x3["kelly"] <= A.KELLY_CAP_MULTI
    # senza abbastanza segnali: niente schedine inventate
    assert A.build_slips(pool[:2], TODAY)[-1]["n"] == 2
    assert A.build_slips([], TODAY) == []
    print("ok schedine")

    # sistemi k su n
    picked = A.pick_legs(pool, TODAY)
    systems = A.build_systems(picked)
    assert [x["id"] for x in systems] == ["2su3", "3su4", "3su5", "4su5"], [x["id"] for x in systems]   # solo 5 selezioni valide
    s23 = systems[0]
    legs = picked[:3]
    qs = [l["quota"] for l in legs]; ps = [l["p"] for l in legs]
    assert s23["combinazioni"] == 3
    # prob. rientro = almeno 2 su 3
    exp = (ps[0]*ps[1]*ps[2] + ps[0]*ps[1]*(1-ps[2]) + ps[0]*(1-ps[1])*ps[2] + (1-ps[0])*ps[1]*ps[2])
    assert math.isclose(s23["p_rientro"], round(exp, 3), abs_tol=0.001)
    assert math.isclose(s23["ritorno_max"], round((qs[0]*qs[1] + qs[0]*qs[2] + qs[1]*qs[2]) / 3, 2), abs_tol=0.01)
    # EV esatto con 3 su 3 combinazioni indipendenti: E[ritorno] = somma(prod p * prod q) / costo
    ev = sum(ps[a]*ps[b]*qs[a]*qs[b] for a, b in [(0,1),(0,2),(1,2)]) / 3 - 1
    assert math.isclose(s23["ev"], round(ev, 3), abs_tol=0.002)
    assert s23["kelly"] == (A.SYSTEM_STAKE_CAP if s23["ev"] > 0 else 0.0)
    assert A.build_systems(picked[:2]) == []
    print("ok sistemi")


def test_history():
    yesterday, two = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    old = TODAY - timedelta(days=A.PENDING_VOID_AFTER_DAYS + 3)

    def entry(i, day, key, home="A", away="B"):
        return {"id": f"{day.isoformat()}|{home}|{away}|{key}", "day": day.isoformat(), "lg": "E0",
                "campionato": "L", "home": home, "away": away, "key": key, "mercato": "m", "pronostico": "p",
                "quota": 2.0, "p": 0.5, "tipo": "value", "conf": "Media", "status": "P", "score": None}
    hist = {"signals": [entry(1, yesterday, "H", "A", "B"),    # vinta 2-0
                        entry(2, yesterday, "O25", "C", "D"),  # persa 1-0
                        entry(3, old, "H", "E", "F"),          # mai trovata -> annullata
                        entry(4, TODAY + timedelta(days=1), "H", "G", "H"),   # futura: resta in attesa
                        entry(5, two, "BTTS", "I", "L")],      # vinta 1-1
            "slips": [
                {"id": "x2a", "day": yesterday.isoformat(), "tipo": "X2", "quota": 4, "p": .25,
                 "legs": [entry(1, yesterday, "H", "A", "B")["id"], entry(5, two, "BTTS", "I", "L")["id"]], "status": "P"},
                {"id": "x2b", "day": yesterday.isoformat(), "tipo": "X2", "quota": 4, "p": .25,
                 "legs": [entry(1, yesterday, "H", "A", "B")["id"], entry(2, yesterday, "O25", "C", "D")["id"]], "status": "P"},
                {"id": "x2c", "day": yesterday.isoformat(), "tipo": "X2", "quota": 4, "p": .25,
                 "legs": [entry(1, yesterday, "H", "A", "B")["id"], entry(4, TODAY + timedelta(days=1), "H", "G", "H")["id"]],
                 "status": "P"}]}
    results = {"E0": [
        {"home": "A", "away": "B", "day": yesterday, "fh": 2, "fa": 0},
        {"home": "C", "away": "D", "day": yesterday, "fh": 1, "fa": 0},
        {"home": "I", "away": "L", "day": two, "fh": 1, "fa": 1}]}
    new_sig = [{"id": f"{TODAY.isoformat()}|Z|Y|H", "key": "H", "lg": "E0", "campionato": "L", "home": "Z", "away": "Y",
                "day": TODAY.isoformat(), "mercato": "1X2", "pronostico": "1", "quota": 2.0, "p": .5,
                "tipo": "value", "confidenza": "Media"},
               {"id": "extra|x", "key": None, "lg": None, "home": "Q", "away": "W", "day": None}]   # extra: ignorata
    new_slip = [{"id": f"{TODAY.isoformat()}|X1", "day": TODAY.isoformat(), "tipo": "Singola", "quota": 2.0, "p": .5,
                 "legs": [{"id": new_sig[0]["id"]}]}]
    h = A.update_history(hist, new_sig, new_slip, results, TODAY)
    st = {e["id"].split("|")[0] + e["key"]: e["status"] for e in h["signals"]}
    by = {(e["home"], e["key"]): e for e in h["signals"]}
    assert by[("A", "H")]["status"] == "W" and by[("A", "H")]["score"] == "2-0"
    assert by[("C", "O25")]["status"] == "L"
    assert by[("E", "H")]["status"] == "V"
    assert by[("G", "H")]["status"] == "P"
    assert by[("I", "BTTS")]["status"] == "W"
    assert by[("Z", "H")]["status"] == "P" and len(h["signals"]) == 6, "segnale nuovo registrato, extra no"
    sl = {s["id"]: s["status"] for s in h["slips"]}
    assert sl["x2a"] == "W" and sl["x2b"] == "L" and sl["x2c"] == "P"
    assert sl[f"{TODAY.isoformat()}|X1"] == "P"
    # idempotenza: un secondo giro non duplica nulla
    n_sig, n_sl = len(h["signals"]), len(h["slips"])
    h = A.update_history(h, new_sig, new_slip, results, TODAY)
    assert len(h["signals"]) == n_sig and len(h["slips"]) == n_sl
    print("ok registro")


# ------------------------------------------------- giro completo senza rete
def test_full_run():
    rows = make_results()
    results_rows = {"E0": rows}
    tomorrow, after = TODAY + timedelta(days=1), TODAY + timedelta(days=2)
    fx = [fixture("Forti", "Deboli B", tomorrow, h=1.9, d=4.0, a=6.5),
          fixture("Medi A", "Gol Facili", tomorrow, h=2.6, d=3.4, a=2.7, over=1.6, under=2.3),
          fixture("Muro", "Medi C", after, h=2.9, d=3.0, a=2.6, over=2.4, under=1.55),
          fixture("Medi B", "Deboli A", after, h=1.9, d=3.5, a=4.2),
          fixture("Forti", "Muro", TODAY - timedelta(days=1)),                  # passata: scartata
          fixture(rows[0]["HomeTeam"], rows[0]["AwayTeam"], A.parse_day(rows[0]["Date"]))]   # gia' giocata
    extra = [{"Country": "Norway", "League": "Eliteserien", "Date": fmt(tomorrow), "Home": "Bodo", "Away": "Molde",
              "AvgH": "3.1", "AvgD": "3.6", "AvgA": "2.2"},
             {"Country": "Norway", "League": "Eliteserien", "Date": fmt(TODAY - timedelta(days=3)), "Home": "X", "Away": "Y",
              "AvgH": "3.1", "AvgD": "3.6", "AvgA": "2.2"},
             {"Country": "Brazil", "League": "Serie A", "Date": fmt(tomorrow), "Home": "P", "Away": "Q",
              "AvgH": "3.1", "AvgD": "3.6", "AvgA": "2.2"}]

    def fake_fetch(url):
        if url == A.FIXTURES_MAIN_URL:
            return fx
        if url == A.FIXTURES_EXTRA_URL:
            return extra
        code = url.rsplit("/", 1)[1].replace(".csv", "")
        assert f"/{SEASON}/" in url
        return results_rows.get(code, [])

    tmp = tempfile.mkdtemp()
    A.OUTPUT_PATH = os.path.join(tmp, "docs", "data.json")
    A.fetch_csv = fake_fetch
    A.time.sleep = lambda *_: None

    A.main()
    d = json.load(open(A.OUTPUT_PATH, encoding="utf-8"))
    assert d["schema"] == 2 and d["signals"] and "history" in d and "model_note" in d
    ids = [s["id"] for s in d["signals"]]
    assert len(ids) == len(set(ids))
    assert all(s["day"] >= TODAY.isoformat() for s in d["signals"] if s["day"]), "nessuna partita passata"
    assert not any("Medi A - Medi A" in s["partita"] for s in d["signals"])
    extra_sig = [s for s in d["signals"] if s["lg"] is None]
    assert [s["partita"] for s in extra_sig] == ["Bodo - Molde"], "extra: solo il campionato richiesto e non passato"
    assert extra_sig[0]["pronostico"].startswith("Favorita di mercato: 2"), extra_sig[0]["pronostico"]
    logged = d["history"]["signals"]
    assert len(logged) == sum(1 for s in d["signals"] if s["key"])
    assert d["slips"] and d["slips"][0]["n"] == 1
    json.dumps(d)  # serializzabile
    print(f"ok giro completo: {len(d['signals'])} segnali, {len(d['slips'])} schedine, {len(logged)} registrati")

    # il giorno dopo: arrivano i risultati di una partita segnalata -> viene verificata
    target = next(e for e in logged if e["key"] == "H")
    r_day = A.parse_day(target["day"])
    results_rows["E0"] = rows + [{"Date": fmt(r_day), "HomeTeam": target["home"], "AwayTeam": target["away"],
                                  "FTHG": "3", "FTAG": "1"}]
    real_today = A.datetime
    class FakeDT(real_today):
        @classmethod
        def now(cls, tz=None):
            return real_today(r_day.year, r_day.month, r_day.day, 20, 0, tzinfo=timezone.utc) + timedelta(days=1)
    A.datetime = FakeDT
    try:
        A.main()
    finally:
        A.datetime = real_today
    d2 = json.load(open(A.OUTPUT_PATH, encoding="utf-8"))
    t2 = next(e for e in d2["history"]["signals"] if e["id"] == target["id"])
    assert t2["status"] == "W" and t2["score"] == "3-1", t2
    assert len(d2["history"]["signals"]) >= len(logged), "il registro non perde giocate"
    print("ok verifica del giorno dopo")


if __name__ == "__main__":
    for t in (test_parsers, test_poisson, test_kelly, test_outcomes, test_model_and_form,
              test_analyze_fixture, test_slips, test_history, test_full_run):
        t()
    print("\nTUTTI I TEST OK")
