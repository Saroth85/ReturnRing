# Runbook AMD: la sessione MI300X dalla creazione alla distruzione del droplet

Scritto il 15 ottobre 2026 come compagno dettagliato di `docs/release-guide.md` (sezione 3, "La
sessione sulla MI300X") per la parte AMD. In italiano perché è un documento operativo per il
team, come `docs/spec.md`; comandi, nomi di file, variabili e righe attese restano in inglese,
esattamente come compaiono nelle fonti. Le fonti, nell'ordine in cui vincolano questo testo:
`infra/spikes/README.md` (il runbook inglese: "Steps" 1-15, "The GPU session" 1-19, "The demo
export" 1-8), il `Makefile`, `.env.example`, i file di `infra/` (compose, Caddyfile, Dockerfile,
script degli spike), `docs/benchmarks.md`, `docs/graphsage.md`, `docs/spike-results.md`,
`docs/spec.md` (sezioni 11, 13, 14, 16), `docs/adr/ADR-17-deployment-and-demo-package.md`,
`docs/release-guide.md`, `scripts/demo_export.py`, `scripts/demo_walk.py`, `bench/`,
`scripts/release/*.sh`. Nessun comando, variabile, porta, immagine, tempo o costo qui è inventato:
ogni riga rimanda a una di queste fonti; un valore che nessuno conosce ancora è un segnaposto tra
parentesi angolari (`<droplet-ip>`) e sta nell'elenco della sezione 17.

Convenzioni:

- **Tempi**: ogni durata è una stima salvo dove è scritto "misurato" con la fonte. Il runbook
  stesso avverte che velocità di download, estrazione delle immagini e tempo di compilazione di
  vLLM su ROCm non sono mai stati misurati. Ogni tempo misurato nella sessione va annotato.
- **Dove si lancia**: "portatile" è la macchina del team con Docker, GNU make e Python 3.12;
  "droplet" è la MI300X; "server demo" è il piccolo droplet CPU della demo pubblica (sezione 2
  della guida al rilascio), che non è AMD e non è coperto qui se non dove riceve il pacchetto.
- **Comandi**: ogni passo sul droplet è un target `make` lanciato dalla radice del repository;
  `make -n <target>` stampa il comando `docker compose` esatto senza eseguirlo. I comandi di
  questo documento sono stati verificati con `make -n` sul commit `432e096` del 15 ottobre.
- **Numeri di passo**: "Steps N" rimanda ai passi 1-15 della sezione "Steps" di
  `infra/spikes/README.md`; "GPU session N" ai passi 1-19 di "The GPU session"; "Demo export N" ai
  passi 1-8 di "The demo export". I due documenti vanno tenuti allineati.
- **Lo script della sessione**: `scripts/release/06-gpu-session.sh` lancia gli stessi target di
  questo documento per fasi (`spikes`, `bench`, `export`, `live`, `down`) con un log, il tempo e
  un segnaposto per target, così da riprendere da dove si era fermato (guida al rilascio,
  3.1-bis). I suoi checkpoint seguono l'ordine di questo documento; i comandi, le righe attese e
  i rimedi restano quelli scritti qui.

## 0. Scopo, risultato atteso e regole fisse

### 0.1 Che cosa produce la sessione

| Prodotto | Dove finisce | Fonte del passo |
| --- | --- | --- |
| Risultati degli spike SPK-G1..G7 (JSON e log) | `artifacts/spikes/` sul droplet, poi sul portatile; `docs/spike-results.md` compilato | Steps 1-15 |
| La decisione D-12 (Gemma 4 31B o 26B-A4B) | `docs/spike-results.md`, sezione "Decisions to carry into the spec"; `.env` | Steps 10 |
| Benchmark B1-B4 sulla MI300X | `bench/results/<run-id>.json`, `latest.json`, `latest-gpu.json`; `artifacts/bench/<run-id>/` | GPU session 5-14 |
| GraphSAGE US-202 su CPU e GPU del droplet | `artifacts/eval/gnn/us-202-asos-cpu.json`, `us-202-asos-gpu.json`; `artifacts/gnn/models/` | GPU session 9 |
| Embedding A4 dei clienti | `data/features/customer_embeddings.parquet`, `data/ood/features/customer_embeddings.parquet` | GPU session 10 |
| Le bozze di Gemma per ogni reso aperto | `serving/llm_cache/` | Demo export 4 |
| Il pacchetto della demo cached | `demo-cache/`, `demo-cache.tar.gz` (con `MANIFEST.json`) | Demo export 5 |
| Lo stack live con HTTPS per il video e il pitch | `https://<PUBLIC_HOST>` | Demo export 8 |
| Lo snapshot del droplet per sabato e domenica | pannello di controllo | Steps 14 |

Sul portatile, con i file copiati indietro, Claude scrive `docs/results/benchmarks-mi300x.md`,
la riga A4 di `docs/results/us-204-risk.md`, `docs/results/us-202-graphsage.md` e aggiorna gli
stati della spec e i testi di consegna (guida al rilascio, sezioni 3.3 e 6).

### 0.2 Le tre regole che valgono per tutta la sessione

1. **Nessun segreto nel repo né sul disco del droplet oltre a `.env`.** `.env` non si committa
   mai (gitleaks gira in CI). Sul droplet non va nessun token GitHub e nessun token Hugging Face:
   il disco finisce nello snapshot. Per spingere dal droplet si usa `ssh -A` con la chiave
   dell'agente del portatile (runbook, "Before the session", punto 4; guida al rilascio). I soli
   segreti sul droplet stanno in `.env`: `VLLM_API_KEY`, `POSTGRES_PASSWORD` (anche dentro
   `DATABASE_URL`) e `ADMIN_PASSWORD_HASH`; `.env` resta a `chmod 600` e lo snapshot che lo
   contiene è privato.
2. **`data/truth/` non lascia mai il portatile** (regola 12). Sul droplet si copiano solo
   `data/raw/asos_*.parquet`, `data/store/`, `data/features/`, `data/ood/` e `serving/`
   (GPU session 2; Demo export 2). Il container `trainer-graphsage` non monta `data/truth`
   per costruzione; `trainer-gbm` e `trainer-gnn` montano `data/` in sola lettura, quindi
   `data/truth` non deve esserci.
3. **Lo snapshot `serving/` si costruisce una volta sola sul portatile e si copia.** La cache
   delle bozze è indicizzata dall'`llm_case`, quindi lo snapshot letto da `make batch-generate`
   deve essere quello che la demo serve: mai ricostruirlo da una parte sola ("The demo export",
   introduzione).

### 0.3 Costi e fatturazione (spec, sezione 11; runbook)

- Prezzo: 1,99 USD per ora di MI300X. Crediti: 100 USD, circa 50 ore.
- La fatturazione corre **dalla creazione alla distruzione** del droplet, acceso o spento, anche
  mentre lo snapshot viene scritto. Spegnere non basta: solo "destroy" ferma il contatore.
- Lo snapshot costa 0,06 USD per GB al mese: con 170-220 GB attesi, circa 10-13 USD al mese
  (runbook, Steps 14). Che gli snapshot funzionino per i droplet GPU di AMD Developer Cloud, e
  quanto ci mettano, non è confermato.
- Il piano della spec usa circa 29 ore più 6 di riserva, con circa 15 ore di margine; oltre i
  crediti ogni ora costa 1,99 USD (R-04). Secondo la guida al rilascio nessuna sessione MI300X è
  ancora stata eseguita: i crediti dovrebbero essere intatti, da verificare nel pannello.
- Il dettaglio per blocco è nella sezione 14.

## 1. Prima della sessione, sul portatile (senza costo)

Tutto ciò che segue si fa il giorno prima, a droplet non ancora creato.

### 1.1 Il repository e i test

```bash
git clone https://github.com/Saroth85/ReturnRing.git && cd ReturnRing
git checkout <branch>        # main, o il branch che Claude indica
make lint test               # GPU session 1: il branch deve essere verde
```

`make test` dura circa 15 minuti (5.874 test, guida al rilascio 1.3); in CI è già verde sullo
stesso commit, quindi sul portatile serve solo se il Python non è 3.12.

### 1.2 I dati: `data/` e `serving/`

Serve il percorso del giudice già eseguito (guida al rilascio 1.1, script
`scripts/release/02-laptop-docker-check.sh`):

```bash
cp .env.example .env
make demo-data               # oppure make pipeline con il Python dell'host
```

`make demo-data` scrive `data/`, `artifacts/` e `serving/` (seed 42) come utente del portatile,
non root; i suoi comandi hanno preso 9 min 39 s su 4 vCPU condivise il 14 ottobre, più la build
dell'immagine (Makefile, commento di `demo-data`). Alla fine esiste `serving/manifest.json`.

Poi il dataset ASOS per B1 e B2 (US-101):

```bash
make asos                    # scarica da OSF, verifica gli sha256, converte, profila
make asos-verify             # solo il controllo degli hash: "All 6 ASOS files match the pinned sha256 (ADR-10)."
```

`make asos` scarica i sei pickle di ASOS GraphReturns (CC BY 4.0) in `data/raw/asos/`, li
controlla contro gli sha256 e le dimensioni fissate in `pipeline/ingest/asos_download.py`
(742.171.568 byte in tutto: `event_table_training.p` 32.859.910, `event_table_testing.p`
35.049.502, `product_nodes_training.p` 143.204.587, `product_nodes_testing.p` 143.221.671,
`customer_nodes_training.p` 188.037.655, `customer_nodes_testing.p` 199.798.243) e scrive
`data/raw/asos_events.parquet`, `asos_customers.parquet`, `asos_variants.parquet` e
`asos_profile.txt`. Un file i cui byte non corrispondono non viene mai installato
(`IntegrityError`). La guida al rilascio stima circa 770 MB in `data/raw/`; il tempo del download
non è nelle fonti. Stampa alla fine l'attribuzione, `report:`, `calibration:` e
`peak resident memory:`.

### 1.3 La foto per SPK-G5

Una foto di un capo reso: JPEG o PNG, sotto 2 MB, nessuna persona, nessun dato personale sulle
etichette (runbook, "Before the session", punto 2). Nome consigliato dal runbook: `garment.jpg`
(il target `spike-g5` la cerca in `artifacts/spikes/garment.jpg`; un altro nome si passa con
`IMAGE=artifacts/spikes/<file>`).

### 1.4 La chiave SSH e l'agente

- La chiave pubblica del portatile va caricata nel pannello di AMD Developer Cloud prima di
  creare il droplet (Steps 1).
- La chiave GitHub del portatile deve stare nell'agente SSH, perché dal droplet si spinge con
  `ssh -A` (runbook, punto 4; guida al rilascio 3.3). Nessun token GitHub viene copiato.
- Il repository è pubblico, quindi `git clone` via HTTPS non richiede l'agente; `-A` serve solo
  per il push dei risultati.

### 1.5 Il firewall cloud

Un DigitalOcean Cloud Firewall che permette in ingresso 22, 80 e 443 (spec 11; runbook "Before
the session", punto 3: per i soli spike basta 22, ma `make gpu-up` pubblica 80 e 443). Le porte
pubblicate da Docker aggirano ufw, quindi ufw da solo non basta.

### 1.6 I valori da preparare per `.env`

| Valore | Come si ottiene | Quando |
| --- | --- | --- |
| `VLLM_API_KEY` | sul droplet: `openssl rand -hex 24` (Steps 2) | dopo il clone |
| `POSTGRES_PASSWORD` e `DATABASE_URL` | sul droplet: `openssl rand -hex 16`, da scrivere in entrambe le righe (come fa `scripts/release/04-demo-server.sh`, passo 3) | dopo il clone |
| `ADMIN_PASSWORD_HASH` | sul portatile, con Docker: `make admin-password` chiede due volte la password e stampa l'hash bcrypt a costo 10 (`$2a$10$...`); in `.env` lo scrive la riga Python della sezione 3.3, **tra apici singoli** (mai `sed`, mai un editor: l'hash contiene `$`) | il giorno prima |
| `PUBLIC_HOST` | `<ip-con-trattini>.sslip.io` dall'IP del droplet, per esempio `203-0-113-10.sslip.io` per 203.0.113.10 (`.env.example`) | dopo la creazione |

La password di admin la sceglie chi fa la sessione; non va mandata a Claude, né l'hash (guida al
rilascio, sezione 2).

### 1.7 Le decisioni

- **D-12** (Gemma 4 31B o 26B-A4B) si decide durante SPK-G1 con i token al secondo (sezione 8).
  `.env.example` punta al 31B.
- **D-07** (formato del pitch) e **D-13** (da solo o in team) non toccano la sessione (guida al
  rilascio 3.1).

### 1.8 I file che verranno copiati e le loro dimensioni

Dimensioni misurate su questo checkout il 15 ottobre 2026 (`ls -l`, `du -sh`) dopo `make pipeline`
con il Python dell'host e `make asos`; `make demo-data` (Docker) produce gli stessi file (stesso
seme, stessa catena) ma non è stato eseguito qui, dove non c'è un daemon Docker. I valori tra
parentesi sono quelli del runbook.

| Da copiare | Dimensione | Verso | Quando |
| --- | --- | --- | --- |
| `garment.jpg` | < 2 MB | `ReturnRing/artifacts/spikes/` | Steps 2 |
| `data/raw/asos_customers.parquet`, `asos_events.parquet`, `asos_variants.parquet` | 20,1 + 23,4 + 25,8 MB, circa 69 MB (runbook: circa 70 MB) | `ReturnRing/data/raw/` | prima di GPU session 8 |
| `data/store/`, `data/features/`, `data/ood/` | 18 + 9,9 + 27 MB, circa 55 MB (runbook: circa 55 MB) | `ReturnRing/data/` | prima di GPU session 8 |
| `serving/` | 30 MB | `ReturnRing/serving/` | prima di Demo export 4 |

Mai copiati: `data/truth/` (252 KB qui, ma è la ground truth), `data/raw/asos/` (i pickle, 708 MB:
i job leggono solo i parquet), `artifacts/` del portatile, `.env` del portatile (il droplet parte
da `.env.example`).

### 1.9 Checklist della vigilia

- [ ] `make lint test` verde sul branch della sessione
- [ ] `serving/manifest.json` esiste; `data/store`, `data/features`, `data/ood` esistono
- [ ] `make asos-verify` stampa che i sei file corrispondono
- [ ] `garment.jpg` pronta (< 2 MB, nessuna persona)
- [ ] chiave SSH nel pannello; chiave GitHub nell'agente
- [ ] firewall cloud con 22, 80, 443 in ingresso
- [ ] hash di admin da `make admin-password` salvato in un posto sicuro (non nel repo)
- [ ] crediti visibili nel pannello di AMD Developer Cloud (checklist spec 17)
- [ ] un foglio dove scrivere: ora di creazione, ora di distruzione, tempi misurati

## 2. Creare il droplet (Steps 1, circa 5 min)

### 2.1 Attivare la piattaforma (una volta sola, prima della vigilia)

Le fonti del progetto non descrivono il pannello; questi passi vengono dal tutorial lablab
"Host Your First LLM on AMD GPU" (`https://lablab.ai/ai-tutorials/amd-developer-cloud-host-llm-vllm`)
e dalla pagina dell'evento ACT III, letti il 15 ottobre 2026. I nomi dei menù possono cambiare.

1. **AMD AI Developer Program (ADP)**: account gratuito su
   `https://www.amd.com/en/developer/ai-dev-program.html` (dalla pagina ACT III: "Sign up with
   AMD"; l'iscrizione all'ADP è richiesta per essere approvati all'hackathon). I nuovi iscritti
   ricevono 100 USD di crediti ("Free $100 AMD credits for new members", circa 50 ore di
   MI300X), applicati da soli all'account del Developer Cloud. Secondo la FAQ AMD riportata dai
   risultati di ricerca i crediti omaggio scadono 30 giorni dal deposito: verificarlo nella mail
   di conferma.
2. **AMD Developer Cloud**: accesso da
   `https://www.amd.com/en/developer/resources/cloud-access/amd-developer-cloud.html` con lo
   stesso account AMD; il pannello è quello di DigitalOcean.
3. **Billing**: nel menù a sinistra, "Billing", aggiungere una carta. Senza carta il pulsante
   "Create GPU Droplet" resta grigio; con i crediti la carta non viene addebitata finché non si
   superano (tutorial lablab). Nota: la FAQ della pagina ACT III dice "no billing or credit card
   info needed" e che "developers don't need the $100 credit to participate", perché l'evento è
   ospitato su `notebooks.amd.com/tokenfactory` (un'API di inferenza). Questo progetto ha bisogno
   del droplet (XGBoost e DGL su GPU, vLLM locale con Gemma 4, benchmark B1-B4, regola 2), quindi
   i crediti del Developer Cloud e la carta servono comunque: se il pannello non li mostra,
   chiedere su Discord come vengono erogati ai partecipanti di ACT III.
4. **Chiave SSH**: `ssh-keygen -t ed25519` sul portatile; il contenuto di `~/.ssh/id_ed25519.pub`
   va incollato nel campo SSH key alla creazione del droplet (i GPU Droplet accettano solo chiavi).
5. **Creare il droplet**: "GPU Droplets" nel menù, poi "Create GPU Droplet"; regione ATL1; piano
   MI300X con 1 GPU a 1,99 USD/h (`gpu-mi300x1-192gb`); immagine "vLLM" fra le Quick Start
   ("Pre-configured LLM inference engine with OpenAI-compatible API"); la chiave SSH; "Create GPU
   Droplet". Il provisioning dura 2-4 minuti: quando lo stato passa da Creating ad Active, copiare
   l'IP pubblico. Il firewall (22, 80, 443) si crea nella sezione Networking del pannello e si
   applica al droplet.
6. **Fermare i costi**: spento, il droplet si paga lo stesso ("Even when the droplet is powered off,
   you are still billed"): si distrugge dal pannello ("Destroy"), dopo lo snapshot se serve
   (GPU Droplets > il droplet > Snapshots > Take Snapshot; lo snapshot ha un piccolo costo di
   archiviazione).


Le etichette esatte dei menù non sono nelle fonti del progetto: il pannello è quello di
DigitalOcean (il runbook parla di "DigitalOcean Cloud Firewall" e di "control panel"). Quello
che le fonti fissano:

| Campo | Valore | Fonte |
| --- | --- | --- |
| Account | AMD Developer Cloud, iscrizione attiva e crediti visibili (100 USD) | spec 11 e 17 |
| Regione | `ATL1` (Atlanta), la regione che il tutorial lablab usa per la MI300X; se il pannello ne propone altre, va bene quella con la MI300X disponibile | tutorial lablab (2.1) |
| Taglia | `gpu-mi300x1-192gb`: 1x MI300X (192 GB), 20 vCPU, 240 GiB di RAM, disco di boot da 720 GB | runbook Steps 1 |
| Immagine | la Quick Start image con vLLM | spec 11; runbook Steps 1 |
| Firewall | il Cloud Firewall con 22, 80, 443 in ingresso (sezione 1.5) | spec 11 |
| Chiave SSH | quella del portatile | runbook Steps 1 |
| Nome | `<nome-droplet>`, a scelta | — |

Appena il pannello mostra il droplet nella lista dei Droplets con il suo indirizzo IP:

1. scrivere l'**ora di creazione** (UTC): la fatturazione parte ora (runbook; guida al rilascio
   3.3 chiede ora di creazione e di distruzione);
2. scrivere l'IP: è `<droplet-ip>`, e da lui deriva `PUBLIC_HOST` (`<ip-con-trattini>.sslip.io`).

Da mandare a Claude: l'ora di creazione, l'IP (serve per `PUBLIC_HOST` nel README) e la taglia e
l'immagine scelte (riga "Droplet image and size" di `docs/spike-results.md`).

### 2.2 Alternativa: una VM Hot Aisle (quando il Developer Cloud non sblocca il limite GPU)

Scelta il 16 ottobre 2026, con la richiesta di limite GPU ad AMD ancora senza risposta. Fonti:
`https://hotaisle.xyz/quick-start`, `https://hotaisle.xyz/pricing`, il blog
`https://hotaisle.xyz/blog/opencode-vllm-hotaisle` (letti il 16 ottobre). Il regolamento ACT III
ammette "other approved AMD powered infrastructure": l'hardware è una MI300X, il pitch dice
"AMD Instinct MI300X" e il pacchetto della demo registra la GPU vera.

| Voce | Hot Aisle | Fonte |
| --- | --- | --- |
| Accesso | terminale di amministrazione nel browser su `https://admin.hotaisle.app`, o `ssh admin.hotaisle.app`; email e codice di verifica | quick start |
| Account | nel terminale: creare il team, aggiungere crediti con la carta, "provision compute in under 60 seconds"; nessuna approvazione documentata | quick start, pricing |
| Prezzo | 2,99 USD per GPU/ora, fatturati **al minuto**; consigliati 100 USD di crediti | pricing |
| VM | "Small": 1x MI300X (192 GB HBM3), 8 o 13 vCPU, 224 GB di RAM, 12 TB NVMe | pricing |
| Immagine | la VM "already comes with a recent ROCm setup, and Docker or Podman is ready to go": versioni di Ubuntu e ROCm da leggere al primo accesso | quick start |
| Utente | nel blog il login è `hotaisle@<ip>`: non root; i nostri script vogliono root, quindi `sudo -i` dopo il login (da verificare) | blog |
| Chiave SSH | il terminale genera una chiave di sessione da scaricare (`id_hotaisle_ed25519` e `.pub`): dalla pagina del saldo, Esc, il proprio nome, Invio; `ssh -i ~/.ssh/id_hotaisle_ed25519 hotaisle@<ip>` | quick start |
| API token | stesso menù del proprio nome (serve solo per l'API `https://admin.hotaisle.app/api/docs/` o il CLI `hotaisle`) | quick start, hotaisle-cli |
| Snapshot | non documentati: la VM o resta accesa (si paga) o si distrugge e si ricrea, riscaricando i 62,6 GB di pesi | — |
| Porte pubbliche | non documentate; non servono: la demo pubblica resta sul server CPU (guida al rilascio, sezione 2) e la camminata e il video passano dal tunnel SSH (11.3) | — |

Che cosa cambia rispetto al droplet, sezione per sezione:

- **Sezione 3 (accesso)**: `ssh -i ~/.ssh/id_hotaisle_ed25519 hotaisle@<ip>` poi `sudo -i`; i tre controlli prima
  di tutto: `docker --version && docker compose version && ls -l /dev/kfd && (rocm-smi --showproductname || amd-smi static | head -30); nproc; free -g | head -2; df -h /`.
  Se `docker compose` manca: `apt-get update && apt-get install -y docker-compose-v2 make git`;
  se c'è solo Podman: `apt-get install -y docker.io docker-compose-v2`. Nessun container Quick
  Start `rocm` da fermare. Da mandare a Claude: l'output dei controlli.
- **Sezione 1.5 (firewall)**: non c'è il Cloud Firewall di DigitalOcean; `ufw` sulla VM non ferma
  le porte pubblicate da Docker. Le porte 80 e 443 del proxy restano eventualmente esposte
  durante `make gpu-up`: tenere `make gpu-up` acceso solo per la registrazione, poi `make gpu-down`.
- **Sezioni 7 e 9 (tempi)**: 8-13 vCPU invece di 20: le baseline CPU di G2, G3 e B2 sono più
  lente; `BENCH_ARGS="--b2-samples 1"` dall'inizio.
- **Sezione 13 (chiusura)**: senza snapshot, a fine giornata si sceglie: VM accesa da venerdì a
  domenica (circa 53 ore, circa 160 USD, stima) oppure distruzione la sera e nuova VM la mattina
  dopo (riscaricare i pesi: circa 20 min, non misurato; totale 16-19 ore, 50-60 USD, stima). La
  seconda, salvo che il download risulti lento. Prima di distruggere: copia dei file della
  sezione 12 e di `demo-cache.tar.gz`; il `.env` si ricrea dal portatile (3.3).
- **Sezione 14 (costi)**: ore x 2,99 USD invece di 1,99; la carta è addebitata subito dai
  crediti prepagati; i crediti AMD non si usano.
- **Lo script `06-gpu-session.sh`**: invariato; va lanciato da root (`sudo -i`), con
  `DROPLET_CREATED=<ora UTC di creazione della VM>`.

Da mandare a Claude alla creazione: IP, ora di creazione, l'output dei tre controlli, la versione
di Ubuntu e di ROCm (`cat /etc/os-release | head -2; cat /opt/rocm/.info/version 2>/dev/null`).

## 3. Primo accesso e configurazione (Steps 2-3, circa 5 min)

### 3.1 SSH e tmux

```bash
ssh root@<droplet-ip>         # senza -A: il repository è pubblico; -A solo per il push della 12.2
tmux new -s spikes            # la sessione sopravvive a una caduta di SSH
```

`-A` per tutta la sessione inoltrerebbe la chiave GitHub del portatile in una shell di root su
una VM cloud per ore; il runbook inglese lo chiede solo se il repository è privato (Steps 2), e
qui serve solo per il push facoltativo della sezione 12.2, in una connessione a parte.

Se SSH cade: `ssh root@<droplet-ip>` e poi `tmux attach -t spikes`. Il download dei pesi
(`infra/spikes/weights.sh`) gira con `setsid` e sopravvive comunque; gli altri comandi vanno
rilanciati dal passo in cui erano.

### 3.2 make, docker compose, il clone

```bash
command -v make >/dev/null && command -v rsync >/dev/null || { apt-get update -q && apt-get install -y --no-install-recommends make rsync; }
docker compose version        # deve stampare una versione 2.x
git clone https://github.com/Saroth85/ReturnRing.git ReturnRing && cd ReturnRing && git checkout <branch>
```

`apt-get update -q` prima dell'install: su un'immagine appena creata le liste dei pacchetti possono
mancare e `apt-get install` risponde `Unable to locate package` (è ciò che fa il passo 1 di
`scripts/release/04-demo-server.sh`). `rsync` serve sul droplet perché la sezione 4 lo usa dal
portatile verso il droplet (il lato remoto deve averlo); senza, `scp -r`.

Se `docker compose` manca (runbook Steps 3): se `dpkg -l docker-ce` elenca i pacchetti di Docker,
`apt-get update -q && apt-get install -y docker-compose-plugin`; altrimenti
`apt-get update -q && apt-get install -y --no-install-recommends docker-compose-v2`.

### 3.3 `.env`

Si parte **da `.env.example`, non dal `.env` del portatile**: un `.env` copiato dal vecchio
esempio ha ancora immagini `:latest` e `AMD_PYPI_INDEX_URL` vuoto (runbook Steps 2). I valori
restano senza spazi e senza virgolette, salvo gli apici singoli dell'hash: il Makefile legge il
file con `sh` (`set -a && . ./.env`).

```bash
cp .env.example .env
sed -i "s/^VLLM_API_KEY=.*/VLLM_API_KEY=$(openssl rand -hex 24)/" .env
pg=$(openssl rand -hex 16)
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$pg/" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://returnring:$pg@postgres:5432/returnring|" .env
sed -i "s/^PUBLIC_HOST=.*/PUBLIC_HOST=<ip-con-trattini>.sslip.io/" .env
chmod 600 .env
mkdir -p artifacts/spikes
```

La riga `ADMIN_PASSWORD_HASH='$2a$10$...'` non si scrive né con `sed` né con un editor: l'hash
contiene `$`, e un `$` mangiato dalla shell o un hash incollato senza apici passa ogni controllo
della sessione (il preflight non lo guarda) e si scopre solo alla prova del pitch come
`401 anche con la password giusta` (sezione 15). Si legge da un prompt e lo scrive Python, tra
apici singoli come in `.env.example`, con lo stesso metodo del passo 3 di
`scripts/release/04-demo-server.sh`:

```bash
read -r -s -p 'ADMIN_PASSWORD_HASH da make admin-password: ' h; printf '\n'
python3 - "$h" <<'EOF'
import re, sys
t = open('.env').read()
t = re.sub(r"(?m)^ADMIN_PASSWORD_HASH=.*$", lambda m: "ADMIN_PASSWORD_HASH='" + sys.argv[1] + "'", t)
open('.env', 'w').write(t)
EOF
unset h
grep -cE "^ADMIN_PASSWORD_HASH='[$]2[aby][$]10[$][./A-Za-z0-9]{53}'$" .env   # deve stampare 1
```

Il `grep` conta le righe con un hash bcrypt completo (`$2a$10$` più 53 caratteri, 60 in tutto)
tra apici singoli: `0` vuol dire hash mangiato o incompleto, da riscrivere. Senza nulla da
incollare, l'hash si può generare sul droplet stesso (Docker c'è, e l'immagine di caddy è la
stessa del servizio `proxy`, che `make gpu-up` scarica comunque), con la password letta da un
prompt, e poi scriverlo con la stessa riga Python e controllarlo con lo stesso `grep`:

```bash
read -r -s -p 'password di admin: ' pw; printf '\n'
h=$(docker run --rm "$(sed -n 's/^CADDY_IMAGE = //p' Makefile)" caddy hash-password --algorithm bcrypt --bcrypt-cost 10 --plaintext "$pw")
unset pw                      # poi la riga python3 qui sopra, unset h, e il grep
```

(`make admin-password` usa `docker run -it` e chiede la password due volte: comodo sul portatile,
ma il suo output va copiato a mano; `--plaintext` è ciò che fa `04-demo-server.sh`.) Lo script
della sessione, fase `live`, rifiuta un hash che non sia completo o non sia tra apici. Le righe
`POSTGRES_PASSWORD` e `DATABASE_URL` sono l'equivalente del passo 3 di
`scripts/release/04-demo-server.sh`; postgres non pubblica porte, ma `change-me` non deve restare.

Controllo (mostra le chiavi, non mandare l'output a Claude: contiene i segreti):

```bash
grep -E '^(VLLM_API_KEY|POSTGRES_PASSWORD|DATABASE_URL|PUBLIC_HOST|ADMIN_PASSWORD_HASH)=' .env
```

Se serve mandare `.env` a Claude, va oscurato come fa `scripts/release/05-collect.sh`:

```bash
sed -E 's/^(POSTGRES_PASSWORD|DATABASE_URL|VLLM_API_KEY|HF_TOKEN|ADMIN_PASSWORD_HASH)=.*/\1=<redacted>/' .env
```

#### Tutte le variabili di `.env.example`

| Variabile | Significato | Valore sul droplet | Da dove |
| --- | --- | --- | --- |
| `POSTGRES_USER` | utente di PostgreSQL | `returnring` (invariato) | `.env.example` |
| `POSTGRES_PASSWORD` | password di PostgreSQL | `openssl rand -hex 16` | generata sul droplet (sezione 3.3) |
| `POSTGRES_DB` | database | `returnring` (invariato) | `.env.example` |
| `DATABASE_URL` | URL letto da api, reset, telemetry-sampler | `postgresql://returnring:<password>@postgres:5432/returnring`, con la stessa password | generata sul droplet |
| `DEVICE` | `cpu` o `gpu` | lasciare `cpu`: `compose.gpu.yml` impone `gpu` ad api, reset e trainer-graphsage; `batch-generate` e `bench` lo fissano a `gpu` nel compose | `.env.example`, `infra/compose.gpu.yml` |
| `LLM_MODE` | `mock` o `vllm` | lasciare `mock`: `compose.gpu.yml` impone `vllm` ad api e reset; `batch-generate` e `bench` lo fissano | idem |
| `DEMO_MODE` | `live` o `cached` | `live` (invariato; `compose.gpu.yml` lo impone) | idem |
| `SEED` | seme | `42` | `.env.example` |
| `HOST_UID`, `HOST_GID` | utente con cui i job `pipeline` e `batch-generate` scrivono | il Makefile li esporta da `id -u` e `id -g` (0 come root), che vincono sul file; lasciare `1000` | `.env.example`, Makefile |
| `VLLM_API_KEY` | chiave che vLLM richiede (`--api-key`) e che api, bench e spike-client inviano | `openssl rand -hex 24` | generata sul droplet (Steps 2) |
| `VLLM_BASE_URL` | dove l'api raggiunge vLLM | `http://vllm:8001/v1` (invariato: rete interna di compose, nessuna porta pubblicata) | `.env.example` |
| `GEMMA_MODEL` | modello servito da vLLM e richiesto da batch e bench | `google/gemma-4-31B-it`; piano B `google/gemma-4-26B-A4B-it` (sezione 8) | `.env.example` |
| `GEMMA_REVISION` | commit Hugging Face dei pesi (`--revision`, `--tokenizer-revision`) | `842da3794eaa0b77d5f08bae87a17459d91ff475` (13 ott 2026); piano B `4d7ae4984b7db7de8f8457170b3f1a419ee76d52`, da cambiare insieme a `GEMMA_MODEL` | `.env.example` |
| `HF_TOKEN` | token Hugging Face | vuoto: i due modelli Gemma 4 non sono gated (D-05); si valorizza solo se il download risponde 401 o 403 (Steps 8) e **non** va nello snapshot | `.env.example`, runbook Steps 8 |
| `VLLM_IMAGE` | immagine vLLM 0.31.0 su ROCm 7.2.3, tag@digest | `vllm/vllm-openai-rocm:v0.31.0@sha256:749f6f3f...` come in `.env.example`; il preflight rifiuta un valore diverso; piano B 0.30.0 nella sezione 7.6 | `.env.example` |
| `DGL_IMAGE` | immagine rocm/dgl 2.4.0, ROCm 7.0.0, PyTorch 2.8.0, Python 3.12 | invariato, tag@digest | `.env.example` |
| `ROCM_BASE_IMAGE` | base ROCm 7.2.3 `-complete` per trainer-gbm, bench e telemetry | invariato, tag@digest; mai `:latest` (ROCm 10.x, non supportato da amd_xgboost) | `.env.example` |
| `AMD_PYPI_INDEX_URL` | indice AMD per `amd_xgboost==3.2.0` | `https://pypi.amd.com/rocm-7.2.3/simple` (invariato; usato solo con `--index-url`) | `.env.example` |
| `DEMO_CLOCK` | `on`: il "now" dell'API è l'istante dello snapshot più il tempo dal reset | `on` (invariato) | `.env.example` |
| `PUBLIC_READ_ONLY` | `true`: l'API rifiuta le scritture (403) | non impostata: default `false` sul droplet (le scritture restano, dietro basic auth sul proxy) | `.env.example` |
| `LLM_CONCURRENCY` | chiamate al modello in volo (reset, batch, API) | non impostata: 32 | `.env.example`, `apps/api/settings.py` |
| `VLLM_TIMEOUT_S` | timeout di una chiamata a vLLM in secondi | non impostata: 20 | idem |
| `PUBLIC_HOST` | nome sslip.io su cui Caddy serve HTTPS | `<ip-con-trattini>.sslip.io`; vuoto in `.env.example` apposta: `make gpu-up` si ferma senza | `.env.example`, `infra/compose.public.yml` |
| `ADMIN_PASSWORD_HASH` | hash bcrypt della password dell'utente `admin` (basic auth su `/api/telemetry` e sulle scritture) | `'$2a$10$...'` tra apici singoli, da `make admin-password` sul portatile | `.env.example`, Makefile |

#### Che cosa sovrascrivono i file di override

- `infra/compose.gpu.yml`: `DEVICE=gpu`, `LLM_MODE=vllm`, `DEMO_MODE=live` per `api` e `reset`
  (il reset aspetta che vLLM sia healthy); `DEVICE=gpu` per `trainer-graphsage`; e **rifiuta di
  partire** (`${VAR:?...}`) se mancano `VLLM_IMAGE`, `GEMMA_REVISION`, `ROCM_BASE_IMAGE`,
  `AMD_PYPI_INDEX_URL` o `DGL_IMAGE`.
- `infra/compose.public.yml` (aggiunto da `make gpu-up`): `SITE_ADDRESS=PUBLIC_HOST`,
  `ADMIN_AUTH=on`, porte 80 e 443; rifiuta di partire senza `PUBLIC_HOST` o
  `ADMIN_PASSWORD_HASH`.
- `infra/docker-compose.yml`, servizio `batch-generate`: `DEVICE=gpu`, `LLM_MODE=vllm`,
  `DEMO_MODE=live` fissi; `infra/compose.bench.yml`, servizio `bench`: `DEVICE=gpu`,
  `LLM_MODE=vllm`, `VLLM_BASE_URL=http://vllm:8001/v1` fissi.
- `infra/compose.spike.yml`: monta `infra/spikes/` in sola lettura nei trainer e aggiunge
  `spike-client` (`python:3.12-slim`, profilo `spike`) con `VLLM_BASE_URL`, `VLLM_API_KEY` e
  `GEMMA_MODEL`.

### 3.4 La foto

Dal portatile, in un secondo terminale:

```bash
scp garment.jpg root@<droplet-ip>:ReturnRing/artifacts/spikes/garment.jpg
```

### 3.5 Il container della Quick Start image

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
```

Se c'è un container in esecuzione, **di qualunque nome** (nelle guide AMD si chiama `rocm`, ma il
nome è loro, non del progetto): scrivere nome, immagine e porte nella riga "Quick Start container
found" di `docs/spike-results.md`, poi

```bash
docker stop <nome> && docker update --restart=no <nome>      # rocm nelle guide AMD
```

Lo script della sessione si ferma prima di `spike-prep` se gira un container che non è del
progetto (i suoi si chiamano `returnring-<servizio>-N`); non lo ferma da solo.

Perché: può tenere memoria GPU (vLLM la misura all'avvio e ci dimensiona la cache KV) e può
pubblicare Jupyter o un vLLM sull'IP pubblico (runbook Steps 3). Il preflight registra in
`host.json` i processi in ascolto sulle porte 80, 443, 8000, 8001, 8080 e 8888.

## 4. Copiare i dati dal portatile (GPU session 2; Demo export 2)

Dal portatile, dalla radice del repository, con `data/` e `serving/` già costruiti (sezione 1).

```bash
ssh root@<droplet-ip> mkdir -p ReturnRing/data/raw
scp data/raw/asos_*.parquet root@<droplet-ip>:ReturnRing/data/raw/
rsync -a data/store data/features data/ood root@<droplet-ip>:ReturnRing/data/
rsync -a --delete serving/ root@<droplet-ip>:ReturnRing/serving/
```

| Che cosa | Dimensione (sezione 1.8) | Letto da | Va copiato prima di |
| --- | --- | --- | --- |
| `data/raw/asos_*.parquet` | circa 69 MB | `bench` (B1), `trainer-graphsage` (B2) | GPU session 8 |
| `data/store/`, `data/features/`, `data/ood/` | circa 55 MB | `trainer-graphsage` (A4: `make embeddings-gpu`) | GPU session 10 |
| `serving/` | 30 MB | `batch-generate`, `reset`, `api` (`make gpu-up` controlla `serving/manifest.json`) | Demo export 3 |

Mai: `data/truth/`, `data/raw/asos/` (i pickle), `.env`, `artifacts/`.

`rsync` deve esistere anche sul droplet (sezione 3.2); senza, per `data/` basta
`scp -r data/store data/features data/ood root@<droplet-ip>:ReturnRing/data/`, mentre per
`serving/` il `--delete` conta (vedi sotto) e conviene installare `rsync`.

Due avvertenze:

- `rsync -a --delete serving/` cancella anche `serving/llm_cache/` sul droplet: va eseguito
  **prima** di `make batch-generate`, mai dopo (Demo export 2), altrimenti le bozze di Gemma
  spariscono.
- Il clone sul droplet è di root e i job (`batch-generate`, `trainer-*`) girano come root
  (`HOST_UID` 0): i file copiati come root vanno bene.

Da mandare a Claude: niente, salvo un errore di `scp` o `rsync`.

## 5. Preflight (Steps 3, circa 2 min, senza GPU)

```bash
make spike-preflight          # python3 infra/spikes/preflight.py --env-file .env
```

Gira con il `python3` dell'host (solo libreria standard), legge `.env` e `.env.example`,
interroga l'host e scrive `artifacts/spikes/host.json` (la base delle righe "Session" di
`docs/spike-results.md`: OS, kernel, modulo amdgpu, pacchetti del driver, ROCm dell'host, versione
di amd-smi, Docker, Compose, nproc, memoria, container, porte in ascolto, spazio libero per
Docker).

### 5.1 Che cosa stampa quando passa

```text
os               <PRETTY_NAME di /etc/os-release>
kernel           <uname -r>
amdgpu_module    <cat /sys/module/amdgpu/version>
host_rocm        <cat /opt/rocm/.info/version>
docker_server    <docker version --format {{.Server.Version}}>
docker_compose   <docker compose version --short>
docker free GB   <numero>
containers       (none)   # oppure la lista nome/immagine/porte
wrote artifacts/spikes/host.json
```

Nessuna riga `ERROR:`; codice di uscita 0. Un `make` che si ferma con `Error 1` vuol dire che
almeno una riga `ERROR:` è stata stampata sopra.

### 5.2 I controlli duri (uscita 1) e i rimedi

| Riga `ERROR:` | Causa | Rimedio |
| --- | --- | --- |
| `.env missing: cp .env.example .env` | manca `.env` | sezione 3.3 |
| `<VAR> must be tag@sha256:digest, got ...` | `VLLM_IMAGE`, `DGL_IMAGE` o `ROCM_BASE_IMAGE` senza digest o con `:latest` | ricopiare la riga da `.env.example` |
| `<VAR> differs from .env.example (...): copy the pinned value` | l'immagine non è quella fissata | ricopiare la riga; se è una scelta voluta (piano B vLLM 0.30.0, sezione 7.6), l'errore è atteso e va annotato: il preflight si lancia prima del cambio |
| `AMD_PYPI_INDEX_URL must be the AMD index from .env.example` | indice vuoto o diverso | ricopiare `https://pypi.amd.com/rocm-7.2.3/simple` |
| `VLLM_API_KEY is empty or change-me: openssl rand -hex 24` | chiave non generata | il `sed` della sezione 3.3 |
| `GEMMA_MODEL is empty` | riga cancellata | ricopiare |
| `GEMMA_REVISION must be the 40-character commit sha from .env.example` | revisione non è uno sha di 40 esadecimali | ricopiare |
| `/dev/kfd missing: no AMD GPU driver on this host` | immagine o taglia sbagliata | distruggere il droplet e ricrearlo (runbook Steps 3) |
| `Docker daemon not reachable (docker version shows no server)` | Docker non in esecuzione | `systemctl enable --now docker` (come fa `04-demo-server.sh`) |
| `docker compose v2 missing: apt-get install -y docker-compose-v2` | manca il plugin | sezione 3.2 |
| `only N GB free for Docker; need about 250` | meno di 250 GB liberi nella root di Docker (tre immagini da 7-12 GB compresse, molto di più scompattate, più 62,6 GB di pesi) | immagine o taglia sbagliata: distruggere e ricreare (runbook Steps 3) |

### 5.3 Quello che non ferma ma va annotato

- **Driver dell'host**: il modulo amdgpu 6.4.x (versione `6.12.12.*`) e i driver 30.x/31.x
  supportano lo user space ROCm 7.0 (DGL) e 7.2 (vLLM, amd_xgboost) nella matrice di
  compatibilità AMD. Un driver più vecchio è un rischio per SPK-G1 e SPK-G2: annotarlo e andare
  avanti (runbook Steps 3).
- `amd_smi_version` che dice `not found: amd-smi`: amd-smi manca sul PATH dell'host anche dopo
  `/opt/rocm/bin`; SPK-G4 e B4 campionano allora dentro il container (sezione 15).
- `listeners`: righe sulle porte 80/443/8000/8001/8080/8888 indicano il container Quick Start o
  altro: vanno fermati prima di `make gpu-up` (sezione 3.5).

Da mandare a Claude: l'output completo di `make spike-preflight` e `artifacts/spikes/host.json`.

## 6. Immagini e pesi (Steps 4-5)

### 6.1 `make spike-prep` (5-15 min stimati, senza GPU)

```bash
make spike-prep
```

Tre job in parallelo, ognuno con il suo log in `artifacts/spikes/logs/`:

| Job | Che cosa fa | Da dove | Log |
| --- | --- | --- | --- |
| `pull-vllm` | scarica `VLLM_IMAGE` (vLLM 0.31.0, ROCm 7.2.3, Ubuntu 22.04; entrypoint `vllm serve`) | Docker Hub, `vllm/vllm-openai-rocm` | `pull-vllm.log` |
| `pull-dgl` | scarica `DGL_IMAGE` (rocm/dgl 2.4.0, ROCm 7.0.0, PyTorch 2.8.0) e `python:3.12-slim` (spike-client) | Docker Hub | `pull-dgl.log` |
| `build-gbm` | costruisce `trainer-gbm` da `infra/docker/trainer-gbm.Dockerfile`: scarica `ROCM_BASE_IMAGE` (`rocm/dev-ubuntu-24.04:7.2.3-complete`), installa `amd_xgboost==3.2.0` dall'indice AMD con hash fissato (`infra/docker/requirements-gbm-amd.txt`), poi i pacchetti PyPI fissati (`requirements-gbm.txt`) e `pip check`; fallisce se a `libxgboost.so` manca una libreria ROCm o se la ruota non è la build HIP | pypi.amd.com e pypi.org | `build-gbm.log` |

Atteso sullo schermo, in ordine di fine:

```text
pull-vllm: exit 0, took N s
pull-dgl: exit 0, took N s
build-gbm: exit 0, took N s
```

Le stesse righe stanno in `artifacts/spikes/logs/prep-status.txt`; `logs/docker-df.txt` tiene
`docker system df` (lo spazio usato, utile per la stima dello snapshot). Dentro `build-gbm.log`
la riga che prova la build giusta è `xgboost 3.2.0 USE_HIP`. Il target fallisce se una riga non
dice `exit 0,`. I tre tempi `took N s` sono misure: annotarle (riga SPK-G7 di
`docs/spike-results.md`, campo `logs/prep-status.txt`).

| Fallimento | Che cosa fare (runbook Steps 4) |
| --- | --- |
| `build-gbm` con errore pip sull'indice AMD (irraggiungibile, 403, hash mismatch) | leggere `logs/build-gbm.log`; non si fa debug a tempo pagato: piano B di SPK-G2 (XGBoost su CPU). Alternativa, se la ruota 7.2.3 è stata sostituita: cambiare insieme indice, hash della ruota e immagine base alla ROCm 7.1.1 (`infra/docker/requirements-gbm-amd.txt` dice che 7.1.1 e 7.0.2 hanno lo stesso nome di file ma un hash diverso) |
| `build-gbm` elenca `librccl.so.1 => not found` | `ROCM_BASE_IMAGE` non è l'immagine `-complete`: ricopiarla da `.env.example` |
| `pull-dgl` o `pull-vllm` con `429 Too Many Requests` | `docker login` con un proprio account Docker Hub, poi rilanciare `make spike-prep` |

### 6.2 `make spike-weights` (parte in pochi secondi, poi in background)

```bash
make spike-weights
```

Lancia `infra/spikes/weights.sh start`: dentro l'immagine vLLM, con `HF_HUB_OFFLINE=0` e senza
GPU, `huggingface_hub.snapshot_download(GEMMA_MODEL, revision=GEMMA_REVISION)` nel volume
`hf-cache` (62,6 GB per il 31B). Gira con `setsid`, quindi sopravvive alla fine dello script e a
una caduta di SSH. Atteso subito:

```text
weights download started: log artifacts/spikes/logs/download-weights.log; later run make spike-weights-wait
```

Si prosegue **subito** con la sezione 7 (le metà GPU di G2 e G3 girano mentre i pesi scaricano).
Il tempo del download è la misura di SPK-G7 e lo stampa `make spike-weights-wait` (sezione 7.5).

È l'unico passo che parla con Hugging Face: vLLM parte poi con `HF_HUB_OFFLINE=1` (regola 2) e
una sessione successiva dallo snapshot non riscarica nulla.

Da mandare a Claude: `artifacts/spikes/logs/prep-status.txt`, `docker-df.txt` e, in caso di
errore, il log del job fallito.

## 7. Gli spike SPK-G1..G7 nell'ordine del runbook (Steps 6-13)

L'ordine non è quello dei numeri: G2 e G3 (metà GPU) girano mentre i pesi scaricano; poi le metà
CPU, da sole sulla CPU e prima che vLLM compili; poi vLLM con G7, G1, G5, G6 e infine G4. I
criteri di superamento e i piani B sono quelli della spec, sezione 13; ogni esito, anche il
fallimento, va in `docs/spike-results.md` com'è (regola 13). Gli output stanno in
`artifacts/spikes/` (git-ignorato, di root).

Per ogni spike: scopo, comando, condizioni, tempo stimato, righe attese, file, criterio, piano B,
che cosa annotare.

### 7.1 `make spike-gpu-check` (Steps 6, circa 1 min)

Scopo: la GPU è visibile in entrambe le immagini dei trainer. Esegue sempre entrambi i controlli
e fallisce se uno fallisce.

```bash
make spike-gpu-check
```

Atteso: da `trainer-gbm` la riga di `ls -l /dev/kfd` e poi `gfx942` (da `rocminfo`); da
`trainer-gnn` una riga `dgl 2.4.0 torch 2.8.0... hip <versione> gpu True`. Se fallisce per una
sola immagine, l'altra riga dice che l'host passa la GPU: il problema è quell'immagine.
Annotare: la riga `gfx942` e la riga di DGL (campo "torch / HIP / DGL" di SPK-G3).

### 7.2 SPK-G2, metà GPU: `make spike-g2` e `make spike-g2-load` (Steps 6, circa 5 min)

Scopo: amd_xgboost addestra sulla MI300X (criterio spec 13: "training su device GPU dell'esempio
ufficiale") e il modello salvato si legge con xgboost-cpu 3.2.0 su CPU (spec, sezione 10).

```bash
make spike-g2          # trainer-gbm: infra/spikes/g2_xgboost_gpu.py --device gpu
make spike-g2-load     # spike-client: pip install xgboost-cpu==3.2.0, poi g2_load_cpu.py
```

Condizioni: pesi in download in background (`--context "Gemma weights downloading in the
background"`, registrato nel file). Che cosa fa `g2_xgboost_gpu.py`: stampa l'ambiente, esegue
lo snippet di verifica dei documenti AMD (1000 x 10, `tree_method hist`, `device cuda`,
10 round) e rilegge dal booster il device effettivo, poi il benchmark (1,4 M righe x 40 feature,
200 round, profondità 4, eta 0,1; riscaldamento di 2 round a parte; log loss su held-out) e salva
`g2_gpu_model.json` più `g2_gpu_check.json` (un campione di predizioni).

Righe attese (JSON su una riga ciascuna):

- `{"xgboost": "3.2.0", "use_hip": true, "use_rccl": ..., "rocm": "7.2.3...", ...}`
- `{"official_verify": {"device_requested": "cuda", "device_effective": "<non cpu>"}}`: la
  stringa esatta (probabilmente `cuda:0`) non è confermata;
- `{"gpu": {"device_effective": ..., "train_s": ..., "train_s_median": ..., "test_logloss": ...,
  "finite": true}}`
- il riepilogo finale con `"pass": true`.

Poi `make spike-g2-load` stampa `{"xgboost": "3.2.0", "rounds": 200, "max_abs_diff": <vicino a
0>, "pass": true}` e scrive `g2_load_cpu.json`. Il device è `cuda` anche su ROCm (`hip` è
rifiutato da XGBoost).

| File | Contenuto |
| --- | --- |
| `artifacts/spikes/g2_gpu.json` | ambiente, verifica ufficiale, risultato GPU, config, contesto |
| `artifacts/spikes/g2_gpu_model.json`, `g2_gpu_check.json` | modello e campione per il caricamento su CPU |
| `artifacts/spikes/g2_load_cpu.json` | versione, round, `max_abs_diff`, `pass` |

Codici di uscita di `g2_xgboost_gpu.py`: 0 pass, 1 errore, **2 GPU non usata** (amd_xgboost
ricade su CPU con un solo avviso: lo script lo rileva dal device effettivo). Con 2: `make
spike-gpu-check` (sezione 7.1). Piano B (spec 13): XGBoost su CPU per il training, GPU solo per
Gemma e DGL.

Annotare (riga SPK-G2): device della verifica ufficiale, `train_s` GPU, `test_logloss` GPU,
`warmup_2_rounds_s`, `max_abs_diff` del caricamento, contesto. La metà CPU arriva in 7.5.

### 7.3 SPK-G3, metà GPU: `make spike-g3` (Steps 7, 3-5 min)

Scopo: DGL da rocm/dgl fa girare GraphSAGE sulla MI300X (criterio spec 13: "un esempio GraphSAGE
gira").

```bash
make spike-g3
```

Tre esecuzioni di `infra/spikes/g3_graphsage_synthetic.py` in `trainer-gnn`, dati sintetici
seminati, nessun download: `--small` (fumo, CPU e GPU: 20.000 nodi, 200.000 archi, 5 epoche),
poi sulla GPU la taglia della spec (1,2 M nodi, 12 M archi, 20 epoche più 3 di riscaldamento) e
`--hetero` (grafo cliente/prodotto con nodi virtuali di brand, tipo di prodotto e motivo). Modello
della spec 8.1: SAGEConv 16, max-pool, dropout 0,2, Adam 0,01.

Righe attese per ogni esecuzione: `{"environment": {"torch": "2.8.0...", "torch_hip":
"<impostato>", "dgl": "2.4.0", "gpu_visible": true, "gpu_count": 1, "gpu_name": ...,
"gpu_arch": "gfx942", ...}}`, poi `{"graph": ..., "build_s": ...}`, poi un `{"result": ...}`
per device con `epoch_s_median`, `gpu_peak_allocated_gb`, `gpu_peak_reserved_gb`,
`gpu_device_used_gb_at_end` (solo GPU) e `"finite": true`, e il riepilogo con `"pass": true`.

| File | Esecuzione |
| --- | --- |
| `artifacts/spikes/g3_small.json` | `--small` (CPU e GPU) |
| `artifacts/spikes/g3_gpu.json` | omogeneo, GPU |
| `artifacts/spikes/g3_hetero_gpu.json` | eterogeneo, GPU |

Uscita 2: GPU non visibile nel container DGL (`FAIL: GPU requested but torch.cuda.is_available()
is False...`): `make spike-gpu-check`. Uscita 1: errore di training o loss non finita. Piano B
(spec 13): GraphSAGE solo come upgrade; benchmark B2 rinviato.

Facoltativo (scarica Cora da data.dgl.ai; non passare `--gpu 0`, lo script non ha quel flag):

```bash
docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml \
  --env-file .env --profile gpu run --rm -T -w /src/dgl/examples/pytorch/graphsage \
  trainer-gnn python train_full.py --dataset cora
```

Annotare (riga SPK-G3): `epoch_s_median` GPU (omogeneo ed etero), `gpu_peak_allocated_gb`,
torch / HIP / DGL, contesto.

### 7.4 Aspettare i pesi: `make spike-weights-wait` (Steps 8, SPK-G7 prima metà)

```bash
make spike-weights-wait
```

Attende fino a 90 minuti (`weights.sh wait`, polling ogni 15 s) e stampa la riga di
`artifacts/spikes/logs/weights-status.txt`:

```text
download-weights: exit 0, took N s (google/gemma-4-31B-it@842da3794eaa0b77d5f08bae87a17459d91ff475)
```

`N` è il tempo di download di SPK-G7: ha girato accanto alle metà GPU di G2 e G3, che usano la
GPU e poca CPU. Fallimenti:

- `exit` diverso da 0: leggere `artifacts/spikes/logs/download-weights.log`. Un 401 o 403 non è
  atteso (nessuno dei due modelli è gated): mettere `HF_TOKEN` in `.env`, poi
  `make spike-weights spike-weights-wait`, e togliere il token prima dello snapshot
  (`sed -i 's/^HF_TOKEN=.*/HF_TOKEN=/' .env`; la fase `down` dello script si rifiuta di
  proseguire finché ha un valore).
- `weights download not finished after 90 min: tail artifacts/spikes/logs/download-weights.log`:
  il download è ancora in corso o bloccato; `tail` del log dice quale.

Annotare: `N` (campo "download s" di SPK-G7) e se `HF_TOKEN` è servito.

### 7.5 SPK-G2 e SPK-G3, metà CPU: `make spike-g2-cpu`, `make spike-g3-cpu` (Steps 8, 5-10 min)

Condizioni: dopo il download, da soli sulla CPU, **prima** di avviare vLLM (un download o una
compilazione sugli stessi 20 vCPU rallenterebbero la baseline e gonfierebbero lo speedup).

```bash
make spike-g2-cpu
make spike-g3-cpu
```

Stessi dati e parametri delle metà GPU (`--context "alone on the CPU"`, `--gpu-result` sul file
GPU). Scrivono gli speedup: `speedup_train` in `g2_cpu.json`, `speedup_median_epoch` in
`g3_cpu.json` e `g3_hetero_cpu.json`. Si fermano con `FAIL: ... has config ...` se il file GPU
è stato prodotto con una configurazione diversa: rilanciare la metà GPU corrispondente e poi la
CPU.

Annotare: `train_s` CPU e `speedup_train` (SPK-G2); `epoch_s_median` CPU, `speedup_median_epoch`
omogeneo ed etero (SPK-G3); `test_logloss` CPU e GPU.

### 7.6 Avviare vLLM: `make spike-vllm`, `make spike-wait`, `make spike-g7` (Steps 9, 5-15 min)

```bash
make spike-vllm        # scrive logs/vllm-start.txt e fa `up -d --no-build vllm`
make spike-wait        # fino a 60 min di polling di /health ogni 15 s
make spike-g7          # le righe del log che misurano caricamento e cache KV
```

I pesi sono già in `hf-cache`: questo è tempo di caricamento e di compilazione (torch.compile e
cattura dei grafi, poi salvati in `vllm-cache`). vLLM parte con il comando di
`infra/docker-compose.yml`: modello e `--revision`/`--tokenizer-revision` a `GEMMA_REVISION`,
`--port=8001`, `--gpu-memory-utilization=0.55`, `--max-model-len=16384`, `--api-key`,
`--enable-auto-tool-choice`, `--tool-call-parser=gemma4`,
`--chat-template=/app/vllm/examples/tool_chat_template_gemma4.jinja`,
`--limit-mm-per-prompt={"image": 1, "audio": 0}`; nessuna porta pubblicata. Il suo healthcheck
compose ha `start_period` di 45 min.

Atteso da `make spike-wait`:

```text
vllm healthy: started <ISO UTC>, healthy <ISO UTC>
```

Atteso da `make spike-g7` (prima il contenuto di `weights-status.txt`, `vllm-start.txt`,
`vllm-healthy.txt`, poi le righe del log filtrate in `artifacts/spikes/g7-vllm-log.txt`):
`Loading weights took`, `Available KV cache memory`, `GPU KV cache size: N tokens`,
`Maximum concurrency for 16,384 tokens per request: X` (e `Time spent downloading weights` se
compare).

| Fallimento | Che cosa fare (runbook Steps 9) |
| --- | --- |
| `vllm is not running` seguito dalle ultime 100 righe del log | vLLM si è fermato: leggere il log (`make spike-logs` stampa le ultime 200 righe) |
| `Free memory on device ... is less than desired GPU memory utilization` | qualcos'altro tiene memoria GPU (container Quick Start, un trainer ancora in corso): fermarlo, poi `make spike-vllm spike-wait` |
| chat template non trovato | il percorso del template in `infra/docker-compose.yml` deve restare assoluto |
| errore di compilazione o di cattura dei grafi, o nessun server healthy dopo 15 min di compilazione | **solo sul droplet**, aggiungere `- --enforce-eager` al `command` di vllm in `infra/docker-compose.yml`, poi `make spike-vllm spike-wait`; annotarlo: in modalità eager è più lento, i numeri di G1 diventano un limite inferiore |
| errore di engine o di kernel in vLLM 0.31.0 | in `.env`: `VLLM_IMAGE=vllm/vllm-openai-rocm:v0.30.0@sha256:2e7da1ad1c66836802072588adea75f9f4991da5f9545b4318e91d422c22ce6a` (altri 11 GB di pull), poi `make spike-vllm spike-wait`; da qui in poi il preflight segnala che l'immagine differisce (atteso) |
| `vllm not healthy after 60 min` | leggere il log; se è ancora in compilazione, `make spike-wait` di nuovo; altrimenti i rimedi sopra |
| Gemma 4 31B non parte affatto | piano B D-12 (sezione 8) |

Annotare (SPK-G7): "vLLM start to healthy s" (differenza tra le due date di `spike-wait`), i
token della cache KV e la concorrenza massima a 16.384, e se è servito `--enforce-eager` o la
0.30.0.

### 7.7 SPK-G1: `make spike-g1`, `make spike-g1-bench` (Steps 10, circa 8 min)

Scopo (spec 13): Gemma 4 31B su vLLM risponde con JSON guidato valido e si misurano i token al
secondo. Decide D-12 (sezione 8).

```bash
make spike-g1          # spike-client: g1_guided_json.py --out artifacts/spikes/g1.json
make spike-g1-bench    # vllm bench serve dentro il container vllm
```

`spike-g1` manda tre richieste con lo schema `contracts/json/llm_output.schema.json`
(`response_format json_schema`, `temperature 0.2`, `max_tokens 1024`); la prima è riscaldamento
(compilazione della grammatica). Controlla anche che una richiesta senza chiave riceva HTTP 401
(NFR-11). Righe attese: tre JSON `{"run": i, "warmup": ..., "elapsed_s": ..., "completion_tokens":
..., "tokens_per_s": ..., "finish_reason": "stop", "problems": []}`, poi l'ultimo output del
modello, poi

```text
SPK-G1: pass=True median <n> tokens/s (single request, after warm-up)
```

I token al secondo di `spike-g1` includono il tempo al primo token di una richiesta non in
streaming: la misura "da B3" è quella di `spike-g1-bench`. Uscita 1 con `FAIL: request without
the API key was not refused with 401` significa che vLLM non applica la chiave (NFR-11): da
annotare, non da ignorare.

`spike-g1-bench` lancia `vllm bench serve --backend openai-chat` contro `/v1/chat/completions`
con il tokenizer letto da `hf-cache` alla `GEMMA_REVISION`: 16 richieste una alla volta
(`--max-concurrency 1`), poi 64 richieste 32 alla volta; 1500 token in ingresso e 400 in uscita
(`BENCH_IN`, `BENCH_OUT` per cambiarli), 2 riscaldamenti, `--ignore-eos`, percentili 50 e 95 di
ttft, tpot, itl, e2el. Scrive `artifacts/spikes/logs/g1-bench-c1.txt` e `g1-bench-c32.txt` e li
stampa; fallisce se una richiesta non riesce (cerca `Successful requests: 16` e `Successful
requests: 64`).

- `finish_reason` `length`: alzare `--max-tokens` (default 1024) nel comando stampato da
  `make -n spike-g1`.
- Criterio D-12: flusso singolo sotto 15 token/s (1000 / TPOT mediano in ms della corsa C=1) o
  errore di avvio: piano B (sezione 8).

Annotare (riga SPK-G1): tokens/s mediano di `g1.json` (`tokens_per_s_median_after_warmup`),
`nfr11_key_enforced`; da `g1-bench-c1.txt` output tok/s, TPOT mediano in ms, TTFT P95 in ms; da
`g1-bench-c32.txt` output tok/s.

### 7.8 SPK-G5: `make spike-g5` (Steps 11, circa 1 min)

Scopo (spec 13): una foto produce una checklist JSON.

```bash
make spike-g5                          # IMAGE=artifacts/spikes/garment.jpg
make spike-g5 IMAGE=artifacts/spikes/<file>
```

Stesso script di G1 con `--image`: schema da spike (non un contratto) con `tag_attached`,
`visible_damage`, `photo_quality_ok` (ognuno con `outcome` `yes`/`no`/`not_observable` e
`confidence` `low`/`medium`/`high`) e `notes` (al massimo 300 caratteri). Atteso: il JSON della
checklist e `SPK-G5: pass=True ...`. File: `artifacts/spikes/g5.json`. Piano B (spec 13):
ispezione fuori ambito (era Could). Annotare: checklist valida sì/no e `elapsed_s`.

### 7.9 SPK-G6: `make spike-g6` (Steps 12, circa 1 min)

Scopo (spec 13): un ciclo con uno strumento finto funziona.

```bash
make spike-g6                   # --mode auto: tool_choice auto, serve il parser gemma4
make spike-g6 G6_MODE=named     # solo se auto fallisce: la funzione forzata via structured outputs
```

Passo 1: il modello deve chiamare `get_case_orders` con `case_id` `R-1042`; passo 2: il risultato
finto torna come messaggio `tool` e la risposta deve citare `O-1001` e `O-1002`. Atteso: un JSON
con `"pass": true`; se fallisce, `SPK-G6 FAIL (auto). Plan B: --mode named.` su stderr. File:
`artifacts/spikes/g6-auto.json`, eventualmente `g6-named.json`. Piano B (spec 13): il codice
dell'agente sceglie lo strumento, Gemma scrive solo la risposta (è ciò che `named` prova).
Annotare: modalità passata, `elapsed_s` dei due passi.

### 7.10 SPK-G4: `make spike-g4` (Steps 13, circa 7 min)

Scopo (spec 13): vLLM a 0,55 più training XGBoost e DGL sulla stessa GPU senza errori di memoria.

```bash
make spike-g4                   # DURATION=300 di default, POLL=45
```

`infra/spikes/g4_coexistence.sh`: controlla che vLLM sia healthy (altrimenti `vLLM is not
healthy: run make spike-vllm and make spike-wait first`, uscita 1); campiona `amd-smi metric
--json` e `amd-smi process` **sull'host** ogni 5 s in `artifacts/spikes/g4_amdsmi.log` (con
`/opt/rocm/bin` aggiunto al PATH); lancia in parallelo il DGL (`--min-seconds 300`) e l'XGBoost
(`--min-seconds 300`) sulla GPU; ogni 45 s manda una richiesta guided-JSON (`--runs 2`). Righe
attese:

```text
guided-JSON request 1 during training: pass
...
DGL job exit 0, XGBoost job exit 0, LLM failures 0, vLLM health 0
vLLM error lines since <ISO>: 0
SPK-G4: pass
```

File: `g4_dgl.json`, `g4_xgb.json`, `g4_llm_<n>.json`, `g4_amdsmi.log`, `logs/g4_dgl.log`,
`logs/g4_xgb.log`, `logs/g4_vllm_errors.log`. Se amd-smi manca sull'host stampa `amd-smi not
found on the host: record memory with rocm-smi or skip` e va avanti senza campioni. In caso di
`SPK-G4: FAIL`, piano B (spec 13): training a vLLM spento (`make spike-down`, poi i job).

Annotare (riga SPK-G4): VRAM di picco da `g4_amdsmi.log`, `gpu_peak_allocated_gb` di
`g4_dgl.json`, `train_s_median` di `g4_xgb.json` contro `g2_gpu.json` (sotto carico i minimi
nascondono l'interferenza: si confrontano le mediane), `epoch_s_median` di `g4_dgl.json` contro
`g3_gpu.json`, richieste LLM passate.

### 7.11 Che cosa mandare a Claude dopo gli spike

Tutto `artifacts/spikes/` (i JSON, `logs/*.txt`, `logs/*.log`, `g7-vllm-log.txt`,
`g4_amdsmi.log`), che sono i campi di `docs/spike-results.md` (i nomi dei campi sono le chiavi
JSON); più i tempi misurati tra un passo e l'altro. La sezione 12 dice come.

Mai l'output grezzo di `make spike-logs` (o la coda del log che `make spike-wait` stampa quando
vLLM si è fermato) senza controllare che non contenga la riga degli argomenti di avvio di vLLM
con `--api-key=<chiave>`: vLLM la stampa all'avvio. Lo script della sessione sostituisce i valori
di `VLLM_API_KEY`, `POSTGRES_PASSWORD`, `HF_TOKEN` e `ADMIN_PASSWORD_HASH` in ogni log prima di
impacchettare `release-logs-<data>.tgz`; un log copiato a mano no.

## 8. La decisione D-12 (31B o 26B-A4B)

Fonti: spec 13 (SPK-G1, piano B "Gemma 4 26B-A4B (MoE), più veloce"), spec 14 (R-02: "meno di
15 token al secondo o errore di avvio"), spec 18 (D-12: "token al secondo misurati in SPK-G1 e
B3"), runbook Steps 10, `.env.example`.

### 8.1 Il criterio

Si resta sul 31B se vLLM parte e il flusso singolo è **almeno 15 token/s**. Il numero si legge
in `artifacts/spikes/logs/g1-bench-c1.txt` (la corsa con 16 richieste una alla volta): token/s
in flusso singolo = 1000 / TPOT mediano in millisecondi. Il tokens/s di `g1.json`
(`tokens_per_s_median_after_warmup`) è una seconda lettura, più bassa perché include il tempo al
primo token. Anche un errore di avvio di vLLM con il 31B (sezione 7.6, ultima riga della tabella)
porta al piano B.

### 8.2 Come si passa al piano B

In `.env`, **entrambe** le righe insieme (`.env.example` lo dice nel commento):

```text
GEMMA_MODEL=google/gemma-4-26B-A4B-it
GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52
```

poi si riparte da `spike-weights`:

```bash
make spike-weights spike-weights-wait spike-vllm spike-wait   # altri 51,6 GB di download
make spike-g1 spike-g1-bench                                   # ripetere Steps 10
```

`spike-vllm` sostituisce il vLLM in esecuzione con il nuovo modello (`up -d` ricrea il
container perché il suo comando è cambiato); `hf-cache` tiene anche i pesi del 31B. Il tokenizer di
`spike-g1-bench` segue `GEMMA_MODEL` e `GEMMA_REVISION`. Dopo il cambio G5, G6 e G4 (e tutto ciò
che segue) girano sul modello del piano B; `make batch-generate` e `make demo-export` registrano
il modello di ogni bozza, e il pacchetto della demo porta il nome del piano B in
`llm_cache.gemma_model` (ADR-17, seconda revisione: un pacchetto del piano B è etichettato
MI300X, non prova).

### 8.3 Che cosa non si sa ancora

- La revisione del piano B fissata in `.env.example` (`4d7ae498...`, 13 ottobre 2026) non è mai
  stata scaricata né servita: la guida al rilascio dice che è Claude a mandare `GEMMA_MODEL` e
  `GEMMA_REVISION` da incollare. Prima di incollarla, chiedere conferma a Claude (sezione 17).
- Il tempo di download dei 51,6 GB e il tempo di compilazione del 26B-A4B: come per il 31B, non
  misurati.

Da mandare a Claude: i due file `g1-bench-c*.txt`, `g1.json`, la decisione presa e l'ora
(serve per spec 18 e per i testi di consegna).

## 9. La sessione GPU: B1-B4, GraphSAGE, embedding A4 ("The GPU session" 3-14)

Si esegue dopo gli spike (dopo Steps 13 e prima di Steps 14), oppure in una sessione successiva
dallo snapshot. Metodo e le quattro regole della spec 12: `docs/benchmarks.md`; i job GraphSAGE:
`docs/graphsage.md`. I passi 1 e 2 (portatile: branch verde, dati copiati) sono le sezioni 1 e 4
di questo documento. `$RUN` sotto è `artifacts/bench/$BENCH_RUN`.

### 9.1 Le regole del blocco

- **vLLM giù per B1, B2, i test GNN e A4**: la loro metà CPU gira da sola sui 20 vCPU e la metà
  GPU da sola sulla GPU. `bench-b1`, `bench-b2`, `embeddings-gpu`, `gnn-asos-gpu` e
  `gnn-asos-cpu-container` si rifiutano di partire con vLLM in esecuzione:
  `vLLM is running: B1 and B2 run alone on the GPU and the CPU (make gpu-down first)`.
- **Un solo `BENCH_RUN` per tutta la sessione**: `export BENCH_RUN=$(date -u +%Y%m%dT%H%M%SZ)-gpu`
  nella shell di tmux. Senza export il Makefile ne genera uno nuovo a ogni comando e le parti
  finirebbero in cartelle diverse. Se la shell cade, `ls artifacts/bench/` mostra l'id da
  riesportare. Con lo script della sessione l'id sta in `release-logs/gpu/BENCH_RUN`: in una
  shell nuova, `export BENCH_RUN=$(cat release-logs/gpu/BENCH_RUN)` prima di un `make` a mano.
  Non fare `git pull` tra una parte e il merge: `bench-merge` scrive nel file della corsa il
  commit **al momento del merge** (`bench/__main__.py`, `env.git_info()`), non quello su cui le
  parti hanno girato; lo script conserva il commit della prima corsa in
  `release-logs/gpu/BENCH_RUN.commit` e avvisa se `HEAD` è cambiato.
- **`BENCH_ARGS`** passa opzioni a `python -m bench run`: `BENCH_ARGS="--b2-samples 1"` salta le
  taglie 25% e 50% di B2 se la sessione è corta; `BENCH_ARGS="--b3-batches 1,8,32
  --b3-concurrency 4,16 --b3-repeats 2"` cambia i carichi di B3. `python -m bench run --help`
  elenca tutto (`--b1-fractions`, `--b1-repeats`, `--threads`, `--b4-scale`, ...).
- **Stati**: ogni parte in `$RUN/<B>.json` dice `measured`, `skipped` (con il motivo) o `failed`
  (con l'errore); una parte fallita tiene ciò che ha misurato e **non ferma la successiva**
  (`python -m bench run` esce 1 se una parte è fallita). Un `measured` con dentro un carico
  `failed` > 0 (B3) va letto nei dettagli.
- **Rilancio**: dopo un fallimento si rilancia **dal passo fallito** con lo stesso `BENCH_RUN`:
  le parti già scritte in `$RUN` restano. `make bench DEVICE=gpu` eseguirebbe i passi 5, 6, 8,
  9, 11, 12, 13, 14 in quell'ordine, ma i passi 7 e 10 ci vanno in mezzo e il 10 rifiuta di
  partire a vLLM acceso: meglio un comando alla volta.
- **Tempo**: non misurato sul droplet. B2 è il passo più lungo: il job US-202 a dati pieni ha
  preso 92 minuti sui 4 vCPU condivisi del portatile; il suo tempo sui 20 vCPU del droplet è ciò
  che B2 misura. La guida al rilascio stima 2-4 ore per tutto il blocco.

### 9.2 I passi, in ordine

| # | Comando (droplet) | vLLM | Che cosa fa | Righe da guardare | Scrive |
| --- | --- | --- | --- | --- | --- |
| 3 | `make spike-down` | giù dopo | ferma vLLM e ogni trainer (`--profile gpu --profile spike down`), tiene i volumi | nessun container di `returnring` in `docker ps` | nulla |
| 4 | `export BENCH_RUN=$(date -u +%Y%m%dT%H%M%SZ)-gpu` | giù | un id per tutta la sessione; `.env` come per gli spike | `echo $BENCH_RUN` | nulla |
| 5 | `make bench-build DEVICE=gpu` | giù | costruisce l'immagine `bench` (`infra/docker/bench.Dockerfile`: ROCm 7.2.3, la stessa ruota amd_xgboost di trainer-gbm, lo stack di pipeline e llm) | nel log `xgboost 3.2.0 USE_HIP` e `bench stack ok` | l'immagine `bench` |
| 6 | `make gnn-image` | giù | costruisce `trainer-graphsage` (`infra/docker/trainer-gnn.Dockerfile`): rocm/dgl più numpy 2.5.4, pandas 3.0.6 e ciò che importano ml.gnn, B2 e i test GPU; la build fallisce se DGL si rompe su numpy 2 | `numpy 2.5.4` e `dgl 2.4.0 on numpy 2.5.4`, poi la riga `torch ... hip ... dgl ... xgboost ... polars ... pandas ...` | l'immagine `trainer-graphsage` |
| 7 | `make gnn-test-gpu` | giù | pytest `-m "torch or gpu"` su `tests/test_gnn_model.py` e `tests/test_gnn_jobs.py` nel container: DGL contro i layer PyTorch su CPU e GPU, i test dei job su CPU, i test GPU dei job | il riepilogo pytest senza `failed` | nulla (cartelle temporanee) |
| 8 | `make bench-b1 DEVICE=gpu` | giù | B1 nel container `bench`: XGBoost su CPU e MI300X, protocolli A e B, taglie 10/25/50/100%, 200 round fissi (gli speedup) e early stopping (a parte), 3 ripetizioni, scoring con il modello a round fissi | `B1 cuda: cold start ...` e nessun errore "GPU not used" (B1 rifiuta la corsa invece di riportarla) | `$RUN/B1.json` |
| 9 | `make bench-b2 DEVICE=gpu` | giù | B2 in `trainer-graphsage`: `ml.gnn.b2_timing` esegue il job US-202 (`python -m ml.gnn asos`) su CPU e MI300X al 25, 50 e 100% dei clienti; le corse a dati pieni sono le corse US-202 | `B2 A cuda 100%: median epoch ...`; in `us-202-asos-gpu.json` `torch_hip` impostato, backend dgl, `gpu_arch` gfx942; uscita 2 dal job = GPU non visibile (`make spike-gpu-check`) | `$RUN/B2.json`; `artifacts/eval/gnn/us-202-asos-cpu.json`, `us-202-asos-gpu.json`; `artifacts/gnn/models/asos_{A,B}_graphsage_{cpu,gpu}.pt` e `.meta.json`; `$RUN/b2-sample-25/`, `$RUN/b2-sample-50/` |
| 10 | `make embeddings-gpu` | giù | A4: encoder dei clienti addestrato sulla GPU sul negozio principale, applicato al negozio OOD, embedding copiati in `data/` | i due job di `python -m ml.gnn embed` escono 0 | `artifacts/gnn/models/customer_encoder.pt` e `.meta.json`; `artifacts/eval/gnn/us-202-embeddings-main.json`, `-ood.json`; `data/features/customer_embeddings.parquet`, `data/ood/features/customer_embeddings.parquet` |
| 11 | `make bench-vllm DEVICE=gpu` | parte | scarica la page cache (`BENCH_DROP_CACHES=0` la tiene), avvia vLLM, aspetta `/health` (polling 15 s), poi cronometra la prima richiesta prima che altro usi vLLM | `page cache dropped before the start` (o `not dropped`), `vllm healthy: started ..., healthy ...`, `first-request.json` con `"ok": true` | `$RUN/vllm-start-conditions.txt`, `vllm-start.txt`, `vllm-healthy.txt`, `first-request.json` |
| 12 | `make bench-b4 DEVICE=gpu` | su | B4: la pipeline notturna (generate, graph, features, anomalies, risk, scoring su CPU; batch generation sulla MI300X via vLLM), amd-smi campionato sull'host ogni 5 s | niente `no amd-smi on the host: sampling inside the container` (se compare, è annotato); `batch_generation` esce 1 se vLLM è mancato per una decisione | `$RUN/B4.json`, `$RUN/amdsmi.jsonl`, `$RUN/b4-logs/`, `bench-work/$BENCH_RUN/` (negozio generato, truth cancellata) |
| 13 | `make bench-b3 DEVICE=gpu` | su | registra il disco usato (per lo snapshot), poi B3: latenza p50 e p95 e token/s di Gemma, batch 1, 8, 32 e concorrenza 4, 16, sulla stessa lista di casi del negozio di B4; costo per decisione (cost-v2) | `failed` deve essere 0 in ogni carico di `B3.json` (dettagli) | `$RUN/disk-used-gb.txt`, `$RUN/B3.json` |
| 14 | `make bench-merge DEVICE=gpu` | qualsiasi | il file della corsa, i puntatori che `GET /api/benchmarks` legge, un rapporto | nessun errore; `ls bench/results/` mostra i tre file | `bench/results/$BENCH_RUN.json`, `bench/results/latest.json`, `bench/results/latest-gpu.json`, `$RUN/report.md` |

Note per passo:

- Passo 6: un fallimento della build è l'immagine rocm/dgl che rifiuta numpy 2 (spedisce numpy
  1.26.4, fissato da amd-dgl): annotarlo, piano B della spec 13 (GraphSAGE come upgrade, B2
  rinviato) e proseguire con il passo 8 (si saltano 7, 9 e 10).
- Passo 11: `vllm-start-conditions.txt` dice se la page cache è stata scaricata (serve root
  sull'host, `echo 3 > /proc/sys/vm/drop_caches`). Il tempo da `vllm-start.txt` a
  `vllm-healthy.txt` è il cold start che B3 riporta, "on a running droplet, not a start from a
  snapshot" (la cache di compilazione è tenuta). Se vLLM era già acceso stampa `vLLM already
  running: B3 reports no cold start`.
- Passo 12: `amd-smi` deve esistere sull'host (`/opt/rocm/bin` è aggiunto al PATH e serve anche
  `python3`: il campionatore è `python3 -m bench.amdsmi`); altrimenti il target lo dice e
  campiona dentro il container. `B4` genera un negozio in `bench-work/` (`--b4-scale`) e vi esegue
  generate, graph, features, anomalies, risk, scoring e infine la batch generation dei resi
  aperti tramite vLLM (`docs/benchmarks.md`, "B4 in detail"). Ordine di grandezza, non una misura
  di B4: la catena simile di `make demo-data` ha preso 9 min 39 s su 4 vCPU condivise (commento
  del Makefile); B4 aggiunge scoring e batch e non ha il passo serving.
- Passo 13: i fallimenti restano fuori dalle latenze e sono annotati; con `failed` > 0 in un
  carico il numero di quel carico non va in slide senza dirlo. B3 legge i casi dal negozio con
  punteggi che B4 ha generato in `bench-work/$BENCH_RUN`: se B4 è fallito prima del passo
  scoring, o è stato saltato, prima si rilancia `bench-b4` con lo stesso `BENCH_RUN`; senza B4
  B3 non ha casi (`data/` nel container `bench` ha solo `data/raw`: né store né scores, e
  l'errore sembra un altro). Lo script si ferma prima di `bench-b3` se `bench-b4` è stato saltato.
- Passo 14: anche se una parte è `failed`, il merge scrive il file della corsa con quella parte
  marcata; `latest.json` si sposta perché è una corsa GPU.

### 9.3 Quando qualcosa fallisce

| Sintomo | Che cosa fare |
| --- | --- |
| `vLLM is running: B1 and B2 run alone...` | `make spike-down` (o `make gpu-down`), poi il passo |
| `make bench-build` fallisce (pip sull'indice AMD: irraggiungibile, 403, hash mismatch) | l'immagine `bench` non esiste: oltre a B1 non possono girare sul droplet nemmeno `bench-vllm` (la prima richiesta), `bench-b4`, `bench-b3` e `bench-merge`, che girano tutti nel container `bench` (Makefile `BENCH_RUN_GPU`). Non saltarli uno alla volta. O si ripara la build (sezione 6.1: indice, hash e base image insieme alla ROCm 7.1.1) e si rilancia, o si eseguono solo i target di `trainer-graphsage` (passi 6, 7, 9, 10), si annotano B1, B3 e B4 come non misurati (regola 13) e si fa il merge di B2 sul portatile, sullo stesso commit, con le parti copiate: `scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/` poi `python -m bench merge --run-id $BENCH_RUN` (il merge legge solo le parti). `make demo-export` avvisa allora che manca `latest-gpu.json`: il pacchetto non porta benchmark MI300X |
| `bench-b3` fallisce leggendo `scores/request_scores` | B4 saltato o fallito prima dello scoring: `make bench-b4 DEVICE=gpu` con lo stesso `BENCH_RUN`, poi B3 (nota al passo 13) |
| B1 si ferma con un errore "GPU not used" | la GPU non è visibile al container `bench`: `make spike-gpu-check`; non si riporta mai la CPU come MI300X |
| passo 9 esce 2 | `make spike-gpu-check` |
| passo 12 `batch_generation` esce 1 | vLLM non ha risposto per qualche decisione: `make spike-logs`, poi rilanciare il passo 12 con lo stesso `BENCH_RUN` |
| un passo `failed` nel suo JSON | leggere `error` nel file, correggere la causa, rilanciare quel passo con lo stesso `BENCH_RUN` |

Da mandare a Claude: `bench/results/*.json`, tutto `artifacts/bench/$BENCH_RUN/` (JSON, `report.md`,
`amdsmi.jsonl`, i log), `artifacts/eval/gnn/*.json`, i due `customer_embeddings.parquet` e il
valore di `BENCH_RUN` (sezione 12).

## 10. Le bozze di Gemma e il pacchetto della demo ("The demo export" 2-7)

La demo cached (`DEMO_MODE=cached`, GPU spenta) serve le bozze che Gemma ha scritto sulla MI300X
per i resi aperti di **uno** snapshot di servizio. Il pacchetto prende lo snapshot, la cache e
`bench/results/`: per questo `make bench-merge` (passo 14) va fatto prima dell'export, così
`bench/results/latest.json` nomina la corsa MI300X (una corsa CPU viene impacchettata anche lei,
etichettata, ma mai servita come latest).

### 10.1 Lo snapshot sul droplet (Demo export 2, dal portatile)

Già fatto nella sezione 4: `rsync -a --delete serving/ root@<droplet-ip>:ReturnRing/serving/`.
Se nel frattempo `serving/` è stato ricostruito sul portatile, va ricopiato **prima** del batch;
`--delete` svuota `serving/llm_cache/`.

### 10.2 vLLM da solo e healthy (Demo export 3)

```bash
make spike-vllm spike-wait
```

Dopo il passo 13 di B3 vLLM è ancora acceso: `make spike-vllm` lo lascia com'è (`up -d` di un
container già in esecuzione) e `spike-wait` conferma `vllm healthy`. Se è spento, parte da
`hf-cache` e `vllm-cache`.

### 10.3 `make batch-generate` (Demo export 4; 30-60 min stimati dalla guida al rilascio)

```bash
make batch-generate                 # docker compose ... --profile gpu run --rm batch-generate
```

Esegue `python -m apps.api.batch_generate` nell'immagine dell'API (profilo `gpu-jobs`, mai avviato
da `up`), con `DEVICE=gpu`, `LLM_MODE=vllm`, `DEMO_MODE=live` fissi e `serving/` montato in
scrittura. Che cosa fa (docstring di `apps/api/batch_generate.py`):

1. chiede a vLLM `/v1/models` e **si ferma (uscita 2)** se `GEMMA_MODEL` non è tra i modelli
   serviti (`the batch needs GEMMA_MODEL on the local vLLM: ...`): un server finto risponde a
   qualsiasi nome;
2. per ogni reso aperto dello snapshot, casi d'oro per primi, costruisce l'`llm_case` che l'API
   costruisce al reset, lo manda a vLLM via `llm.generate` (validatore, una rigenerazione,
   template) e scrive `serving/llm_cache/<sha256 dell'llm_case>.json`;
3. al massimo `LLM_CONCURRENCY` chiamate in volo (default 32; `.env` per cambiarlo) e
   `VLLM_TIMEOUT_S` secondi per chiamata (default 20);
4. ogni voce registra il modello e il `DEVICE` (`gpu`) del batch; una voce in cache di un altro
   modello (per esempio la prova contro il server finto) viene rigenerata, mai tenuta;
5. una bozza caduta sul template perché vLLM era irraggiungibile **non** va in cache.

Sullo schermo: righe `[batch  N.N s] 25 of <n> cases` ogni 25 casi, poi il riepilogo JSON con
`as_of`, `returns`, `paths` (conteggio per percorso di generazione), `skipped_cached`, `failed`
(dizionario reso -> motivo) e `cache`. Nelle fonti i resi aperti dello snapshot di prova sono
623 (ADR-17, guida al rilascio); il numero vero è `returns`.

| Uscita | Significato | Che cosa fare |
| --- | --- | --- |
| 0 | tutte le bozze in cache | avanti |
| 1 | qualche reso fallito (`failed` non vuoto: `vLLM unavailable: not cached` o un'eccezione) | `make spike-logs`; rilanciare `make batch-generate`: le voci già in cache vengono saltate (`skipped_cached`), si rigenerano solo le mancanti |
| 2 | uso: `LLM_MODE` non `vllm`, vLLM non configurato, server che non serve `GEMMA_MODEL`, snapshot illeggibile (il motivo è stampato) | correggere `.env` o lo snapshot, rilanciare |

Opzioni: `--limit N` (solo i primi N, casi d'oro per primi) e `--force` (rigenera anche ciò che
è in cache) si passano con il comando stampato da `make -n batch-generate`.

### 10.4 `make demo-export` (Demo export 5)

```bash
make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer Cloud)"
```

Gira con il `python3` dell'host (3.8 o successivo, sola libreria standard): `python3 -m
scripts.demo_export build --serving serving --bench bench/results --out demo-cache
--generated-on "$DEMO_GPU" --model "$GEMMA_MODEL"`, poi `tar -czf demo-cache.tar.gz demo-cache`.
`DEMO_GPU` **non ha default**. Che cosa controlla e che cosa rifiuta (uscita 1, messaggio
`demo-export: ...` su stderr):

| Rifiuto | Messaggio | Causa |
| --- | --- | --- |
| snapshot non conforme al suo manifest | `the serving snapshot does not match its manifest: ...` (`missing from the snapshot`, `changed since the export`, `not in the serving manifest`) | `serving/` ricostruito da una parte sola, o file aggiunti fuori da `llm_cache/` |
| percorso con `truth` nel pacchetto | `... a ground-truth path in the package (rule 12)` | non deve succedere: lo snapshot non ne ha |
| cache vuota | `serving/llm_cache holds no draft: run make batch-generate on the MI300X first (ALLOW_EMPTY_CACHE=1 packs a CPU rehearsal whose drafts are templates)` | batch non eseguito, o `rsync --delete` dopo il batch |
| `DEMO_GPU` senza `MI300X` | `name the GPU the batch ran on: make demo-export DEMO_GPU='AMD Instinct MI300X (AMD Developer Cloud)' (got ...)` | variabile mancante o diversa |
| bozze di un altro modello | `llm_cache/ holds drafts of <modello>, not only of GEMMA_MODEL <modello>: not packed as the MI300X demo` | `.env` cambiato dopo il batch (piano B senza rifare il batch) o voci della prova rimaste |
| bozze di un batch non `DEVICE=gpu` | `llm_cache/ holds drafts of a batch run with DEVICE cpu, not only DEVICE=gpu` | cache della prova sul portatile copiata |
| `demo-cache/` non vuota e senza `MANIFEST.json` | `demo-cache is not empty and holds no MANIFEST.json: not replaced` | cartella sporca: rimuoverla a mano |
| puntatore benchmark rotto | `<pointer>: names a run file that is not in bench/results: ...` | `bench/results/` incompleto: rifare `make bench-merge` |

`REHEARSAL=1` e `ALLOW_EMPTY_CACHE=1` impacchettano una prova, etichettata come tale ("Precomputed
in a rehearsal, not verified on AMD MI300X"): non sono per il droplet.

Atteso quando passa: un JSON con `out`, `files`, `as_of`, `llm_cache` (`entries`, `models`,
`generated_on`, `rehearsal` `false`), `bench` (i puntatori), poi
`demo-cache.tar.gz: copy it to the demo server, then make demo-import demo-up`.

I campi di `demo-cache/MANIFEST.json` (`scripts/demo_export.py`, `build`):

| Campo | Contenuto |
| --- | --- |
| `format` | `returnring.demo_export.v1` |
| `created_at` | istante UTC dell'export |
| `git_commit` | `BENCH_GIT_COMMIT` o `git rev-parse HEAD` |
| `serving.as_of`, `serving.seed`, `serving.contract_version`, `serving.versions`, `serving.manifest_sha256`, `serving.nature` | dallo snapshot (`serving/manifest.json`) |
| `llm_cache.entries`, `models`, `devices`, `generation_paths`, `fallback_reasons`, `snapshot_instants` | che cosa tiene la cache: voci per modello, device, percorso di generazione, motivo di fallback |
| `llm_cache.generated_on` | il valore di `DEMO_GPU` |
| `llm_cache.rehearsal` | `false` per il pacchetto MI300X |
| `llm_cache.gemma_model` | `GEMMA_MODEL` del droplet (l'API etichetta le bozze confrontandole con questo, non con il suo `GEMMA_MODEL`) |
| `llm_cache.nature` | il testo di natura (regola 11) |
| `bench.pointers` | per ogni `latest*.json`: `run_id`, `device`, `file` |
| `files` | per ogni file del pacchetto: `sha256` e `bytes` |

### 10.5 Il pacchetto sul portatile e la release (Demo export 6; guida al rilascio 4.1)

Dal portatile:

```bash
scp root@<droplet-ip>:ReturnRing/demo-cache.tar.gz .
sha256sum demo-cache.tar.gz
make demo-import demo-local          # controlla ogni hash, poi la demo cached su http://localhost:8080
```

`make demo-import` stampa `demo-verify: demo-cache matches its MANIFEST.json`; un hash diverso
stampa `demo-verify: changed: <file>` ed esce 1. Poi `curl -s http://127.0.0.1:8000/readyz` deve
mostrare `"mode":"cached"`, `"label":"Precomputed on AMD MI300X"`, `"public_read_only":true`,
`clock` con `"frozen":true`.

La release: `demo-cache.tar.gz` si pubblica come asset di una release su GitHub con il suo
sha256 (l'output di `sha256sum`); Claude aggiunge il link alla sezione "The cached demo" del
README (runbook "The demo export", nota finale; guida al rilascio 4.1). Fino ad allora il
percorso del portatile è per il team.

### 10.6 Il cambio di pacchetto sul server della demo (Demo export 7; guida al rilascio 4.1)

Sul server CPU, dove `scripts/release/04-demo-server.sh` ha già messo `.env` e il pacchetto di
prova:

```bash
# dal portatile
scp demo-cache.tar.gz root@<ip-server-demo>:ReturnRing/
# sul server, in ReturnRing/
make demo-down && make demo-import demo-up
```

Atteso: `https://<PUBLIC_HOST del server>/readyz` con `"label":"Precomputed on AMD MI300X"`,
`"mode":"cached"`, `"public_read_only":true`; nella UI l'etichetta su ogni schermo. Poi
`make demo-reset` sul server e, dal portatile, `make demo-walk API_URL=https://<PUBLIC_HOST del
server>`: 12 passi `ok`, le sette scritture del pitch rifiutate con 403, l'assistente 503
`llm_unavailable` come documentato per la modalità cached. L'etichetta segue il `MANIFEST.json`
del pacchetto, non il `GEMMA_MODEL` del server.

Da mandare a Claude: l'output di `make batch-generate` (il riepilogo JSON), `demo-cache/MANIFEST.json`,
lo sha256 del tar, l'output di `make demo-import` sul portatile e il JSON di `/readyz` del server
dopo il cambio.

## 11. Lo stack live per video e pitch (Demo export 8; guida al rilascio 4.2-4.3)

### 11.1 `make gpu-up`

```bash
make gpu-up
```

In ordine (`make -n gpu-up`): `serving-check` (si ferma con `No serving snapshot in serving/:
make demo-data, or copy serving/ from the laptop` se manca `serving/manifest.json`), poi
`spike-vllm` e `spike-wait` (vLLM da solo e healthy prima che altro tocchi la GPU: dimensiona la
cache KV dalla memoria libera che vede), poi `docker compose ... --profile gpu -f
infra/compose.public.yml up -d --build`. `--build` ricostruisce a ogni `up` **ogni** servizio del
profilo `gpu` con una sezione `build` (api, ui, telemetry, trainer-gbm, trainer-graphsage), dalla
cache quando nulla è cambiato (veloce dopo `spike-prep` e `gnn-image`); poi avvia, secondo le
dipendenze di `infra/docker-compose.yml`:

1. `postgres` (healthy con `pg_isready`); appena è healthy partono anche, in parallelo al reset,
   `telemetry` (amd-smi ogni 5 s in un volume; non dipende da nulla) e `telemetry-sampler` (in
   `telemetry_samples`; dipende solo da postgres e telemetry);
2. `reset` (one-shot: ricrea le tabelle, decide ogni reso aperto, casi d'oro per primi, e fa le
   bozze con Gemma via vLLM, `LLM_CONCURRENCY` chiamate in volo; esce 0, altrimenti l'api non
   parte; esce 1 se vLLM è mancato per qualche bozza, che usa il template etichettato);
3. `api` (healthy quando `/readyz` non dice `not_ready` e scores e models sono `ok`; porta
   `127.0.0.1:8000` solo in loopback);
4. `ui` e `proxy` (Caddy: HTTPS su `PUBLIC_HOST`, porte 80 e 443, basic auth).

I servizi `trainer-gbm`, `trainer-gnn` e `trainer-graphsage` stanno nello stesso profilo `gpu`:
`up` li avvia e i loro comandi di default stampano una riga ed escono; vederli `Exited` in
`docker compose ... ps -a` non è un errore. Se la build di `trainer-graphsage` fallisce su DGL e
numpy 2 (sezione 9.2, passo 6: è il caso in cui `gnn-image` è stato saltato), `make gpu-up` si
ferma lì. Rimedio: avviare solo i servizi dello stack live, per nome (i trainer non vengono né
costruiti né avviati; `docker compose up <servizi>` crea solo quelli e le loro dipendenze):

```bash
docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file .env --profile gpu \
  -f infra/compose.public.yml up -d --build postgres reset api ui proxy telemetry telemetry-sampler
```

vLLM è già healthy dopo `spike-vllm` e `spike-wait`, che `make gpu-up` ha appena eseguito (e
`reset` dipende da lui, quindi `up` lo include senza toccarlo). Comando derivato dai file compose,
non nel runbook: annotarlo. Lo script della sessione lo stampa quando `gnn-image` risulta saltato.
`restart: unless-stopped` per postgres, api, ui, proxy, vllm, telemetry e telemetry-sampler: un
riavvio del droplet li riporta su; il reset no.

`make gpu-reset` (`run --rm reset`) rifà il reset su uno stack acceso: i casi d'oro appena
rigenerati da Gemma (guida al rilascio 4.2). `make gpu-down` ferma tutto tenendo i volumi
(`hf-cache` con i 62 GB di pesi: mai `-v`).

### 11.2 L'URL, l'autenticazione, `/readyz`

- URL: `https://<PUBLIC_HOST>` (il nome sslip.io). Caddy prende il certificato da solo: può
  volerci fino a due minuti (`04-demo-server.sh`, passo 6); `https://<PUBLIC_HOST>/healthz` deve
  rispondere `{"status":"ok"}`.
- Basic auth, utente fisso `admin`, password quella di `make admin-password`: su
  `/api/telemetry` e, perché sul droplet `PUBLIC_READ_ONLY` resta `false`, su **ogni scrittura**
  sotto `/api/` (qualsiasi metodo tranne GET, HEAD, OPTIONS). Il browser chiede la password alla
  prima approvazione; la pagina `/system` è pubblica, i suoi dati no. Un altro nome utente riceve
  401 prima di qualsiasi hash.
- `curl -s https://<PUBLIC_HOST>/readyz` deve mostrare: `"status":"ready"`, `"mode":"live"`,
  `"llm_mode":"vllm"`, `"device":"gpu"`, `"label":null`, `"public_read_only":false`,
  `"contract_version":"1.8.1"`, `clock` con `"kind":"demo"` e `"frozen":false`, e nei `checks`
  `database` `ok`, `scores` `ok`, `models` `ok`, `vllm` `ok` (il probe controlla che
  `GEMMA_MODEL` sia servito), `demo_cache` `disabled`, `telemetry` con un campione recente.
  `"status":"degraded"` con `vllm` `unavailable` vuol dire che l'API serve i template: vLLM è
  giù o non serve `GEMMA_MODEL`.
- `curl -s -o /dev/null -w '%{http_code}' https://<PUBLIC_HOST>/api/telemetry` deve dare `401`.

### 11.3 `make demo-walk` contro lo stack live: la strada scelta

Il runbook dice: `make demo-walk API_URL=http://127.0.0.1:8000` su un host con
`requirements/dev.txt`, dopo un reset (`make gpu-reset`). La guida al rilascio (3.2) scrive
`make demo-walk API_URL=https://<PUBLIC_HOST>` dal portatile con 12 passi `ok`. Ma
`scripts/demo_walk.py` non manda credenziali (solo l'header dell'attore; `--help` offre solo
`--base-url`, `--report`, `--timeout`) e sul droplet il proxy risponde **401** a ogni scrittura
anonima quando `PUBLIC_READ_ONLY` non è `true` (ADR-17, verifica: "an anonymous approval ...
401 at the proxy otherwise"). Quindi, contro `https://<PUBLIC_HOST>`, la camminata fallisce al
primo passo che scrive (`giulia`). **Strada scelta (15 ottobre): la 1, il tunnel SSH**; la
riga della guida al rilascio va letta così. Le altre due restano qui come alternative.

1. **Contro l'API in loopback, come dice il runbook**: `API_URL=http://127.0.0.1:8000` evita il
   proxy. Dal portatile serve un tunnel SSH verso la 8000 del droplet (comando SSH standard, non
   nelle fonti del progetto: `ssh -L 8000:127.0.0.1:8000 root@<droplet-ip>`), poi
   `make gpu-reset` sul droplet e `make demo-walk API_URL=http://127.0.0.1:8000` sul portatile
   (con `requirements/dev.txt` installato: `pip install -r requirements/dev.txt`). È la prova
   di sabato: 12 passi `ok` con le bozze di Gemma, coda sotto i 2 s, checkout sotto 1 s; il
   rapporto in `artifacts/demo-walk.json`. Il droplet stesso non ha il Python del progetto.
2. **In sola lettura contro l'URL pubblico**: con `PUBLIC_READ_ONLY=true` in `.env` e un nuovo
   `make gpu-up`, le scritture arrivano all'API e ricevono 403; la camminata diventa quella di
   sola lettura (nessuna approvazione con Gemma, l'agente risponde). Non è la prova del pitch.
3. Chiedere a Claude un'opzione di basic auth in `scripts/demo_walk.py` (modifica di codice, non
   prevista nelle fonti).

Il browser, invece, passa: chiede la password di `admin` alla prima scrittura. Lo script della
sessione, alla fine della fase `live`, stampa la strada 1.

### 11.4 La pagina della telemetria e amd-smi per il video

- `https://<PUBLIC_HOST>/system`: readiness, versioni, benchmark B1-B4 (da
  `bench/results/latest.json`: `Measured` o `Pending`) e telemetria aggiornata ogni 5 s (utilizzo
  GPU, memoria, potenza, latenza p50/p95 e token/s di vLLM; la latenza API è ancora `null`,
  US-905). I dati chiedono la password di `admin`.
- Sull'host, per l'angolo dello schermo durante le generazioni (spec 16, V1; guida al rilascio
  4.2):

  ```bash
  export PATH="$PATH:/opt/rocm/bin"
  watch -n 2 amd-smi monitor
  ```

  Se `amd-smi` manca sull'host (`host.json`, campo `amd_smi_version`), il container `telemetry`
  lo ha: `docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file .env
  --profile gpu exec telemetry amd-smi monitor` (stesso binario dell'immagine ROCm; comando `docker compose exec` derivato, non nel runbook).

### 11.5 La registrazione (spec 16; guida al rilascio 4.2)

- Sul droplet dallo snapshot: `make gpu-up`, poi `make gpu-reset` per i casi d'oro appena
  rigenerati da Gemma.
- OBS o QuickTime a 1920x1080, browser a 1440x900 su `https://<PUBLIC_HOST>`, un terminale
  `ssh` con `watch -n 2 amd-smi monitor` in un angolo durante le generazioni.
- Scaletta del video (spec 16, al massimo 5 minuti, sotto i 300 MB): 0:00-0:30 il problema e i
  quattro casi; 0:30-3:30 il prodotto vivo sulla MI300X (i quattro momenti, l'agente che
  risponde a una domanda, l'ispezione con una foto, amd-smi nell'angolo); 3:30-4:15
  architettura, ablazione e benchmark; 4:15-5:00 valore, limiti dichiarati, prossimi passi.
- Una seconda registrazione di riserva sul server cached.
- Compressione: `ffmpeg -i registrazione.mov -vf scale=1920:-2 -c:v libx264 -crf 23 -preset slow
  -c:a aac -b:a 128k video.mp4`; link verificato da un browser anonimo.
- Tre prove del pitch cronometrate (3 minuti): schede aperte nell'ordine dei beat; comando di
  reset pronto (`make gpu-reset`); la cached su un secondo URL; il video scaricato sul portatile.

### 11.6 L'avviso dell'orologio della demo

Con le scritture attive l'orologio della demo corre (`DEMO_CLOCK=on`: "now" è l'istante dello
snapshot più il tempo trascorso dal reset). Dopo due giorni dal reset i rimborsi dei resi aperti
risultano scaduti e ogni atto dopo la scadenza risponde 409 (`apps/api/schemas.py`,
`late_without_message`): quindi `make gpu-reset` sabato mattina e domenica mattina prima delle
prove (guida al rilascio 4.3). Sul server cached, in sola lettura, l'orologio è fermo
(`"frozen":true`) e il problema non si pone.

Da mandare a Claude: il JSON di `/readyz` dell'URL pubblico, l'output di `make gpu-up` e di
`make gpu-reset`, `artifacts/demo-walk.json` della camminata (strada 1), e `PUBLIC_HOST` (non la
password né l'hash).

## 12. Cosa riportare sul portatile e come (GPU session 15, 17-19; Steps 14-15; guida 3.3)

### 12.1 I file, con `scp` dal portatile

```bash
scp -r root@<droplet-ip>:ReturnRing/artifacts/spikes ./artifacts/
scp root@<droplet-ip>:"ReturnRing/bench/results/*.json" ./bench/results/
scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/
scp root@<droplet-ip>:"ReturnRing/artifacts/eval/gnn/*.json" ./artifacts/eval/gnn/
scp root@<droplet-ip>:ReturnRing/data/features/customer_embeddings.parquet ./data/features/
scp root@<droplet-ip>:ReturnRing/data/ood/features/customer_embeddings.parquet ./data/ood/features/
scp root@<droplet-ip>:ReturnRing/demo-cache.tar.gz .
```

`$BENCH_RUN` sul portatile va impostato a mano con il valore del droplet. `artifacts/spikes/` e
`artifacts/bench/` sul droplet sono di root: `scp` li copia comunque.

| File | Chi lo legge sul portatile |
| --- | --- |
| `artifacts/spikes/*.json`, `logs/*` | Claude per `docs/spike-results.md` |
| `bench/results/$BENCH_RUN.json`, `latest.json`, `latest-gpu.json` | `make bench-report`; `GET /api/benchmarks` |
| `artifacts/bench/$BENCH_RUN/` | `report.md`, log, campioni amd-smi: prove dei numeri |
| `artifacts/eval/gnn/us-202-asos-{cpu,gpu}.json`, `us-202-embeddings-{main,ood}.json` | `make gnn-results` |
| i due `customer_embeddings.parquet` | `make risk` (riga A4) |
| `demo-cache.tar.gz` | `make demo-import`, la release, il server della demo |

### 12.2 In alternativa: un branch dei risultati spinto dal droplet (guida al rilascio 3.3)

In una connessione **nuova e a parte**, `ssh -A root@<droplet-ip>` (la chiave GitHub è nell'agente
del portatile), **fuori da tmux**: dopo una riconnessione la shell di tmux ha un `SSH_AUTH_SOCK`
che punta al socket della connessione caduta e `git push` risponde `Permission denied (publickey)`
anche se l'agente del portatile ha la chiave (comportamento standard di OpenSSH, non nelle fonti
del progetto). Dentro tmux l'alternativa è `export SSH_AUTH_SOCK=$(ls -t /tmp/ssh-*/agent.* |
head -n 1)`. Un droplet appena creato non ha un'identità git (`Please tell me who you are`): si
passa sul comando, senza scriverla nel disco. Sul droplet, con `BENCH_RUN` esportato:

```bash
git remote set-url origin git@github.com:Saroth85/ReturnRing.git
git checkout -b gpu-session-$(date -u +%Y%m%d)
git add -f artifacts/spikes/*.json artifacts/spikes/logs/*.log docs/spike-results.md \
  bench/results/*.json artifacts/bench/$BENCH_RUN/*.json artifacts/bench/$BENCH_RUN/report.md \
  artifacts/eval/gnn/*.json data/features/customer_embeddings.parquet \
  data/ood/features/customer_embeddings.parquet demo-cache/MANIFEST.json
git -c user.name='<nome>' -c user.email='<email>' commit -m "GPU session results" && git push -u origin HEAD
```

Oppure si salta il push: la 12.1 copia gli stessi file con `scp` dal portatile, senza `-A`.

`git add -f` perché `artifacts/`, `data/*` e `*.parquet` sono in `.gitignore`; i file sono
piccoli. È un branch di trasporto per Claude, non `main`. Mai in quel branch né altrove:
`demo-cache.tar.gz` (va con `scp` e poi come asset della release), i pesi, i log grandi, `.env`.

### 12.3 I passi sul portatile

```bash
make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu     # scrive docs/results/benchmarks-mi300x.md
make risk                                             # riga A4 con gli embedding GPU: legge data/truth (resta qui)
make gnn-results                                      # docs/results/us-202-graphsage.md
```

| Comando | Scrive | Note |
| --- | --- | --- |
| `make bench-report BENCH_RUN=... DEVICE=gpu` | `docs/results/benchmarks-mi300x.md` | `python -m bench report --run bench/results/$BENCH_RUN.json` |
| `make risk` | `artifacts/eval/risk/metrics.json`, `docs/results/us-204-risk.md`, `data/scores/` | valutazione offline, legge `data/truth` (regola 12); esce 1 se un controllo dei casi d'oro fallisce |
| `make gnn-results` | `docs/results/us-202-graphsage.md` | dai file CPU e GPU del droplet (speedup sull'epoca mediana quando le impostazioni coincidono) e la riga A4 |

### 12.4 Che cosa si committa e che cosa mai

Si committano (runbook "The GPU session" e Steps 15): `docs/spike-results.md`,
`bench/results/$BENCH_RUN.json`, `bench/results/latest.json`, `bench/results/latest-gpu.json`,
`docs/results/benchmarks-mi300x.md`, `docs/results/us-202-graphsage.md`,
`docs/results/us-204-risk.md`. Mai: `artifacts/` (record grezzi, log, campioni amd-smi,
modelli), `data/`, `bench-work/`, `demo-cache/`, `demo-cache.tar.gz`, `.env`, `serving/`.

Da mandare a Claude: i file della sezione 12.1 (o il branch della 12.2), `BENCH_RUN`, l'ora di
creazione e di distruzione del droplet, i token/s di `spike-g1-bench` per D-12, e ogni tempo
misurato tra un passo e l'altro.

## 13. Chiusura e riavvio dallo snapshot (Steps 14; guida al rilascio 4.2-4.3)

### 13.1 Chiusura (circa 5 min più lo snapshot)

Prima: i file sono già sul portatile (sezione 12); `HF_TOKEN` in `.env` è di nuovo vuoto se
era servito; nessun token GitHub sul disco; `data/truth/` non c'è. I tre controlli, sul droplet
(sono quelli con cui la fase `down` dello script si rifiuta di proseguire):

```bash
grep -c '^HF_TOKEN=$' .env                                   # deve stampare 1 (vuoto)
test ! -d data/truth && echo 'no data/truth'                 # regola 12
ls ~/.git-credentials ~/.netrc ~/.config/gh/hosts.yml 2>&1   # tutti "No such file"
```

Poi:

```bash
df -h / && docker system df     # disco in uso: lo snapshot è circa questa dimensione
make spike-down                 # tiene i volumi (hf-cache ha i pesi); rimuove i container
shutdown -h now
```

Nel pannello di controllo: fare lo snapshot del droplet, aspettare che compaia nella lista degli
snapshot, **distruggere** il droplet, controllare che sia sparito dalla lista dei Droplets.
Scrivere l'**ora di distruzione** (UTC). Il droplet fattura 1,99 USD/ora finché non è distrutto,
anche mentre lo snapshot viene scritto. Se lo snapshot dovesse durare più del download misurato
in 7.4, distruggere senza snapshot e annotare la decisione (runbook Steps 14): sabato si
rifarebbero `spike-prep` e `spike-weights` (sezione 6) prima di `make gpu-up`.

### 13.2 Che cosa deve contenere lo snapshot

| Contenuto | Perché |
| --- | --- |
| le immagini Docker: `VLLM_IMAGE`, `DGL_IMAGE`, `python:3.12-slim`, `trainer-gbm`, `bench`, `trainer-graphsage`, `returnring-api:local`, `ui`, `telemetry`, postgres, caddy | niente pull né build sabato |
| il volume `hf-cache` | i 62,6 GB di pesi (e i 51,6 del piano B, se scaricati): niente download |
| il volume `vllm-cache` | la cache di torch.compile e dei grafi: niente ricompilazione |
| i volumi `pgdata`, `caddy-data`, `caddy-config`, `telemetry` | database (il reset lo ricrea comunque), certificato, campioni |
| `ReturnRing/` con `.env`, `serving/` (con `llm_cache/`), `data/`, `artifacts/`, `bench/results/`, `demo-cache/` | lo stack riparte senza copie dal portatile |

### 13.3 Che cosa non deve contenere

`HF_TOKEN` valorizzato, un token GitHub, `data/truth/`. `.env` con `VLLM_API_KEY`,
`POSTGRES_PASSWORD` e `ADMIN_PASSWORD_HASH` ci resta: lo snapshot è privato, non si condivide.

### 13.4 Riavvio dallo snapshot (sabato per il video; domenica alle 13:00 per la prova delle 13:30)

1. Nel pannello: creare un droplet **dallo snapshot** (immagine = lo snapshot), stessa taglia
   `gpu-mi300x1-192gb`, stesso firewall, stessa chiave SSH. Scrivere l'ora di creazione.
2. Il droplet ha un **IP nuovo**: `PUBLIC_HOST` cambia. Su di esso:

   ```bash
   ssh root@<droplet-ip-nuovo>
   tmux new -s spikes
   cd ReturnRing
   sed -i "s/^PUBLIC_HOST=.*/PUBLIC_HOST=<ip-nuovo-con-trattini>.sslip.io/" .env
   docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'    # niente deve girare: spike-down ha rimosso i container
   make spike-preflight                                       # host.json di questo droplet; ERROR solo se qualcosa è cambiato
   make gpu-up
   make gpu-reset                                             # casi d'oro rigenerati da Gemma, orologio della demo azzerato
   ```

   Caddy chiede un certificato nuovo per il nuovo nome (fino a due minuti). Il browser chiederà
   di nuovo la password di `admin` alla prima scrittura.
3. Che cosa è conservato: pesi (`hf-cache`), cache di compilazione (`vllm-cache`), immagini,
   `.env`, snapshot di servizio e cache delle bozze, dati, risultati. Il primo avvio di vLLM
   legge un disco freddo (runbook: "a droplet created from a snapshot reads a cold disk"), quindi
   `spike-wait` dura più di quello misurato in 9.2 passo 11: annotarlo se lo si cronometra.
4. Che cosa va rifatto: `tmux`, `PUBLIC_HOST`, `make gpu-up`, `make gpu-reset` (sabato mattina e
   domenica mattina: sezione 11.6), l'eventuale `export BENCH_RUN=...` se si rilancia un passo
   dei benchmark, e `watch -n 2 amd-smi monitor` per il video. Con lo script della sessione:
   `release-logs/gpu/` è nel disco dello snapshot con i segnaposto di venerdì; le fasi `live` e
   `down` eseguono comunque tutti i loro target ogni volta, e `DROPLET_CREATED=<ora UTC del
   droplet nuovo>` alla prima chiamata sostituisce l'ora salvata venerdì (lo script avvisa se
   l'ora salvata precede il boot).
5. A fine giornata: `make spike-down`, `shutdown -h now`, snapshot nuovo (il vecchio si può
   eliminare dopo che il nuovo è nella lista: due snapshot costano il doppio), distruzione,
   controllo nella lista dei Droplets, ora di distruzione. `make spike-down` va fatto anche
   oggi (con lo script: `bash scripts/release/06-gpu-session.sh down`, che non salta mai
   `spike-down` né `disk-usage`): i container hanno `restart: unless-stopped` e uno snapshot
   preso con loro definiti li fa ripartire al boot successivo, con Caddy che chiede un
   certificato per il vecchio nome sslip.io.

Da mandare a Claude: ora di creazione e distruzione di ogni droplet, il nuovo `PUBLIC_HOST`
(serve per il README e per lablab), la dimensione dello snapshot e quanto ha impiegato.

## 14. Tempi e costi

Prezzo 1,99 USD/ora (spec 11). Ogni ora è una stima salvo dove indicato; i blocchi vengono
dalla tabella 3.2 della guida al rilascio e dalla "Timeline" del runbook. Il costo è ore x 1,99,
arrotondato.

| Blocco | Passi | Ore stimate | USD stimati | Che cosa lo fa crescere |
| --- | --- | --- | --- | --- |
| Creazione, accesso, `.env`, foto, preflight | Steps 1-3 (sezioni 2, 3, 5) | 0,2-0,25 | 0,4-0,5 | un `.env` sbagliato, un droplet da ricreare |
| Immagini e avvio del download | Steps 4-5 (sezione 6) | 0,1-0,25 | 0,2-0,5 | Docker Hub a 429, la build di trainer-gbm |
| Metà GPU di G2 e G3 | Steps 6-7 | 0,15-0,2 | 0,3-0,4 | — |
| Fine del download e baseline CPU | Steps 8 | resto del download + 0,1-0,2 | ? + 0,2-0,4 | la velocità di rete verso Hugging Face (62,6 GB), non misurata |
| vLLM, G7, G1, G5, G6, G4 | Steps 9-13 | 0,3-0,6 | 0,6-1,2 | la compilazione di vLLM (non misurata; fino a 15 min prima di passare a `--enforce-eager`), un piano B con altri 51,6 GB |
| Totale spike (budget del runbook) | Steps 1-13 | circa 1,5 | circa 3 | il runbook stesso: "about 90 minutes ... about 3 USD" |
| Benchmark B1-B4, GraphSAGE, A4 | GPU session 3-14 (sezione 9) | 2-4 | 4-8 | B2: la baseline CPU ha preso 92 min su 4 vCPU; qui 20 vCPU, non misurato; `--b2-samples 1` la accorcia |
| Bozze di Gemma e pacchetto | Demo export 2-5 (sezione 10) | 0,5-1 | 1-2 | 623 bozze a 32 in parallelo; un `batch-generate` da rilanciare |
| Stack live e camminata | Demo export 8 (sezione 11) | 0,25 | 0,5 | la build di api e ui, il certificato |
| Chiusura, snapshot, distruzione | Steps 14 (sezione 13) | 0,1 + lo snapshot | 0,2 + ? | la durata dello snapshot, non confermata |
| **Venerdì, totale** | | **circa 4,5-7 ore + download + snapshot** | **circa 9-14 USD + ?** | |
| Sabato: riavvio, `gpu-up`, registrazione, tre prove | sezione 13.4, 11.5 | non nelle fonti (ordine di grandezza: un pomeriggio, 3-5 ore) | 6-10 | ogni ora in cui il droplet esiste, anche in pausa |
| Domenica: 13:00 riavvio, 13:30 prova, 14:00-16:00 demo e pitch, distruzione | spec 2; guida 4.3 | circa 3-3,5 | 6-7 | un ritardo nella distruzione |
| Snapshot | Steps 14 | 170-220 GB x 0,06 USD/GB/mese | 10-13 al mese | due snapshot tenuti insieme |

- Crediti: 100 USD, circa 50 ore. Il piano della spec usa 29 ore più 6 di riserva (circa 15 di
  margine). La somma dei blocchi sopra sta sotto le 20 ore, ma ogni ora non misurata è un'ipotesi:
  la regola è annotare l'ora di creazione e di distruzione di ogni droplet e sommare.
- Il contatore gira anche a droplet spento: il risparmio sta solo nel distruggere la sera e
  ripartire dallo snapshot.
- Il server della demo pubblica (CPU, 2 vCPU, 4 GB, 25 GB di disco; guida al rilascio sezione 2)
  **non è AMD** e non attinge ai crediti: il suo costo non è nelle fonti del progetto, lo
  fattura il suo provider per tutto il tempo in cui resta acceso, e la decisione D-01 (per
  quanto resta attivo l'URL) lo dimensiona.
- Il costo per decisione dei benchmark (cost-v2, `docs/benchmarks.md`) è un'altra cosa: è
  calcolato da B3 e B4 sotto ipotesi dichiarate, e non riguarda il conto di questa sessione.

## 15. Problemi e rimedi

| Sintomo | Causa | Rimedio | Fonte |
| --- | --- | --- | --- |
| `make gnn-image` fallisce al controllo `dgl ... on numpy 2.5.4`, o B2 `failed` con un errore numpy | l'immagine rocm/dgl spedisce numpy 1.26.4 (amd-dgl lo fissa) e i job vogliono numpy 2 | annotarlo; piano B spec 13 (GraphSAGE come upgrade, B2 rinviato); saltare 7, 9, 10 e proseguire con B1 | runbook "What to look at", step 6; `infra/docker/trainer-gnn.Dockerfile` |
| `build-gbm` con pip in errore sull'indice AMD: irraggiungibile, 403, `hash mismatch` | `pypi.amd.com` non risponde o la ruota 3.2.0 è cambiata | non fare debug a tempo pagato: piano B SPK-G2 (XGBoost su CPU); oppure indice, hash e base image insieme alla ROCm 7.1.1. Lo stesso errore in `make bench-build` lascia senza l'immagine `bench`, cioè senza B1, `bench-vllm`, B4, B3 e il merge sul droplet (sezione 9.3): si riparano insieme, o si fanno solo i target di `trainer-graphsage` e il merge di B2 sul portatile | runbook Steps 4; `requirements-gbm-amd.txt`; Makefile `BENCH_RUN_GPU` |
| `build-gbm.log` con `librccl.so.1 => not found` | `ROCM_BASE_IMAGE` non è la `-complete` | ricopiare la riga da `.env.example` | runbook Steps 4 |
| `pull-dgl` o `pull-vllm` con `429 Too Many Requests` | limite di Docker Hub per anonimi | `docker login` con un account proprio, rilanciare `make spike-prep` | runbook Steps 4 |
| `spike-g2` esce 2 (`FAIL: GPU not used`), `spike-g3` esce 2, B1 "GPU not used", B2 esce 2 | la GPU non è visibile nel container | `make spike-gpu-check`: dice quale immagine fallisce; controllare `/dev/kfd`, `/dev/dri`, gruppo `video`; con l'host a posto il problema è l'immagine | runbook Steps 6-7; sezione 7.1 |
| `spike-gpu-check` fallisce in entrambe le immagini | l'host non passa la GPU: driver assente o immagine sbagliata | `make spike-preflight` (`/dev/kfd`, modulo amdgpu); se manca il driver, distruggere e ricreare | runbook Steps 3 |
| `vllm is not running` + log con `Free memory on device ... is less than desired GPU memory utilization` | qualcos'altro tiene memoria GPU: container Quick Start, un trainer in corso | fermarlo (`docker stop <nome>`, `rocm` nelle guide AMD; aspettare la fine del trainer), `make spike-vllm spike-wait` | runbook Steps 9 |
| vLLM non diventa healthy dopo 15 min di compilazione, o errore di compile/graph capture | torch.compile su ROCm | solo sul droplet, `- --enforce-eager` nel `command` di vllm in `infra/docker-compose.yml`, poi `make spike-vllm spike-wait`; annotare: G1 è un limite inferiore | runbook Steps 9 |
| `vllm not healthy after 60 min` | ancora in compilazione o bloccato | `make spike-logs`; se compila ancora, `make spike-wait`; altrimenti eager o 0.30.0 | Makefile `spike-wait` |
| errore di engine o di kernel in vLLM 0.31.0 | regressione dell'immagine | `VLLM_IMAGE` alla 0.30.0 con il digest di runbook Steps 9 (altri 11 GB), `make spike-vllm spike-wait`; il preflight segnalerà la differenza | runbook Steps 9 |
| vLLM: chat template non trovato | percorso relativo | il percorso in `infra/docker-compose.yml` resta assoluto (`/app/vllm/examples/tool_chat_template_gemma4.jinja`) | runbook Steps 9 |
| Gemma 4 31B non parte, o meno di 15 token/s | modello troppo pesante per la configurazione | piano B D-12 (sezione 8) | spec 13, 14 (R-02) |
| `spike-g1`: `finish_reason` `length` | output troncato a 1024 token | alzare `--max-tokens` nel comando di `make -n spike-g1` | runbook Steps 10 |
| `spike-g1`: `FAIL: request without the API key was not refused with 401` | vLLM non applica `--api-key` | annotarlo (NFR-11); controllare `VLLM_API_KEY` in `.env` e il comando del container | `g1_guided_json.py` |
| `spike-g1-bench`: `not all N requests succeeded` | richieste fallite sotto carico | leggere `logs/g1-bench-c*.txt`; rilanciare; se persiste, annotare | Makefile |
| `spike-g6` fallisce in `auto` | il parser `gemma4` non estrae la chiamata | `make spike-g6 G6_MODE=named`; piano B: il codice sceglie lo strumento | runbook Steps 12 |
| `spike-weights-wait`: `exit` non 0 con 401/403 | gating inatteso | `HF_TOKEN` in `.env`, `make spike-weights spike-weights-wait`, togliere il token prima dello snapshot | runbook Steps 8 |
| `weights download not finished after 90 min` | rete lenta o download fermo | `tail artifacts/spikes/logs/download-weights.log`; aspettare o rilanciare `make spike-weights` | `weights.sh` |
| `spike-g2-cpu` o `spike-g3-cpu`: `FAIL: ... has config ...` | la metà GPU è stata prodotta con un'altra configurazione | rilanciare la metà GPU, poi la CPU | `g2_xgboost_gpu.py`, `g3_graphsage_synthetic.py` |
| `amd-smi not found on the host` (G4), `no amd-smi on the host: sampling inside the container` (B4), `amd_smi_version` `not found` nel preflight | amd-smi non è sul PATH dell'host nemmeno in `/opt/rocm/bin` | G4 va avanti senza campioni; B4 campiona dentro il container (annotato nel run); per il video usare `exec telemetry amd-smi monitor` (sezione 11.4) | `g4_coexistence.sh`, Makefile `bench-b4`, `preflight.py` |
| `SPK-G4: FAIL` | OOM o errore HIP con vLLM a 0,55 più i due training | piano B: training a vLLM spento (`make spike-down`, poi i job) | runbook Steps 13 |
| `make batch-generate` esce 2: `the batch needs GEMMA_MODEL on the local vLLM: ...` | vLLM giù, o serve un altro modello (`.env` cambiato senza riavviare vLLM) | `make spike-vllm spike-wait`; `.env` e vLLM devono dire lo stesso `GEMMA_MODEL` | `batch_generate.py` |
| `make batch-generate` esce 2: `the serving snapshot ... cannot be read` | `serving/` mancante o incompleto | `rsync` di `serving/` dal portatile (sezione 4) | `batch_generate.py` |
| `make batch-generate` esce 1 (`failed` non vuoto) | vLLM mancato per qualche reso (timeout 20 s, errore HTTP) | `make spike-logs`; rilanciare: le voci in cache sono saltate; se persiste, `VLLM_TIMEOUT_S` più alto o `LLM_CONCURRENCY` più basso in `.env` | `batch_generate.py`, `settings.py` |
| `demo-export: serving/llm_cache holds no draft` | batch non fatto, o `rsync --delete` dopo il batch | `make batch-generate`; mai `rsync --delete` dopo | `demo_export.py`; sezione 10.4 |
| `demo-export: llm_cache/ holds drafts of <altro modello>` | `GEMMA_MODEL` cambiato dopo il batch, o voci della prova | `make batch-generate` (rigenera le voci di un altro modello) | `demo_export.py` |
| `demo-export: ... DEVICE cpu, not only DEVICE=gpu` | cache copiata dal portatile | cancellare `serving/llm_cache/` sul droplet e rifare il batch | `demo_export.py` |
| `demo-export: the serving snapshot does not match its manifest` | `serving/` ricostruito da una parte sola, o file estranei | ricopiare `serving/` dal portatile (prima del batch) | `demo_export.py`; regola 3 della sezione 0.2 |
| `demo-export: name the GPU the batch ran on` | `DEMO_GPU` mancante o senza `MI300X` | `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer Cloud)"` | Makefile, `demo_export.py` |
| `demo-export: demo-cache is not empty and holds no MANIFEST.json` | cartella sporca | rimuoverla a mano e rilanciare | `demo_export.py` |
| `make demo-walk`: scritture con 401 contro `https://<PUBLIC_HOST>` | il proxy chiede basic auth sulle scritture e la camminata non la manda | sezione 11.3: tunnel verso la 8000 (`API_URL=http://127.0.0.1:8000`), oppure `PUBLIC_READ_ONLY=true` per la camminata in sola lettura | `infra/Caddyfile`, `demo_walk.py`, ADR-17 |
| `make demo-walk`: un passo fallisce con 409 su un'approvazione, casi `refund_overdue` in coda | più di due giorni dal reset: l'orologio della demo ha superato la scadenza del rimborso | `make gpu-reset` (o `make reset`/`make demo-reset`), poi la camminata | `apps/api/settings.py`, `schemas.py`; guida 4.3 |
| `make demo-walk`: `agent` risponde 503 `llm_unavailable` | in modalità cached è atteso e documentato (la camminata lo verifica); sullo stack live vuol dire vLLM giù | sul live: `/readyz` (`vllm` `unavailable`), `make spike-logs`, `make spike-vllm spike-wait` | `demo_walk.py` (H35), `health.py` |
| `/readyz` `"status":"degraded"` con `vllm` `unavailable` | vLLM giù o non serve `GEMMA_MODEL` | `make spike-vllm spike-wait`; confrontare `GEMMA_MODEL` di `.env` con il comando del container | `health.py` |
| `make gpu-up` si ferma: `set PUBLIC_HOST in .env` o `set ADMIN_PASSWORD_HASH in .env` | i due valori obbligatori del profilo pubblico mancano | sezione 3.3 | `infra/compose.public.yml` |
| `make gpu-up` si ferma: `No serving snapshot in serving/` | `serving/manifest.json` assente | `rsync` di `serving/` (sezione 4) | Makefile `serving-check` |
| porta 80, 443, 8000 o 8080 già occupata all'`up` | il container Quick Start (Jupyter o vLLM sull'IP pubblico) o un altro processo | `docker stop <nome> && docker update --restart=no <nome>` (sezione 3.5); `host.json` `listeners` dice chi ascolta | runbook Steps 3; `preflight.py` |
| `https://<PUBLIC_HOST>/healthz` non risponde dopo due minuti | certificato non ancora emesso, porte 80/443 chiuse nel firewall, `PUBLIC_HOST` sbagliato | `docker compose ... logs proxy`; firewall cloud con 80 e 443; `PUBLIC_HOST` uguale all'IP con i trattini | `04-demo-server.sh` passo 6; `.env.example` |
| disco pieno (`only N GB free for Docker`, pull o build falliti per spazio) | meno di 250 GB liberi: immagine o taglia sbagliata, oppure immagini vecchie | al preflight: distruggere e ricreare; dopo: `docker system df`, rimuovere immagini non usate (`bench-work/` si cancella da solo) | `preflight.py`; runbook Steps 3 |
| SSH cade durante un passo | rete | `ssh root@<droplet-ip>`, `tmux attach -t spikes`; il download dei pesi sopravvive (`setsid`); gli altri passi si rilanciano dal punto in cui erano (i benchmark con lo stesso `BENCH_RUN`); per un push dentro quella shell di tmux vedi 12.2 (`SSH_AUTH_SOCK` morto) | runbook Steps 2; `weights.sh` |
| proxy: il browser chiede una password su un'approvazione | basic auth sulle scritture con `PUBLIC_READ_ONLY=false` | inserire `admin` e la password di `make admin-password`; è il comportamento previsto | `infra/Caddyfile` |
| proxy: 401 anche con la password giusta | nome utente diverso da `admin`, o hash incollato senza apici singoli (`$` interpretati), o hash mangiato da `sed`/`echo` | utente `admin`; riscrivere l'hash con la riga Python della sezione 3.3 e controllarlo con il suo `grep` (deve stampare 1), poi `make gpu-up` | `.env.example`, `infra/Caddyfile`; sezione 3.3 |
| preflight: `VLLM_IMAGE differs from .env.example` dopo il piano B 0.30.0 | scelta voluta | atteso: annotare in `docs/spike-results.md` | `preflight.py` |
| B3: `failed` > 0 in un carico | timeout o errori sotto concorrenza | tenere il numero con la nota; `BENCH_ARGS="--b3-timeout ..."` o carichi diversi per una seconda corsa | `docs/benchmarks.md`; runbook step 13 |
| `make gpu-up` fallisce sulla build di `trainer-graphsage` | `up -d --build` costruisce ogni servizio del profilo `gpu` con una sezione `build`, e rocm/dgl rifiuta numpy 2 | avviare solo i servizi dello stack live, per nome (i trainer non vengono né costruiti né avviati): `docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file .env --profile gpu -f infra/compose.public.yml up -d --build postgres reset api ui proxy telemetry telemetry-sampler` (vLLM è già healthy dopo `spike-vllm` e `spike-wait`); comando derivato dai file compose, da annotare (sezione 11.1) | `infra/docker-compose.yml`, Makefile `gpu-up`; sezione 11.1 |
| il droplet continua a fatturare dopo lo spegnimento | `shutdown` non ferma il contatore | distruggere dal pannello e controllare la lista dei Droplets | runbook Steps 14 |

## 16. Checklist finale della sessione

- [ ] 1 Vigilia: `make lint test` verde; `serving/manifest.json`; `make asos-verify`; foto;
      chiave SSH e agente; firewall 22/80/443; hash di admin pronto; crediti visibili
- [ ] 2 Droplet `gpu-mi300x1-192gb` con la Quick Start image con vLLM; ora di creazione scritta; IP scritto
- [ ] 3 `ssh` (senza `-A`), `tmux new -s spikes`, make, rsync e compose presenti, clone sul branch giusto
- [ ] 3 `.env` da `.env.example`: `VLLM_API_KEY`, `POSTGRES_PASSWORD` e `DATABASE_URL`, `PUBLIC_HOST`, `ADMIN_PASSWORD_HASH` scritto con la riga Python (il `grep` stampa 1); `chmod 600`
- [ ] 3 Foto in `artifacts/spikes/garment.jpg`; container Quick Start (`rocm` o altro nome) annotato e fermato
- [ ] 4 `data/raw/asos_*.parquet`, `data/store`, `data/features`, `data/ood`, `serving/` copiati; `data/truth` no
- [ ] 5 `make spike-preflight` senza `ERROR`; `host.json` scritto
- [ ] 6 `make spike-prep`: tre righe `exit 0`; `make spike-weights` avviato
- [ ] 7 `spike-gpu-check` (`gfx942`, `gpu True`); `spike-g2` e `spike-g2-load` `pass`; `spike-g3` `pass`
- [ ] 7 `spike-weights-wait` `exit 0` con il tempo annotato; `spike-g2-cpu` e `spike-g3-cpu` con gli speedup
- [ ] 7 `spike-vllm`, `spike-wait` healthy, `spike-g7` con le righe della cache KV
- [ ] 7 `spike-g1` `pass=True`; `spike-g1-bench` con 16 e 64 richieste riuscite
- [ ] 8 D-12 decisa dai token/s (se piano B: `.env` cambiato, download, vLLM e G1 ripetuti)
- [ ] 7 `spike-g5` checklist valida; `spike-g6` `pass` (auto o named); `spike-g4` `SPK-G4: pass`
- [ ] 9 `make spike-down`; `export BENCH_RUN=...` annotato
- [ ] 9 `bench-build`, `gnn-image`, `gnn-test-gpu`, `bench-b1`, `bench-b2`, `embeddings-gpu` (vLLM giù)
- [ ] 9 `bench-vllm` (`first-request.json` ok), `bench-b4`, `bench-b3` (`failed` 0), `bench-merge` (`latest-gpu.json`)
- [ ] 10 `serving/` è quello del portatile; `spike-vllm spike-wait`; `make batch-generate` esce 0
- [ ] 10 `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer Cloud)"`: `MANIFEST.json` con `"rehearsal": false`
- [ ] 10 `demo-cache.tar.gz` sul portatile, `sha256sum` annotato, `make demo-import demo-local` verde
- [ ] 11 `make gpu-up`; `https://<PUBLIC_HOST>/readyz` `ready`, `live`, `vllm`, `gpu`; `/api/telemetry` 401
- [ ] 11 `make gpu-reset`; camminata contro la 8000 (tunnel) con 12 passi `ok`; `/system` mostra i benchmark `Measured`
- [ ] 12 File copiati sul portatile (o branch `gpu-session-<data>` spinto); `BENCH_RUN` comunicato
- [ ] 13 `HF_TOKEN` vuoto, niente `data/truth`, nessun file di credenziali git; `make spike-down`; `shutdown -h now`; snapshot nella lista; droplet distrutto; ora di distruzione scritta
- [ ] 12 Sul portatile: `make bench-report`, `make risk`, `make gnn-results`; solo i file della 12.4 committati
- [ ] 10 Server della demo: `make demo-down && make demo-import demo-up`; etichetta MI300X; release con sha256
- [ ] 13 Sabato e domenica: droplet dallo snapshot, `PUBLIC_HOST` nuovo, `make gpu-up`, `make gpu-reset`, distruzione la sera

## 17. Valori da fornire

| Segnaposto o valore | Chi lo fornisce | Quando | Dove si usa |
| --- | --- | --- | --- |
| `<branch>` | Claude (oggi `main` a `432e096`, o il branch che Claude indica) | la vigilia | sezioni 1.1, 3.2 |
| `<regione>` del droplet | `ATL1` secondo il tutorial lablab; chi fa la sessione conferma dal pannello | alla creazione | sezione 2 |
| `<nome-droplet>` | chi fa la sessione | alla creazione | sezione 2 |
| `<droplet-ip>` | il pannello, alla creazione | alla creazione | ovunque: `ssh`, `scp`, `rsync` |
| `<ip-con-trattini>` e `PUBLIC_HOST` | derivati dall'IP (`203.0.113.10` -> `203-0-113-10.sslip.io`) | dopo la creazione; di nuovo a ogni riavvio dallo snapshot (IP nuovo) | sezioni 3.3, 11, 13.4 |
| `VLLM_API_KEY` | `openssl rand -hex 24` sul droplet | sezione 3.3 | `.env` |
| `POSTGRES_PASSWORD` (e `DATABASE_URL`) | `openssl rand -hex 16` sul droplet | sezione 3.3 | `.env` |
| password di `admin` e `ADMIN_PASSWORD_HASH` | chi fa la sessione sceglie la password; `make admin-password` sul portatile dà l'hash; nessuno dei due va a Claude | la vigilia | `.env`, browser, `/api/telemetry` |
| `garment.jpg` (o `<file>` per `IMAGE=`) | chi fa la sessione | la vigilia | sezione 3.4, 7.8 |
| nome della chiave SSH nel pannello | chi fa la sessione | la vigilia | sezione 2 |
| account Docker Hub | chi fa la sessione, solo se `429` | sezione 6.1 | `docker login` |
| `HF_TOKEN` | chi fa la sessione, solo se il download risponde 401/403 (non atteso) | sezione 7.4 | `.env`, da svuotare prima dello snapshot |
| `GEMMA_MODEL` e `GEMMA_REVISION` del piano B | Claude conferma i valori da incollare (`.env.example` fissa `google/gemma-4-26B-A4B-it` a `4d7ae4984b7db7de8f8457170b3f1a419ee76d52`, 13 ott 2026, mai scaricati) | durante SPK-G1, se D-12 va al piano B | sezione 8.2 |
| `$BENCH_RUN` | `date -u +%Y%m%dT%H%M%SZ`-gpu sul droplet, poi riportato a mano sul portatile | GPU session 4 | sezioni 9, 12 |
| ora di creazione e di distruzione di ogni droplet (UTC) | chi fa la sessione | ogni volta | `docs/spike-results.md`, sezione 14 |
| nome e dimensione dello snapshot, minuti impiegati; oppure il motivo per cui si è saltato | chi fa la sessione | Steps 14 | `docs/spike-results.md` |
| `<ip-server-demo>` e il `PUBLIC_HOST` del server della demo | `scripts/release/04-demo-server.sh` lo stampa (guida al rilascio, sezione 2) | prima di sabato | sezione 10.6 |
| tag e nome della release GitHub con `demo-cache.tar.gz` e lo sha256 | chi fa la sessione con Claude | sabato | sezione 10.5 |
| i tempi misurati: `took N s` di `spike-prep`, il download, vLLM start-to-healthy, i blocchi della sezione 14 | chi fa la sessione | durante | `docs/spike-results.md`, sezione 14 |
| se gli snapshot funzionano per i droplet GPU di AMD Developer Cloud e quanto durano | si scopre alla prima chiusura | Steps 14 | sezione 13 |

Risolti il 15 ottobre, non più da fornire: la camminata contro lo stack live passa dal tunnel SSH
verso la 8000 (sezione 11.3, strada 1); se `make gpu-up` fallisce sulla build di
`trainer-graphsage`, lo stack live si avvia per nome senza i trainer (sezioni 11.1 e 15).
