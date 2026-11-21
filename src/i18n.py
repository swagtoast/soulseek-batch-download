"""Lightweight zero-dependency bilingual i18n (Italian / English) with automatic OS language detection.

Rule: if the system language is Italian -> 'it'; for every other language -> 'en'.
Can be overridden via SLSK_LANG=it or SLSK_LANG=en.
"""

from __future__ import annotations

import locale
import os
import re
import subprocess
import sys


_CACHED_SYSTEM_LANG: str | None = None
_LOCALE_LANG_RE = re.compile(r"^\s*([a-zA-Z]{2,3})(?:[_\-.@]|$)")


def _extract_lang_code(raw: str | None) -> str | None:
    if not raw:
        return None
    # LANGUAGE can be colon-separated (e.g. "it_IT:en_US")
    first = raw.split(":", 1)[0].strip()
    if not first:
        return None
    upper = first.upper()
    if upper in {"C", "POSIX", "UTF-8", "UTF8", "C.UTF-8", "C.UTF8"}:
        return None
    m = _LOCALE_LANG_RE.match(first)
    if not m:
        return None
    return m.group(1).lower()


def _detect_macos_language() -> str | None:
    try:
        proc = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                cleaned = line.strip().strip('",() ')
                code = _extract_lang_code(cleaned)
                if code:
                    return code
    except Exception:
        pass
    return None


def _detect_windows_language() -> str | None:
    try:
        import ctypes

        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined]
        primary = lang_id & 0x3FF
        return "it" if primary == 0x10 else "en"
    except Exception:
        return None


def detect_language() -> str:
    """Detect system language: returns 'it' if Italian, otherwise always 'en'."""
    override = os.environ.get("SLSK_LANG", "").strip()
    if override:
        code = _extract_lang_code(override)
        return "it" if code == "it" else "en"

    # On macOS & Windows, prefer OS UI language when POSIX env vars aren't explicitly set to a language.
    for env_key in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        code = _extract_lang_code(os.environ.get(env_key))
        if code:
            return "it" if code == "it" else "en"

    if sys.platform == "darwin":
        code = _detect_macos_language()
        if code:
            return "it" if code == "it" else "en"
    elif os.name == "nt":
        code = _detect_windows_language()
        if code:
            return "it" if code == "it" else "en"

    try:
        loc_code = _extract_lang_code(locale.getlocale()[0])
        if loc_code:
            return "it" if loc_code == "it" else "en"
    except Exception:
        pass

    return "en"


def get_lang() -> str:
    """Return active language ('it' or 'en'). Respects dynamic SLSK_LANG overrides."""
    global _CACHED_SYSTEM_LANG
    if os.environ.get("SLSK_LANG", "").strip():
        return detect_language()
    if _CACHED_SYSTEM_LANG is None:
        _CACHED_SYSTEM_LANG = detect_language()
    return _CACHED_SYSTEM_LANG


def reset_lang_cache() -> None:
    """Clear cached system language (useful in tests)."""
    global _CACHED_SYSTEM_LANG
    _CACHED_SYSTEM_LANG = None


def is_italian() -> bool:
    return get_lang() == "it"


STRINGS: dict[str, dict[str, str]] = {
    "it": {
        # Kind labels
        "kind_spotify_playlist": "Playlist Spotify rilevata",
        "kind_spotify_album": "Full Album Spotify rilevato",
        "kind_spotify_track": "Brano Spotify rilevato",
        "kind_youtube_playlist": "Playlist / Album YouTube rilevato",
        "kind_youtube_video": "Video / Full Album YouTube rilevato",
        "kind_generic": "Link rilevato",
        "summary_playlist": "Playlist",
        "summary_album": "Full Album",
        "summary_single_track": "Brano singolo",
        "summary_artist_album": "Artista: {artist} | Album: {album}",
        "summary_tracks_extracted": "Tracce estratte: {count}",
        "summary_formatted_llm": " (formattate con LLM)",
        "summary_more_tracks": "  ... e altre {count} tracce",
        # CLI & GUI prompts
        "cli_detected": "[Input] Rilevato: {kind} -> {url}",
        "cli_unsupported": "Tipo sorgente non gestito: {kind}",
        "cli_setup_cancelled": "Configurazione iniziale annullata.",
        "cli_cancelled": "Operazione annullata.",
        "cli_paste_prompt": "Incolla URL Spotify o YouTube (playlist / album / video full album): ",
        "cli_paste_dialog": "Incolla un URL Spotify o YouTube (playlist, album o video full album):",
        "cli_interrupted": "\nInterrotto dall'utente.",
        "cli_error": "\n[Errore] {exc}",
        "cli_press_enter": "\nPremi Invio per chiudere...",
        "ui_format_label": "formato",
        "ui_btn_download": "Scarica",
        "ui_btn_cancel": "Annulla",
        "ui_btn_next": "Avanti",
        "ui_btn_save": "Salva e avvia",
        "ui_btn_browse": "Sfoglia…",
        "ui_url_label": "Link Spotify / YouTube:",
        "ui_pill_download": "  ⬇ Scarica con Soulseek  ",
        # First-run setup
        "setup_title": "Soulseek Batch Download — Configurazione iniziale",
        "setup_heading": "Configurazione iniziale (chiesta solo al primo avvio)",
        "setup_term_header": "\n=== Configurazione iniziale — Soulseek Batch Download ===",
        "setup_term_sub": "Queste impostazioni vengono chieste solo al primo avvio.\n",
        "setup_user_dialog": "Benvenuto! Inserisci il tuo Username Soulseek (chiesto solo al primo avvio):",
        "setup_user_label": "Username Soulseek:",
        "setup_pass_dialog": "Inserisci la tua Password Soulseek:",
        "setup_pass_label": "Password Soulseek:",
        "setup_pass_term": "Password Soulseek (nascosta): ",
        "setup_dir_dialog": "Scegli la cartella di download per Soulseek:",
        "setup_dir_label": "Cartella download:",
        "setup_format_heading": "Formato:",
        "setup_choose_123": "Scegli [1/2/3, default {default}]: ",
        "setup_delete_orig_q": "Elimina file originale dopo la conversione? [{hint}]: ",
        "setup_err_creds": "Inserisci sia Username che Password Soulseek.",
        "setup_saved": "[Setup] Configurazione salvata! Cartella download: {dir} | Formato: {fmt}",
        # Downloader
        "dl_bin_missing": "Binario sockseek non trovato: {path}",
        "dl_conf_missing": "Configurazione sockseek non trovata: {path}",
        "dl_export_csv": "[Export] Salvata lista ({count} tracce): {path}",
        "dl_export_no_list": "[Export] Album '{name}' senza tracklist dettagliata: ricerca diretta su Soulseek.",
        "dl_single_search": "[Download] Ricerca traccia singola: {query}",
        "dl_playlist_start": "[Download] Playlist ({count} tracce) -> {target}",
        "dl_album_start": "[Download] Ricerca cartella Full Album su Soulseek: {query}{count_label}",
        "dl_album_expected": " ({count} tracce attese)",
        "dl_album_complete": "[OK] Full Album completo ({got}/{expected} tracce) in: {dir}",
        "dl_album_partial_no_list": "[Avviso] Album scaricato ({count} file) in: {dir}",
        "dl_album_not_found": "[Errore] Nessuna cartella Full Album trovata e nessuna tracklist disponibile per il fallback.",
        "dl_album_fallback": "\n[Fallback] Cartella Full Album non trovata su Soulseek. Avvio download traccia-per-traccia...",
        "dl_check_complete": "[Controllo tracce] Completo: {got}/{expected} tracce scaricate.",
        "dl_check_warn": "[Controllo tracce] Avviso: scaricate {got}/{expected} tracce.",
        "dl_check_partial": "[Controllo tracce] Scaricate {got}/{expected} tracce.",
        "dl_check_missing": "[Controllo tracce] Scaricate {got}/{expected} tracce — mancano {missing} brani.",
        "dl_check_rescued_all": "[Controllo tracce] Tutte le {got}/{expected} tracce scaricate!",
        "dl_check_still_missing": "[Controllo tracce] Scaricate {got}/{expected} tracce. Non trovate su Soulseek ({missing}): {names}",
        "dl_rescue_start": "\n[Recupero] Cerco {count} tracce mancanti con ricerca estesa: {names}{suffix}",
        "dl_rescue_more": " (+ altri {count})",
        "dl_report_filename": "BRANI_MANCANTI.txt",
        "dl_report_header": "Soulseek Batch Download — Brani non trovati su Soulseek",
        "dl_report_collection": "Collezione: {name}",
        "dl_report_counts": "Scaricati: {got}/{expected} (mancanti: {missing})",
        "dl_report_saved": "[Controllo tracce] Elenco brani mancanti salvato in: {path}",
        "dl_cleanup_removed": "[Pulizia] Rimossi {count} file non audio.",
        "dl_open_item": "[{fm}] Apro in {fm}: {path}",
        "dl_open_folder": "[{fm}] Apro cartella in {fm}: {path}",
        "dl_fm_win": "Esplora File",
        "dl_fm_linux": "File Manager",
        "dl_fm_mac": "Finder",
        "dl_tagger_skipped": "[Tagger] Avviso: tagging saltato ({exc})",
        # Converter
        "conv_desc_mp3": "MP3 (320 kbps CBR)",
        "conv_desc_aiff": "AIFF (PCM 24-bit, massima qualità)",
        "conv_desc_flac": "FLAC (lossless)",
        "conv_none_mp3_kept": "[{tag}] Nessun file da convertire ({count} file .mp3 mantenuti invariati).",
        "conv_keep_delete": " — elimino originali",
        "conv_keep_preserve": " — mantengo originali",
        "conv_start": "\n[{tag}] Conversione di {count} file audio in {desc}{keep_note}...",
        "conv_err": "  [{idx}/{total}] ERRORE {name}: {err} (file originale mantenuto)",
        "conv_kept_mp3": "[{tag}] Mantenuti invariati {count} file .mp3 originali.",
        "conv_done": "[{tag}] Completato: {converted} convertiti in {tag}, {failed} errori.",
        # Tagger
        "tag_start": "\n[Tagger] Verifica metadati e copertine su {count} brani...",
        "tag_all_ok": "[Tagger] Tutti i {count} brani hanno già metadati e copertine corretti.",
        "tag_mlx_start": "[Tagger] Audit metadati con MiniCPM5-2B-MLX + Tagger LoRA (Apple Silicon Metal)...",
        "tag_mlx_warn": "[Tagger] Avviso MLX: {err} (uso fallback deterministico)",
        "tag_cover_note": " + copertina",
        "tag_write_fail": "  [Avviso] Impossibile aggiornare tag di {name} (file originale mantenuto)",
        "tag_summary": "[Tagger] Verificati {total} brani: {updated} aggiornati, {ok} già perfetti{err_part}.",
        "tag_summary_err": ", {failed} errori",
        # Updater
        "upd_up_to_date": "[Update] Già aggiornato all'ultima versione ({sha}).",
        "upd_offline": "[Update] Impossibile contattare GitHub (offline o limite API).",
        "upd_git_pull": "[Update] Cartella git locale rilevata: aggiorno tramite git pull --ff-only...",
        "upd_downloading": "[Update] Nuova versione disponibile ({sha}), aggiorno automaticamente...",
        "upd_done": "[Update] Aggiornamento completato ({sha})! Riavvio con la nuova versione...\n",
        "upd_skipped": "[Update] Aggiornamento saltato ({exc}); continuo con la versione attuale.",
    },
    "en": {
        # Kind labels
        "kind_spotify_playlist": "Spotify Playlist detected",
        "kind_spotify_album": "Spotify Full Album detected",
        "kind_spotify_track": "Spotify Track detected",
        "kind_youtube_playlist": "YouTube Playlist / Album detected",
        "kind_youtube_video": "YouTube Video / Full Album detected",
        "kind_generic": "Link detected",
        "summary_playlist": "Playlist",
        "summary_album": "Full Album",
        "summary_single_track": "Single Track",
        "summary_artist_album": "Artist: {artist} | Album: {album}",
        "summary_tracks_extracted": "Tracks extracted: {count}",
        "summary_formatted_llm": " (formatted with LLM)",
        "summary_more_tracks": "  ... and {count} more tracks",
        # CLI & GUI prompts
        "cli_detected": "[Input] Detected: {kind} -> {url}",
        "cli_unsupported": "Unsupported source type: {kind}",
        "cli_setup_cancelled": "Initial setup cancelled.",
        "cli_cancelled": "Operation cancelled.",
        "cli_paste_prompt": "Paste a Spotify or YouTube URL (playlist / album / full album video): ",
        "cli_paste_dialog": "Paste a Spotify or YouTube URL (playlist, album, or full album video):",
        "cli_interrupted": "\nInterrupted by user.",
        "cli_error": "\n[Error] {exc}",
        "cli_press_enter": "\nPress Enter to close...",
        "ui_format_label": "format",
        "ui_btn_download": "Download",
        "ui_btn_cancel": "Cancel",
        "ui_btn_next": "Next",
        "ui_btn_save": "Save & start",
        "ui_btn_browse": "Browse…",
        "ui_url_label": "Spotify / YouTube URL:",
        "ui_pill_download": "  ⬇ Download with Soulseek  ",
        # First-run setup
        "setup_title": "Soulseek Batch Download — Initial Setup",
        "setup_heading": "Initial setup (asked only on first run)",
        "setup_term_header": "\n=== Initial Setup — Soulseek Batch Download ===",
        "setup_term_sub": "These settings are only requested on first launch.\n",
        "setup_user_dialog": "Welcome! Enter your Soulseek Username (asked only on first run):",
        "setup_user_label": "Soulseek Username:",
        "setup_pass_dialog": "Enter your Soulseek Password:",
        "setup_pass_label": "Soulseek Password:",
        "setup_pass_term": "Soulseek Password (hidden): ",
        "setup_dir_dialog": "Choose your Soulseek download folder:",
        "setup_dir_label": "Download folder:",
        "setup_format_heading": "Format:",
        "setup_choose_123": "Choose [1/2/3, default {default}]: ",
        "setup_delete_orig_q": "Delete original file after conversion? [{hint}]: ",
        "setup_err_creds": "Please enter both your Soulseek Username and Password.",
        "setup_saved": "[Setup] Settings saved! Download folder: {dir} | Format: {fmt}",
        # Downloader
        "dl_bin_missing": "sockseek binary not found: {path}",
        "dl_conf_missing": "sockseek config not found: {path}",
        "dl_export_csv": "[Export] Saved tracklist ({count} tracks): {path}",
        "dl_export_no_list": "[Export] Album '{name}' has no detailed tracklist: searching directly on Soulseek.",
        "dl_single_search": "[Download] Single track search: {query}",
        "dl_playlist_start": "[Download] Playlist ({count} tracks) -> {target}",
        "dl_album_start": "[Download] Searching Soulseek for Full Album folder: {query}{count_label}",
        "dl_album_expected": " ({count} tracks expected)",
        "dl_album_complete": "[OK] Complete Full Album ({got}/{expected} tracks) in: {dir}",
        "dl_album_partial_no_list": "[Notice] Album downloaded ({count} files) in: {dir}",
        "dl_album_not_found": "[Error] No Full Album folder found and no tracklist available for fallback.",
        "dl_album_fallback": "\n[Fallback] Full Album folder not found on Soulseek. Starting track-by-track download...",
        "dl_check_complete": "[Track Check] Complete: {got}/{expected} tracks downloaded.",
        "dl_check_warn": "[Track Check] Notice: downloaded {got}/{expected} tracks.",
        "dl_check_partial": "[Track Check] Downloaded {got}/{expected} tracks.",
        "dl_check_missing": "[Track Check] Downloaded {got}/{expected} tracks — {missing} track(s) missing.",
        "dl_check_rescued_all": "[Track Check] All {got}/{expected} tracks downloaded!",
        "dl_check_still_missing": "[Track Check] Downloaded {got}/{expected} tracks. Not found on Soulseek ({missing}): {names}",
        "dl_rescue_start": "\n[Rescue] Searching {count} missing track(s) with relaxed matching: {names}{suffix}",
        "dl_rescue_more": " (+ {count} more)",
        "dl_report_filename": "MISSING_TRACKS.txt",
        "dl_report_header": "Soulseek Batch Download — Tracks not found on Soulseek",
        "dl_report_collection": "Collection: {name}",
        "dl_report_counts": "Downloaded: {got}/{expected} (missing: {missing})",
        "dl_report_saved": "[Track Check] Missing tracks report saved to: {path}",
        "dl_cleanup_removed": "[Cleanup] Removed {count} non-audio file(s).",
        "dl_open_item": "[{fm}] Revealing in {fm}: {path}",
        "dl_open_folder": "[{fm}] Opening folder in {fm}: {path}",
        "dl_fm_win": "File Explorer",
        "dl_fm_linux": "File Manager",
        "dl_fm_mac": "Finder",
        "dl_tagger_skipped": "[Tagger] Notice: tagging skipped ({exc})",
        # Converter
        "conv_desc_mp3": "MP3 (320 kbps CBR)",
        "conv_desc_aiff": "AIFF (24-bit PCM, lossless)",
        "conv_desc_flac": "FLAC (lossless)",
        "conv_none_mp3_kept": "[{tag}] No files to convert ({count} .mp3 file(s) kept untouched).",
        "conv_keep_delete": " — deleting originals",
        "conv_keep_preserve": " — keeping originals",
        "conv_start": "\n[{tag}] Converting {count} audio file(s) to {desc}{keep_note}...",
        "conv_err": "  [{idx}/{total}] ERROR {name}: {err} (original kept)",
        "conv_kept_mp3": "[{tag}] Kept {count} original .mp3 file(s) untouched.",
        "conv_done": "[{tag}] Done: {converted} converted to {tag}, {failed} error(s).",
        # Tagger
        "tag_start": "\n[Tagger] Auditing metadata and cover art on {count} track(s)...",
        "tag_all_ok": "[Tagger] All {count} track(s) already have clean metadata and cover art.",
        "tag_mlx_start": "[Tagger] Auditing metadata with MiniCPM5-2B-MLX + Tagger LoRA (Apple Silicon Metal)...",
        "tag_mlx_warn": "[Tagger] MLX notice: {err} (using deterministic fallback)",
        "tag_cover_note": " + cover art",
        "tag_write_fail": "  [Notice] Could not update tags for {name} (original kept)",
        "tag_summary": "[Tagger] Audited {total} track(s): {updated} updated, {ok} already clean{err_part}.",
        "tag_summary_err": ", {failed} error(s)",
        # Updater
        "upd_up_to_date": "[Update] Already up to date ({sha}).",
        "upd_offline": "[Update] Could not reach GitHub (offline or rate-limited).",
        "upd_git_pull": "[Update] Local git checkout detected: updating via git pull --ff-only...",
        "upd_downloading": "[Update] New version available ({sha}), updating automatically...",
        "upd_done": "[Update] Update complete ({sha})! Restarting with the new version...\n",
        "upd_skipped": "[Update] Update skipped ({exc}); continuing with current version.",
    },
}


def t(key: str, **kwargs: object) -> str:
    """Translate a message key into Italian (if system is Italian) or English."""
    lang = get_lang()
    table = STRINGS.get(lang, STRINGS["en"])
    template = table.get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
