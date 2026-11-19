# Soulseek Batch Download (macOS, senza Docker)

Pipeline nativa e leggera per macOS (Apple Silicon & Intel) per scaricare playlist, full album e brani da **Spotify** e **YouTube** tramite **Soulseek**, senza Docker, senza Homebrew e senza ambienti virtuali Python.

## Installazione rapida (1 comando, senza Homebrew)

Apri il **Terminale** su macOS e incolla questo comando (ti chiederà solo **username** e **password** Soulseek):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.sh)
```

L'installer scarica automaticamente tutto ciò che serve in `~/Applications/Soulseek Batch Download` (senza richiedere `sudo`, Homebrew o Xcode):
- `sockseek` v3.0.6 nativo per macOS (`arm64` / `x64`)
- `yt-dlp` + `deno` standalone (solo se non già installati)
- `ffmpeg` + `ffprobe` statici per la conversione `FLAC → AIFF` (solo se non già installati)
- Runtime Python 3 standalone (solo se non già presente)
- Servizio macOS **tasto destro → Servizi → Scarica con Soulseek**

## Funzionalità

- **Spotify**: playlist pubbliche, full album e tracce singole (funziona out-of-the-box senza API key grazie al fallback Embed pubblico; opzionalmente supporta anche API key in `config/pipeline.json` per playlist da >100 brani).
- **YouTube & YouTube Music**: playlist, playlist album (`OLAK5uy_...` / `browse/MPREb_...`), video full album (estrae tracklist da capitoli e descrizione) e brani singoli. Se hai **Ollama** installato in locale, lo avvia on-demand con `keep_alive: 0` per normalizzare i titoli; altrimenti usa il parser locale integrato + arricchimento metadati `yt-dlp`.
- **Full Album con fallback automatico**: cerca prima la cartella album completa su Soulseek (`-a`) e, se non trovata o incompleta, ripiega automaticamente sul download traccia-per-traccia.
- **Checkbox conversione `FLAC → AIFF`**: dialog nativo macOS per convertire automaticamente i soli `.flac` scaricati nella sessione in AIFF PCM 24-bit (tag ID3v2.3 + copertina preservati) eliminando i `.flac` originali e lasciando intatti gli `.mp3`.
- **Apertura automatica nel Finder**: al termine di download e conversione apre la cartella scaricata (o seleziona il file singolo) nel Finder.

## Come si usa

1. **Doppio click** su `Soulseek Batch Pipeline.command` dentro `~/Applications/Soulseek Batch Download`: incolla un link Spotify o YouTube (se ce l'hai già negli appunti viene precompilato!) e scegli se attivare `Converti FLAC → AIFF`.
2. **Menu Servizi macOS**: evidenzia un link Spotify o YouTube in qualsiasi app → **tasto destro → Servizi → Scarica con Soulseek**.
3. **Da terminale**:
   ```bash
   "$HOME/Applications/Soulseek Batch Download/Soulseek Batch Pipeline.command" --convert-aiff "URL"
   ```
   Opzioni di `src/cli.py`: `--export-only`, `--mode auto|album|tracks`, `--convert-aiff` / `--no-convert-aiff`, `--no-finder`, `--no-gui`.
