# Runbook AMD senza Docker: la sessione su un host AMD con solo Python, pip, una GPU ROCm e un terminale

Scritto il 16 ottobre 2026 come compagno senza Docker di `docs/amd-runbook.md` (il runbook del
droplet MI300X di AMD Developer Cloud, che resta la fonte per ogni passo che qui non cambia). In
italiano perché è un documento operativo per il team; comandi, nomi di file, variabili e righe
attese restano in inglese, come compaiono nelle fonti. Le fonti, nell'ordine in cui vincolano
questo testo: `docs/amd-runbook.md`, `infra/spikes/README.md`, il `Makefile`, `.env.example`,
`infra/docker-compose.yml`, `infra/compose.bench.yml`, `infra/docker/bench.Dockerfile`,
`infra/docker/requirements-gbm-amd.txt`, `infra/docker/requirements-bench.txt`,
`infra/spikes/*.py` e `weights.sh`, `bench/`, `docs/benchmarks.md`, `scripts/demo_export.py`,
`scripts/demo_walk.py`, `apps/api/batch_generate.py`, `apps/api/reset.py`,
`apps/api/services/demo_package.py`, `apps/ui/README.md`, `docs/release-guide.md`, `docs/spec.md`
(sezioni 11, 13, 16), `docs/results/us-202-graphsage.md`.

Convenzioni:

- **Tempi e costi**: ogni durata e ogni cifra è una stima salvo dove è scritto "misurato" con
  la fonte (regola 13). Quello che è stato misurato per questo documento, il 16 ottobre su un
  host CPU senza GPU e senza Docker (4 vCPU condivise): `python -m apps.api.reset` su SQLite con
  il mock ha deciso i 623 resi aperti in 1 min 32 s; `python -m scripts.demo_walk` contro
  `uvicorn` sullo stesso database ha passato i 12 passi in 43 s. Tutto il resto, GPU compresa,
  non è stato misurato.
- **Dove si lancia**: "host" è la macchina AMD (il notebook o la VM); "portatile" è la macchina
  del team con Docker, Node 22 e Python 3.12; "server demo" è il piccolo server CPU della demo
  pubblica (guida al rilascio, sezione 2), che riceve solo il pacchetto.
- **Comandi**: niente `make` sull'host, perché quasi tutti i target lanciano `docker compose`. Ogni
  comando qui viene dal repository (il comando che un target `make` lancerebbe dentro il
  container, letto con `make -n <target>` o dal file compose) o dal `--help` dello strumento,
  provato il 16 ottobre nel venv del progetto. Quello che non viene da lì è segnato **da
  verificare sull'host**: lo si prova, e l'esito va annotato.
- **Due ambienti Python** sull'host (sezione 3): `.venv` con `requirements/dev.txt` (API, reset,
  batch, camminata, pacchetto, spike G1/G5/G6, la metà CPU di G2) e `.venv-bench` con la ricetta
  dell'immagine `bench` (`amd_xgboost` dall'indice AMD più `requirements-bench.txt`: G2 su GPU,
  B1, B4, B3, merge e report). Non si mescolano: `xgboost-cpu` e `amd_xgboost` spediscono lo
  stesso modulo `xgboost` e uno sovrascrive l'altro (commento di `requirements-bench.txt`).
- **Segnaposto**: un valore che nessuno conosce ancora sta tra parentesi angolari (`<host-ip>`) ed
  è nell'elenco della sezione 12.
- **Rimandi**: "runbook AMD, N" rimanda alla sezione N di `docs/amd-runbook.md`; "Steps N", "GPU
  session N" e "Demo export N" ai passi di `infra/spikes/README.md`, come là.

## 0. Quando usare questo runbook e cosa produce

### 0.1 I due host

Il droplet MI300X di AMD Developer Cloud (runbook AMD, sezione 2) è bloccato da un limite
dell'account, senza risposta da AMD. Le regole di ACT III (lette il 16 ottobre 2026 su
lablab.ai) ammettono "AMD Developer Cloud" oppure "other approved AMD powered infrastructure
provided during the event", e chiedono che "every project must run a meaningful part of its
workload on AMD infrastructure or AMD hardware; the AMD component must be part of the working
product shown to the judges". Due macchine AMD senza Docker sono ora il piano:

| | (a) AMD AI Notebooks (`notebooks.amd.com`) | (b) Hot Aisle, VM con una MI300X |
| --- | --- | --- |
| Che cos'è | JupyterLab con una GPU AMD e ROCm, un terminale | VM self-service, 1x MI300X, 224 GB di RAM, 12 TB NVMe |
| GPU | **sconosciuta finché il team non entra**: può non essere una MI300X | una MI300X (da confermare con `amd-smi static`) |
| Accesso | browser; nessun SSH, nessuna porta pubblica | SSH: `ssh -i ~/.ssh/id_hotaisle_ed25519 hotaisle@<host-ip>` poi `sudo -i` (runbook AMD 2.2) |
| Docker | probabilmente no | probabile (quick start: "Docker or Podman is ready to go", runbook AMD 2.2): se c'è, runbook AMD dalla 3.2 e `make gpu-up-local` per lo stack live (sotto); Ubuntu e versione ROCm da leggere al primo accesso |
| Costo | gratuito; **limiti di sessione sconosciuti** (durata, inattività, disco, persistenza dei file) | 2,99 USD per GPU all'ora, fatturazione al minuto (da verificare sul pannello) |
| Trasferimento file | upload e download dal pannello di JupyterLab; download dalla rete verso l'host | `scp`, `rsync` |
| Tunnel per il video | quick tunnel (sezione 8.3) | tunnel SSH (sezione 8.2) |

Che cosa si sa e che cosa no, al 16 ottobre: i valori di questa tabella vengono dal piano del
team, non dai pannelli; la sezione 1 serve a sostituirli con quello che l'host dice davvero.

**Se sull'host c'è Docker** (`docker compose version` stampa una versione 2.x e `docker ps`
risponde): non serve questo documento. Si usa `docs/amd-runbook.md` dalla sezione 3.2 in poi
(clone, `.env`, preflight, immagini, pesi, spike, benchmark, export), saltando la creazione del
droplet e, su Hot Aisle, sostituendo `root@<droplet-ip>` con `hotaisle@<host-ip>` più `sudo -i`
(runbook AMD 2.2). Per lo stack live su un host senza porte pubbliche il repository ha già il
target `make gpu-up-local` (Makefile: lo stesso stack in HTTP semplice su `127.0.0.1:8080` e
l'API su `127.0.0.1:8000`, senza basic auth) più il tunnel SSH della sezione 8.2 con
`-L 8080:127.0.0.1:8080 -L 8000:127.0.0.1:8000` (runbook AMD 2.2, voce "Sezione 11"): con
Docker su Hot Aisle è quella la strada. La sezione 8 di questo documento è per il caso senza
Docker, e per il notebook, dove il quick tunnel (8.3) sostituisce `ssh -L` e `make gpu-up`
(Caddy su 80 e 443) non serve a nulla.

### 0.2 Che cosa produce la sessione

| Prodotto | Dove finisce | Che cosa cambia rispetto al droplet |
| --- | --- | --- |
| Inventario dell'host (`host.json`, le righe di `amd-smi`, le versioni) | `artifacts/spikes/host.json`, poi `docs/spike-results.md` ("Session") | la riga "Droplet image and size" diventa "Host: notebook o Hot Aisle, GPU, ROCm" |
| SPK-G7 (pesi, avvio di vLLM, cache KV), SPK-G1, G5, G6, G2, G4 ridotto | `artifacts/spikes/` sull'host, poi sul portatile; `docs/spike-results.md` | G3 saltato; G4 senza la metà DGL; `spike-g1-bench` solo se `vllm bench` esiste |
| La decisione D-12 (31B o 26B-A4B) | `docs/spike-results.md`; `.env` | come sul droplet, con la riserva della sezione 4.6 se la GPU non ha 192 GB |
| Benchmark B1, B4, B3 | `bench/results/<run-id>.json`, `latest.json`, `latest-gpu.json`; `artifacts/bench/<run-id>/` | **B2 saltato** (niente immagine `trainer-graphsage`); il merge sposta `latest.json` a ogni corsa `gpu`, quindi su una GPU che non è una MI300X il merge non si fa sull'host (sezione 7.3) |
| Le bozze di Gemma per ogni reso aperto | `serving/llm_cache/` | invariato |
| Il pacchetto della demo | `demo-cache/`, `demo-cache.tar.gz` | etichetta MI300X solo su una MI300X; altrimenti pacchetto di prova (sezione 6.3) |
| Lo stack live per il video | `http://127.0.0.1:8000` sull'host, la UI sul portatile attraverso un tunnel | niente HTTPS pubblico, niente Caddy, niente basic auth: solo durante la registrazione |

### 0.3 Che cosa resta del piano del droplet e che cosa si perde

| Resta | Si perde (e il motivo) |
| --- | --- |
| vLLM con Gemma 4 alla revisione fissata, con gli stessi flag (sezione 4) | le immagini fissate per tag e digest di `.env.example` (`VLLM_IMAGE`, `DGL_IMAGE`, `ROCM_BASE_IMAGE`): la versione di vLLM è quella installata sull'host, da annotare |
| G1, G5, G6, G7 (script a sola libreria standard contro `VLLM_BASE_URL`) | **G3** (DGL GraphSAGE sintetico): serve l'immagine `rocm/dgl`. Piano B della spec 13: GraphSAGE resta come upgrade, addestrato su CPU come in `docs/results/us-202-graphsage.md` (Intel Xeon, 4 vCPU, `torch 2.8.0+cpu`, backend torch) |
| G2 su GPU con `amd_xgboost` installato nel venv dall'indice AMD (sezione 3.3) | **B2** (GraphSAGE su ASOS, CPU contro GPU) e **A4 su GPU** (`make embeddings-gpu`): girano in `trainer-graphsage`. Restano i numeri CPU già nei risultati |
| G4 ridotto: vLLM a 0,55 più il training XGBoost sulla stessa GPU (senza DGL) | G4 intero (`infra/spikes/g4_coexistence.sh` lancia tutto con `docker compose`) |
| B1, B4, B3, merge e report con `python -m bench` (sezione 7) | `make bench-build`, `make gnn-image`, `make gnn-test-gpu` |
| reset, `batch_generate`, `demo_export`, `demo_walk`, `uvicorn` con il Python dell'host (sezioni 6 e 8) | `make gpu-up`: postgres, Caddy, HTTPS su `PUBLIC_HOST`, basic auth, `telemetry` in container; `make demo-walk` resta come `python -m scripts.demo_walk` |
| la frase "una MI300X" nel pitch, nel README e in lablab, **solo se la GPU dell'host è una MI300X** (`market_name` di `amd-smi static`) | lo snapshot del droplet: su Hot Aisle si distrugge la VM e il giorno dopo si riscaricano i pesi (sezione 9); sul notebook dipende dai limiti di sessione |

### 0.4 Le regole fisse (le stesse del runbook AMD, 0.2)

1. **Nessun segreto nel repo né in un file che lascia l'host oltre a `.env`.** `.env` non si
   committa; sull'host resta a `chmod 600`. Nessun token GitHub e nessun token Hugging Face sul
   disco (i due modelli Gemma 4 non sono gated, D-05). Il log di vLLM stampa gli argomenti di
   avvio **con `--api-key=<chiave>`**: non si manda mai a Claude senza l'oscuramento della
   sezione 4.4. L'URL del quick tunnel (sezione 8.3) non va in nessun file, nemmeno nella
   cronologia della shell del portatile (8.4, 8.5).
2. **`data/truth/` non lascia mai il portatile** (regola 12). Sull'host arrivano solo `serving/`,
   `data/raw/asos_*.parquet` (o l'ASOS scaricato dall'host stesso) e `garment.jpg`. `data/ood/`
   non contiene `truth` (la verità OOD sta in `data/truth/ood/`), ma qui non serve: A4 e B2 sono
   saltati.
3. **Lo snapshot `serving/` si costruisce una volta sola sul portatile e si copia.** La cache
   delle bozze è indicizzata dall'`llm_case`: lo snapshot letto da `batch_generate` deve essere
   quello che la demo serve.
4. **Ogni numero porta il nome vero dell'hardware** (regola 11). `--generated-on` del pacchetto,
   `--device` del campionatore di telemetria e le righe di `docs/spike-results.md` dicono la GPU
   che `amd-smi static` ha stampato, mai "MI300X" per abitudine. Dove il codice scrive "MI300X"
   da solo (sezione 7.3) e la GPU non lo è, il risultato non si pubblica finché l'etichetta non
   è corretta.

### 0.5 Lo script 07: le stesse sezioni, guidate

`bash scripts/release/07-host-session.sh <fase>` (fasi: `probe`, `env`, `vllm`, `vllm-stop`,
`spikes`, `export`, `bench`, `live`, `down`; `all` = `probe env vllm spikes bench export`, i
benchmark prima del pacchetto perché il pacchetto porta `latest-gpu.json`) esegue le sezioni 1,
3, 4, 5, 6, 7, 8 e 9 di questo documento con gli stessi comandi, un checkpoint per target
(`release-logs/host/<fase>/<target>.done`), i log, i tempi in `release-logs/host/times.tsv` e
il piano B stampato a ogni errore; l'intestazione dello script dice fasi, target, variabili e
opzioni, e `--dry-run` stampa i comandi senza eseguirli. Corrispondenza:

| Fase dello script | Sezione di questo documento |
| --- | --- |
| `probe` | 1.2 (l'inventario; il nome della GPU resta in `release-logs/host/gpu-name.txt`) |
| `env` | 3.2, 3.3, 3.4 (`VENV`, `VENV_AMD` per altri percorsi) |
| `vllm` | 4.1-4.4 più la prima richiesta guided-JSON (`first-request`); `vllm-stop` ferma quel vLLM |
| `spikes` | 4.5 (`spike-g1-bench`) e 5 (G2, G7, G1, G5, G6, G4 ridotto; G3 non c'è) |
| `bench` | 7 (B1, B4, B3, merge; B2 saltato con il motivo scritto) |
| `export` | 6 (reset, batch, pacchetto, tarball) |
| `live` | 8.1, 8.5, 8.6, più le istruzioni dei tunnel stampate (8.2-8.4 restano a mano) |
| `down` | 9.2 (ferma ciò che ha avviato; non cancella nulla) |

Con lo script: `HOST_PROVIDER="Hot Aisle"` o `"AMD AI Notebooks"` (finisce nell'etichetta del
pacchetto, regola 11: la fase `export` lo pretende), `PRICE_USD_PER_HOUR` (2,99 su Hot Aisle;
0 sul notebook, impostato da solo quando `HOST_PROVIDER` contiene "Notebook"),
`HOST_DATABASE_URL=<il DATABASE_URL di .env>` se si usano anche i comandi a mano (altrimenti lo
script usa `release-logs/host/returnring.db`, non `artifacts/returnring.db` della 3.1), e i file
di vLLM e dell'API sono `release-logs/host/vllm.log`, `vllm.pid`, `uvicorn.log`, `uvicorn.pid`,
non `artifacts/spikes/logs/`. Lo script non modifica `.env` (passa `DEVICE`, `LLM_MODE`,
`VLLM_BASE_URL`, `DATABASE_URL` a ogni comando) e oscura la chiave vLLM e la password del
database in ogni log del pacchetto `release-logs-<data>.tgz`. **Una sezione si fa o con lo
script o a mano, mai metà e metà**: un `uvicorn` a mano dopo il reset dello script legge un
altro database (vuoto), e `vllm-stop` non conosce il pid di un `vllm serve` avviato a mano
(4.3). La checklist della sezione 11 dice, voce per voce, quale fase dello script la copre.

## 1. Riconoscere la macchina

I primi comandi da lanciare e da mandare, prima di qualsiasi altra cosa: decidono quale GPU si
ha, se vLLM c'è già, se c'è Docker (sezione 0.1) e quanto tempo e disco ci sono.

### 1.1 Entrare

- **Notebook**: dal pannello di JupyterLab, File > New > Terminal. Poi `tmux new -s host` (se
  `tmux` esiste: `tmux -V`): un terminale di JupyterLab chiuso per sbaglio chiude anche i
  processi figli; dentro `tmux` sopravvivono, finché la sessione del notebook esiste.
  Da verificare sull'host: che cosa succede ai processi e ai file quando si chiude il browser,
  e dopo quanto tempo di inattività la sessione termina (sezione 1.4).
- **Hot Aisle**: `ssh -i ~/.ssh/id_hotaisle_ed25519 hotaisle@<host-ip>` con la chiave di
  sessione scaricata dal terminale di amministrazione, poi `sudo -i` (runbook AMD 2.2: utente
  `hotaisle`, chiave e `sudo` come là, da confermare al primo accesso; `<user>` in questo
  documento è `hotaisle`), poi `tmux new -s host`. Se SSH cade: `ssh` di nuovo e
  `tmux attach -t host`.

### 1.2 L'inventario (circa 2 min)

```bash
export PATH="$PATH:/opt/rocm/bin"           # amd-smi e rocminfo stanno lì, spesso fuori dal PATH
cat /etc/os-release | head -2; uname -r; nproc; free -g; id; date -u
df -h / /tmp "$HOME"
ls -l /dev/kfd /dev/dri
cat /opt/rocm/.info/version
amd-smi version
amd-smi static --json | head -60            # market_name, target_graphics_version, vram size
rocminfo | grep -m1 -o 'gfx[0-9a-f]*'       # gfx942 su una MI300X
python3 --version; python3 -m pip --version
python3 -c "import vllm; print('vllm', vllm.__version__)"
python3 -c "import torch; print(torch.__version__, 'hip', torch.version.hip, 'gpu', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
command -v vllm docker tmux git ssh scp rsync curl cloudflared
docker compose version
ss -ltnp
curl -sI https://huggingface.co | head -1   # la rete verso Hugging Face per i pesi
curl -sI https://pypi.amd.com/rocm-7.2.3/simple/ | head -1
[ "$(id -u)" = 0 ] && echo root-ok || echo "no root"     # /proc/sys/vm/drop_caches è a sola scrittura: non si legge nemmeno da root
```

Che cosa si legge:

| Riga | Che cosa decide |
| --- | --- |
| `market_name` e `target_graphics_version` di `amd-smi static`, `gfx...` di `rocminfo` | se la GPU è una MI300X (`gfx942`, 192 GB). È **il** valore di `<gpu-name>` per tutto il resto |
| `vram` `size` di `amd-smi static` | se il 31B (62,6 GB di pesi) ci sta con `--gpu-memory-utilization=0.55` (sezione 4.6) |
| `/opt/rocm/.info/version` | la ROCm dell'host: la ruota `amd_xgboost` fissata è per la 7.2.3 (sezione 3.3) |
| `import vllm` | se vLLM è già installato e in quale Python (sezione 3.4) |
| `torch.version.hip` e `get_device_name` | se il PyTorch presente è una build ROCm che vede la GPU |
| `docker compose version` | se c'è Docker: allora `docs/amd-runbook.md` (sezione 0.1) |
| `df -h "$HOME"` | spazio per i 62,6 GB di pesi (il 31B) o i 51,6 GB (il piano B), più i venv: servono circa 80 GB liberi dove sta `HF_HOME` |
| `ss -ltnp` | che cosa ascolta già sulle porte 8000 e 8001 (un vLLM o un Jupyter preinstallato) |
| `nproc`, `free -g` | i vCPU della baseline CPU di B1 e di G2 (da annotare accanto ai tempi) |
| `root-ok` | se si può scaricare la page cache prima dell'avvio di vLLM (sezione 7.2, passo 3) |

Atteso su una MI300X (notebook o Hot Aisle): `gfx942`, `market_name` che contiene `MI300X`,
`vram` intorno a 196.592 MB (`bench/amdsmi.py`: amd-smi riporta in unità binarie); `/dev/kfd`
presente; ROCm 7.x. Qualsiasi altra cosa è da annotare, non è un errore.

### 1.3 `preflight.py` sull'host (circa 1 min)

Lo script del preflight (`infra/spikes/preflight.py`, sola libreria standard) interroga l'host
e scrive `artifacts/spikes/host.json`; senza Docker **esce 1** con le righe `ERROR: Docker daemon
not reachable (docker version shows no server)` e `ERROR: docker compose v2 missing: ...`, e
probabilmente `only N GB free for Docker; need about 250` (misura `/`): sono attese e non
fermano nulla. Il file vale lo stesso: OS, kernel, modulo amdgpu, pacchetti del driver, ROCm,
versione di amd-smi, processi sulla GPU, porte in ascolto. Si lancia dopo il clone e `.env`
(sezione 3.1):

```bash
python3 infra/spikes/preflight.py --env-file .env     # scrive artifacts/spikes/host.json; exit 1 atteso senza Docker
```

### 1.4 I limiti della sessione (notebook)

Da verificare sul pannello e nella documentazione di `notebooks.amd.com`, e da scrivere nel
foglio della sessione: durata massima, timeout di inattività, se il filesystem persiste tra due
sessioni (altrimenti i pesi si riscaricano ogni volta), quota di disco, se il terminale
sopravvive alla chiusura del browser, se ci sono limiti sul traffico in uscita. Finché non si
sa, si lavora come se la sessione potesse chiudersi: `tmux`, i file importanti copiati sul
portatile appena esistono (sezione 9.1), i pesi in `HF_HOME` su un disco che persiste.

Da mandare a Claude: l'output completo della sezione 1.2 (nessun segreto dentro),
`artifacts/spikes/host.json`, le risposte della sezione 1.4, e se c'è Docker.

## 2. Preparare il portatile (senza costo)

### 2.1 Che cosa deve esistere

Come nel runbook AMD, sezione 1 (la vigilia): `make lint test` verde sul branch della sessione;
`serving/manifest.json` esiste (`make demo-data`, o `make pipeline` con il Python dell'host);
`make asos` fatto e `make asos-verify` che stampa `All 6 ASOS files match the pinned sha256
(ADR-10).`; `garment.jpg` (JPEG o PNG sotto 2 MB, nessuna persona, nessun dato personale sulle
etichette); per la UI del video, `cd apps/ui && npm ci` (Node 22 o successivo, `package.json`).

### 2.2 Che cosa si copia e quanto pesa

Dimensioni misurate su questo checkout il 16 ottobre (`du -sh`):

| Da copiare | Dimensione | Letto da | Serve per |
| --- | --- | --- | --- |
| `serving/` (con `manifest.json`, senza `llm_cache/`) | 30 MB | reset, API, `batch_generate`, `demo_export` | sezioni 6 e 8 |
| `data/raw/asos_customers.parquet`, `asos_events.parquet`, `asos_variants.parquet` | 20,1 + 23,4 + 25,8 MB, circa 69 MB | B1 (`--raw data/raw`) | sezione 7 |
| `artifacts/spikes/garment.jpg` | < 2 MB | SPK-G5 | sezione 5 |

Mai copiati: `data/truth/` (regola 12), `data/raw/asos/` (i pickle, 708 MB), `data/store/`,
`data/features/`, `data/ood/` (servivano ad A4 e B2, saltati), `artifacts/`, `.env` del
portatile. Il tarball, dalla radice del repository sul portatile:

```bash
mkdir -p artifacts/spikes && cp <la-foto> artifacts/spikes/garment.jpg     # artifacts/ è git-ignorato: sull'host arriva già al suo posto
tar --exclude=serving/llm_cache -czf ~/returnring-host.tar.gz serving data/raw/asos_customers.parquet data/raw/asos_events.parquet data/raw/asos_variants.parquet artifacts/spikes/garment.jpg
tar -tzf ~/returnring-host.tar.gz | grep -c truth        # deve stampare 0
tar -tzf ~/returnring-host.tar.gz | grep -c llm_cache    # deve stampare 0: una cache di prova del portatile (mock, DEVICE=cpu) farebbe rifiutare il pacchetto (sezione 10)
sha256sum ~/returnring-host.tar.gz                        # da confrontare sull'host
```

(`tar` e `sha256sum` sono comandi standard, non del progetto. Il tarball sta nella home del
portatile, non nel repository, e la foto in `artifacts/spikes/`: nulla di estraneo nell'albero
git, perché un `git status --porcelain` non vuoto segna `git.dirty: true` in ogni parte dei
benchmark, sezione 7.1.)

### 2.3 Come arriva sull'host

| Host | Come | Note |
| --- | --- | --- |
| Hot Aisle | dal portatile: `scp ~/returnring-host.tar.gz <user>@<host-ip>:` (nella home dell'host, non nel repository) | oppure, come nel runbook AMD 4: `rsync -a --delete serving/ <user>@<host-ip>:ReturnRing/serving/` e `scp data/raw/asos_*.parquet <user>@<host-ip>:ReturnRing/data/raw/` (`rsync` deve esistere anche sull'host) |
| Notebook | il pulsante Upload del pannello file di JupyterLab, nella cartella home; poi sull'host, dalla home, `sha256sum returnring-host.tar.gz` e `tar -xzf returnring-host.tar.gz -C ReturnRing/` (il tarball resta nella home, fuori dall'albero git) | da verificare sull'host: il limite di dimensione dell'upload del pannello (circa 100 MB qui) e la velocità |
| Notebook, alternativa per ASOS | l'host scarica ASOS da solo, come `make asos`: `python -m pipeline.ingest --dest data/raw/asos` nel `.venv` (sezione 3.2), che scarica i sei pickle da OSF (742 MB), ne verifica gli sha256 e scrive i tre parquet | serve rete verso OSF (da verificare); il dataset è CC BY 4.0 "used and not redistributed" (`infra/spikes/README.md`), quindi **non** si pubblica il tarball su un link pubblico per farlo scaricare all'host |
| Notebook, alternativa per `serving/` | un link privato controllato dal team (una cartella condivisa del team) scaricato con `curl -L -o returnring-host.tar.gz '<link>'` | da verificare: il link non va in nessun file del repo; lo snapshot è sintetico e non contiene verità, ma resta un file del team |

Sull'host, dopo l'estrazione: `ls ReturnRing/serving/manifest.json ReturnRing/data/raw/asos_*.parquet`
e `test ! -d ReturnRing/data/truth && echo 'no data/truth'`.

Da mandare a Claude: niente, salvo un errore di trasferimento o un hash diverso.

## 3. L'ambiente Python sull'host

### 3.1 Clone, cartelle, `.env`

```bash
git clone https://github.com/Saroth85/ReturnRing.git ReturnRing && cd ReturnRing && git checkout <branch>
mkdir -p artifacts/spikes/logs artifacts/bench data/raw
cp .env.example .env
sed -i "s/^VLLM_API_KEY=.*/VLLM_API_KEY=$(openssl rand -hex 24)/" .env
sed -i "s|^VLLM_BASE_URL=.*|VLLM_BASE_URL=http://127.0.0.1:8001/v1|" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=sqlite:///$PWD/artifacts/returnring.db|" .env   # sqlite:/// più il percorso assoluto: sqlite:////home/<utente>/ReturnRing/artifacts/returnring.db
sed -i "s/^DEVICE=.*/DEVICE=gpu/; s/^LLM_MODE=.*/LLM_MODE=vllm/" .env
printf 'SERVING_DIR=serving\nBENCHMARKS_DIR=bench/results\n' >> .env
chmod 600 .env
```

Perché così: il Makefile legge `.env` con `set -a && . ./.env && set +a` (`LOAD_ENV`), e qui si
fa lo stesso a mano prima di ogni comando (`set -a; . ./.env; set +a`), quindi i valori restano
senza spazi e senza virgolette. Rispetto al droplet (runbook AMD, 3.3):

| Variabile | Sul droplet | Sull'host | Perché |
| --- | --- | --- | --- |
| `VLLM_BASE_URL` | `http://vllm:8001/v1` (rete compose) | `http://127.0.0.1:8001/v1` | vLLM gira sull'host; `llm/client.py` accetta `localhost`, `127.0.0.1` e le reti private, rifiuta tutto il resto (regola 2) |
| `DATABASE_URL` | PostgreSQL in container | `sqlite:///` seguito dal percorso assoluto del file, quattro barre in tutto (`sqlite:////home/<utente>/ReturnRing/artifacts/returnring.db`; `apps/api/settings.py` usa `aiosqlite`, in `requirements/dev.txt`); se sull'host c'è un PostgreSQL, `postgresql://<user>:<password>@127.0.0.1:5432/returnring` (asyncpg) | niente container postgres; `artifacts/` è git-ignorato |
| `DEVICE`, `LLM_MODE` | `cpu`, `mock` nel file, imposti a `gpu`, `vllm` dal compose | `gpu`, `vllm` nel file | nessun override di compose: ogni bozza registra `DEVICE` (sezione 6.3) |
| `SERVING_DIR`, `BENCHMARKS_DIR` | montati dal compose in `/app/serving`, `/app/bench/results` | `serving`, `bench/results`, relativi alla radice: **ogni comando si lancia dalla radice** | `apps/api/settings.py`, `apps/api/services/other/benchmarks.py` |
| `POSTGRES_*`, `PUBLIC_HOST`, `ADMIN_PASSWORD_HASH` | usati | inutilizzati (niente postgres, niente Caddy): si lasciano come sono | — |
| `VLLM_IMAGE`, `DGL_IMAGE`, `ROCM_BASE_IMAGE` | le immagini | inutilizzati; restano per il preflight, che li controlla | — |
| `GEMMA_MODEL`, `GEMMA_REVISION`, `AMD_PYPI_INDEX_URL`, `HF_TOKEN` | come in `.env.example` | uguali | il piano B D-12 cambia le due righe insieme (runbook AMD, 8.2) |

Controllo (mostra la chiave: non mandare l'output a Claude):

```bash
grep -E '^(VLLM_API_KEY|VLLM_BASE_URL|DATABASE_URL|DEVICE|LLM_MODE|DEMO_MODE|SERVING_DIR|BENCHMARKS_DIR|GEMMA_MODEL|GEMMA_REVISION)=' .env
```

Se serve mandare `.env` a Claude, oscurato come fa `scripts/release/05-collect.sh`:

```bash
sed -E 's/^(POSTGRES_PASSWORD|DATABASE_URL|VLLM_API_KEY|HF_TOKEN|ADMIN_PASSWORD_HASH)=.*/\1=<redacted>/' .env
```

### 3.2 `.venv`: il Python del prodotto (circa 3-5 min, rete)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements/dev.txt           # README, "Development"
python -c "import xgboost; print(xgboost.__version__)"      # 3.2.0 (xgboost-cpu)
deactivate
```

Da verificare sull'host: la versione di Python. I pin di `requirements/base.txt` sono risolti
per Python 3.12 (`numpy 2.5.4`, `polars 2.0.0`, `pandas 3.0.6`); su un Python più vecchio pip
può non trovare le ruote. Se `python3` non è 3.12 e non c'è `python3.12`, annotarlo e chiedere
a Claude prima di cambiare un pin. `pip install python3-venv` di sistema (`apt-get install -y
python3-venv`, da verificare: serve `sudo`) se `python3 -m venv` manca.

### 3.3 `.venv-bench`: la ricetta dell'immagine `bench` (circa 3-5 min, rete)

Gli stessi tre passi di `infra/docker/bench.Dockerfile`, con le stesse righe di controllo:
`amd_xgboost` da solo, dall'indice AMD soltanto, con l'hash fissato (`requirements-gbm-amd.txt`:
mai `--extra-index-url`, il nome `amd-xgboost` non è registrato su pypi.org); poi tutto il resto
da PyPI con `--no-deps` e `pip check`; poi il controllo che la ruota sia la build HIP e che a
`libxgboost.so` non manchi una libreria ROCm.

```bash
set -a; . ./.env; set +a
python3 -m venv .venv-bench && . .venv-bench/bin/activate
pip install --no-cache-dir --no-deps --require-hashes --index-url "$AMD_PYPI_INDEX_URL" -r infra/docker/requirements-gbm-amd.txt
pip install --no-cache-dir --no-deps --index-url https://pypi.org/simple -r infra/docker/requirements-bench.txt && pip check
lib="$(python -c "import importlib.util as u, pathlib; print(pathlib.Path(u.find_spec('xgboost').origin).parent / 'lib' / 'libxgboost.so')")" \
  && test -f "$lib" \
  && ! ldd "$lib" | grep 'not found' \
  && python -c "import xgboost as x; assert x.build_info().get('USE_HIP'), x.build_info(); print('xgboost', x.__version__, 'USE_HIP')" \
  && python -c "import polars, pyarrow, sklearn, networkx, pydantic; print('bench stack ok')"
deactivate
```

Atteso: `xgboost 3.2.0 USE_HIP` e `bench stack ok`. I due venv stanno nella radice perché sono
git-ignorati (`.gitignore`: `.venv/` e `.venv-*/`, quindi anche `.venv-bench/` e `.venv-vllm/`):
un venv non ignorato renderebbe `git status --porcelain` non vuoto, e ogni parte dei benchmark
registrerebbe `git.dirty: true` senza che il codice sia cambiato (7.1, regola 13); un venv
altrove va bene lo stesso (per lo script 07: `VENV=<percorso> VENV_AMD=<percorso>`). Che cosa
può andare storto, e il piano B
(la ruota è quella del droplet: `amd_xgboost-3.2.0-py3-none-manylinux_2_39_x86_64.whl`, ROCm
7.2.3, glibc 2.39, cioè Ubuntu 24.04):

| Sintomo | Causa | Che cosa fare |
| --- | --- | --- |
| pip: `No matching distribution` o `not a supported wheel on this platform` | glibc dell'host sotto la 2.39 (`ldd --version`: Ubuntu 22.04 ha la 2.35) | da verificare sull'host se l'indice AMD ha una ruota per quella glibc (`pip index versions amd_xgboost --index-url ...`, da verificare); altrimenti piano B di SPK-G2 (spec 13): XGBoost su CPU, B1 misura solo la CPU e lo dice |
| `ldd` elenca `librccl.so.1 => not found` o altre librerie ROCm | le librerie non sono nel percorso del loader | `export LD_LIBRARY_PATH=/opt/rocm/lib:$LD_LIBRARY_PATH` prima dei comandi di `.venv-bench` (derivato, da verificare: sull'immagine `-complete` le librerie stanno lì) |
| `/opt/rocm/.info/version` non è 7.2.x | ruota e ROCm dell'host non coincidono | `requirements-gbm-amd.txt`: le build 7.1.1 e 7.0.2 hanno lo stesso nome di file ma un altro hash, e l'indice è `https://pypi.amd.com/rocm-<versione>/simple`; cambiare indice e hash insieme (l'hash: `sha256sum` della ruota scaricata con `pip download`, da verificare) e annotare che la ruota non è quella fissata; oppure piano B |
| `pip check` fallisce | un pin di `requirements-bench.txt` senza ruota per il Python dell'host | come in 3.2: annotare, chiedere a Claude |
| la riga `USE_HIP` fallisce | `xgboost-cpu` finito in questo venv, o ruota CPU | ricreare `.venv-bench` da capo; mai `pip install xgboost` o `xgboost-cpu` qui |

### 3.4 vLLM: quello installato o pip

In ordine, e ci si ferma al primo che funziona:

1. **vLLM preinstallato** (il notebook probabilmente ne ha uno, o un PyTorch ROCm): il comando
   della sezione 1.2, `python3 -c "import vllm; print(vllm.__version__)"`, e `vllm --version`.
   Se c'è, si usa quel Python per vLLM e per `huggingface_hub` (sezione 4.1), tenendo `.venv` e
   `.venv-bench` separati. Annotare la versione: sostituisce la riga `vllm` della tabella
   "Images" di `docs/spike-results.md` (0.31.0 su ROCm 7.2.3 era l'immagine; qui non c'è
   un'immagine).
2. **Nessuna ruota pip di vLLM per ROCm nelle fonti.** Mai `pip install vllm` da pypi.org: quella
   ruota è la build CUDA, non usa la GPU AMD e trascina un torch CUDA sopra il torch ROCm (lo
   script 07 dice lo stesso: "Do NOT pip install vllm from pypi.org"); AMD documenta per ROCm
   l'immagine Docker `rocm/vllm` (inutilizzabile qui) o la build dai sorgenti (ore di tempo
   pagato: no), e `https://pypi.amd.com/rocm-7.2.3/simple/vllm/` ha risposto 403 il 16 ottobre
   2026 (nessun progetto `vllm` visibile lì). L'unico indice da provare, **da verificare
   sull'host**, è quello di AMD per la ROCm dell'host
   (`https://repo.radeon.com/rocm/manylinux/rocm-rel-<versione>/`, dove AMD pubblica le ruote
   torch ROCm), in un venv a parte (`python3 -m venv .venv-vllm`, git-ignorato): torch e vllm
   **dallo stesso indice**, e dopo l'installazione `vllm --version` e il controllo di torch
   (`torch.version.hip` non vuoto, `torch.cuda.is_available()` `True`). Se per quella ROCm non
   c'è una ruota `vllm`, punto 3: nessuna Gemma su questo host. Un vLLM che non vede la GPU
   fallisce all'avvio (sezione 4.3), non in silenzio.
3. **Se nessuno dei due**: il piano crolla per la parte Gemma; si annota e si chiede su Discord
   e a Claude. Il resto (G2, B1, B4 senza generazione) si può fare comunque.

`huggingface_hub` serve per il download dei pesi: arriva con vLLM; se manca,
`pip install huggingface_hub` nel venv di vLLM (Apache-2.0, regola 15; da verificare la
versione).

Da mandare a Claude: le righe `xgboost 3.2.0 USE_HIP`, `bench stack ok`, `vllm <versione>`, la
versione di torch con `hip`, e ogni deviazione dalla ricetta (ruota, indice, LD_LIBRARY_PATH).

## 4. vLLM con Gemma 4

Fonte dei flag: il servizio `vllm` di `infra/docker-compose.yml` (modello posizionale,
`--revision` e `--tokenizer-revision` a `GEMMA_REVISION`, `--port=8001`,
`--gpu-memory-utilization=0.55`, `--max-model-len=16384`, `--api-key`,
`--enable-auto-tool-choice`, `--tool-call-parser=gemma4`, `--chat-template` assoluto,
`--limit-mm-per-prompt={"image": 1, "audio": 0}`; `HF_HUB_OFFLINE=1`; `--reasoning-parser` lasciato
fuori apposta). Qui l'entrypoint `vllm serve` dell'immagine diventa il comando `vllm serve`
dell'host.

### 4.1 I pesi (SPK-G7, prima metà; durata non misurata)

Quello che `infra/spikes/weights.sh start` lancia dentro l'immagine, sull'host, con il Python di
vLLM (sezione 3.4), in background con `setsid` così sopravvive al terminale e a una caduta di
SSH, con la stessa riga di stato:

```bash
set -a; . ./.env; set +a
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"   # su un disco con almeno 80 GB liberi (df -h, sezione 1.2)
mkdir -p artifacts/spikes/logs
setsid bash -c '
  s=$(date +%s)
  HF_HUB_OFFLINE=0 python3 -c "import sys, huggingface_hub as h; print(h.snapshot_download(sys.argv[1], revision=sys.argv[2]))" "$1" "$2"
  rc=$?
  echo "download-weights: exit $rc, took $(( $(date +%s) - s )) s ($1@$2)" > "$3"
' _ "$GEMMA_MODEL" "$GEMMA_REVISION" artifacts/spikes/logs/weights-status.txt > artifacts/spikes/logs/download-weights.log 2>&1 < /dev/null &
echo "weights download started: log artifacts/spikes/logs/download-weights.log"
```

(`python3` qui è quello del venv in cui vive vLLM: attivarlo prima, o scrivere il suo percorso.)
Si aspetta come `weights.sh wait`: `cat artifacts/spikes/logs/weights-status.txt` finché esiste;
atteso

```text
download-weights: exit 0, took N s (google/gemma-4-31B-it@842da3794eaa0b77d5f08bae87a17459d91ff475)
```

`N` è il tempo di download di SPK-G7 (campo "download s"). Intanto si fa la sezione 3.3 e la metà
GPU di G2 (sezione 5), come sul droplet. Un 401 o 403 non è atteso (i modelli non sono gated):
`HF_TOKEN` solo in quel caso, nell'ambiente del comando e mai in un file, e va annotato. Dove
finiscono i pesi: `$HF_HOME/hub/models--google--gemma-4-31B-it/snapshots/$GEMMA_REVISION/`
(è il percorso che `make spike-g1-bench` costruisce per il tokenizer). Sul notebook, se la home
non persiste tra le sessioni, i pesi si riscaricano ogni volta: `HF_HOME` su un disco che
persiste, se c'è (sezione 1.4).

### 4.2 Il template della chat (da verificare sull'host)

Nell'immagine il file sta in `/app/vllm/examples/tool_chat_template_gemma4.jinja` (commento del
compose: "the template ships in /app/vllm/examples"). La ruota pip di vLLM di norma non contiene
la cartella `examples/` del repository. Come si trova, in ordine:

```bash
python3 -c "import vllm, pathlib; p = pathlib.Path(vllm.__file__).parent; print(p); print([str(f) for f in p.rglob('*gemma4*.jinja')])"
find / -name 'tool_chat_template_gemma4.jinja' 2>/dev/null
```

1. Se uno dei due stampa un percorso: `--chat-template=<quel percorso assoluto>`.
2. Se no: il file del repository di vLLM alla versione installata, scaricato una volta nella
   cartella degli spike (git-ignorata) e passato con il percorso assoluto; da verificare sull'host
   che il file esista a quel tag e che il nome sia quello (è un template di prompt, non un
   modello né un'API: la regola 2 riguarda le chiamate a modelli a runtime):

   ```bash
   v=$(python3 -c "import vllm; print(vllm.__version__)")
   curl -fsSL -o artifacts/spikes/tool_chat_template_gemma4.jinja "https://raw.githubusercontent.com/vllm-project/vllm/v$v/examples/tool_chat_template_gemma4.jinja"
   ```

3. Se neanche questo: si avvia senza `--chat-template` (il template del tokenizer di Gemma 4) e
   si annota che `--tool-call-parser=gemma4` potrebbe non estrarre le chiamate: G6 lo dice
   (`--mode named` è il piano B, sezione 5).

### 4.3 L'avvio (SPK-G7, seconda metà; 5-15 min stimati, compilazione non misurata)

Prima dell'avvio, l'ora di partenza, come `make spike-vllm`:
`date -u +%Y-%m-%dT%H:%M:%SZ > artifacts/spikes/logs/vllm-start.txt`. Il blocco che segue
**non** la scrive, così la sezione 7.2 (passo 3) lo riusa tale e quale senza sovrascrivere
l'ora del cold start di B3.

```bash
set -a; . ./.env; set +a
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
HF_HUB_OFFLINE=1 setsid nohup bash -c 'echo $$ > "$0"; exec "$@"' artifacts/spikes/logs/vllm.pid \
  vllm serve "$GEMMA_MODEL" \
  --revision="$GEMMA_REVISION" --tokenizer-revision="$GEMMA_REVISION" \
  --host 127.0.0.1 --port=8001 \
  --gpu-memory-utilization=0.55 --max-model-len=16384 \
  --api-key="$VLLM_API_KEY" \
  --enable-auto-tool-choice --tool-call-parser=gemma4 \
  --chat-template=<percorso-assoluto-del-template> \
  '--limit-mm-per-prompt={"image": 1, "audio": 0}' \
  > artifacts/spikes/logs/vllm.log 2>&1 < /dev/null &
```

Tre differenze dal compose, volute: `--host 127.0.0.1`, perché nel compose vLLM non pubblica
porte (NFR-11) e sull'host, con l'ascolto di default su tutte le interfacce, una VM con IP
pubblico lo esporrebbe (da verificare che `vllm serve --help` elenchi `--host`); `setsid nohup`
e il log su file al posto di `docker compose logs`: `setsid` mette vLLM in una sessione e in un
gruppo di processi propri (sopravvive al terminale e a una caduta di SSH, come il download
della 4.1), e il `bash -c` scrive in `vllm.pid` il pid del processo vero (con `setsid ... &` in
una shell interattiva `$!` sarebbe il pid di un `setsid` già uscito); `HF_HUB_OFFLINE=1` come
nel compose (regola 2): l'unico passo che parla con Hugging Face è la sezione 4.1. L'engine di
vLLM vive in processi figli (`EngineCore`): si ferma **tutto il gruppo**,
`kill -TERM -- -$(cat artifacts/spikes/logs/vllm.pid)` (il trattino prima del pid è il gruppo),
mai il solo pid, altrimenti un figlio resta con la memoria GPU e l'avvio successivo fallisce con
`Free memory on device` (tabella della 4.6 e sezione 10). Lo script 07 fa lo stesso (`do_start`,
`do_stop`).

### 4.4 Healthy, le righe della cache KV, il log senza la chiave

Quello che `make spike-wait` e `make spike-g7` fanno, senza il container:

```bash
for i in $(seq 1 240); do
  if curl -fsS -o /dev/null http://127.0.0.1:8001/health; then
    date -u +%Y-%m-%dT%H:%M:%SZ > artifacts/spikes/logs/vllm-healthy.txt
    echo "vllm healthy: started $(cat artifacts/spikes/logs/vllm-start.txt), healthy $(cat artifacts/spikes/logs/vllm-healthy.txt)"; break; fi
  kill -0 "$(cat artifacts/spikes/logs/vllm.pid)" 2>/dev/null || { echo "vllm is not running"; tail -100 artifacts/spikes/logs/vllm.log; break; }
  sleep 15
done
grep -E 'Time spent downloading weights|Loading weights took|Available KV cache memory|KV cache size|Maximum concurrency' artifacts/spikes/logs/vllm.log | tee artifacts/spikes/g7-vllm-log.txt
```

Atteso: `vllm healthy: started <ISO UTC>, healthy <ISO UTC>` (la differenza è "vLLM start to
healthy s" di SPK-G7), poi le righe `Loading weights took`, `Available KV cache memory`,
`GPU KV cache size: N tokens`, `Maximum concurrency for 16,384 tokens per request: X`.

Il log di vLLM contiene `--api-key=<chiave>` nella riga degli argomenti. Prima di mandarlo:

```bash
set -a; . ./.env; set +a
sed "s|$VLLM_API_KEY|<redacted>|g" artifacts/spikes/logs/vllm.log > artifacts/spikes/logs/vllm.redacted.log
grep -c "$VLLM_API_KEY" artifacts/spikes/logs/vllm.redacted.log     # deve stampare 0
```

Si manda solo `vllm.redacted.log` (o `g7-vllm-log.txt`, che non contiene la riga degli argomenti).

### 4.5 I token al secondo (SPK-G1 e D-12)

- Con `vllm bench serve`, se il vLLM installato lo ha (`vllm bench serve --help`): il comando di
  `make spike-g1-bench`, con `127.0.0.1` al posto del container e il tokenizer letto da `HF_HOME`:

  ```bash
  set -a; . ./.env; set +a
  export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"   # .env non lo contiene: in una nuova finestra di tmux sarebbe vuoto e il tokenizer non si troverebbe
  tok=$HF_HOME/hub/models--$(echo "$GEMMA_MODEL" | sed 's|/|--|g')/snapshots/$GEMMA_REVISION
  for c in 1 32; do
    if [ $c -eq 1 ]; then n=16; else n=64; fi
    log=artifacts/spikes/logs/g1-bench-c$c.txt
    OPENAI_API_KEY="$VLLM_API_KEY" vllm bench serve --backend openai-chat \
      --base-url http://127.0.0.1:8001 --endpoint /v1/chat/completions --model "$GEMMA_MODEL" \
      --tokenizer "$tok" \
      --dataset-name random --random-input-len 1500 --random-output-len 400 \
      --num-prompts $n --max-concurrency $c --num-warmups 2 --ignore-eos --temperature 0.2 \
      --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95 > $log 2>&1; rc=$?
    cat $log; [ $rc -eq 0 ] || break
    grep -Eq "Successful requests: +$n\b" $log || { echo "not all $n requests succeeded"; break; }
  done
  ```

  Token/s in flusso singolo = 1000 / TPOT mediano in ms della corsa C=1 (runbook AMD, 8.1).
- Senza `vllm bench`: il tokens/s di `g1.json` (`tokens_per_s_median_after_warmup`, sezione 5),
  che include il tempo al primo token ed è quindi più basso; annotare che la misura "da B3"
  manca e che D-12 è stata decisa su quel numero.

Criterio D-12 (spec 13 e 14, R-02): sotto 15 token/s in flusso singolo, o errore di avvio, piano
B: in `.env` `GEMMA_MODEL=google/gemma-4-26B-A4B-it` e
`GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52` insieme (mai scaricati finora: chiedere
conferma a Claude prima), poi di nuovo 4.1 (altri 51,6 GB),
`kill -TERM -- -$(cat artifacts/spikes/logs/vllm.pid)` (il gruppo, 4.3), 4.3, 4.4, 4.5.

### 4.6 Se la GPU non ha 192 GB (notebook)

Il 31B in bf16 sono 62,6 GB di pesi; con `--gpu-memory-utilization=0.55` vLLM riserva 0,55 della
VRAM (spec 11: circa 106 GB su 192). Stima, non misura:

| VRAM della GPU (`amd-smi static`) | 31B a 0,55 | 26B-A4B (51,6 GB) a 0,55 | Che cosa fare |
| --- | --- | --- | --- |
| 192 GB (MI300X) | sì, circa 43 GB per la cache KV | sì | i flag del compose, com'è |
| 128 GB (una MI300A, per esempio) | 70 GB riservati: pesi sì, cache KV stretta | sì | provare com'è; se `Available KV cache memory` è piccolo o l'avvio fallisce, alzare a `0.65` (spec 11 lo prevede) e annotarlo |
| 64 GB (una MI210, per esempio) | no (35 GB riservati) | no | `--gpu-memory-utilization=0.9` è una deviazione dal compose, da annotare; se nemmeno così parte, D-12 piano B e lo stesso flag; se nemmeno il piano B parte, la parte Gemma non si fa su quell'host |

Ogni valore diverso da `0.55` va scritto in `docs/spike-results.md` ("gpu-memory-utilization")
e detto a Claude: cambia i numeri di G1, G4 e B3, e il G4 ridotto non ha senso senza margine.

| Fallimento | Che cosa fare (runbook AMD, 7.6 e Steps 9) |
| --- | --- |
| `Free memory on device ... is less than desired GPU memory utilization` | qualcos'altro tiene memoria GPU (un vLLM o un Jupyter kernel preinstallato: `amd-smi process`, `ss -ltnp`): fermarlo; su un notebook, chiudere i kernel dal pannello; dopo un kill: `pgrep -af 'vllm\|EngineCore'` deve essere vuoto, altrimenti `pkill -TERM -f EngineCore`, poi `amd-smi process` vuoto, poi 4.3 |
| chat template non trovato | il percorso deve essere assoluto ed esistere (sezione 4.2) |
| errore di compilazione o di cattura dei grafi, o nessun healthy dopo 15 min di compilazione | aggiungere `--enforce-eager` al comando (è ciò che il runbook fa nel compose, solo sul droplet); annotare: eager è più lento, G1 e B3 sono un limite inferiore |
| errore di engine o di kernel | sul droplet si tornava alla 0.30.0; qui la versione è quella dell'host: annotare l'errore, provare `--enforce-eager`, poi piano B D-12 |
| `vllm serve` non vede la GPU (`torch.cuda.is_available()` falso, `No ROCm` o simili) | il vLLM è la build CUDA, o il PyTorch non è ROCm (sezione 3.4, punto 2) |
| Gemma 4 31B non parte affatto | piano B D-12 (4.5) |

Da mandare a Claude: `weights-status.txt`, `vllm-start.txt`, `vllm-healthy.txt`,
`g7-vllm-log.txt`, `g1-bench-c1.txt` e `g1-bench-c32.txt` (se esistono), la versione di vLLM, il
percorso del template trovato, il valore di `--gpu-memory-utilization` usato, e la decisione
D-12 con l'ora. Mai `vllm.log` non oscurato.

## 5. Gli spike senza Docker

Gli script degli spike che parlano con vLLM (`g1_guided_json.py`, `g6_tool_call.py`) sono a sola
libreria standard e leggono `VLLM_BASE_URL`, `VLLM_API_KEY` e `GEMMA_MODEL` dall'ambiente (il
default di `VLLM_BASE_URL` è `http://vllm:8001/v1`: senza `set -a; . ./.env; set +a` falliscono
con `cannot reach`). I file di output vanno in `artifacts/spikes/`, gli stessi nomi del Makefile,
così `docs/spike-results.md` si compila allo stesso modo. Ogni comando dalla radice del
repository, con vLLM healthy (sezione 4.4) salvo dove è scritto il contrario.

| Spike | Venv | Comando (dopo `set -a; . ./.env; set +a`) | Atteso | Output | Piano B (spec 13) |
| --- | --- | --- | --- | --- | --- |
| SPK-G7 | vLLM | sezioni 4.1, 4.3, 4.4 | `download-weights: exit 0, took N s`; `vllm healthy: ...`; le righe della cache KV | `logs/weights-status.txt`, `vllm-start.txt`, `vllm-healthy.txt`, `g7-vllm-log.txt` | snapshot non disponibile: i pesi si riscaricano (sezione 9) |
| SPK-G1 | `.venv` | `python infra/spikes/g1_guided_json.py --out artifacts/spikes/g1.json` | tre JSON `{"run": i, ..., "finish_reason": "stop", "problems": []}`, l'ultimo output, poi `SPK-G1: pass=True median <n> tokens/s (single request, after warm-up)` | `g1.json` | `finish_reason` `length`: `--max-tokens 2048`; sotto 15 tok/s: D-12 (4.5) |
| SPK-G1 bench | vLLM | sezione 4.5 | `Successful requests: 16` e `64` | `logs/g1-bench-c1.txt`, `c32.txt` | senza `vllm bench`: solo `g1.json`, annotato |
| SPK-G5 | `.venv` | `python infra/spikes/g1_guided_json.py --image artifacts/spikes/garment.jpg --out artifacts/spikes/g5.json` | la checklist JSON (`tag_attached`, `visible_damage`, `photo_quality_ok`, `notes`), `SPK-G5: pass=True ...` | `g5.json` | ispezione fuori ambito (era Could) |
| SPK-G6 | `.venv` | `python infra/spikes/g6_tool_call.py --mode auto --out artifacts/spikes/g6-auto.json` | un JSON con `"pass": true`: passo 1 chiama `get_case_orders` con `R-1042`, passo 2 cita `O-1001` e `O-1002` | `g6-auto.json` | `SPK-G6 FAIL (auto). Plan B: --mode named.` su stderr: `--mode named --out artifacts/spikes/g6-named.json` (il codice sceglie lo strumento) |
| SPK-G2, metà GPU | `.venv-bench` | `python infra/spikes/g2_xgboost_gpu.py --device gpu --context "<che altro girava: Gemma weights downloading in the background, o vLLM idle>" --out artifacts/spikes/g2_gpu.json` | `{"xgboost": "3.2.0", "use_hip": true, ...}`, `{"official_verify": {"device_requested": "cuda", "device_effective": "<non cpu>"}}`, il risultato GPU, `"pass": true` | `g2_gpu.json`, `g2_gpu_model.json`, `g2_gpu_check.json` | uscita 2 (`FAIL: GPU not used`): la GPU non è visibile al venv (`ldd`, `LD_LIBRARY_PATH`, 3.3); XGBoost su CPU per il training |
| SPK-G2, caricamento su CPU | `.venv` | `python infra/spikes/g2_load_cpu.py --check artifacts/spikes/g2_gpu_check.json` | `{"xgboost": "3.2.0", "rounds": 200, "max_abs_diff": <vicino a 0>, "pass": true}` | `g2_load_cpu.json` | — |
| SPK-G2, metà CPU | `.venv-bench` | **a download finito e con vLLM fermo o inattivo**: `python infra/spikes/g2_xgboost_gpu.py --device cpu --context "alone on the CPU" --gpu-result artifacts/spikes/g2_gpu.json --out artifacts/spikes/g2_cpu.json` | `speedup_train` nel file, `"pass": true`; `FAIL: ... has config ...` se la metà GPU aveva un'altra configurazione | `g2_cpu.json` | — |
| SPK-G3 | — | **saltato**: `g3_graphsage_synthetic.py` importa `dgl`, che c'è solo nell'immagine `rocm/dgl` (qui: `ModuleNotFoundError: No module named 'dgl'`) | — | — | GraphSAGE come upgrade, addestrato su CPU (`docs/results/us-202-graphsage.md`); B2 rinviato |
| SPK-G4 ridotto | entrambi | sotto | `guided-JSON request N during training: pass` per ogni richiesta; XGBoost esce 0; `/health` risponde; 0 righe di errore nel log | `g4_xgb.json`, `g4_llm_<n>.json`, `g4_amdsmi.log`, `logs/g4_*.log` | training a vLLM spento |

Per G5 la foto: dopo l'estrazione (2.3) è già in `artifacts/spikes/garment.jpg` (il tarball della
2.2 la porta lì); se è stata copiata altrove, `mv` nella cartella git-ignorata, mai una copia nella
radice (albero git pulito, 7.1).

**SPK-G4 ridotto.** `g4_coexistence.sh` lancia i job con `docker compose`, quindi non gira. La
stessa prova senza la metà DGL, con i comandi che lo script lancia, a mano (derivata dallo
script, non una fonte: va annotata come "G4 ridotto, senza DGL"): con vLLM healthy, tre
terminali di `tmux` (o `&`), dalla radice:

```bash
# 1. il campionatore, come nello script (amd-smi sull'host)
export PATH="$PATH:/opt/rocm/bin"
(while true; do echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"; amd-smi metric --json 2>&1; amd-smi process 2>&1; sleep 5; done) > artifacts/spikes/g4_amdsmi.log &
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# 2. XGBoost sulla GPU per almeno 300 s (.venv-bench)
. .venv-bench/bin/activate
python infra/spikes/g2_xgboost_gpu.py --device gpu --min-seconds 300 --context "SPK-G4 reduced: vLLM serving, no DGL" --out artifacts/spikes/g4_xgb.json > artifacts/spikes/logs/g4_xgb.log 2>&1 &
xgb=$!
deactivate
# 3. una richiesta guided-JSON ogni 45 s finché XGBoost gira (.venv)
. .venv/bin/activate; n=0
while kill -0 "$xgb" 2>/dev/null; do sleep 45; n=$((n + 1))
  if python infra/spikes/g1_guided_json.py --runs 2 --out artifacts/spikes/g4_llm_$n.json > artifacts/spikes/logs/g4_llm_$n.log 2>&1; then echo "guided-JSON request $n during training: pass"; else echo "guided-JSON request $n during training: FAIL (see artifacts/spikes/logs/g4_llm_$n.log)"; fi
done
wait "$xgb"; echo "XGBoost job exit $?"
curl -fsS -o /dev/null http://127.0.0.1:8001/health && echo "vLLM health 0"
grep -i -E 'out of memory|hipError|HIP error|Traceback' artifacts/spikes/logs/vllm.log | wc -l     # 0 atteso (conta tutto il log, non solo da $since)
kill %1 2>/dev/null
```

Criterio come nello script: XGBoost esce 0, ogni richiesta passa, vLLM è ancora healthy, nessuna
riga di errore. Annotare (riga SPK-G4): VRAM di picco da `g4_amdsmi.log`, `train_s_median` di
`g4_xgb.json` contro `g2_gpu.json` (sotto carico si confrontano le mediane), richieste passate,
e "senza DGL".

Da mandare a Claude: tutto `artifacts/spikes/` (i JSON, `logs/*.txt`, `logs/g4_*.log`,
`g7-vllm-log.txt`, `g4_amdsmi.log`), **mai** `logs/vllm.log` non oscurato (4.4), più i tempi
misurati e il contesto di ogni metà di G2.

## 6. Bozze e pacchetto (Demo export 2-7)

Tutto con `.venv`, dalla radice, dopo `set -a; . ./.env; set +a` (che porta `DEVICE=gpu`,
`LLM_MODE=vllm`, `DEMO_MODE=live`, `SERVING_DIR=serving`, `VLLM_BASE_URL`, `VLLM_API_KEY`,
`GEMMA_MODEL`, `DATABASE_URL`). `serving/` è quello del portatile (sezione 2); se nel frattempo
è stato ricostruito, va ricopiato **prima** del batch, perché una ricopia con `rsync --delete`
cancella `serving/llm_cache/`.

### 6.1 Il reset (facoltativo qui, obbligatorio per lo stack live)

```bash
. .venv/bin/activate
python -m apps.api.reset            # exit 0; 1 se vLLM è mancato per qualche bozza (template etichettato); 2 snapshot illeggibile
```

Su SQLite con il mock, qui, 1 min 32 s per 623 resi (misurato il 16 ottobre, 4 vCPU condivise,
senza GPU); con Gemma via vLLM il tempo è quello delle 623 bozze, `LLM_CONCURRENCY` (32) in volo,
non misurato. Il riepilogo JSON finale ha `"returns": 623`, `"failed": {}`, `generation` con
`"model": 623`. Non serve per il batch (6.2), che legge lo snapshot, non il database.

### 6.2 Le bozze: `python -m apps.api.batch_generate` (Demo export 4; 30-60 min stimati)

Il comando del servizio `batch-generate` del compose (`python -m apps.api.batch_generate`), con
`DEVICE=gpu`, `LLM_MODE=vllm`, `DEMO_MODE=live` dall'`.env` dell'host e `serving/` scrivibile:

```bash
python -m apps.api.batch_generate            # opzioni: --limit N (i primi N, casi d'oro per primi), --force (rigenera anche ciò che è in cache)
```

Che cosa fa (docstring): chiede a vLLM `/v1/models` e **si ferma (uscita 2)** se `GEMMA_MODEL` non
è tra i modelli serviti (`the batch needs GEMMA_MODEL on the local vLLM: ...`); per ogni reso
aperto, casi d'oro per primi, costruisce l'`llm_case`, lo manda a vLLM e scrive
`serving/llm_cache/<sha256>.json`; ogni voce registra il modello e il `DEVICE` del batch (qui
`gpu`: per questo `DEVICE=gpu` sta in `.env`); una voce di un altro modello viene rigenerata; una
bozza caduta sul template perché vLLM era irraggiungibile **non** va in cache. Sullo schermo:
`[batch  N.N s] <k> cases to generate, 32 at once`, righe ogni 25 casi, poi il riepilogo JSON con
`returns`, `paths`, `skipped_cached`, `failed`, `cache`.

| Uscita | Significato | Che cosa fare |
| --- | --- | --- |
| 0 | tutte le bozze in cache | avanti |
| 1 | `failed` non vuoto (`vLLM unavailable: not cached`, timeout 20 s, errore HTTP) | rilanciare: le voci in cache vengono saltate (`skipped_cached`); se persiste, `VLLM_TIMEOUT_S=60` o `LLM_CONCURRENCY=8` nell'ambiente |
| 2 | `LLM_MODE` non `vllm`, vLLM non configurato (`VLLM_API_KEY` vuota, URL non locale), server che non serve `GEMMA_MODEL`, snapshot illeggibile | correggere `.env` (`DEVICE`, `LLM_MODE`, `VLLM_BASE_URL`), vLLM e `.env` devono dire lo stesso `GEMMA_MODEL` |

### 6.3 Il pacchetto: `python3 -m scripts.demo_export build` (Demo export 5)

Il comando di `make demo-export` (sola libreria standard, qualsiasi `python3` 3.8 o successivo),
seguito dal `tar` del target:

```bash
python3 -m scripts.demo_export build --serving serving --bench bench/results --out demo-cache --generated-on "<gpu-name> (<host>)" --model "$GEMMA_MODEL"
tar -czf demo-cache.tar.gz demo-cache
sha256sum demo-cache.tar.gz
```

Che cosa controlla e rifiuta (uscita 1, `demo-export: ...` su stderr): la tabella del runbook AMD
10.4, invariata (snapshot contro il suo manifest, nessun percorso `truth`, cache vuota, bozze di
un altro modello, bozze di un batch con `DEVICE` diverso da `gpu`, `demo-cache/` sporca,
puntatore benchmark rotto). Quello che cambia è `--generated-on`, perché l'etichetta della demo
è un'affermazione sull'hardware (`apps/api/services/demo_package.py`):

| GPU dell'host | `--generated-on` | Flag | Etichetta in `/readyz` e nella UI | Perché |
| --- | --- | --- | --- | --- |
| una MI300X (Hot Aisle, o il notebook se lo è) | `"AMD Instinct MI300X (Hot Aisle)"`, oppure `"AMD Instinct MI300X (AMD AI Notebooks)"`: deve contenere `MI300X` | nessuno | `Precomputed on AMD MI300X` | `MANIFEST.json`: `rehearsal` `false`, `generated_on` con `MI300X`, `gemma_model` = `GEMMA_MODEL`, ogni bozza di quel modello con `device` `gpu` |
| un'altra GPU AMD | il nome vero, per esempio `"AMD Instinct MI250X (AMD AI Notebooks)"` | `--rehearsal` | `Precomputed in a rehearsal, not verified on AMD MI300X` | senza `--rehearsal` l'export rifiuta (`name the GPU the batch ran on: ... (got '...')`); con `MI300X` scritto a mano sarebbe falso (regole 11 e 13). Con lo script: `REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu bash scripts/release/07-host-session.sh export` (le bozze sono di Gemma su quella GPU, `device` `gpu`, pacchetto `--rehearsal`); `REHEARSAL=1` da solo usa il mock e `DEVICE=cpu` e non fa nessuna bozza |
| una terza etichetta ("Precomputed on AMD <GPU>") | — | — | **non esiste**: `CachedLabel` in `apps/api/schemas.py` ammette solo le due stringhe sopra, e la UI le mostra tali e quali | servirebbe una modifica del contratto (schema, esempi, UI, test): da chiedere a Claude prima della sessione, non durante |

Atteso quando passa: un JSON con `out`, `files`, `as_of`, `llm_cache` (`entries`, `models`,
`generated_on`, `rehearsal`), `bench` (i puntatori). Il pacchetto prende anche `bench/results/`:
se la sezione 7 è stata fatta prima e ha spostato `latest.json`, il pacchetto porta i benchmark
GPU; altrimenti solo `latest-cpu.json` e la sua corsa (etichettata, mai servita come latest), e
la demo mostra i benchmark come `Pending`.

### 6.4 Portare `demo-cache.tar.gz` sul portatile e sul server della demo

| Host | Come |
| --- | --- |
| Hot Aisle | dal portatile: `scp <user>@<host-ip>:ReturnRing/demo-cache.tar.gz .` |
| Notebook | nel pannello file di JupyterLab: tasto destro su `demo-cache.tar.gz` > Download (da verificare: dimensione, decine di MB stimati) |

Poi, come nel runbook AMD 10.5 e 10.6: sul portatile `sha256sum demo-cache.tar.gz` (uguale a
quello dell'host), `make demo-import demo-local` (`demo-verify: demo-cache matches its
MANIFEST.json`, poi `curl -s http://127.0.0.1:8000/readyz` con `"mode":"cached"`, l'etichetta della
tabella 6.3, `"public_read_only":true`); sul server della demo `scp demo-cache.tar.gz
root@<ip-server-demo>:ReturnRing/` e `make demo-down && make demo-import demo-up`; la release su
GitHub con lo sha256.

Da mandare a Claude: il riepilogo JSON di `batch_generate`, `demo-cache/MANIFEST.json`, lo sha256
del tar, l'output di `make demo-import` sul portatile, il JSON di `/readyz` del server dopo il
cambio, e la stringa esatta di `--generated-on`.

## 7. Benchmark senza Docker (B1, B4, B3; B2 saltato)

Fonti: `bench/__main__.py` (il `--help` provato), `docs/benchmarks.md`, i target `bench-*` del
Makefile (letti con `make -n`). Sul droplet B1 gira nel container `bench` e B2 in
`trainer-graphsage`; qui B1, B4, B3, `first-request`, `merge` e `report` girano in `.venv-bench`
(che ha la stessa ricetta dell'immagine `bench`), e **B2 non gira**: `ml.gnn.b2_timing` esegue
`python -m ml.gnn asos` che vuole PyTorch e DGL dell'immagine `rocm/dgl`; `python -m bench run`
lo scriverebbe come `skipped` con il motivo di `ml.gnn.b2_unavailable()` (nessun PyTorch), ma
non si chiede nemmeno: `--parts B1,B3,B4`, e il merge lo proietta come `pending` con la nota
`Not run in run <run-id>` (`bench/api_view.py`). Il piano B della spec 13 vale: GraphSAGE come
upgrade, B2 rinviato, i numeri CPU di `docs/results/us-202-graphsage.md` restano.

### 7.1 Le regole del blocco (runbook AMD, 9.1, adattate)

- **vLLM giù per B1**: la sua metà CPU gira da sola sui vCPU dell'host e la metà GPU da sola
  sulla GPU. `make bench-b1` si rifiuta di partire con vLLM acceso; qui il controllo è a mano:
  `kill -TERM -- -$(cat artifacts/spikes/logs/vllm.pid)` prima (il gruppo, 4.3),
  `curl -fsS http://127.0.0.1:8001/health` deve fallire e `pgrep -af 'vllm|EngineCore'` deve
  essere vuoto. B4 e B3 vogliono invece vLLM su.
- **Albero git pulito**: `git status --porcelain` vuoto prima di ogni parte, altrimenti
  `BENCH_GIT_DIRTY=1` e `git.dirty: true` nel file della corsa senza che il codice sia cambiato
  (regola 13): per questo i venv sono git-ignorati (`.venv/`, `.venv-*/`), la foto sta in
  `artifacts/spikes/`, il tarball nella home e `cloudflared` in `~/bin/` (2.2, 3.3, 8.3).
- **Un solo `BENCH_RUN`**: `export BENCH_RUN=$(date -u +%Y%m%dT%H%M%SZ)-gpu` nella shell di tmux;
  `bench/record.py` accetta `<yyyymmddThhmmssZ>-<cpu|gpu>[-tag]` e rifiuta un id `gpu` con
  `--device cpu`. Se la shell cade, `ls artifacts/bench/` mostra l'id da riesportare. Niente
  `git pull` tra una parte e il merge: `env.git_info()` scrive il commit al momento del merge.
- **Le variabili**: `set -a; . ./.env; set +a` prima di ogni comando porta `VLLM_BASE_URL`,
  `VLLM_API_KEY`, `GEMMA_MODEL`, `GEMMA_REVISION`, `DEVICE=gpu`, `LLM_MODE=vllm`, `SEED`
  (`compose.bench.yml` le passa al container); `BENCH_GIT_COMMIT` e `BENCH_GIT_DIRTY` le calcola
  il Makefile: qui `export BENCH_GIT_COMMIT=$(git rev-parse HEAD)` e
  `export BENCH_GIT_DIRTY=$(git status --porcelain | grep -q . && echo 1 || echo 0)`, altrimenti
  `env.git_info()` li legge da `git` nella radice (va bene lo stesso).
- **`PATH` con `/opt/rocm/bin`**: `amd-smi` serve a `env.gpu_static()` (nome, architettura e
  VRAM della GPU nel campo `hardware` di ogni parte) e al campionatore di B4.
- **Stati e rilancio**: ogni parte in `artifacts/bench/$BENCH_RUN/<B>.json` dice `measured`,
  `skipped` o `failed`; una parte fallita non ferma la successiva (`python -m bench run` esce 1);
  si rilancia dal passo fallito con lo stesso `BENCH_RUN`, le parti già scritte restano.
- **Tempo**: non misurato. Ordine di grandezza (non misure di B1 e B4): la catena simile di
  `make demo-data` ha preso 9 min 39 s su 4 vCPU condivise (commento del Makefile); B4 aggiunge
  scoring e la generazione di 623 bozze via vLLM. B1 dipende dai vCPU dell'host (sezione 1.2).

### 7.2 I passi, in ordine

Tutti in `.venv-bench`, dalla radice, `$RUN` è `artifacts/bench/$BENCH_RUN`:

| # | Comando | vLLM | Che cosa fa (dal Makefile e dal `--help`) | Righe da guardare | Scrive |
| --- | --- | --- | --- | --- | --- |
| 1 | `kill -TERM -- -$(cat artifacts/spikes/logs/vllm.pid)` (4.3); `export BENCH_RUN=$(date -u +%Y%m%dT%H%M%SZ)-gpu`; `export PATH="$PATH:/opt/rocm/bin"`; `set -a; . ./.env; set +a`; `. .venv-bench/bin/activate` | giù | un id per tutta la sessione, l'ambiente | `echo $BENCH_RUN` | nulla |
| 2 | `python -m bench run --device gpu --parts B1 --run-id $BENCH_RUN --raw data/raw --no-merge` (il comando di `make bench-b1`) | giù | B1: XGBoost su CPU e GPU (`cuda` su ROCm), protocolli A e B, taglie 10/25/50/100%, 200 round fissi e early stopping, 3 ripetizioni, scoring | `B1 cuda: cold start ...`; nessun `GPU not used` (`train.check_device` rifiuta la corsa) | `$RUN/B1.json` |
| 3 | `mkdir -p $RUN`; `if sync && { echo 3 > /proc/sys/vm/drop_caches; } 2>/dev/null; then echo "page cache dropped before the start"; else echo "page cache not dropped (a warm restart)"; fi > $RUN/vllm-start-conditions.txt`; `cat $RUN/vllm-start-conditions.txt`; `date -u +%Y-%m-%dT%H:%M:%SZ \| tee $RUN/vllm-start.txt > artifacts/spikes/logs/vllm-start.txt`; `export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"`; poi il blocco di 4.3 tale e quale (non scrive `vllm-start.txt`) | parte | quello che `make bench-vllm-start` fa: le condizioni dell'avvio (la page cache si scarica solo da root: sul notebook probabilmente no, e lo dice), l'ora di avvio per il cold start di B3 | `page cache dropped ...` o `not dropped` | `$RUN/vllm-start-conditions.txt`, `$RUN/vllm-start.txt` |
| 4 | il ciclo di `/health` della sezione 4.4; poi `cp artifacts/spikes/logs/vllm-healthy.txt $RUN/vllm-healthy.txt`; `python -m bench first-request --run-id $BENCH_RUN` | su | `make bench-vllm`, seconda metà: l'ora healthy e la prima richiesta guided-JSON prima che altro usi vLLM | `first request done -> $RUN/first-request.json`; dentro `"ok": true` | `$RUN/vllm-healthy.txt`, `$RUN/first-request.json` |
| 5 | `mkdir -p bench-work`; `python -m bench run --device gpu --parts B4 --run-id $BENCH_RUN --no-merge` | su | B4: la pipeline notturna (generate, graph, features, anomalies, risk, scoring su CPU; `batch_generation` via vLLM) in `bench-work/$BENCH_RUN/`; il campionatore amd-smi è `inprocess` di default con `--device gpu` (un thread del processo lancia `amd-smi metric --json` ogni 5 s), l'equivalente senza container del campionatore esterno di `make bench-b4` | `B4 <step>: N s` per ogni passo; `batch_generation` esce 1 se vLLM è mancato per una decisione | `$RUN/B4.json`, `$RUN/amdsmi.jsonl`, `$RUN/b4-logs/`, `bench-work/$BENCH_RUN/` (verità cancellata alla fine) |
| 6 | `df -BG --output=used / \| tail -1 \| tr -dc '0-9' > $RUN/disk-used-gb.txt`; `python -m bench run --device gpu --parts B3 --run-id $BENCH_RUN --no-merge` | su | `make bench-b3`: il disco usato (il costo cost-v2 lo legge al posto dell'ipotesi di 220 GB; qui non c'è uno snapshot, ma la riga resta la stessa), poi B3: latenza p50/p95 e token/s, batch 1, 8, 32 e concorrenza 4, 16, sulla lista di casi del negozio di B4 | `failed` 0 in ogni carico di `B3.json` | `$RUN/disk-used-gb.txt`, `$RUN/B3.json` |
| 7 | `python -m bench merge --run-id $BENCH_RUN`; `python -m bench report --run bench/results/$BENCH_RUN.json --out $RUN/report.md` | qualsiasi | `make bench-merge`: il file della corsa, i puntatori, il rapporto | `wrote bench/results/$BENCH_RUN.json and bench/results/latest.json` | `bench/results/$BENCH_RUN.json`, `latest.json`, `latest-gpu.json`, `$RUN/report.md` |

Opzioni di `python -m bench run --help` per una sessione corta: `--b1-repeats 1`,
`--b1-fractions 0.1,0.5,1`, `--b3-batches 1,8,32 --b3-concurrency 4,16 --b3-repeats 2`,
`--b3-timeout 180`, `--threads N`. Il passo 7 va fatto **prima** della sezione 6.3, così il
pacchetto porta `latest.json` della corsa GPU (ma leggere 7.3 prima di farlo su una GPU che non è
una MI300X).

### 7.3 Che cosa dicono poi i risultati, onestamente

- **Natura e device**: ogni misura porta origine, periodo e natura (regola 11): un tempo misurato
  su hardware vero è `real` anche su dati sintetici; il costo cost-v2 è `simulated`. Il campo
  `hardware` di ogni parte ha CPU, vCPU, RAM, kernel, `in_container` `false` e, da `amd-smi
  static --json`, `market_name`, `target_graphics_version` e la VRAM della GPU: è lì che si legge
  che cosa ha girato davvero.
- **Le etichette "MI300X" sono scritte nel codice**: con `--device gpu`, B1 etichetta il device
  GPU `MI300X (<gpu_name>)` (`bench/b1_xgboost.py`), B4 scrive `the MI300X droplet (the GPU
  through vLLM)` e `amd-smi readings of the MI300X` (`bench/b4_pipeline.py`), B3 `MI300X droplet`
  (`bench/b3_llm.py`), il rapporto titola `run ... (MI300X droplet)` e le colonne `CPU / MI300X`
  (`bench/report.py`), e `GET /api/benchmarks` serve i titoli `CPU against MI300X`
  (`bench/api_view.py`). **Su una MI300X è corretto. Su un'altra GPU no**: i numeri sono veri, le
  etichette false. In quel caso si fanno i passi 1-6 (le parti grezze in `$RUN/` dicono la GPU
  vera in `hardware`), **non** il passo 7 sull'host, e si manda tutto a Claude: il merge e il
  rapporto si fanno sul portatile dopo una correzione delle etichette, che non è un ritocco di
  testo ma **una modifica del contratto**: `contracts/json/api/benchmarks.schema.json` ammette
  per `device` solo `cpu|mi300x`, `bench/record.py` lo ripete (`API_DEVICES`) e `bench/b3_llm.py`
  scrive `mi300x` per ogni corsa live; serve quindi uno schema nuovo con una nota in `docs/adr/`
  (CLAUDE.md: un contratto cambia solo con una nota là), **da decidere con Claude prima della
  sessione**, come la terza etichetta della demo (6.3). Senza quella decisione, su una GPU
  diversa i benchmark non si pubblicano né si servono (`GET /api/benchmarks` li rifiuterebbe), e
  pagarne la corsa serve solo alle parti grezze. Nessun `latest.json` di quella corsa va sul
  server della demo finché le etichette non sono giuste.
- **Nessun digest di immagine**: `software` registra `vllm_image`, `dgl_image`, `rocm_base_image`
  solo se le variabili sono nell'ambiente (`bench/env.py`); con `.env` dell'host ci sono (sono le
  righe di `.env.example`), ma **non descrivono nulla che abbia girato**. Prima del passo 1,
  `unset VLLM_IMAGE DGL_IMAGE ROCM_BASE_IMAGE` dopo `set -a; . ./.env; set +a`, così il file
  della corsa non le riporta; la versione di vLLM che ha girato sta in `B3.json` (`server`:
  `vllm_version` da `/version`, `served_model`, `max_model_len`), la ROCm in `software.rocm`, la
  ruota amd_xgboost in `xgboost 3.2.0`, `xgboost_use_hip true`. Il commit e i content hash delle
  cartelle di codice (`code_bench`, `code_ml`, ...) ci sono comunque.
- **Il cold start di B3**: "on a running droplet, not a start from a snapshot" nel codice; qui
  non c'è né droplet né snapshot: `vllm-start-conditions.txt` dice se la page cache è stata
  scaricata, e il testo di `docs/results/benchmarks-mi300x.md` va letto come "su un host acceso,
  pesi già su disco". Il costo cost-v2 (`bench/cost.py`) ipotizza 1,99 USD/ora e uno snapshot:
  su Hot Aisle il prezzo è 2,99 USD/GPU/ora e non c'è snapshot; le ipotesi sono dichiarate nel
  `details` di B3 e il numero del costo va mostrato con quelle, non riadattato a mano (una
  cost-v3 è una modifica di codice).
- **amd-smi assente** (`command -v amd-smi` vuoto anche con `/opt/rocm/bin`): il campionatore
  `inprocess` scrive ogni 5 s un campione con `"error": "FileNotFoundError: ... 'amd-smi'"` e
  `"gpus": []`, in silenzio (la riga `amd-smi sampler: first sample failed (...); going on`,
  provata qui, la stampa solo il campionatore esterno `python3 -m bench.amdsmi --out ...`, quello
  di `make bench-b4` e dello script 07, non il thread `inprocess` del passo 5), B4 ha
  `n` 0 ed `errors` > 0 in ogni finestra e **nessun numero GPU**; `hardware.gpu` è `null`. Non
  si inventa nulla: il rapporto lo mostra così, e va detto. `rocm-smi` non è un sostituto nel
  codice.
- **B2**: `pending`, `Not run in run <run-id>`; la riga US-202 della spec resta "tempi MI300X da
  misurare".
- **I file da committare** sono gli stessi del runbook AMD 12.4 (`bench/results/$BENCH_RUN.json`,
  `latest.json`, `latest-gpu.json`, `docs/results/benchmarks-mi300x.md` da `make bench-report
  BENCH_RUN=$BENCH_RUN DEVICE=gpu` sul portatile); mai `artifacts/`, `bench-work/`.

| Sintomo | Che cosa fare |
| --- | --- |
| B1 si ferma con `GPU not used: requested cuda, effective device 'cpu'` | la GPU non è visibile a `.venv-bench` (3.3: `ldd`, `LD_LIBRARY_PATH`); non si riporta mai la CPU come GPU |
| `run id ... is not a gpu run` | `BENCH_RUN` senza `-gpu`, o `--device` sbagliato |
| B3 fallisce leggendo `scores/request_scores` | B4 saltato o fallito prima dello scoring: rilanciare il passo 5 con lo stesso `BENCH_RUN`; senza B4, `--data data` non basta (sull'host `data/` ha solo `raw/`) |
| B4: `batch_generation` esce 1 | vLLM mancato per qualche decisione: `vllm.redacted.log`, rilanciare il passo 5 |
| B3: `failed` > 0 in un carico | timeout o errori sotto concorrenza: tenere il numero con la nota; `--b3-timeout` più alto per una seconda corsa |
| `python -m bench merge`: `no part in artifacts/bench/<run-id>` | `BENCH_RUN` diverso da quello dei passi 2-6 |

Da mandare a Claude: `bench/results/*.json` (se il passo 7 è stato fatto), tutto
`artifacts/bench/$BENCH_RUN/` (le parti, `report.md`, `amdsmi.jsonl`, `b4-logs/`,
`first-request.json`, i file `vllm-*.txt`, `disk-used-gb.txt`), il valore di `BENCH_RUN`, il nome
della GPU da `hardware`, e se la page cache è stata scaricata.

## 8. Lo stack live per il video (senza porte pubbliche)

Sul droplet era `make gpu-up` (postgres, reset, api, ui, Caddy su HTTPS). Qui: l'API con `uvicorn`
sull'host in loopback, la UI sul portatile in modalità live che raggiunge l'API attraverso un
tunnel, la camminata del pitch sull'host stesso, `amd-smi` in un terminale. Niente basic auth,
niente HTTPS: l'API è aperta solo sul loopback dell'host (SSH) o, per il tempo della
registrazione, su un URL casuale (quick tunnel).

### 8.1 L'API sull'host (circa 5 min più il reset)

Quello che `apps/ui/README.md` ("Live end-to-end tests") fa senza Docker, con le variabili
dell'host:

```bash
set -a; . ./.env; set +a                     # DEVICE=gpu LLM_MODE=vllm DEMO_MODE=live SERVING_DIR=serving ...
export DEMO_CLOCK=on PUBLIC_READ_ONLY=off
. .venv/bin/activate
python -m apps.api.reset                     # i casi d'oro decisi e le bozze di Gemma via vLLM; exit 0
nohup uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 > artifacts/spikes/logs/api.log 2>&1 &
echo $! > artifacts/spikes/logs/api.pid
for i in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8000/healthz 2>/dev/null && break; sleep 2; done
curl -s http://127.0.0.1:8000/healthz; echo
curl -s http://127.0.0.1:8000/readyz
```

Atteso (provato qui con il mock e `DEVICE=cpu`: `{"status":"ok"}` da `/healthz`; `/readyz` con
`"status":"ready"`, `"mode":"live"`, `"public_read_only":false`, `"contract_version":"1.8.1"`,
`database` `ok`, `scores` `ok`, `models` `ok`). Sull'host, in più: `"llm_mode":"vllm"`,
`"device":"gpu"`, `"label":null`, `vllm` `ok` (il probe controlla che `GEMMA_MODEL` sia servito),
`demo_cache` `disabled`, `telemetry` `unavailable` e quindi `"status":"degraded"` finché non
gira il campionatore della 8.6: con un campione negli ultimi 60 s `telemetry` passa a `ok` e lo
stato a `ready` (`telemetry.py`: senza campione lo stato è `unavailable` con `DEVICE=gpu` e
`disabled` solo con `DEVICE=cpu`, com'era nella prova qui; `health.py`: `degraded` se un
componente è `unavailable`). L'API serve normalmente in entrambi i casi. `"status":"degraded"`
con `vllm` `unavailable` è un'altra cosa: vLLM giù o un altro `GEMMA_MODEL`, l'API serve i
template. L'API tiene lo snapshot in memoria (circa 1,6-2,2 GB residenti, commento del compose,
misurato una volta su CPU).

### 8.2 Il tunnel SSH (Hot Aisle)

Dal portatile, in un terminale che resta aperto per tutta la registrazione (comando SSH
standard, lo stesso della strada 1 del runbook AMD 11.3):

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<host-ip>
```

Da quel momento `http://127.0.0.1:8000` sul portatile è l'API dell'host:
`curl -s http://127.0.0.1:8000/readyz` dal portatile deve rispondere come sopra. Niente è esposto
oltre al portatile.

### 8.3 Il quick tunnel (notebook senza SSH; da verificare sull'host)

`cloudflared` non è nelle fonti del progetto. Un "quick tunnel" apre un URL pubblico casuale
`https://<parole-casuali>.trycloudflare.com` verso una porta locale, senza account. Sull'host:

```bash
mkdir -p ~/bin && curl -L -o ~/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/bin/cloudflared   # fuori dal repository (albero git pulito, 7.1); da verificare: URL, architettura; licenza Apache-2.0
~/bin/cloudflared tunnel --url http://127.0.0.1:8000
```

Stampa, tra le righe di log, l'URL `https://<parole-casuali>.trycloudflare.com`. Da sapere
**prima** di lanciarlo:

- **espone l'API senza autenticazione a chiunque conosca l'URL**, con le scritture attive
  (`PUBLIC_READ_ONLY=off`: approvazioni, dispute, attività di catalogo, ordini di prova). Si
  apre solo per il tempo della registrazione, con il terminale sotto gli occhi, e si chiude con
  Ctrl-C appena finito;
- dopo la chiusura: `python -m apps.api.reset` (riporta il database allo stato del reset: se
  qualcuno ha scritto, sparisce) e, se si sospetta altro, si ferma anche l'API;
- **l'URL non va in nessun file**, non nel repo, non nel video (la barra del browser mostra l'URL
  di Vite sul portatile, non quello del tunnel), non a Claude, non su Discord, e non nella
  cronologia della shell del portatile, che è un file (`~/.bash_history`, `~/.zsh_history`):
  i comandi di 8.4 e 8.5 che lo contengono si danno con `HISTCONTROL=ignorespace` e uno spazio
  davanti, o in un terminale con `unset HISTFILE`;
- il servizio è "best effort" di Cloudflare: velocità, limiti e durata non sono garantiti (da
  verificare); l'agente usa uno stream (SSE) e la camminata lo verifica frame per frame: se lo
  stream non passa dal tunnel, le tre domande si fanno sull'host con la camminata (8.5) e nel
  video si mostra la risposta dal terminale;
- in alternativa, se il portatile ha un indirizzo raggiungibile dall'host (SSH sul portatile,
  da verificare), un tunnel inverso dall'host `ssh -R 8000:127.0.0.1:8000 <utente>@<portatile>`
  evita l'URL pubblico (comando SSH standard, non nelle fonti).

### 8.4 La UI sul portatile

`apps/ui/vite.config.ts`: in modalità live il server di sviluppo inoltra `/api`, `/healthz` e
`/readyz` a `RETURNRING_API_URL` (`http://127.0.0.1:8000` di default). Dalla radice del
repository sul portatile, con `npm ci` già fatto:

```bash
cd apps/ui && RETURNRING_API_URL=http://127.0.0.1:8000 npm run dev:live      # tunnel SSH (8.2)
export HISTCONTROL=ignorespace    # quick tunnel (8.3): la cronologia della shell è un file, l'URL non deve finirci
 cd apps/ui && RETURNRING_API_URL=https://<parole-casuali>.trycloudflare.com npm run dev:live   # lo spazio iniziale lo tiene fuori dalla cronologia; da verificare: inoltro verso https
```

Vite stampa l'URL locale da aprire nel browser (di norma `http://localhost:5173`, da leggere
dall'output). Con `RETURNRING_API_URL=mock` la UI servirebbe gli esempi: **non** è il live.
`npm run build` e `vite preview` sono l'alternativa di produzione (`apps/ui/README.md`), stesso
`RETURNRING_API_URL`.

### 8.5 La camminata del pitch

A differenza del droplet, l'host ha il Python del progetto: la camminata gira **sull'host**,
contro il loopback, senza tunnel e senza proxy (il problema dei 401 del runbook AMD 11.3 non
esiste qui):

```bash
. .venv/bin/activate
python -m apps.api.reset                    # la camminata approva casi: un reset prima di ogni corsa
python -m scripts.demo_walk --base-url http://127.0.0.1:8000 --report artifacts/demo-walk.json
```

Atteso (provato qui con il mock: 43 s, exit 0): `passed` con i 12 passi `readiness`,
`demo_cases`, `queue`, `giulia`, `marco`, `checkout`, `rossi`, `elena`, `insufficient`,
`dispute`, `agent`, `manager`; `"failed": {}`; `queue_first_page_s` sotto 2 (US-301),
`checkout_evaluate_s` sotto 1 (US-403). Sull'host con Gemma: le bozze sono di Gemma (`facts`,
`llm_mode` `vllm`), e le tre domande dell'agente passano da vLLM. Dal portatile, attraverso il
tunnel, lo stesso comando con `--base-url http://127.0.0.1:8000` (SSH) o
`--base-url https://<parole-casuali>.trycloudflare.com` (quick tunnel) prova il tunnel stesso;
con il quick tunnel, `export HISTCONTROL=ignorespace` e uno spazio davanti al comando (o
`unset HISTFILE` in quel terminale): la cronologia della shell è un file e l'URL non deve
finirci (`artifacts/demo-walk.json` non lo contiene: `demo_walk.py` lo usa solo nel client).
Dopo la camminata, **un altro reset** prima di registrare: i casi d'oro devono essere appena
decisi.

### 8.6 amd-smi e la telemetria per il video

- Il terminale nell'angolo (spec 16, V1; guida al rilascio 4.2), sull'host:

  ```bash
  export PATH="$PATH:/opt/rocm/bin"
  watch -n 2 amd-smi monitor
  ```

  Su Hot Aisle è un secondo `ssh`; sul notebook una seconda scheda Terminal di JupyterLab,
  registrata insieme al browser (la finestra di JupyterLab è lei stessa nel browser).
- La pagina `/system` della UI (readiness, versioni, benchmark, telemetria ogni 5 s) ha i dati
  solo se gira il campionatore della telemetria, che sul droplet era il container `telemetry` più
  `telemetry-sampler`. Qui, facoltativo, in `.venv` con `DATABASE_URL` e `/opt/rocm/bin` nel
  PATH (`python -m apps.api.services.other.telemetry --help`): lancia lui `amd-smi metric --json`
  ogni 5 s e scrive in `telemetry_samples`:

  ```bash
  nohup python -m apps.api.services.other.telemetry --device "<gpu-name>" > artifacts/spikes/logs/telemetry.log 2>&1 &
  ```

  `--device` ha come default `AMD Instinct MI300X`: su un'altra GPU va passato il nome vero
  (regola 11). Dopo qualche campione `/readyz` mostra `telemetry` con un campione recente.
  (`DEFAULT_DEVICE` e il default di `--command` vengono da `telemetry.py`; il comando è
  derivato dal `--help`, da verificare sull'host.)

### 8.7 La registrazione (guida al rilascio 4.2; spec 16)

- Prima: `python -m apps.api.reset` sull'host (sostituisce `make gpu-reset`), vLLM healthy, l'API
  su, il tunnel su, la UI del portatile aperta sull'URL di Vite con le schede nell'ordine dei
  beat, il terminale con `watch -n 2 amd-smi monitor`.
- OBS o QuickTime a 1920x1080; browser a 1440x900 sull'URL di Vite (non su `https://<PUBLIC_HOST>`,
  che qui non esiste); il terminale `amd-smi` in un angolo durante le generazioni.
- Scaletta (spec 16, al massimo 5 minuti, sotto i 300 MB): 0:00-0:30 il problema e i quattro
  casi; 0:30-3:30 il prodotto vivo sulla GPU (i quattro momenti, l'agente che risponde a una
  domanda, l'ispezione con una foto, amd-smi nell'angolo); 3:30-4:15 architettura, ablazione e
  benchmark; 4:15-5:00 valore, limiti dichiarati, prossimi passi. Nel parlato e nelle slide la GPU
  si chiama con il nome di `amd-smi static`: "MI300X" solo se lo è.
- Una seconda registrazione di riserva sul server cached.
- Compressione: `ffmpeg -i registrazione.mov -vf scale=1920:-2 -c:v libx264 -crf 23 -preset slow
  -c:a aac -b:a 128k video.mp4`; link verificato da un browser anonimo.
- Tre prove del pitch cronometrate (3 minuti): `python -m apps.api.reset` pronto; la cached su un
  secondo URL; il video scaricato sul portatile.
- Con il quick tunnel: Ctrl-C su `cloudflared` appena finito, poi reset (8.3).

### 8.8 L'orologio della demo

Come nel runbook AMD 11.6: con le scritture attive `DEMO_CLOCK=on` fa correre il "now" dal reset;
dopo due giorni dal reset i rimborsi dei resi aperti risultano scaduti e gli atti rispondono 409:
un reset prima di ogni sessione di prove o di registrazione.

Da mandare a Claude: il JSON di `/readyz` dell'host, l'output del reset, `artifacts/demo-walk.json`,
quale tunnel è stato usato (mai l'URL del quick tunnel), e se la UI del portatile ha mostrato
tutte le schermate attraverso il tunnel.

## 9. Chiusura

### 9.1 Che cosa riportare sul portatile

Gli stessi file del runbook AMD 12.1, senza quelli di A4 e B2 (che qui non esistono):

| File | Chi lo legge sul portatile |
| --- | --- |
| `artifacts/spikes/*.json`, `artifacts/spikes/logs/*.txt`, `logs/g4_*.log`, `g7-vllm-log.txt`, `g4_amdsmi.log`, `host.json`, `logs/vllm.redacted.log` | Claude, per `docs/spike-results.md` |
| `bench/results/$BENCH_RUN.json`, `latest.json`, `latest-gpu.json` (solo se il passo 7 della sezione 7.2 è stato fatto) | `make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu`; `GET /api/benchmarks` |
| `artifacts/bench/$BENCH_RUN/` | le parti grezze, `report.md`, i campioni amd-smi, i log |
| `demo-cache.tar.gz` (e `demo-cache/MANIFEST.json`) | `make demo-import`, la release, il server della demo |
| `artifacts/demo-walk.json` | la prova dello stack live |

Come: Hot Aisle, dal portatile, `scp -r <user>@<host-ip>:ReturnRing/artifacts/spikes ./artifacts/`,
`scp <user>@<host-ip>:"ReturnRing/bench/results/*.json" ./bench/results/`,
`scp -r <user>@<host-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/`,
`scp <user>@<host-ip>:ReturnRing/demo-cache.tar.gz .`; notebook: sull'host
`tar --exclude=artifacts/spikes/logs/vllm.log -czf returnring-results.tar.gz artifacts/spikes artifacts/bench bench/results artifacts/demo-walk.json demo-cache/MANIFEST.json`
(l'`--exclude` tiene fuori il log di vLLM non oscurato), poi Download dal pannello, insieme a
`demo-cache.tar.gz`. Sul notebook conviene farlo **appena ogni blocco
finisce**, non alla fine (sezione 1.4). `$BENCH_RUN` sul portatile va impostato a mano. Poi sul
portatile: `make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu` (dopo l'eventuale correzione delle
etichette, 7.3); si committano `docs/spike-results.md`, `bench/results/$BENCH_RUN.json`,
`latest.json`, `latest-gpu.json`, `docs/results/benchmarks-mi300x.md`; mai `artifacts/`,
`demo-cache/`, `demo-cache.tar.gz`, `.env`, `serving/`.

### 9.2 Fermare tutto

```bash
kill $(cat artifacts/spikes/logs/api.pid) 2>/dev/null
kill -TERM -- -$(cat artifacts/spikes/logs/vllm.pid) 2>/dev/null        # il gruppo di processi (4.3)
sleep 10; pgrep -af 'vllm|EngineCore' || echo 'no vllm process'          # vuoto; altrimenti pkill -TERM -f EngineCore
pkill -f "apps.api.services.other.telemetry" 2>/dev/null; pkill -f cloudflared 2>/dev/null
grep -c '^HF_TOKEN=$' .env                                   # 1: nessun token nel file
ls ~/.git-credentials ~/.netrc ~/.config/gh/hosts.yml 2>&1   # tutti "No such file"
test ! -d data/truth && echo 'no data/truth'
```

- **Hot Aisle**: dopo la copia (9.1), distruggere la VM dal pannello e controllare che non
  compaia più nella lista; scrivere l'**ora di distruzione** (UTC). Non c'è uno snapshot come sul
  droplet (da verificare sul pannello se esiste un "stop" che non fattura e che cosa conserva):
  il giorno dopo si rifà la sezione 3 e il download dei pesi (4.1) e la compilazione (4.3). Il
  `.env` dell'host muore con la VM: la chiave vLLM si rigenera.
- **Notebook**: fermare la sessione dal pannello se il pannello lo prevede; i file restano o
  no secondo la sezione 1.4. `.env` resta a `chmod 600` se il filesystem persiste; se la
  sessione è condivisa o non si sa, `rm .env` dopo la copia.

### 9.3 Tempi e costi

Hot Aisle: 2,99 USD per GPU all'ora, al minuto (da verificare sul pannello: prezzo, unità di
fatturazione, se una VM ferma costa). Ogni ora è una stima: gli stessi blocchi del runbook AMD,
sezione 14, meno quelli che qui non esistono (immagini, G3, B2, A4, snapshot), più la sezione 1.
Il costo è ore x 2,99, arrotondato.

| Blocco | Sezioni | Ore stimate | USD stimati | Che cosa lo fa crescere |
| --- | --- | --- | --- | --- |
| Accesso, inventario, clone, `.env`, i due venv, vLLM | 1, 3 | 0,3-0,6 | 0,9-1,8 | una ruota che non si installa, un vLLM da mettere in piedi con pip |
| Download dei pesi (62,6 GB) | 4.1 | non misurato: circa 11 min a 100 MB/s, circa 55 min a 20 MB/s (ipotesi sulla rete, non misure) | 0,5-2,7 | la rete verso Hugging Face; un piano B con altri 51,6 GB |
| Avvio e compilazione di vLLM, G7 | 4.3-4.4 | 0,1-0,5 | 0,3-1,5 | la compilazione (non misurata; `--enforce-eager` dopo 15 min) |
| G1, bench, G5, G6, G2 (due metà e caricamento), G4 ridotto | 4.5, 5 | 0,4-0,7 | 1,2-2,1 | — |
| Benchmark B1, B4, B3, merge | 7 | 1-2 | 3-6 | B1 sui vCPU della VM (non noti); B4 (ordine di grandezza: 10 min su 4 vCPU per la catena simile) più 623 bozze; B3 |
| Bozze e pacchetto | 6 | 0,5-1 | 1,5-3 | un `batch_generate` da rilanciare |
| Stack live, camminata, tunnel, registrazione, prove | 8 | 1-3 | 3-9 | ogni minuto di VM accesa durante le prove |
| Copia e distruzione | 9 | 0,1-0,2 | 0,3-0,6 | un ritardo nella distruzione |
| **Totale, una sessione** | | **circa 3,5-8 ore + download** | **circa 10-24 USD + download** | |
| Una seconda giornata (video, prove, domenica) | 3-4, 8 | tutto da rifare: ambiente, pesi, compilazione, più le prove | come sopra | nessuno snapshot: il download e la compilazione si ripagano ogni giorno |

Sul droplet il costo per decisione dei benchmark (cost-v2) ipotizza 1,99 USD/ora e uno snapshot:
è un'altra cosa da questo conto, e la sezione 7.3 dice come leggerlo.

Con lo script 07: `PRICE_USD_PER_HOUR=2.99` su Hot Aisle, `0` sul notebook (impostato da solo
quando `HOST_PROVIDER` contiene "Notebook"): la colonna di costo di `times.tsv` e i riepiloghi
di fase lo usano, e con `0` non stampano nessun costo.

Notebook: gratuito. Il tempo è lo stesso, ma i limiti di sessione sconosciuti (1.4) possono
interrompere un blocco a metà: i blocchi lunghi (download, B1, B4, batch) vanno fatti quando si
sa quanto dura la sessione, e i file copiati sul portatile appena esistono.

## 10. Problemi e rimedi

| Sintomo | Causa | Rimedio | Fonte |
| --- | --- | --- | --- |
| `docker compose version` funziona | c'è Docker | `docs/amd-runbook.md` dalla 3.2 | sezione 0.1 |
| `preflight.py` esce 1 con `Docker daemon not reachable`, `docker compose v2 missing`, `only N GB free for Docker` | atteso senza Docker | `host.json` è scritto comunque; si va avanti | `preflight.py` |
| `preflight.py`: `/dev/kfd missing` | nessun driver AMD visibile | fermarsi: senza `/dev/kfd` nessun job GPU parte; chiedere sul pannello o su Discord | `preflight.py` |
| `amd-smi: command not found` anche con `/opt/rocm/bin` | amd-smi non installato | `rocminfo` e `torch.cuda.get_device_name(0)` per il nome; nessun campione GPU in B4 e G4 (7.3); `rocm-smi` solo per l'angolo del video | `bench/amdsmi.py`, `g4_coexistence.sh` |
| pip: ruota `amd_xgboost` non supportata o hash diverso | glibc, ROCm o Python dell'host diversi dalla ricetta | sezione 3.3; piano B SPK-G2: XGBoost su CPU, B1 solo CPU | `requirements-gbm-amd.txt`, `bench.Dockerfile` |
| `ldd libxgboost.so` con `not found` | librerie ROCm fuori dal percorso del loader | `LD_LIBRARY_PATH=/opt/rocm/lib` (da verificare) | sezione 3.3 |
| `import vllm` fallisce ovunque | vLLM non installato | sezione 3.4; senza vLLM ROCm niente Gemma su questo host | — |
| `vllm serve` non vede la GPU | build CUDA di vLLM o torch non ROCm | sezione 3.4 punto 2 | — |
| `download-weights: exit` non 0 con 401/403 | gating inatteso | `HF_TOKEN` solo nell'ambiente di quel comando, annotato, mai in un file | runbook AMD 7.4 |
| download lento o fermo | rete | `tail artifacts/spikes/logs/download-weights.log`; `setsid` lo tiene vivo oltre il terminale | `weights.sh` |
| spazio esaurito durante il download | `HF_HOME` su un disco piccolo | `HF_HOME` altrove (4.1); `df -h` prima | sezione 1.2 |
| chat template non trovato | ruota senza `examples/` | sezione 4.2 | compose, commento di `vllm` |
| `Free memory on device ... is less than desired GPU memory utilization` | un altro processo sulla GPU (`amd-smi process`): un kernel Jupyter, un vLLM preinstallato, o un `EngineCore` sopravvissuto a un kill del solo pid padre | fermarlo; dopo un kill: `pgrep -af 'vllm\|EngineCore'` deve essere vuoto, altrimenti `pkill -TERM -f EngineCore`, poi `amd-smi process` vuoto, poi 4.3 | runbook AMD 15; 4.3 |
| nessun healthy dopo 15 min di compilazione, o errore di compile/graph capture | torch.compile su ROCm | `--enforce-eager`, annotare: G1 e B3 limite inferiore | runbook AMD 15 |
| `vllm is not running` nel ciclo di 4.4 | vLLM uscito | `tail -100 artifacts/spikes/logs/vllm.log` (per Claude: oscurato) | — |
| il 31B non ci sta nella VRAM | GPU sotto i 192 GB | sezione 4.6 | spec 11 |
| G1, G5, G6: `cannot reach http://vllm:8001/v1` | `.env` non caricato nella shell | `set -a; . ./.env; set +a` | `g1_guided_json.py` |
| G1: `FAIL: request without the API key was not refused with 401` | vLLM non applica `--api-key` | annotare (NFR-11); controllare il comando di avvio | `g1_guided_json.py` |
| G1: `finish_reason` `length` | output troncato | `--max-tokens 2048` | runbook AMD 15 |
| G6 fallisce in `auto` | il parser `gemma4` non estrae la chiamata (o template mancante, 4.2) | `--mode named`; piano B | runbook AMD 15 |
| G2 esce 2 (`GPU not used`) | la GPU non è visibile al venv | 3.3; piano B | `g2_xgboost_gpu.py` |
| G2 metà CPU: `FAIL: ... has config ...` | metà GPU con altra configurazione | rilanciare la metà GPU, poi la CPU | `g2_xgboost_gpu.py` |
| G4 ridotto: una richiesta `FAIL` o righe di errore nel log | memoria o HIP sotto carico | piano B: training a vLLM spento | spec 13 |
| `batch_generate` esce 2: `the batch needs GEMMA_MODEL on the local vLLM` | vLLM giù o altro modello | 4.4; `.env` e vLLM devono dire lo stesso `GEMMA_MODEL` | `batch_generate.py` |
| `batch_generate` esce 2: `vLLM is not configured` o `batch_generate runs with LLM_MODE=vllm` | `.env` dell'host senza `LLM_MODE=vllm`, `VLLM_API_KEY` o con un URL non locale | sezione 3.1 | `batch_generate.py`, `llm/client.py` |
| `batch_generate` esce 1 | vLLM mancato per qualche reso | rilanciare; `VLLM_TIMEOUT_S`, `LLM_CONCURRENCY` | `batch_generate.py` |
| `demo-export: ... DEVICE cpu, not only DEVICE=gpu` | batch fatto con `DEVICE=cpu` | `.env` con `DEVICE=gpu`, cancellare `serving/llm_cache/`, rifare il batch | `demo_export.py` |
| `demo-export: name the GPU the batch ran on` | `--generated-on` senza `MI300X` su una GPU che non lo è | `--rehearsal` (6.3): etichetta di prova, mai `MI300X` scritto a mano | `demo_export.py` |
| `demo-export: the serving snapshot does not match its manifest` | `serving/` ricostruito da una parte sola, o file estranei | ricopiare `serving/` dal portatile (prima del batch) | `demo_export.py` |
| B1 `GPU not used` | come G2 | 3.3 | `ml/propensity/train.py` |
| B4 senza numeri GPU (`n` 0, `errors` > 0) | amd-smi assente | 7.3: si mostra così | `bench/amdsmi.py` |
| le etichette dicono MI300X e la GPU non lo è | scritte nel codice | niente merge sull'host; Claude corregge le etichette prima di rapporto, release e server demo (7.3) | `bench/*.py` |
| `/readyz` `degraded` con `vllm` `unavailable` | vLLM giù o altro modello | 4.4; `GEMMA_MODEL` | `health.py` |
| `python -m apps.api.reset` esce 1 | vLLM mancato per qualche bozza (template etichettato) | `vllm.redacted.log`; reset di nuovo | `reset.py` |
| `uvicorn: command not found` | `.venv` non attivo | `. .venv/bin/activate` | — |
| la UI del portatile non raggiunge l'API | tunnel chiuso, `RETURNRING_API_URL` sbagliato, `npm run dev` invece di `dev:live` | 8.2-8.4; `curl` dal portatile verso l'URL | `vite.config.ts` |
| la camminata fallisce con 409 su un'approvazione | più di due giorni dal reset | reset (8.8) | `apps/api/settings.py` |
| la camminata: `agent` risponde 503 `llm_unavailable` | sul live vuol dire vLLM giù | 4.4 | `demo_walk.py` |
| il quick tunnel non parte o cade | servizio best effort | riprovare; oppure la camminata sull'host e il video con il terminale | sezione 8.3 |
| scritture sconosciute nel database dopo il quick tunnel | qualcuno ha trovato l'URL | reset; nessun dato personale c'è (negozio sintetico), ma si annota | sezione 8.3 |
| sessione del notebook terminata a metà | limiti di sessione | 1.4: ripartire da dove si era, i file copiati prima restano sul portatile | — |
| la VM di Hot Aisle fattura dopo la sessione | non distrutta | distruggere dal pannello e controllare la lista | sezione 9.2 |

## 11. Checklist

Ogni voce dice la sezione e, tra parentesi, la fase dello script 07 che la copre (0.5) o
"a mano" se lo script non la fa.

- [ ] 2 (a mano, portatile) Vigilia: `make lint test` verde; `serving/manifest.json`; `make asos-verify`; `artifacts/spikes/garment.jpg`; `npm ci` in `apps/ui`; `~/returnring-host.tar.gz` con `grep -c truth` a 0, `grep -c llm_cache` a 0 e lo sha256 annotato
- [ ] 1 (a mano) Accesso (notebook: terminale di JupyterLab; Hot Aisle: `ssh -i ~/.ssh/id_hotaisle_ed25519 hotaisle@<host-ip>`, `sudo -i`), `tmux new -s host`; ora di creazione della VM scritta
- [ ] 1 (`probe`) Inventario della 1.2 mandato; `<gpu-name>` deciso da `amd-smi static`; Docker sì/no; limiti di sessione annotati; **se la GPU non è una MI300X**: decisione con Claude sulla terza etichetta della demo (6.3) e sul contratto dei benchmark (7.3) **prima** di pagare la corsa
- [ ] 3 (a mano) Clone sul branch giusto; `.env` dell'host (`VLLM_API_KEY`, `VLLM_BASE_URL` in loopback, `DATABASE_URL` SQLite, `DEVICE=gpu`, `LLM_MODE=vllm`, `SERVING_DIR`, `BENCHMARKS_DIR`); `chmod 600`
- [ ] 2 (a mano) Tarball nella home dell'host, sha256 uguale, estratto; `no data/truth`; foto in `artifacts/spikes/garment.jpg`; `git status --porcelain` vuoto
- [ ] 1 (a mano) `preflight.py` lanciato; `host.json` mandato (gli `ERROR` su Docker attesi)
- [ ] 3 (`env`) `.venv` (`requirements/dev.txt`) e `.venv-bench` (`xgboost 3.2.0 USE_HIP`, `bench stack ok`); vLLM trovato o installato, versione annotata
- [ ] 4 (`vllm`) Download dei pesi avviato con `setsid`; `download-weights: exit 0, took N s` annotato
- [ ] 5 (`spikes --only spike-g2`) G2 metà GPU durante il download (`"pass": true`); G2 caricamento su CPU (`max_abs_diff` vicino a 0)
- [ ] 4 (`vllm`) Template trovato (percorso annotato); vLLM avviato con i flag del compose più `--host 127.0.0.1`; `vllm healthy: ...`; `g7-vllm-log.txt`; prima richiesta guided-JSON (`first-request`)
- [ ] 4 (`spikes`, `spike-g1-bench`) Token/s (`vllm bench serve` o `g1.json`); D-12 decisa e annotata con l'ora; `--gpu-memory-utilization` usato annotato
- [ ] 5 (`spikes`) G1 `pass=True`; G5 checklist valida; G6 `"pass": true` (auto o named); G4 ridotto passato; G3 saltato annotato
- [ ] 5 (`spikes`, `spike-g2` fa le due metà) vLLM fermo; G2 metà CPU con `speedup_train`
- [ ] 7 (`bench`) `export BENCH_RUN=...-gpu`; `unset VLLM_IMAGE DGL_IMAGE ROCM_BASE_IMAGE`; `git status --porcelain` vuoto; B1 a vLLM giù; `vllm-start-conditions.txt`, `vllm-start.txt`; vLLM su; `vllm-healthy.txt`; `first-request.json` ok (solo se vLLM è stato avviato in questo passo)
- [ ] 7 (`bench`) B4 (`B4.json`, `amdsmi.jsonl`), `disk-used-gb.txt`, B3 (`failed` 0); merge e report **solo su una MI300X**, altrimenti parti grezze a Claude; un rilancio della fase con `--from <target>`, mai senza (vLLM verrebbe riavviato e il cold start rimisurato)
- [ ] 6 (`export`, dopo `bench`) `serving/` è quello del portatile; `batch_generate` esce 0; `demo_export build` con `--generated-on` vero (e `--rehearsal` se non MI300X: `REHEARSAL=1 LLM_MODE=vllm DEVICE=gpu`); sha256 del tar
- [ ] 6 (a mano, portatile) `demo-cache.tar.gz` sul portatile; `make demo-import demo-local` verde; etichetta attesa in `/readyz`
- [ ] 8 (`live`) Reset; `uvicorn` su; `/readyz` `live`, `vllm`, `gpu` (`degraded` per `telemetry` `unavailable` finché il campionatore della 8.6 non gira; `ready` solo con il campionatore); camminata sull'host con 12 passi e `failed` vuoto; reset di nuovo
- [ ] 8 (a mano; `tunnel-help` stampa i comandi) Tunnel (SSH o quick tunnel); UI del portatile con `npm run dev:live` e `RETURNRING_API_URL` (quick tunnel: fuori dalla cronologia della shell); `watch -n 2 amd-smi monitor`; registrazione; quick tunnel chiuso e reset dopo
- [ ] 9 (a mano; `down` stampa il promemoria) File copiati sul portatile (sul notebook: blocco per blocco); `BENCH_RUN` comunicato; `vllm.log` mai mandato non oscurato
- [ ] 9 (`down`, più `cloudflared` a mano) API, vLLM (tutto il gruppo: `pgrep -af 'vllm|EngineCore'` vuoto), telemetria, cloudflared fermati; `HF_TOKEN` vuoto; nessun file di credenziali; VM distrutta e ora scritta, o sessione del notebook chiusa
- [ ] 9 (a mano, portatile) Sul portatile: `make bench-report` (dopo l'eventuale correzione delle etichette), `docs/spike-results.md`, server della demo, release con sha256

## 12. Valori da fornire

| Segnaposto o valore | Chi lo fornisce | Quando | Dove si usa |
| --- | --- | --- | --- |
| `<branch>` | Claude (oggi `4d7ab7d`, Merge #13: gli strumenti Hot Aisle e `gpu-up-local`, sul branch che Claude indica) | la vigilia | sezione 3.1 |
| quale host (notebook o Hot Aisle), e se c'è Docker | chi fa la sessione, dalla sezione 1 | all'accesso | sezione 0.1 |
| `<host-ip>` e `<user>` di Hot Aisle | il pannello di Hot Aisle | alla creazione | `ssh`, `scp`, sezione 8.2 |
| `<gpu-name>` (`market_name` di `amd-smi static`), `gfx...`, VRAM | l'host, sezione 1.2 | all'accesso | `--generated-on`, `--device` della telemetria, `docs/spike-results.md`, il pitch |
| ROCm dell'host, Python, versione di vLLM e di torch (`hip`) | l'host, sezioni 1.2 e 3.4 | all'accesso | `docs/spike-results.md` ("Images" diventa "Host software") |
| i limiti di sessione del notebook (durata, inattività, persistenza, disco) | il pannello o la documentazione di `notebooks.amd.com` | prima dei blocchi lunghi | sezioni 1.4, 9.3 |
| prezzo e unità di fatturazione di Hot Aisle, se una VM ferma costa | il pannello di Hot Aisle | alla creazione | sezione 9.3 |
| `VLLM_API_KEY` | `openssl rand -hex 24` sull'host | sezione 3.1 | `.env` |
| `HF_HOME` | chi fa la sessione, da `df -h` | sezione 4.1 | download e avvio di vLLM |
| il percorso del template `tool_chat_template_gemma4.jinja` | l'host, sezione 4.2 | prima dell'avvio | `--chat-template` |
| `--gpu-memory-utilization` se diverso da `0.55`, e `--enforce-eager` se usato | chi fa la sessione, sezione 4.6 | all'avvio | `docs/spike-results.md` |
| `GEMMA_MODEL` e `GEMMA_REVISION` del piano B | Claude conferma i valori (`.env.example`: `google/gemma-4-26B-A4B-it` a `4d7ae4984b7db7de8f8457170b3f1a419ee76d52`, mai scaricati) | durante G1, se D-12 va al piano B | sezione 4.5 |
| se `vllm bench serve` esiste nel vLLM installato | l'host | sezione 4.5 | D-12 (su quale numero) |
| `garment.jpg` | chi fa la sessione | la vigilia | sezione 5 |
| `$BENCH_RUN` | `date -u +%Y%m%dT%H%M%SZ`-gpu sull'host, poi riportato a mano sul portatile | sezione 7 | sezioni 7, 9 |
| la stringa di `--generated-on` | chi fa la sessione, dal nome vero della GPU | sezione 6.3 | `MANIFEST.json` |
| tag e nome della release GitHub con `demo-cache.tar.gz` e lo sha256 | chi fa la sessione con Claude | dopo il pacchetto | sezione 6.4 |
| `<ip-server-demo>` e il `PUBLIC_HOST` del server della demo | `scripts/release/04-demo-server.sh` lo stampa (guida al rilascio, sezione 2) | prima del cambio di pacchetto | sezione 6.4 |
| quale tunnel, e se la UI e lo stream dell'agente passano dal quick tunnel | chi fa la sessione, sezione 8 | alla prova del live | sezione 8; **mai l'URL del quick tunnel** |
| ora di creazione e di distruzione della VM (UTC), o ore di sessione del notebook | chi fa la sessione | ogni volta | `docs/spike-results.md`, sezione 9.3 |
| i tempi misurati: download, vLLM start-to-healthy, ogni blocco della 9.3 | chi fa la sessione | durante | `docs/spike-results.md`, sezione 9.3 |
| se la correzione delle etichette "MI300X" nel codice serve (GPU diversa) | risulta dalla sezione 1.2; il team decide con Claude | **prima della sessione**, come la terza etichetta | sezione 7.3: una modifica del contratto (`benchmarks.schema.json` ammette solo `cpu\|mi300x`, `bench/record.py`, `bench/b3_llm.py`) con una nota in `docs/adr/`; senza, i benchmark di una GPU diversa non si pubblicano né si servono |
| se serve una terza etichetta della demo ("Precomputed on AMD <GPU>") | il team decide con Claude, **prima** della sessione | la vigilia | sezione 6.3; una modifica del contratto |

## 13. La prova in sandbox del 16 ottobre (CPU, senza Docker, senza GPU, senza vLLM)

Che cosa è stato eseguito qui e con quale esito (regola 13: così com'è):

- `bash -n scripts/release/07-host-session.sh`: ok.
- `--dry-run` di tutte le fasi (`probe`, `env`, `vllm`, `vllm-stop`, `spikes`, `export`, `bench`,
  `live`, `down`, `all`) con un `.env` di prova: tutte passate; i comandi stampati sono quelli delle
  sezioni 4, 5, 6, 7 e 8 (chiave API oscurata), sotto `release-logs/host-test-dry-run/`.
- `probe` reale: `release-logs/host-test-probe/probe.txt` (nessuna GPU, nessun vllm, docker senza
  daemon, rete verso huggingface.co, pypi.org, pypi.amd.com e GitHub a 200): informa e non fallisce.
- `env` reale in un venv di prova sotto `release-logs/host-test/venv` (non il venv della
  sessione) con `requirements/dev.txt`: passata in 3 min 05 s (`venv` 1 min 51 s: Python 3.12.3,
  i 5 pacchetti della sessione e gli import di `apps.api.main`, `bench`, `scripts.demo_walk`,
  `scripts.demo_export` ok; `venv-amd` 1 min 13 s: **`amd_xgboost==3.2.0` si installa davvero da
  `pypi.amd.com/rocm-7.2.3/simple` con l'hash fissato e `--require-hashes`**, poi
  `requirements-bench.txt` da PyPI e `pip check` pulito; `ldd` di `libxgboost.so` trova
  `librccl.so.1` e `libamdhip64.so.7` mancanti, perché qui non c'è ROCm: piano B di SPK-G2
  registrato in `env/venv-amd.planb`, come progettato, senza fermare la fase).
- `export` reale con `REHEARSAL=1 LLM_MODE=mock DEVICE=cpu` contro una **copia** dello snapshot
  (`release-logs/host-test/serving`; `serving/` e `data/` del repository mai scritti, verificato
  con `find -mmin`): passata in 1 min 24 s (`reset` su SQLite 1 min 22 s per 623 resi con le
  bozze del mock; `batch-generate` registrato come "exit 2 by design" perché il modulo rifiuta
  `LLM_MODE=mock`; `demo-export --rehearsal --allow-empty-cache`; `MANIFEST.json` con
  `drafts: 0`, `rehearsal: true`; tarball e sha256 scritti).
- `export` reale una seconda volta con `FAKE_VLLM=1` (`HOST_RUN=host-test-fake`): lo script
  avvia `bench/fake_vllm.py` in background, il reset passa per il server finto (`LLM_MODE=vllm`,
  1 min 25 s), `python -m apps.api.batch_generate` scrive **623 bozze** nella copia dello snapshot
  in 24 s (`skipped_cached` 0, `failed` vuoto), `demo-export --rehearsal` le impacchetta:
  `MANIFEST.json` con `models: {fake/gemma-dry-run: 623}`, `devices: {cpu: 623}`,
  `rehearsal: true`. La sequenza reset, batch, export, tarball è quindi provata con le bozze.
- `live` reale in mock sulla porta 8171: `live-reset` 1 min 21 s, `uvicorn` staccato (pid
  file), `/readyz` risponde dopo 35 s con `status ready, mode live, llm_mode mock, device cpu,
  public_read_only false`, telemetria saltata (`DEVICE=cpu`), **camminata della demo 12 passi ok
  su 12** (coda p50 0,70 s, checkout p50 0,07 s) in 42 s, poi le istruzioni dei tunnel. Poi `down`:
  uvicorn fermato in 2 s, il server finto fermato, nessun processo e nessuna porta rimasti; i
  marker, il resume (`export` rilanciata: quattro target saltati come fatti), lo stop con piano B
  e il pacchetto dei log (`spikes --only spike-g1` senza vLLM: uscita 1, piano B del passo 10
  stampato, `release-logs-<data>.tgz`), gli errori d'uso (fase ignota, `--skip` di un target di
  un'altra fase) provati.

I tempi sopra sono misurati qui (4 vCPU condivise, mock o server finto): dicono che la sequenza
funziona, non quanto durerà sull'host.

Dopo le due revisioni del 16 ottobre (le correzioni: `first-request` con `--runs 2`, `bench-vllm`
che misura la prima richiesta solo dopo un avvio proprio, `all` con `bench` prima di `export`,
`rc=$?` sostituiti da `|| rc=$?` sotto `set -e`, la riga `resume:` con `--from` per `bench`, la
password di `HOST_DATABASE_URL` oscurata, `HOST_PROVIDER` obbligatorio e `MI300X` solo dal probe
per il pacchetto, prezzo 0 sul notebook, il template con il percorso assoluto, il grep di
`spike-g7` sotto `pipefail`, la cronologia della shell nelle istruzioni del quick tunnel) la stessa
prova è stata rifatta: `bash -n` ok; `--dry-run` di tutte le dieci fasi, uscita 0 e nessun `FAIL`;
`export --force` reale in mock (1 min 22 s, `reset` 1 min 19 s), `live` reale sulla porta 8171
(2 min 58 s: `live-reset` 1 min 26 s, `/readyz` `ready` con `DEVICE=cpu`, camminata 12 passi ok su
12 in 45 s), `down` (2 s, nessun pid e nessuna porta rimasti); `tests/test_readme.py` 12 passati.
Misurato qui, stesse condizioni di sopra.

Non verificabile qui: vLLM su ROCm (avvio, compilazione, template dal pacchetto), amd_xgboost
dall'indice AMD su un host ROCm, G1-G7 e B1/B3/B4 veri, amd-smi, i tunnel, i tempi e i costi.

Da mandare a Claude dalla sessione vera: `release-logs-<data>.tgz` (con `release-logs/host/`:
`probe.txt`, `times.tsv`, i log, i JSON piccoli di `artifacts/spikes/`, `artifacts/bench/$BENCH_RUN/`,
`bench/results/`, `demo-cache/MANIFEST.json`, `readyz.json`, `demo-walk.json`, la coda di
`vllm.log`), più i tempi di creazione e distruzione della VM. Mai `.env`: nel pacchetto c'è solo
`env-redacted.txt`.
