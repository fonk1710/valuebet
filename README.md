# SchedinaAnalisi — motore automatico con quote reali (100% gratis)

Motore che ogni giorno scarica le partite in programma, le quote reali di
piu' bookmaker e i risultati recenti, calcola segnali statistici e pubblica
tutto su una dashboard web — gratis, senza chiave API, senza server da
gestire.

## Fonte dati

Tutto viene da **football-data.co.uk**, un archivio pubblico gratuito attivo
dal 2001, che pubblica ogni settimana un CSV con le quote reali (Bet365,
Pinnacle, Betfair Exchange e altri, mediate in una colonna "Avg") delle
partite in programma, oltre allo storico dei risultati della stagione in
corso. Nessuna registrazione, nessuna chiave, nessun limite di richieste
documentato.

## Cosa copre e cosa no

**Copre gratis, con quote reali**: Premier League, Championship, League One,
League Two, National League, Bundesliga, 2. Bundesliga, Serie A, Serie B,
LaLiga, LaLiga 2, Ligue 1, Ligue 2, Eredivisie, Belgio.

**Copre solo il favorito di mercato** (nessuno storico gratuito per i segnali
statistici): Irlanda, Norvegia, Svezia.

**Non copre** (nessuna fonte gratuita trovata): Serie C, Eerste Divisie
(Olanda, seconda serie).

## Setup (una volta sola, ~10 minuti — niente chiavi API)

### 1. Crea il repository GitHub
Se non hai un account GitHub, creane uno gratuito su github.com. Poi crea un
nuovo repository **pubblico** (necessario per usare Actions e Pages gratis) e
carica tutti i file di questa cartella (`engine/`, `docs/`,
`.github/workflows/`, `requirements.txt`).

### 2. Attiva GitHub Pages
Nel repository: **Settings → Pages**.
- Source: `Deploy from a branch`
- Branch: `main`, cartella `/docs`
- Salva

Dopo un paio di minuti la dashboard sarà visibile su:
`https://<tuo-username>.github.io/<nome-repository>/`

### 3. Primo avvio manuale
Nel repository: tab **Actions → Analisi campionato → Run workflow**. Impiega
qualche minuto (scarica lo storico di 15 campionati). Al termine,
`docs/data.json` viene aggiornato in automatico e la dashboard mostra i
segnali con le quote reali.

Da quel momento il motore gira da solo ogni giorno alle 7:00 UTC — non devi
più fare nulla.

## Modificare la frequenza

Nel file `.github/workflows/analyze.yml`, la riga `cron: "0 7 * * *"` decide
l'orario (formato UTC). Ad esempio `"0 6,18 * * *"` esegue l'analisi due
volte al giorno.

## Come leggere i segnali

- **1X2**: la forma recente (media punti nelle ultime partite) è nettamente
  a favore di una squadra rispetto a quanto suggerirebbe la quota di mercato.
- **Over/Under**: percentuale di partite Over/Under 2.5 nelle ultime gare di
  entrambe le squadre, con la quota reale del mercato.
- **GG/NG**: percentuale di partite con gol di entrambe le squadre. Qui la
  quota non è disponibile nella fonte gratuita, solo il segnale statistico.

Nessuno di questi è una garanzia di vincita: sono letture statistiche sulla
forma recente, non un modello predittivo validato.

## Estendere il motore in futuro

- Serie C e Eerste Divisie → non risultano fonti gratuite affidabili;
  servirebbe un provider a pagamento (API-Football, The Odds API)
- Più bookmaker o quote più fresche (aggiornate più spesso della settimana)
  → provider a pagamento con API in tempo reale

## Novità (v2)

- **Schedine consigliate** (Singola, X2–X5) costruite ogni giorno con le partite dei prossimi 3 giorni, con quota totale, probabilità stimata, valore atteso (EV) e puntata consigliata (Kelly al 25%, con tetti 2% singola / 1% multipla).
- **Modello Poisson** (forza attacco/difesa casa/trasferta, ridotta verso la media del campionato) mescolato 50/50 con le probabilità di mercato senza margine. *Value bet* = EV ≥ +3% e probabilità ≥ 20%.
- **Verifica**: ogni segnale e schedina pubblicati vengono registrati in `docs/data.json` (`history`) e controllati a partita finita. Il tab mostra % uscite e ROI per tipo, mercato, confidenza e campionato. Servono almeno 30-50 giocate prima che i numeri significhino qualcosa.
- **Storico**: budget, statistiche per tipo/campionato/mercato, grafico del profitto, export CSV, backup/ripristino.
- Corretto un bug del vecchio motore che scartava i risultati con 0 gol.
- Test: `python3 tests/test_engine.py`.

- **Sistemi** integrali (2 su 3, 3 su 4, 3 su 5, 4 su 5, 4 su 6) con probabilità di incasso, ritorno minimo/massimo ed EV calcolati esattamente (eventi supposti indipendenti). Non sono ancora registrati nella scheda Verifica.

Non ancora implementati: Serie C e Eerste Divisie (non disponibili gratis).

## Novità (v3)

- **Backtest** (`engine/backtest.py`): simulazione walk-forward sulle ultime 4 stagioni (il modello conosce solo le partite precedenti), puntata fissa su ogni value bet. Mostra ROI con intervallo di confidenza al 95%, qualità delle probabilità (Brier) contro il mercato, ROI al variare di peso del mercato e soglia di valore, e dettaglio per mercato/stagione/campionato. Si ricalcola una volta al mese dentro `analyze.py` (qualche minuto) e il risultato sta in `data.json` sotto `backtest`. Le quote sono medie di mercato della fonte, non quelle reali di un bookmaker.
- **Movimento quote**: per ogni segnale registrato si tiene l'ultima quota vista prima della partita (`q_last`) e la scheda Verifica mostra quanto spesso la quota scende dopo il segnale.
- **Handicap asiatico** (linee intere e con mezzo punto; i quarti sono saltati): segnali value con rimborso gestito. Non entra in schedine e sistemi.
- **Limiti di gioco** nello Storico: spesa massima e perdita massima mensili, avvisi e conferma prima di registrare giocate oltre il limite (salvati nel browser).

## Novità (v4): ridurre il costo del gioco

Il backtest (v3) ha mostrato che i value bet del modello perdono circa il 9% di quanto puntato, come il margine dei bookmaker. Il modello non batte il mercato.
- **Tab Costi** (`engine/costs.py`): perdita media per 100 € su 24.000 partite reali, per scelta (favorita/sfavorita/pareggio), fascia di quota, singola o multipla e margine di ciascun bookmaker. Si aggiorna insieme al backtest (mensile).
- **Home "Giocate a basso costo"**: singole sulla favorita con quota media 1,30-2,00, con la soglia sotto cui non giocare sul proprio bookmaker. Schedine, sistemi e segnali del modello sono in una sezione "curiosità" con avviso.
- Nessuna giocata ha valore atteso positivo: il sito riduce la perdita attesa, non la elimina.
