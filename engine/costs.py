"""
Quanto costa giocare: margine dei bookmaker e perdita media per tipo di scommessa.

Non usa nessun modello: guarda solo le quote storiche e i risultati veri. Risponde a
"se punto senza alcuna idea, quanto perdo in media, e cosa fa scendere questa perdita?"
(bookmaker/quota migliore, tipo di scommessa, favorita o sfavorita, singola o multipla).
"""

import analyze as A

# prefissi delle colonne dei singoli bookmaker nei file di football-data
BOOKS = {"B365": "Bet365", "BW": "Bwin", "IW": "Interwetten", "PS": "Pinnacle", "WH": "William Hill",
         "VC": "VC Bet", "1XB": "1xBet", "BF": "Betfair", "LB": "Ladbrokes", "SB": "Sportingbet",
         "GB": "Gamebookers", "SJ": "Stan James", "BS": "Blue Square"}
SOURCES = {"Avg": "Media dei bookmaker", "Max": "Quota migliore tra tutti", **BOOKS}
BUCKETS = (("sotto 1.50", 0, 1.5), ("1.50-2.00", 1.5, 2.0), ("2.00-3.00", 2.0, 3.0),
           ("3.00-5.00", 3.0, 5.0), ("oltre 5.00", 5.0, 1e9))


class Acc:
    def __init__(self):
        self.n = 0
        self.profit = 0.0

    def add(self, p):
        self.n += 1
        self.profit += p

    def roi(self):
        return round(self.profit / self.n, 4) if self.n else None


class MeanAcc:
    def __init__(self):
        self.n = 0
        self.total = 0.0

    def add(self, x):
        self.n += 1
        self.total += x

    def mean(self):
        return round(self.total / self.n, 4) if self.n else None


def odds3(row, prefix):
    q = [A.num(row.get(prefix + s)) for s in ("H", "D", "A")]
    return q if all(q) else None


def odds_ou(row, prefix):
    over = "P>2.5" if prefix == "PS" else f"{prefix}>2.5"
    under = "P<2.5" if prefix == "PS" else f"{prefix}<2.5"
    q = [A.num(row.get(over)), A.num(row.get(under))]
    return q if all(q) else None


def odds_ah(row, prefix):
    h, a = {"PS": ("PAHH", "PAHA")}.get(prefix, (prefix + "AHH", prefix + "AHA"))
    q = [A.num(row.get(h)), A.num(row.get(a))]
    return q if all(q) else None


class Costs:
    def __init__(self):
        self.matches = 0
        self.m1x2 = {s: MeanAcc() for s in SOURCES}
        self.mou = {s: MeanAcc() for s in SOURCES}
        self.mah = {s: MeanAcc() for s in SOURCES}
        self.rules = {r: {s: Acc() for s in SOURCES} for r in ("favorita", "sfavorita", "pareggio")}
        self.buckets = {b[0]: {"Avg": Acc(), "Max": Acc()} for b in BUCKETS}

    def add_row(self, row):
        fh, fa = A.goals(row.get("FTHG")), A.goals(row.get("FTAG"))
        if fh is None or fa is None:
            return
        self.matches += 1
        real = "H" if fh > fa else "A" if fa > fh else "D"
        avg = odds3(row, "Avg")
        pick = {}
        if avg:
            pick["favorita"] = min(("H", "A"), key=lambda k: avg["HDA".index(k)])
            pick["sfavorita"] = max(("H", "A"), key=lambda k: avg["HDA".index(k)])
            pick["pareggio"] = "D"

        for s in SOURCES:
            q = odds3(row, s)
            if q:
                self.m1x2[s].add(sum(1 / x for x in q) - 1)
                for rule, key in pick.items():
                    odd = q["HDA".index(key)]
                    self.rules[rule][s].add(odd - 1 if real == key else -1)
            q = odds_ou(row, s)
            if q:
                self.mou[s].add(1 / q[0] + 1 / q[1] - 1)
            q = odds_ah(row, s)
            if q:
                self.mah[s].add(1 / q[0] + 1 / q[1] - 1)

        mx = odds3(row, "Max")
        if avg:
            for i, key in enumerate("HDA"):
                for name, lo, hi in BUCKETS:
                    if lo <= avg[i] < hi:
                        self.buckets[name]["Avg"].add(avg[i] - 1 if real == key else -1)
                        if mx:
                            self.buckets[name]["Max"].add(mx[i] - 1 if real == key else -1)

    def report(self):
        def margins(d):
            out = {s: {"n": a.n, "margin": a.mean()} for s, a in d.items() if a.n >= 200}
            return dict(sorted(out.items(), key=lambda kv: kv[1]["margin"]))

        rules = {}
        for rule, d in self.rules.items():
            rules[rule] = {s: {"n": a.n, "roi": a.roi()} for s, a in d.items() if a.n >= 200}
        multi = {}
        for s in ("Avg", "Max"):
            r = rules["favorita"].get(s, {}).get("roi")
            if r is not None:
                multi[s] = {str(k): round((1 + r) ** k - 1, 4) for k in range(1, 6)}
        return {
            "matches": self.matches,
            "labels": {s: SOURCES[s] for s in SOURCES},
            "margin_1x2": margins(self.m1x2),
            "margin_ou": margins(self.mou),
            "margin_ah": margins(self.mah),
            "rules": rules,
            "buckets": {b: {s: {"n": a.n, "roi": a.roi()} for s, a in d.items()} for b, d in self.buckets.items()},
            "multiples": multi,
        }
