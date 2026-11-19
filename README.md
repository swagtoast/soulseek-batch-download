# Soulseek Batch Download (macOS, senza Docker)

Pipeline nativa e leggera per macOS (Apple Silicon & Intel) per scaricare playlist, full album e brani da **Spotify** e **YouTube** tramite **Soulseek**, senza Docker, senza Homebrew e senza ambienti virtuali Python.

## Installazione rapida (1 comando, senza Homebrew)

Apri il **Terminale** su macOS e incolla questo comando tutto-in-uno:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.sh)
```

Al termine dell'installazione si apre automaticamente (e **solo al primo avvio**) la finestra nativa macOS dove inserire:
- **Username** e **Password** Soulseek (salvati in locale con permessi `600`, solo proprietario)
- **Cartella di download**, scegliendola direttamente dal Finder con il pulsante **Sfoglia…**
- **Conversione file `.flac`**: scelta tra **Nessuna**, **AIFF (24-bit lossless)** o **MP3 (320 kbps)** (elimina i `.flac` convertiti e lascia intatti gli `.mp3` già scaricati)

In tutte le sessioni successive non dovrai scegliere più nulla!

L'installer scarica automaticamente tutto in `~/Applications/Soulseek Batch Download` senza richiedere `sudo`, Homebrew o Xcode:
- `sockseek` v3.0.6 nativo per macOS (`arm64` / `x64`)
- `yt-dlp` + `deno` standalone (solo se non già presenti)
- `ffmpeg` + `ffprobe` statici per la conversione `FLAC → AIFF / MP3 320 kbps` (solo se non già presenti)
- Runtime Python 3 standalone (solo se non già presente)
- Servizio macOS **tasto destro → Servizi → Scarica con Soulseek**

## Funzionalità

- **Setup solo al primo avvio con Finder `Sfoglia…`**: credenziali, cartella di download e formato di conversione si configurano una sola volta. Le esecuzioni successive partono subito senza alcuna finestra aggiuntiva (puoi riaprire il setup quando vuoi con `--setup`).
- **Spotify**: playlist pubbliche, full album e tracce singole (funziona out-of-the-box senza API key grazie al fallback Embed pubblico; opzionalmente supporta anche API key in `config/pipeline.json` per playlist da >100 brani).
- **YouTube & YouTube Music**: playlist, playlist album (`OLAK5uy_...` / `browse/MPREb_...`), video full album (estrae tracklist da capitoli e descrizione) e brani singoli.
  - Supporta **MiniCPM5-2B-MLX** con adapter LoRA fine-tuned su Apple Silicon Metal (`1.3 GB`, 0 MB in idle), **Ollama** on-demand (preferendo `minicpm5-2b` prima di `qwen2.5`, con `keep_alive: 0`) e **parser locale integrato** zero-dipendenze se nessun LLM locale è presente.
- **Full Album con fallback automatico**: cerca prima la cartella album completa su Soulseek (`-a`) e, se non trovata o incompleta, ripiega automaticamente sul download traccia-per-traccia.
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
