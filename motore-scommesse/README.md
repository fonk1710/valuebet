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
