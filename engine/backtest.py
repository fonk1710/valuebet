"""
Backtest walk-forward sulle stagioni passate.

Per ogni partita usa SOLO le partite giocate prima (niente "futuro"), costruisce la stessa
stima del motore (Poisson + mercato) e simula una puntata fissa da 1 unita' su ogni value bet.
Serve a rispondere: "se avessi seguito questi segnali negli anni scorsi, come sarebbe andata?"

Attenzione: e' una simulazione. Le quote sono le medie di mercato della fonte, non quelle che
avresti trovato davvero in agenzia, e il risultato passato non garantisce quello futuro.
"""

import math
import time
from collections import defaultdict

import analyze as A
import costs as C

GRID_WEIGHTS = (0.0, 0.25, 0.5, 0.75)      # peso del mercato nella stima
GRID_THRESHOLDS = (0.0, 0.03, 0.06, 0.10)  # valore atteso minimo
MAIN_W = A.MARKET_WEIGHT
MAIN_T = A.VALUE_MIN_EV
REFIT_EVERY = 15                            # ricalcola il modello ogni 15 partite
WARMUP_MATCHES = 60                         # niente puntate finche' non ci sono abbastanza partite
N_SEASONS = 4
BET_KEYS = ("H", "A", "O25", "U25", "AHH", "AHA")


def previous_season_codes(today, n=N_SEASONS):
    cur = A.current_season_code(today)
    start = int(cur[:2])
    return [f"{(start - i) % 100:02d}{(start - i + 1) % 100:02d}" for i in range(1, n + 1)]


def parse_rows(rows):
    """Righe del CSV di una stagione -> partite ordinate per data, con quote."""
    out = []
    for r in rows:
        home, away = r.get("HomeTeam"), r.get("AwayTeam")
        fh, fa = A.goals(r.get("FTHG")), A.goals(r.get("FTAG"))
        day = A.parse_day(r.get("Date"))
        if not home or not away or fh is None or fa is None or day is None:
            continue
        out.append({
            "home": home, "away": away, "day": day, "fh": fh, "fa": fa,
            "odds": {
                "H": A.num(r.get("AvgH")), "D": A.num(r.get("AvgD")), "A": A.num(r.get("AvgA")),
                "O25": A.num(r.get("Avg>2.5")), "U25": A.num(r.get("Avg<2.5")),
                "AHH": A.num(r.get("AvgAHH")), "AHA": A.num(r.get("AvgAHA")),
            },
            "ah_line": A.ah_line_value(r.get("AHh")),
        })
    out.sort(key=lambda m: m["day"])
    return out


def estimates(model, m, weight):
    """key -> (p, p_lose, quota, ev) per le selezioni scommettibili della partita."""
    odds = m["odds"]
    lam_h, lam_a = A.expected_goals(model, m["home"], m["away"])
    pm = A.outcome_probs(lam_h, lam_a)
    est = {}
    if odds["H"] and odds["D"] and odds["A"]:
        pk = dict(zip(("H", "D", "A"), A.devig([odds["H"], odds["D"], odds["A"]])))
        for k in ("H", "A"):
            p = (1 - weight) * pm[k] + weight * pk[k]
            est[k] = (p, 1 - p, odds[k], p * odds[k] - 1)
    if odds["O25"] and odds["U25"]:
        pk = dict(zip(("O25", "U25"), A.devig([odds["O25"], odds["U25"]])))
        for k in ("O25", "U25"):
            p = (1 - weight) * pm[k] + weight * pk[k]
            est[k] = (p, 1 - p, odds[k], p * odds[k] - 1)
    if m["ah_line"] is not None and odds["AHH"] and odds["AHA"]:
        dist = A.goal_diff_probs(lam_h, lam_a)
        mk = dict(zip(A.AH_KEYS, A.devig([odds["AHH"], odds["AHA"]])))
        for k in A.AH_KEYS:
            pw, pp, _ = A.ah_win_push_lose(dist, m["ah_line"], k)
            pw_b = (1 - weight) * pw + weight * mk[k] * (1 - pp)
            pl_b = max(0.0, 1 - pp - pw_b)
            est[k] = (pw_b, pl_b, odds[k], pw_b * (odds[k] - 1) - pl_b)
    return est


def bet_profit(key, m, quota):
    """Profitto di 1 unita': +quota-1, -1 oppure 0 (rimborso)."""
    fh, fa = m["fh"], m["fa"]
    if key in A.AH_KEYS:
        res = A.ah_outcome(key, m["ah_line"], fh, fa)
        return quota - 1 if res == "W" else 0.0 if res == "V" else -1.0
    return quota - 1 if A.outcome_for_key(key, fh, fa) else -1.0


class Acc:
    __slots__ = ("n", "wins", "profit", "sq")

    def __init__(self):
        self.n = self.wins = 0
        self.profit = self.sq = 0.0

    def add(self, profit):
        self.n += 1
        self.wins += 1 if profit > 0 else 0
        self.profit += profit
        self.sq += profit * profit

    def summary(self):
        if not self.n:
            return {"n": 0, "wins": 0, "roi": None, "ci": None}
        roi = self.profit / self.n
        var = max(0.0, self.sq / self.n - roi * roi)
        ci = 1.96 * math.sqrt(var / self.n)
        return {"n": self.n, "wins": self.wins, "roi": round(roi, 4), "ci": round(ci, 4)}


class Backtest:
    def __init__(self):
        self.grid = {(w, t): Acc() for w in GRID_WEIGHTS for t in GRID_THRESHOLDS}
        self.by_key = defaultdict(Acc)
        self.by_lg = defaultdict(Acc)
        self.by_season = defaultdict(Acc)
        self.by_bucket = defaultdict(Acc)
        self.brier = {w: [0.0, 0] for w in GRID_WEIGHTS}
        self.brier_market = [0.0, 0]
        self.matches = 0
        self.margins = []

    def run_season(self, matches, lg, season):
        """Walk-forward su una stagione di una lega."""
        played = []
        model = None
        for i, m in enumerate(matches):
            if i >= WARMUP_MATCHES and (model is None or i % REFIT_EVERY == 0):
                model = A.league_model(played)
            if model is not None:
                self._evaluate(model, m, lg, season)
            played.append(m)

    def _evaluate(self, model, m, lg, season):
        self.matches += 1
        odds = m["odds"]
        # qualita' delle probabilita' 1X2 (Brier): mercato puro contro modello+mercato
        if odds["H"] and odds["D"] and odds["A"]:
            lam_h, lam_a = A.expected_goals(model, m["home"], m["away"])
            pm = A.outcome_probs(lam_h, lam_a)
            pk = dict(zip(("H", "D", "A"), A.devig([odds["H"], odds["D"], odds["A"]])))
            real = {"H": m["fh"] > m["fa"], "D": m["fh"] == m["fa"], "A": m["fh"] < m["fa"]}
            self.margins.append(1 / odds["H"] + 1 / odds["D"] + 1 / odds["A"] - 1)
            self.brier_market[0] += sum((pk[k] - real[k]) ** 2 for k in real)
            self.brier_market[1] += 1
            for w in GRID_WEIGHTS:
                self.brier[w][0] += sum(((1 - w) * pm[k] + w * pk[k] - real[k]) ** 2 for k in real)
                self.brier[w][1] += 1

        for w in GRID_WEIGHTS:
            est = estimates(model, m, w)
            for key in BET_KEYS:
                if key not in est:
                    continue
                p, _pl, quota, ev = est[key]
                if p < A.VALUE_MIN_P:
                    continue
                for t in GRID_THRESHOLDS:
                    if ev < t:
                        continue
                    profit = bet_profit(key, m, quota)
                    self.grid[(w, t)].add(profit)
                    if w == MAIN_W and t == MAIN_T:
                        self.by_key[key].add(profit)
                        self.by_lg[lg].add(profit)
                        self.by_season[season].add(profit)
            if w == MAIN_W:
                for key, (p, _pl, quota, ev) in est.items():
                    if ev >= MAIN_T and p >= A.VALUE_MIN_P:
                        b = "3-6%" if ev < 0.06 else "6-10%" if ev < 0.10 else "10-20%" if ev < 0.20 else ">20%"
                        self.by_bucket[b].add(bet_profit(key, m, quota))

    def report(self, seasons, generated_at):
        def dump(d, order=None):
            keys = order or sorted(d, key=lambda k: -d[k].n)
            return {k: d[k].summary() for k in keys if k in d}

        return {
            "generated_at": generated_at,
            "seasons": seasons,
            "matches": self.matches,
            "main": {"weight": MAIN_W, "threshold": MAIN_T, "overall": self.grid[(MAIN_W, MAIN_T)].summary()},
            "grid": [
                dict(weight=w, threshold=t, **self.grid[(w, t)].summary())
                for w in GRID_WEIGHTS for t in GRID_THRESHOLDS
            ],
            "by_key": dump(self.by_key),
            "by_lg": dump(self.by_lg),
            "by_season": dump(self.by_season, sorted(self.by_season)),
            "by_bucket": dump(self.by_bucket, [b for b in ("3-6%", "6-10%", "10-20%", ">20%") if b in self.by_bucket]),
            "brier": {
                "market": round(self.brier_market[0] / self.brier_market[1], 4) if self.brier_market[1] else None,
                **{f"w{int(w * 100)}": round(v[0] / v[1], 4) for w, v in self.brier.items() if v[1]},
            },
            "avg_margin": round(sum(self.margins) / len(self.margins), 4) if self.margins else None,
            "note": ("Simulazione walk-forward: per ogni partita il modello conosce solo le partite precedenti. "
                     "Puntata fissa 1 unita' su ogni value bet, quote medie di mercato della fonte (non "
                     "quelle reali di un bookmaker). ROI con intervallo al 95%: se l'intervallo include lo "
                     "zero, non si puo' dire che il metodo guadagni."),
        }


def run(today, codes, generated_at, fetch=None, sleep=1.0):
    fetch = fetch or A.fetch_csv
    seasons = previous_season_codes(today)
    bt = Backtest()
    cost = C.Costs()
    used = []
    for season in seasons:
        got = 0
        for code in codes:
            try:
                rows = fetch(f"{A.HIST_BASE}/{season}/{code}.csv")
            except Exception as exc:  # noqa: BLE001
                print(f"  backtest: {season}/{code} non disponibile ({exc})")
                continue
            for r in rows:
                cost.add_row(r)
            matches = parse_rows(rows)
            if len(matches) > WARMUP_MATCHES + 30:
                bt.run_season(matches, A.MAIN_LEAGUES.get(code, code), f"20{season[:2]}/{season[2:]}")
                got += 1
            time.sleep(sleep)
        if got:
            used.append(f"20{season[:2]}/{season[2:]}")
    report = bt.report(used, generated_at)
    report["costs"] = cost.report()
    return report
