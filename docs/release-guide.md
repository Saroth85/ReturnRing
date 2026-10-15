# Guida al rilascio: cosa serve per rilasciare e provare tutto

Scritta il 15 ottobre 2026 per la consegna di ACT III (domenica 18 ottobre, 12:00; invio entro
le 11:00). In italiano perché è una guida operativa per il team, come `docs/spec.md`. I comandi
sono quelli del `Makefile`, del README e del runbook GPU `infra/spikes/README.md`: qui sono
ordinati per giorno, con ciò che ci si aspetta da ciascuno e ciò che va mandato a Claude dopo ogni
passo. La spec resta la fonte: sezione 2 (vincoli e consegna), 11 (deploy), 16 (demo, video,
pitch), 17 (pacchetto di consegna e checklist).

Tre regole che valgono per tutto:

- **Nessun segreto nel repo.** `.env` non si committa mai (gitleaks gira in CI). Sul droplet non
  va nessun token GitHub: finisce nello snapshot. Per spingere dal droplet si usa `ssh -A` con la
  chiave dell'agente del portatile.
- **`data/truth/` non lascia mai il portatile** (regola 12). Sul droplet si copiano solo
  `data/raw/asos_*.parquet`, `data/store/`, `data/features/`, `data/ood/` e `serving/`.
- **Lo snapshot `serving/` si costruisce una volta sola sul portatile** e si copia: la cache delle
  bozze di Gemma è legata al caso, quindi un `serving/` ricostruito da una parte sola rende il
  pacchetto della demo inutile.

## 0. Stato di partenza (15 ottobre, 07:30)

- `main` a `33c854d`: contratto API 1.8.1, UI sull'API vera, CI verde (test, ui, gnn-cpu, secrets).
- 50 user story: 22 fatte, 11 in parte, 2 tagliate. Le 11 "in parte" aspettano tutte la MI300X,
  Gemma su vLLM (mai eseguito) o le slide e il video (`docs/spec.md`, sezione 5).
- Mai eseguito finora: Docker con un daemon vero (`make demo-data`, `make dev-up`, le immagini);
  gli spike ROCm SPK-G1..G7; i benchmark B1-B4 su GPU; `make batch-generate` e il pacchetto della
  demo con Gemma vera. Tutto il resto è stato provato sull'host con SQLite, uvicorn e il mock.
- Cosa manca ancora per lablab: URL della demo, video, slide, cover, tag; i testi sono in bozza
  (spec 17) e vanno aggiornati con i numeri misurati.

## 1. Stasera (giovedì 15): il portatile con Docker

Serve: Docker con Compose v2, GNU make, circa 8 GB di RAM liberi, Python 3.12 (per
`make demo-walk`), una rete che raggiunge Docker Hub. Su Windows tutto gira dentro WSL2 (Ubuntu)
con l'integrazione WSL di Docker Desktop.

### 1.0 Gli script

I passi 1.1 e 1.2 sono automatizzati in `scripts/release/`: ogni script scrive l'output di ogni
comando in `release-logs/` e alla fine impacchetta tutto in `release-logs-<data>.tgz`, il file da
mandare a Claude qualunque sia l'esito.

```bash
git clone https://github.com/Saroth85/ReturnRing.git && cd ReturnRing
bash scripts/release/01-check-prereqs.sh        # cosa manca sul portatile (non installa nulla)
bash scripts/release/02-laptop-docker-check.sh  # 1.1: demo-data, dev-up, reset, demo-walk
bash scripts/release/03-cached-rehearsal.sh     # 1.2: pacchetto di prova, demo-local, demo-walk
bash scripts/release/05-collect.sh              # solo se serve: raccoglie stato e log
```

`04-demo-server.sh` è per il server della demo pubblica (sezione 2). Gli script sono idempotenti:
rilanciati, ripartono dal `.env` e dai dati già presenti.

### 1.1 Il percorso del giudice (V8, DEP-01): circa 30-40 minuti

```bash
git clone https://github.com/Saroth85/ReturnRing.git && cd ReturnRing
cp .env.example .env
make demo-data        # build dell'immagine api + pipeline nel container: circa 10 min più la build
make dev-up           # postgres, reset (623 resi decisi), api, ui, proxy
```

Atteso:

- `make demo-data` finisce con il manifest dello snapshot (`serving/manifest.json`) e scrive
  `data/`, `artifacts/`, `serving/` come utente del portatile, non root.
- `make dev-up` stampa `ReturnRing (CPU, mock LLM): http://localhost:8080`.
- `curl -s http://127.0.0.1:8000/readyz` risponde `"status":"ready"`, `"mode":"live"`,
  `"llm_mode":"mock"`, `"contract_version":"1.8.1"`, `"public_read_only":false` e un `clock`
  con `"kind":"demo"`, `"frozen":false`.
- Nel browser su http://localhost:8080: "Acting as" sara, luca, ahmed; la coda ha 14 righe per
  pagina; i casi Giulia, Marco, Rossi ed Elena si aprono dalla pagina Demo.

Poi la camminata del pitch via HTTP (approva Marco, quindi prima un reset):

```bash
pip install -r requirements/dev.txt
make reset && make demo-walk
```

Atteso: 12 passi `ok` (readiness, demo_cases, queue, giulia, marco, checkout, rossi, elena,
insufficient, dispute, agent, manager), prima pagina della coda sotto i 2 s, checkout sotto 1 s;
il rapporto è in `artifacts/demo-walk.json`.

Da mandare a Claude: l'output completo del terminale dei quattro comandi (anche se tutto passa),
`artifacts/demo-walk.json`, e in caso di errore `make dev-logs` (le ultime 100 righe di ogni
servizio) e `docker compose -f infra/docker-compose.yml --env-file .env --profile dev ps`.

### 1.2 La demo cached in prova sul portatile: circa 15 minuti

Prova il percorso della demo pubblica senza GPU, con un pacchetto dichiarato prova (etichetta
"Precomputed in a rehearsal, not verified on AMD MI300X"):

```bash
make demo-export ALLOW_EMPTY_CACHE=1 DEMO_GPU="CPU rehearsal (laptop)"   # demo-cache.tar.gz
make dev-down
make demo-import demo-local                                             # http://localhost:8080
make demo-reset && make demo-walk
```

Atteso: `demo-cache.tar.gz` con `MANIFEST.json` (`"rehearsal": true`); `/readyz` con
`"mode":"cached"`, `"public_read_only":true`, la `label` della prova e il `clock` fermo
(`"frozen":true`); la camminata legge gli stessi casi, le sette scritture del pitch ricevono 403,
il checkout simulato risponde, l'assistente risponde 503 come documentato.

Da mandare a Claude: l'output dei comandi e `demo-cache/MANIFEST.json`.

### 1.3 Facoltativo stasera: la suite completa

`make test` (circa 15 minuti, 5.874 test) e `make lint`. In CI sono già verdi sullo stesso
commit: serve solo se il portatile ha un Python diverso da 3.12.

## 2. Il server della demo pubblica (CPU): stasera o venerdì mattina

È l'URL che va su lablab e resta acceso dopo l'evento. Si mette in piedi **prima** della sessione
GPU con il pacchetto di prova di 1.2, così sabato cambia solo il pacchetto.

Serve: un droplet CPU (2 vCPU, 4 GB di RAM bastano: l'API tiene circa 1,6-2,2 GB; 25 GB di disco),
Ubuntu 24.04 con Docker e Compose v2 e make, un firewall con 22, 80 e 443 in ingresso, la chiave
SSH del portatile. Nessun dominio: il nome è `<ip-con-trattini>.sslip.io`, per esempio
`203-0-113-10.sslip.io` per 203.0.113.10; Caddy prende il certificato da solo.

Lo script `scripts/release/04-demo-server.sh` fa tutto da solo sul server, come root: installa
Docker, clona il repo, scrive `.env` (nome sslip.io dall'IP, password PostgreSQL casuale, hash
della password di admin a costo 10), aspetta il pacchetto, poi `make demo-import demo-up` e i
controlli HTTPS. Sul server:

```bash
curl -fsSL https://raw.githubusercontent.com/Saroth85/ReturnRing/main/scripts/release/04-demo-server.sh -o 04-demo-server.sh
bash 04-demo-server.sh            # chiede la password di admin; oppure ADMIN_PASSWORD='...' bash 04-demo-server.sh
```

Quando si ferma su "waiting", dal portatile: `scp demo-cache.tar.gz root@<ip>:ReturnRing/` (il
pacchetto di prova di 1.2; sabato quello della MI300X). A mano, gli stessi passi sono:
`cp .env.example .env`, in `.env` `POSTGRES_PASSWORD` (anche dentro `DATABASE_URL`),
`PUBLIC_HOST=<ip-con-trattini>.sslip.io`, `ADMIN_PASSWORD_HASH='$2a$10$...'` tra apici singoli
(`make admin-password` sul portatile lo stampa), poi `make demo-import demo-up`.

Atteso, da un browser o da un terminale fuori dal server (anche dal telefono, rete mobile):

- `https://<PUBLIC_HOST>/healthz` risponde `{"status":"ok"}`; `https://<PUBLIC_HOST>/readyz`
  mostra `cached`, `public_read_only` vero e, per ora, l'etichetta della prova.
- La UI si apre, i casi della demo si aprono, ogni pulsante di scrittura è disattivato con
  l'avviso di sola lettura; `/api/telemetry` chiede la password di admin.

Da mandare a Claude: `PUBLIC_HOST`, l'output di `make demo-up`, il JSON di `/readyz`. La password
di admin e l'hash no.

## 3. Venerdì 16: la sessione sulla MI300X

Il runbook è `infra/spikes/README.md`: "Steps" 1-15 (gli spike), "The GPU session" 1-19
(benchmark e GraphSAGE) e "The demo export" 1-8 (bozze di Gemma, pacchetto, stack live). Qui solo
l'ordine, i tempi, i punti di controllo e cosa mandare. La fatturazione parte alla creazione del
droplet e si ferma alla distruzione: tutto in una sessione, in `tmux`.

### 3.1 Prima, sul portatile (senza costo)

1. `data/` costruito da `make demo-data` (1.1) e il dataset ASOS per i benchmark B1 e B2:
   `make asos` (scarica e verifica gli sha256; circa 770 MB in `data/raw/`).
2. Una foto di un capo reso per SPK-G5: JPEG o PNG sotto 2 MB, nessuna persona, nessun dato
   personale sulle etichette.
3. Decisioni da prendere prima: **D-13** (da solo o in team) e **D-07** (formato del pitch) non
   toccano la sessione; **D-12** (Gemma 4 31B o 26B-A4B) si decide durante SPK-G1 con i token al
   secondo: sotto i 15 token/s in flusso singolo si passa al piano B. `.env.example` punta al
   31B; se serve il 26B-A4B, Claude manda `GEMMA_MODEL` e `GEMMA_REVISION` da incollare.
4. Il droplet: AMD Developer Cloud, taglia `gpu-mi300x1-192gb`, immagine Quick Start con vLLM,
   firewall con 22 (e 80, 443 per `make gpu-up`), la chiave SSH del portatile. Segnarsi l'ora di
   creazione.

### 3.2 Sul droplet, in ordine

| Blocco | Passi del runbook | Tempo stimato | Punto di controllo |
| --- | --- | --- | --- |
| Setup | Steps 1-3 (`git clone`, `.env` da `.env.example` con `VLLM_API_KEY` casuale, `PUBLIC_HOST`, `ADMIN_PASSWORD_HASH`; foto; `make spike-preflight`) | 10 min | nessuna riga `ERROR`; `artifacts/spikes/host.json` scritto; il container Quick Start `rocm` fermato |
| Immagini e pesi | Steps 4-5 (`make spike-prep`, `make spike-weights`) | 5-15 min + download di 62,6 GB in background | tre righe `exit 0`; la build di trainer-gbm non fallisce sull'indice AMD |
| GPU: XGBoost e DGL | Steps 6-7 (`make spike-gpu-check`, `spike-g2`, `spike-g2-load`, `spike-g3`) | 10 min | `use_hip: true`, `gfx942`; DGL con `gpu_visible: true` |
| CPU baseline | Step 8 (`make spike-weights-wait`, `spike-g2-cpu`, `spike-g3-cpu`) | fine download + 10 min | `download-weights: exit 0` |
| vLLM | Steps 9-13 (`spike-vllm`, `spike-wait`, `spike-g7`, `spike-g1`, `spike-g1-bench`, `spike-g5`, `spike-g6`, `spike-g4`) | 20-35 min | `spike-g1`: `pass=True`, JSON valido; **token/s misurati: decidere D-12 qui**; G5 checklist dalla foto; G6 chiamata di strumento; `SPK-G4: pass` |
| Benchmark e GraphSAGE | "The GPU session" 3-14 (`spike-down`, `BENCH_RUN`, `bench-build`, `gnn-image`, `gnn-test-gpu`, `bench-b1`, `bench-b2`, `embeddings-gpu`, `bench-vllm`, `bench-b4`, `bench-b3`, `bench-merge`) | 2-4 h (B2 è il più lungo: la baseline CPU ha preso 92 min su 4 vCPU; sul droplet ci sono 20) | ogni parte `measured`; `failed` 0 in B3; `bench/results/latest-gpu.json` scritto |
| Bozze e pacchetto | "The demo export" 2-5 (`rsync` di `serving/` dal portatile, `spike-vllm spike-wait`, `make batch-generate`, `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer Cloud)"`) | 30-60 min (623 bozze, 32 chiamate in parallelo) | `batch-generate` esce 0; `demo-cache/MANIFEST.json` con `"rehearsal": false` e il modello di ogni bozza |
| Stack live | "The demo export" 8 (`make gpu-up`) e `make demo-walk API_URL=https://<PUBLIC_HOST>` dal portatile | 15 min | 12 passi `ok` con le bozze di Gemma; è la prova di sabato |
| Chiusura | Step 14 (`make spike-down`, copia, `shutdown -h now`, snapshot, distruzione) | 5 min + snapshot | il droplet non esiste più; lo snapshot sì |

Se `B2` o `gnn-image` falliscono su DGL e numpy 2, il piano B della spec 13 è GraphSAGE come
upgrade e B2 rimandato: si va avanti con B1. Se vLLM non parte con il 31B, piano B D-12. Se
l'indice AMD rifiuta la ruota di `amd_xgboost`, XGBoost resta su CPU (SPK-G2 piano B). Ogni
fallimento si scrive in `docs/spike-results.md` com'è (regola 13).

### 3.3 Cosa mandare a Claude dalla sessione

Il modo più semplice: dal droplet, con `ssh -A` e il remote SSH, un branch con i soli risultati
(i file sono piccoli; `git add -f` perché `artifacts/` e `*.parquet` sono ignorati):

```bash
git remote set-url origin git@github.com:Saroth85/ReturnRing.git
git checkout -b gpu-session-$(date -u +%Y%m%d)
git add -f artifacts/spikes/*.json artifacts/spikes/logs/*.log docs/spike-results.md \
  bench/results/*.json artifacts/bench/$BENCH_RUN/*.json artifacts/bench/$BENCH_RUN/report.md \
  artifacts/eval/gnn/*.json data/features/customer_embeddings.parquet \
  data/ood/features/customer_embeddings.parquet demo-cache/MANIFEST.json
git commit -m "GPU session results" && git push -u origin HEAD
```

Oppure gli stessi file con `scp` sul portatile e il push da lì. Con quei file Claude scrive
`docs/results/benchmarks-mi300x.md`, la riga A4 di `docs/results/us-204-risk.md`,
`docs/results/us-202-graphsage.md`, `docs/spike-results.md`, aggiorna gli stati della spec e i
testi di consegna con i numeri misurati. **Non** nel repo: `demo-cache.tar.gz` (va con `scp` sul
portatile e poi come asset della release su GitHub), i pesi, i log grandi.

Da mandare anche: l'ora di creazione e di distruzione del droplet (le ore GPU usate, spec 11), e
per D-12 i token al secondo di `spike-g1-bench`.

## 4. Sabato 17 (on-site a Milano): pacchetto definitivo, video, prove

### 4.1 Il pacchetto vero sul server della demo

```bash
# sul server della demo, con il demo-cache.tar.gz della MI300X copiato in ReturnRing/
make demo-down && make demo-import demo-up
```

Atteso: `/readyz` con `"label":"Precomputed on AMD MI300X"` e, nella UI, l'etichetta su ogni
schermo; `make demo-reset` poi `make demo-walk API_URL=https://<PUBLIC_HOST>` dal portatile:
12 passi `ok`, scritture 403. Poi la release su GitHub: `demo-cache.tar.gz` come asset con il suo
sha256 (`sha256sum demo-cache.tar.gz`); Claude aggiunge il link alla sezione del README.

### 4.2 Il video (al massimo 5 minuti, sotto i 300 MB)

Sul droplet, dallo snapshot: `make gpu-up`, poi `make gpu-reset` per i casi d'oro appena
rigenerati da Gemma. Registrazione con OBS o QuickTime a 1920×1080, browser a 1440×900 su
`https://<PUBLIC_HOST>`, un terminale `ssh` con `watch -n 2 amd-smi monitor` in un angolo durante
le generazioni (V1). Scaletta: spec 16, "Video". Una seconda registrazione di riserva sul server
cached. Per restare sotto i 300 MB:

```bash
ffmpeg -i registrazione.mov -vf scale=1920:-2 -c:v libx264 -crf 23 -preset slow -c:a aac -b:a 128k video.mp4
```

Il link del video va verificato da un browser anonimo prima di inserirlo.

### 4.3 Le prove del pitch

Tre prove cronometrate (3 minuti, spec 16): schede del browser aperte nell'ordine dei beat sul
server GPU; la demo cached su un secondo URL; il video scaricato sul portatile per il caso senza
rete. Comando di reset pronto: `make gpu-reset` (GPU) o `make demo-reset` (cached). Ricordare che
con le scritture attive l'orologio della demo corre: dopo due giorni dal reset i rimborsi
risultano scaduti, quindi reset sabato mattina e domenica mattina.

Spegnere il droplet GPU la sera (`make spike-down`, snapshot) e riaccenderlo domenica alle 13:00
per la prova delle 13:30 (lo snapshot evita il download dei pesi).

## 5. Domenica 18: la consegna su lablab entro le 11:00

| Campo di lablab | Da dove | Chi |
| --- | --- | --- |
| Titolo (≤ 50 caratteri) | spec 17, aggiornato | Claude prepara, tu incolli |
| Descrizione breve (≤ 255) e lunga (≥ 100 parole) | spec 17 con i numeri misurati venerdì | Claude prepara, tu incolli |
| Track e tag | Track 3; tag dal catalogo lablab: AMD Developer Cloud, AMD ROCm, AMD Instinct, vLLM, Gemma, XGBoost, DGL, FastAPI, React, PostgreSQL | tu |
| Cover 16:9 | la matrice rischio × valore con Giulia e il cluster di Marco (Claude la produce dalla UI) | Claude produce, tu carichi |
| Video (≤ 5 min, < 300 MB) | 4.2 | tu |
| Slide | PDF delle 10 slide (spec 16); Claude le prepara con i numeri misurati | Claude produce, tu carichi |
| Repository | https://github.com/Saroth85/ReturnRing (pubblico, MIT, README, attribuzione ASOS CC BY 4.0, Claude Code dichiarato) | fatto |
| Demo | piattaforma di hosting e `https://<PUBLIC_HOST>` | tu |
| Additional Information | scalabilità, limiti dichiarati (dati sintetici per i ring, legale da validare, FAIR-02), tempo di decisione | Claude prepara |

Controlli finali prima dell'invio: CI verde sull'ultimo commit di `main` (gitleaks, licenze);
`https://<PUBLIC_HOST>/healthz` verde da un browser anonimo; ogni numero in slide, video e testi
con origine e natura (US-906); invio entro le 11:00.

## 6. Cosa produce Claude, e quando

| Cosa | Quando | Serve |
| --- | --- | --- |
| Questa guida, la checklist | ora | — |
| 10 slide (PDF), script del pitch con i tempi, scaletta e inquadrature del video, cover 16:9 | dopo il via, prima di venerdì | nessun dato nuovo: i numeri CPU sono in `docs/results/`; i numeri GPU entrano sabato mattina |
| Testi di consegna aggiornati, Additional Information | sabato mattina | i risultati della sessione GPU (3.3) |
| `docs/results/benchmarks-mi300x.md`, A4, GraphSAGE, `docs/spike-results.md`, stati della spec | sabato mattina | i file di 3.3 |
| README: URL della demo, link all'asset della release, numeri MI300X | sabato | `PUBLIC_HOST`, la release |
| Correzioni a qualunque errore emerso in 1, 2, 3 | appena arriva l'output | l'output completo |

## 7. Decisioni ancora aperte (spec 18)

| ID | Decisione | Quando serve |
| --- | --- | --- |
| D-12 | Gemma 4 31B o 26B-A4B | venerdì, durante SPK-G1 |
| D-13 | Da solo o in team su lablab | prima della registrazione del team |
| D-07 | Formato del pitch: portatile, proiettore, rete | sabato mattina |
| D-14 | Validazione legale informale delle leve e della garanzia | da citare nel pitch se c'è |
| D-09 | Una o due interviste a responsabili resi | da citare nel pitch se ci sono |
| D-01 | Per quanto resta acceso l'URL della demo | dimensiona il server CPU |
| FAIR-02 | Non superato su una categoria (6,78 punti): dichiarato come limite misurato, o lavorato prima della consegna | venerdì |
| GitHub | branch di default su `main`; eliminazione di `claude/nice-gates-ckg76q` (identico a `main`) | quando vuoi |

## 8. Checklist

- [ ] 1.1 `make demo-data`, `make dev-up`, `make reset && make demo-walk` sul portatile: 12 passi ok
- [ ] 1.2 Pacchetto di prova e `make demo-local`: etichetta della prova, scritture 403
- [ ] 2 Server CPU con HTTPS su `<PUBLIC_HOST>`, `/healthz` verde da fuori, pacchetto di prova
- [ ] 3.1 `make asos` sul portatile; foto per SPK-G5; droplet MI300X creato
- [ ] 3.2 Spike SPK-G1..G7 registrati in `docs/spike-results.md`; D-12 deciso
- [ ] 3.2 B1-B4 misurati, GraphSAGE su GPU, embedding A4
- [ ] 3.2 `make batch-generate` e `demo-cache.tar.gz` con `"rehearsal": false`
- [ ] 3.2 `make gpu-up` e `make demo-walk` contro lo stack live: 12 passi ok
- [ ] 3.3 Risultati spinti su un branch; droplet distrutto, snapshot salvato
- [ ] 4.1 Pacchetto vero sul server: etichetta MI300X; release GitHub con sha256
- [ ] 4.2 Video registrato, compresso, link verificato in anonimo; riserva in cached
- [ ] 4.3 Tre prove del pitch cronometrate
- [ ] 5 Titolo, descrizioni, tag, cover, video, slide, repo, URL, Additional Information su lablab
- [ ] 5 CI verde sull'ultimo commit; invio entro le 11:00; prova finale alle 13:30 con la GPU accesa
