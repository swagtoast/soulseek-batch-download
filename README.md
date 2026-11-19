# Soulseek Batch Download (macOS & Windows, senza Docker)

Pipeline nativa e leggera per **macOS** (Apple Silicon & Intel) e **Windows 10/11** per scaricare playlist, full album e brani da **Spotify** e **YouTube** tramite **Soulseek**, senza Docker, senza Homebrew/Winget e senza diritti di amministratore.

## Installazione rapida (1 comando)

### macOS (Terminale)
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.sh)
```

### Windows 10/11 (PowerShell)
```powershell
irm https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.ps1 | iex
```

Su **Windows** installa tutto in `%LOCALAPPDATA%\Soulseek Batch Download` (~465 MB su PC pulito, zero admin), crea l'icona sul Desktop/Start, registra il protocollo `soulseekdl://`, attiva il **pulsante fluttuante nativo** (evidenzia o copia un link Spotify/YouTube in qualsiasi app → compare subito **`Scarica con Soulseek`** vicino al cursore, oppure `Ctrl+Alt+S`) e include la cartella `browser-extension/` per il tasto destro puro su Chrome/Edge/Firefox.

Al termine dell'installazione si apre automaticamente (e **solo al primo avvio**) la finestra nativa dove inserire:
- **Username** e **Password** Soulseek (salvati in locale con permessi `600`, solo proprietario)
- **Cartella di download**, scegliendola direttamente dal Finder con il pulsante **Sfoglia…**
- **Formato**: menu a tendina unico `.flac (lossless)`, `.aiff (PCM 24-bit lossless)` o `.mp3 (320 kbps)` (con `.mp3` cerca prima MP3 320 kbps leggeri su Soulseek; in caso di conversione elimina in automatico il `.flac` originale; con `.flac` salta la conversione)

In tutte le sessioni successive non dovrai scegliere più nulla!

L'installer scarica automaticamente tutto in `~/Applications/Soulseek Batch Download` senza richiedere `sudo`, Homebrew o Xcode:
- `sockseek` v3.0.6 nativo per macOS (`arm64` / `x64`)
- `yt-dlp` + `deno` standalone (solo se non già presenti)
- `ffmpeg` + `ffprobe` statici per la conversione `FLAC → AIFF / MP3 320 kbps` (solo se non già presenti)
- Runtime Python 3 standalone (solo se non già presente)
- Servizio macOS **tasto destro → Servizi → Scarica con Soulseek**

## Funzionalità

- **Auto-aggiornamento ad ogni avvio**: ad ogni esecuzione (sia da `.command` sia da **tasto destro → Servizi → Scarica con Soulseek**) verifica GitHub in ~0,15s tramite `ETag` HTTP `304` (zero consumo rate-limit, timeout `2s`) e si aggiorna da solo preservando credenziali, configurazioni, download e icona personalizzata (disattivabile con `--no-update`).
- **Auto-ottimizzazione per ogni Mac**: rileva automaticamente architettura (`Apple Silicon` / `Intel`), RAM, core CPU e versione macOS per scegliere l'interfaccia nativa più veloce (finestra AppKit Swift su Apple Silicon/Mac potenti, dialog nativi macOS a 0% CPU e zero compilazione su Mac Intel più leggeri), calibrare i thread `yt-dlp` ed evitare di caricare modelli LLM troppo pesanti su Mac da 8 GB di RAM.
- **Setup credenziali e cartella solo al primo avvio (Finder `Sfoglia…`)**: username, password e cartella di download si configurano una sola volta (puoi riaprirli quando vuoi con `--setup`).
- **Scelta formato in un'unica finestra ad ogni download**: per qualsiasi link **Spotify** o **YouTube** (playlist, full album o brano singolo) compare una sola finestra compatta **`formato`** (`.flac (lossless)`, `.aiff (PCM 24-bit lossless)`, `.mp3 (320 kbps)`). Se scegli `.mp3`, `sockseek` cerca preferibilmente MP3 320 kbps leggeri invece dei FLAC; se scarica un `.flac` da convertire, elimina il file originale automaticamente.
- **Cartelle pulite (solo file audio)**: disabilita `_index.csv` e rimuove immediatamente eventuali file non audio scaricati con gli album (`.jpg`, `.png`, `.nfo`, `.txt`, `.log`, `.cue`, cartelle `Artwork/` vuote, ecc.).
- **Spotify**: playlist pubbliche, full album e tracce singole (funziona out-of-the-box senza API key grazie al fallback Embed pubblico; opzionalmente supporta anche API key in `config/pipeline.json` per playlist da >100 brani).
- **YouTube & YouTube Music**: playlist, playlist album (`OLAK5uy_...` / `browse/MPREb_...`), video full album (cerca automaticamente la tracklist in **capitoli, descrizione e primi commenti fissati/più votati** con guardrail anti-tracce perse) e brani singoli.
  - Supporta **MiniCPM5-2B-MLX** con adapter LoRA fine-tuned su Apple Silicon Metal (`1.3 GB`, 0 MB in idle), **Ollama** on-demand (preferendo `minicpm5-2b` prima di `qwen2.5`, con `keep_alive: 0`) e **parser locale integrato** zero-dipendenze se nessun LLM locale è presente.
- **Controllo numero tracce & recupero automatico (Album e Playlist)**: verifica sempre che i file scaricati corrispondano al numero atteso di tracce (sia Spotify sia YouTube); se manca anche un solo brano (es. l'ultima traccia o un album incompleto), avvia automaticamente un passaggio mirato di recupero (`--desperate --remove-ft --remove-brackets --length-tol -1`) nella stessa cartella.
- **Conversione automatica `FLAC → AIFF (24-bit)` o `MP3 (320 kbps)`**: selettore nativo per convertire automaticamente i soli `.flac` scaricati nella sessione (preservando tag ID3v2.3 + copertina), eliminando i `.flac` originali e lasciando intatti gli `.mp3`.
- **Apertura automatica nel Finder**: al termine di download e conversione apre la cartella scaricata (o seleziona il file singolo) nel Finder.

## Come si usa

1. **Menu Servizi macOS (zero click superflui)**: evidenzia un link Spotify o YouTube in qualsiasi app → **tasto destro → Servizi → Scarica con Soulseek**. Parte immediatamente con le impostazioni salvate al primo avvio!
2. **Doppio click** su `Soulseek Batch Pipeline.command` dentro `~/Applications/Soulseek Batch Download` per incollare un link.
3. **Da terminale**:
   ```bash
   "$HOME/Applications/Soulseek Batch Download/Soulseek Batch Pipeline.command" "URL"
   ```
   Opzioni utili: `--setup` (riapre la configurazione iniziale), `--export-only`, `--mode auto|album|tracks`, `--convert none|aiff|mp3` (`--convert-aiff`, `--convert-mp3`, `--no-convert`), `--no-finder`, `--no-gui`.
