"""
Motore di analisi calcistica - piano gratuito, con quote reali.

Fonte dati: football-data.co.uk (gratuito, nessuna chiave richiesta).
- fixtures.csv / new_league_fixtures.csv: partite in programma con quote reali
  di piu' bookmaker (aggiornate il venerdi' per il turno del weekend, il
  martedi' per gli infrasettimanali).
- mmz4281/<stagione>/<codice>.csv: storico risultati della stagione corrente,
  usato per calcolare le statistiche di forma delle squadre.

Nessuna chiave API necessaria. Nessun costo.
"""

import csv
import io
import os
import json
import time
from datetime import datetime, timezone

import requests

FIXTURES_MAIN_URL = "https://www.football-data.co.uk/fixtures.csv"
FIXTURES_EXTRA_URL = "https://www.football-data.co.uk/new_league_fixtures.csv"
HIST_BASE = "https://www.football-data.co.uk/mmz4281"

# Le 15 divisioni "main leagues" coperte gratis, con storico completo per le statistiche.
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

# Campionati "extra": quote reali disponibili, ma nessuno storico gratuito
# per calcolare segnali statistici -> mostriamo solo il favorito di mercato.
EXTRA_LEAGUES = {
    ("Ireland", "Premier Division"): "Irlanda - Premier Division",
    ("Norway", "Eliteserien"): "Norvegia - Eliteserien",
    ("Sweden", "Allsvenskan"): "Svezia - Allsvenskan",
}

N_RECENT_MATCHES = 8
OUTPUT_PATH = "docs/data.json"


def current_season_code():
    """Es. agosto 2026 -> '2627' (stagione 2026-27)."""
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{str(start_year)[2:]}{str(start_year + 1)[2:]}"


SEASON_CODE = current_season_code()


def fetch_csv(url):
    resp = requests.get(url, timeout=30, headers={"User-Agent": "SchedinaAnalisi/1.0"})
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def num(value):
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def implied_prob(odds):
    return 1 / odds if odds else None


def load_team_stats(league_code):
    """Statistiche di forma per ogni squadra, dai risultati della stagione corrente."""
    url = f"{HIST_BASE}/{SEASON_CODE}/{league_code}.csv"
    try:
        rows = fetch_csv(url)
    except Exception as exc:
        print(f"  storico non disponibile per {league_code}: {exc}")
        return {}

    matches_by_team = {}
    for row in rows:
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        fthg, ftag = num(row.get("FTHG")), num(row.get("FTAG"))
        if not home or not away or fthg is None or ftag is None:
            continue
        matches_by_team.setdefault(home, []).append((fthg, ftag))
        matches_by_team.setdefault(away, []).append((ftag, fthg))

    stats = {}
    for team, matches in matches_by_team.items():
        recent = matches[-N_RECENT_MATCHES:]
        if not recent:
            continue
        n = len(recent)
        gf = [m[0] for m in recent]
        ga = [m[1] for m in recent]
        over25 = sum(1 for a, b in zip(gf, ga) if a + b > 2.5)
        btts = sum(1 for a, b in zip(gf, ga) if a > 0 and b > 0)
        points = sum(3 if a > b else 1 if a == b else 0 for a, b in zip(gf, ga))
        stats[team] = {
            "n": n,
            "over25_rate": round(over25 / n, 2),
            "btts_rate": round(btts / n, 2),
            "ppg": round(points / n, 2),
        }
    return stats


def analyze_main_leagues():
    signals = []
    fixtures = fetch_csv(FIXTURES_MAIN_URL)

    stats_cache = {}
    for code, name in MAIN_LEAGUES.items():
        print(f"Statistiche storiche: {name}...")
        stats_cache[code] = load_team_stats(code)
        time.sleep(1.5)  # cortesia verso il server, nessun limite noto ma meglio non esagerare

    for row in fixtures:
        code = row.get("Div")
        if code not in MAIN_LEAGUES:
            continue
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        if not home or not away:
            continue

        league_name = MAIN_LEAGUES[code]
        label = f"{home} - {away}"
        match_date = row.get("Date", "")

        avg_h, avg_d, avg_a = num(row.get("AvgH")), num(row.get("AvgD")), num(row.get("AvgA"))
        avg_over, avg_under = num(row.get("Avg>2.5")), num(row.get("Avg<2.5"))

        home_stats = stats_cache[code].get(home)
        away_stats = stats_cache[code].get(away)

        if home_stats and away_stats:
            ppg_gap = home_stats["ppg"] - away_stats["ppg"]

            if ppg_gap >= 1.2 and avg_h:
                signals.append({
                    "partita": label, "campionato": league_name, "data": match_date,
                    "mercato": "1X2", "pronostico": f"1 (vittoria {home})", "quota": avg_h,
                    "confidenza": "Alta" if ppg_gap >= 1.8 else "Media",
                    "nota": f"Forma recente nettamente a favore del {home} (media punti {home_stats['ppg']} vs {away_stats['ppg']} nelle ultime {home_stats['n']}/{away_stats['n']} partite). Quota di mercato {avg_h}.",
                })
            elif ppg_gap <= -1.2 and avg_a:
                signals.append({
                    "partita": label, "campionato": league_name, "data": match_date,
                    "mercato": "1X2", "pronostico": f"2 (vittoria {away})", "quota": avg_a,
                    "confidenza": "Alta" if ppg_gap <= -1.8 else "Media",
                    "nota": f"Forma recente nettamente a favore del {away} (media punti {away_stats['ppg']} vs {home_stats['ppg']} nelle ultime {home_stats['n']}/{away_stats['n']} partite). Quota di mercato {avg_a}.",
                })

            combined_over = (home_stats["over25_rate"] + away_stats["over25_rate"]) / 2
            if combined_over >= 0.65 and avg_over:
                signals.append({
                    "partita": label, "campionato": league_name, "data": match_date,
                    "mercato": "Over/Under", "pronostico": "Over 2.5", "quota": avg_over,
                    "confidenza": "Alta" if combined_over >= 0.75 else "Media",
                    "nota": f"Over 2.5 nel {round(combined_over * 100)}% delle ultime partite di entrambe. Quota di mercato {avg_over}.",
                })
            elif combined_over <= 0.30 and avg_under:
                signals.append({
                    "partita": label, "campionato": league_name, "data": match_date,
                    "mercato": "Over/Under", "pronostico": "Under 2.5", "quota": avg_under,
                    "confidenza": "Alta" if combined_over <= 0.20 else "Media",
                    "nota": f"Solo il {round(combined_over * 100)}% delle ultime partite di entrambe sopra i 2.5 gol. Quota di mercato {avg_under}.",
                })

            combined_btts = (home_stats["btts_rate"] + away_stats["btts_rate"]) / 2
            if combined_btts >= 0.7:
                signals.append({
                    "partita": label, "campionato": league_name, "data": match_date,
                    "mercato": "GG/NG", "pronostico": "Gol (GG)", "quota": None,
                    "confidenza": "Alta" if combined_btts >= 0.8 else "Media",
                    "nota": f"Entrambe a segno nel {round(combined_btts * 100)}% delle ultime partite (quota GG/NG non disponibile in questa fonte).",
                })

    return signals


def analyze_extra_leagues():
    signals = []
    fixtures = fetch_csv(FIXTURES_EXTRA_URL)

    for row in fixtures:
        key = (row.get("Country"), row.get("League"))
        if key not in EXTRA_LEAGUES:
            continue
        home, away = row.get("Home"), row.get("Away")
        if not home or not away:
            continue

        avg_h, avg_d, avg_a = num(row.get("AvgH")), num(row.get("AvgD")), num(row.get("AvgA"))
        if not (avg_h and avg_d and avg_a):
            continue

        probs = {
            f"1 (vittoria {home})": implied_prob(avg_h),
            "X (pareggio)": implied_prob(avg_d),
            f"2 (vittoria {away})": implied_prob(avg_a),
        }
        odds_map = {f"1 (vittoria {home})": avg_h, "X (pareggio)": avg_d, f"2 (vittoria {away})": avg_a}
        favorite = max(probs, key=probs.get)

        signals.append({
            "partita": f"{home} - {away}", "campionato": EXTRA_LEAGUES[key], "data": row.get("Date", ""),
            "mercato": "1X2", "pronostico": f"Favorita di mercato: {favorite}", "quota": odds_map[favorite],
            "confidenza": "Bassa",
            "nota": "Solo quadro di mercato: per questo campionato non e' disponibile uno storico gratuito per i segnali statistici.",
        })

    return signals


def main():
    all_signals = []

    print("Analizzo i campionati principali (con storico e quote)...")
    all_signals.extend(analyze_main_leagues())

    print("Analizzo Irlanda, Norvegia, Svezia (solo quote)...")
    try:
        all_signals.extend(analyze_extra_leagues())
    except Exception as exc:
        print(f"  Errore campionati extra: {exc}")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_note": (
            "Quote reali gratuite da football-data.co.uk: Premier League, Championship, League One, "
            "League Two, National League, Bundesliga, 2.Bundesliga, Serie A, Serie B, LaLiga, LaLiga 2, "
            "Ligue 1, Ligue 2, Eredivisie, Belgio, Irlanda, Norvegia, Svezia. Non coperti gratuitamente: "
            "Serie C, Eerste Divisie (Olanda 2)."
        ),
        "odds_note": (
            "Le quote 'Avg' sono la media tra piu' bookmaker, raccolte il venerdi' per il turno del "
            "weekend e il martedi' per gli infrasettimanali. Per Irlanda, Norvegia e Svezia mostriamo "
            "solo il favorito di mercato: nessuno storico gratuito disponibile per calcolare segnali."
        ),
        "signals": all_signals,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSalvati {len(all_signals)} segnali in {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
