"""Comprehensive unit and real conversion tests for the Soulseek Batch Pipeline."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import cli
from config import HardwareProfile, load_config
from converter import convert_downloaded_flacs, convert_downloaded_flacs_to_aiff
from downloader import download_collection, write_tracks_csv
from llm_formatter import (
    OllamaSession,
    _mlx_is_available,
    _parse_llm_json,
    extract_numbered_tracklist,
    extract_timestamp_lines,
    format_youtube_playlist_with_llm,
    format_youtube_video_with_llm,
    parse_track_heuristic,
)
from models import CollectionExport, Track
from setup_ui import ensure_configured, is_first_run_needed, write_setup_configs
from tagger import (
    TagDecision,
    _choose_candidate_deterministic,
    _enforce_album_cohesion,
    probe_audio_tags,
    tag_downloaded_files,
    tags_need_repair,
)
from updater import apply_update_from_tarball, check_and_apply_update
from url_utils import classify_url, extract_url_from_text, sanitize_filename
from youtube_extractor import extract_youtube


class UrlUtilsTests(unittest.TestCase):
    def test_extract_url_from_noisy_highlighted_text_and_no_scheme(self) -> None:
        noisy = "   Dai ascolta questo album: (https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy?si=abc123). Top! "
        self.assertEqual(
            extract_url_from_text(noisy),
            "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy?si=abc123",
        )
        bare = "youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_url_from_text(bare), "https://youtube.com/watch?v=dQw4w9WgXcQ")

    def test_classify_spotify_variants(self) -> None:
        p = classify_url("https://open.spotify.com/intl-it/playlist/4rnleEAOdmFAbRcNCgZMpY?si=123")
        self.assertEqual(p.kind, "spotify_playlist")
        self.assertEqual(p.item_id, "4rnleEAOdmFAbRcNCgZMpY")

        a = classify_url("spotify:album:4aawyAB9vmqN3uQ7FjRGTy")
        self.assertEqual(a.kind, "spotify_album")
        self.assertEqual(a.url, "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")

    def test_classify_youtube_variants(self) -> None:
        pl = classify_url("https://music.youtube.com/playlist?list=OLAK5uy_abc123")
        self.assertEqual(pl.kind, "youtube_playlist")
        self.assertEqual(pl.item_id, "OLAK5uy_abc123")

        ytm_curated = classify_url("https://music.youtube.com/watch?v=abc123&list=RDCLAK5uy_kmPRjHDECIcuVwnKsx2Ng7fyNgFKWNJFs")
        self.assertEqual(ytm_curated.kind, "youtube_playlist")
        self.assertEqual(ytm_curated.item_id, "RDCLAK5uy_kmPRjHDECIcuVwnKsx2Ng7fyNgFKWNJFs")

        browse = classify_url("https://music.youtube.com/browse/MPREb_4p6v5mI2P1i")
        self.assertEqual(browse.kind, "youtube_playlist")
        self.assertEqual(browse.item_id, "MPREb_4p6v5mI2P1i")

        watch_pl = classify_url("https://www.youtube.com/watch?v=xyz&list=PL12345")
        self.assertEqual(watch_pl.kind, "youtube_playlist")
        self.assertEqual(watch_pl.item_id, "PL12345")

        mix_vid = classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ")
        self.assertEqual(mix_vid.kind, "youtube_video")
        self.assertEqual(mix_vid.item_id, "dQw4w9WgXcQ")

        embed = classify_url("https://www.youtube.com/embed/jNQXAC9IVRw?autoplay=1")
        self.assertEqual(embed.kind, "youtube_video")
        self.assertEqual(embed.item_id, "jNQXAC9IVRw")

        attrib = classify_url("https://www.youtube.com/attribution_link?a=abc&u=%2Fwatch%3Fv%3DjNQXAC9IVRw")
        self.assertEqual(attrib.kind, "youtube_video")
        self.assertEqual(attrib.item_id, "jNQXAC9IVRw")

        short = classify_url("https://youtu.be/jNQXAC9IVRw?t=12")
        self.assertEqual(short.kind, "youtube_video")
        self.assertEqual(short.item_id, "jNQXAC9IVRw")

    def test_reject_unsupported_url(self) -> None:
        with self.assertRaises(ValueError):
            classify_url("https://example.com/not-music")

    def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename('  A/B: C*?"<>|  '), "A B C")

    def test_unwrap_windows_protocol_uri_and_browser_extension_manifest(self) -> None:
        raw = "soulseekdl://https%3A%2F%2Fopen.spotify.com%2Falbum%2F4aawyAB9vmqN3uQ7FjRGTy"
        self.assertEqual(cli._unwrap_protocol_url(raw), "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
        manifest_path = PROJECT_ROOT / "browser-extension" / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("contextMenus", manifest["permissions"])


class LlmFallbackAndYouTubeExtractorTests(unittest.TestCase):
    def test_heuristic_cleans_garbage_and_avoids_fake_channel_artist(self) -> None:
        tr = parse_track_heuristic("04. Dj NM - Dance Power 12 (Megamix) [Official Video] [HD]", "EuroChannel")
        self.assertEqual(tr.artist, "Dj NM")
        self.assertEqual(tr.title, "Dance Power 12 (Megamix)")

        # Archive channel without separator should NOT become the artist
        tr2 = parse_track_heuristic("Matata [Vinyl Rip]", "KaderFunkyman")
        self.assertEqual(tr2.artist, "")
        self.assertEqual(tr2.title, "Matata")

        # Topic channel IS the artist
        tr3 = parse_track_heuristic("Afro Voodoo", "Alessandro Alessandroni - Topic")
        self.assertEqual(tr3.artist, "Alessandro Alessandroni")

    def test_parse_llm_json_strips_markdown_fences(self) -> None:
        raw = '```json\n{"artist": "Daft Punk", "title": "Voyager"}\n```'
        parsed = _parse_llm_json(raw)
        self.assertEqual(parsed["artist"], "Daft Punk")

    def test_extract_timestamp_lines(self) -> None:
        desc = """
        Tracklist:
        00:00 Cymande - Zion I
        03:20 Cymande - One More
        06:05 Cymande - Getting It Back
        """
        tracks = extract_timestamp_lines(desc, total_duration=600)
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0].artist, "Cymande")
        self.assertEqual(tracks[0].title, "Zion I")
        self.assertEqual(tracks[0].duration_sec, 200)
        self.assertEqual(tracks[2].duration_sec, 235)

    def test_playlist_resolving_to_single_video_falls_back_cleanly(self) -> None:
        cfg = load_config()
        parsed = classify_url("https://www.youtube.com/playlist?list=PLfake")
        single_meta = {
            "_type": "video",
            "title": "Cymande - Zion I (Official Audio)",
            "uploader": "Cymande - Topic",
            "duration": 210,
            "description": "",
        }
        with patch("youtube_extractor._run_yt_dlp_json", return_value=single_meta):
            with patch("llm_formatter._try_mlx_batch", return_value=None):
                with patch("llm_formatter.OllamaSession") as mock_session_cls:
                    inst = mock_session_cls.return_value.__enter__.return_value
                    inst.available = False
                    export = extract_youtube(parsed, cfg)
        self.assertEqual(export.kind, "single_track")
        self.assertEqual(export.tracks[0].artist, "Cymande")
        self.assertEqual(export.tracks[0].title, "Zion I")

    def test_format_playlist_prefers_mlx_minicpm_over_ollama(self) -> None:
        cfg = load_config()
        raw_entries = [
            {"title": "🟡 Milk & Sugar - Higher & Higher (Original Mix) 128 BPM *2000*", "channel": "", "duration": 548},
        ]
        mlx_reply = [
            {
                "is_album": False,
                "album_artist": "",
                "album_title": "",
                "tracks": [{"idx": 0, "artist": "Milk & Sugar", "title": "Higher & Higher (Original Mix)"}],
            }
        ]
        with patch("llm_formatter._try_mlx_batch", return_value=mlx_reply) as mock_mlx:
            with patch("llm_formatter.OllamaSession") as mock_ollama:
                export = format_youtube_playlist_with_llm(
                    cfg=cfg,
                    playlist_title="pnh26",
                    playlist_uploader="User",
                    playlist_id="PLGyZUTYD4yVA",
                    raw_entries=raw_entries,
                    source_url="https://www.youtube.com/playlist?list=PLGyZUTYD4yVA",
                )
        self.assertEqual(export.kind, "playlist")
        self.assertTrue(export.used_llm)
        self.assertEqual(export.tracks[0].artist, "Milk & Sugar")
        self.assertEqual(export.tracks[0].title, "Higher & Higher (Original Mix)")
        mock_mlx.assert_called_once()
        mock_ollama.assert_not_called()

    def test_format_playlist_with_mocked_ollama(self) -> None:
        cfg = load_config()
        raw_entries = [
            {"title": "01. Boiler Room | Fred again.. - Delilah [HD]", "channel": "Boiler Room", "duration": 250},
            {"title": "Winx - Don't Laugh (Original Live Raw Mix) (Official Audio)", "channel": "VinylChannel", "duration": 310},
        ]
        mock_reply = {
            "is_album": False,
            "album_artist": "",
            "album_title": "",
            "tracks": [
                {"idx": 0, "artist": "Fred again..", "title": "Delilah"},
                {"idx": 1, "artist": "Winx", "title": "Don't Laugh (Original Live Raw Mix)"},
            ],
        }
        with patch("llm_formatter._try_mlx_batch", return_value=None):
            with patch("llm_formatter.OllamaSession") as mock_session_cls:
                inst = mock_session_cls.return_value.__enter__.return_value
                inst.available = True
                inst.model = "minicpm5-2b:latest"
                inst.chat_json.return_value = mock_reply

                export = format_youtube_playlist_with_llm(
                    cfg=cfg,
                    playlist_title="Club Cuts",
                    playlist_uploader="User",
                    playlist_id="PL123",
                    raw_entries=raw_entries,
                    source_url="https://www.youtube.com/playlist?list=PL123",
                )
                self.assertEqual(export.kind, "playlist")
                self.assertTrue(export.used_llm)
                self.assertEqual(export.tracks[0].artist, "Fred again..")
                self.assertEqual(export.tracks[1].title, "Don't Laugh (Original Live Raw Mix)")
                inst.chat_json.assert_called_once()
                self.assertEqual(inst.chat_json.call_args.kwargs["keep_alive"], 0)

    def test_format_full_album_video_with_mocked_ollama(self) -> None:
        cfg = load_config()
        meta = {
            "title": "Cymande - Cymande (1972) [Full Album]",
            "uploader": "Vinyl Archive",
            "duration": 2200,
            "chapters": [
                {"title": "Zion I", "start_time": 0, "end_time": 200},
                {"title": "One More", "start_time": 200, "end_time": 365},
                {"title": "Getting It Back", "start_time": 365, "end_time": 600},
            ],
            "description": "",
        }
        mock_reply = {
            "is_full_album": True,
            "album_artist": "Cymande",
            "album_title": "Cymande",
            "tracks": [
                {"artist": "Cymande", "title": "Zion I", "duration_sec": 200},
                {"artist": "Cymande", "title": "One More", "duration_sec": 165},
                {"artist": "Cymande", "title": "Getting It Back", "duration_sec": 235},
            ],
        }
        with patch("llm_formatter._try_mlx_batch", return_value=None):
            with patch("llm_formatter.OllamaSession") as mock_session_cls:
                inst = mock_session_cls.return_value.__enter__.return_value
                inst.available = True
                inst.model = "minicpm5-2b:latest"
                inst.chat_json.return_value = mock_reply

                export = format_youtube_video_with_llm(cfg, meta, "https://www.youtube.com/watch?v=abc")
                self.assertEqual(export.kind, "album")
                self.assertEqual(export.artist, "Cymande")
                self.assertEqual(export.album, "Cymande")
                self.assertEqual(export.total_tracks, 3)

    def test_hardware_auto_profile_protects_8gb_intel_macs(self) -> None:
        cfg = load_config()
        cfg.ollama_model = "auto"
        cfg.hardware = HardwareProfile(
            arch="x86_64",
            cpu_cores=4,
            ram_gb=8,
            is_apple_silicon=False,
            recommended_llm_batch_size=15,
            recommended_yt_workers=2,
            max_ollama_model_billions=3,
        )
        self.assertFalse(_mlx_is_available(cfg))

        sess = OllamaSession(cfg)
        self.assertEqual(sess._pick_model(["qwen2.5:14b", "qwen2.5:7b"]), "")
        self.assertEqual(sess._pick_model(["qwen2.5:14b", "minicpm5-2b:latest", "qwen2.5:3b"]), "minicpm5-2b:latest")

        cfg.hardware = HardwareProfile(
            arch="arm64",
            cpu_cores=12,
            ram_gb=24,
            is_apple_silicon=True,
            recommended_llm_batch_size=25,
            recommended_yt_workers=4,
            max_ollama_model_billions=16,
        )
        self.assertEqual(sess._pick_model(["qwen2.5:14b"]), "qwen2.5:14b")
        self.assertEqual(sess._pick_model(["qwen2.5:14b", "minicpm5-2b:latest"]), "minicpm5-2b:latest")

    def test_youtube_full_album_extracts_tracklist_from_top_comments_and_never_drops_last_track(self) -> None:
        # Also verify flexible timestamp & numbered parsers
        flex_ts = extract_timestamp_lines("01. Intro 0:00\n02. Second Track (03:10)\n03. Final Track 07:45", total_duration=660)
        self.assertEqual(len(flex_ts), 3)
        self.assertEqual(flex_ts[2].title, "Final Track")
        self.assertEqual(flex_ts[2].duration_sec, 195)

        numbered = extract_numbered_tracklist("Tracklist:\n1. First\n2. Second\n3. Third\n4. Fourth\n")
        self.assertEqual([t.title for t in numbered], ["First", "Second", "Third", "Fourth"])

        cfg = load_config()
        meta = {
            "title": "Nu Genea - Bar Mediterraneo (2022) [Full Album]",
            "uploader": "Vinyl Vault",
            "duration": 2100,
            "chapters": [],
            "description": "Enjoy this great record!",
            "top_comments": [
                "Great album!!",
                "Tracklist:\n00:00 Bar Mediterraneo\n04:10 Marechià\n08:30 Tienaté\n12:50 Rire",
            ],
        }
        # Simulate LLM accidentally dropping the last track ('Rire')
        incomplete_llm_reply = {
            "is_full_album": True,
            "album_artist": "Nu Genea",
            "album_title": "Bar Mediterraneo",
            "tracks": [
                {"artist": "Nu Genea", "title": "Bar Mediterraneo", "duration_sec": 250},
                {"artist": "Nu Genea", "title": "Marechià", "duration_sec": 260},
                {"artist": "Nu Genea", "title": "Tienaté", "duration_sec": 260},
            ],
        }
        with patch("llm_formatter._try_mlx_batch", return_value=[incomplete_llm_reply]) as mock_mlx:
            export = format_youtube_video_with_llm(cfg, meta, "https://www.youtube.com/watch?v=fakealb")

        # Verify top comments were passed to the LLM prompt AND the missing last track was recovered!
        prompt_sent = mock_mlx.call_args.args[1][0]["user"]
        self.assertIn("Top comments:", prompt_sent)
        self.assertIn("12:50 Rire", prompt_sent)
        self.assertEqual(export.kind, "album")
        self.assertEqual(export.total_tracks, 4)
        self.assertEqual([t.title for t in export.tracks], ["Bar Mediterraneo", "Marechià", "Tienaté", "Rire"])


class FirstRunSetupTests(unittest.TestCase):
    def test_first_run_only_prompts_once_and_persists_configs_securely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.sockseek_conf = root / "config" / "sockseek.conf"
            cfg.config_file = root / "config" / "pipeline.json"
            chosen_dir = root / "My Custom Downloads"

            # Before setup: first run IS needed.
            self.assertTrue(is_first_run_needed(cfg))

            fake_gui = {
                "confirmed": True,
                "username": "dj_friend",
                "password": "secret_password",
                "output_dir": str(chosen_dir),
                "convert_format": "mp3",
            }
            with patch("setup_ui._can_use_gui", return_value=True):
                with patch("setup_ui._run_gui_setup", return_value=fake_gui) as mock_gui:
                    updated = ensure_configured(cfg)

            self.assertIsNotNone(updated)
            mock_gui.assert_called_once()
            self.assertEqual(updated.output_dir, chosen_dir)
            self.assertEqual(updated.convert_format, "mp3")
            self.assertFalse(updated.convert_flac_to_aiff)
            self.assertTrue(chosen_dir.is_dir())
            self.assertEqual(cfg.sockseek_conf.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cfg.config_file.stat().st_mode & 0o777, 0o600)

            # Subsequent sessions: first run is NOT needed, zero prompts!
            updated.sockseek_conf = cfg.sockseek_conf
            self.assertFalse(is_first_run_needed(updated))
            with patch("setup_ui._run_gui_setup") as mock_gui_again:
                with patch("setup_ui._run_terminal_setup") as mock_term_again:
                    res2 = ensure_configured(updated)
            self.assertIsNotNone(res2)
            mock_gui_again.assert_not_called()
            mock_term_again.assert_not_called()

    def test_placeholder_credentials_trigger_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.sockseek_conf = root / "config" / "sockseek.conf"
            cfg.sockseek_conf.parent.mkdir(parents=True, exist_ok=True)
            cfg.sockseek_conf.write_text(
                "username = YOUR_SOULSEEK_USERNAME\npassword = YOUR_SOULSEEK_PASSWORD\n",
                encoding="utf-8",
            )
            self.assertTrue(is_first_run_needed(cfg))
            write_setup_configs(cfg, "real_user", "real_pass", root / "Music", False)
            self.assertFalse(is_first_run_needed(cfg))

    def test_conversion_box_shown_for_spotify_and_youtube_playlists_and_albums(self) -> None:
        cases = [
            ("https://open.spotify.com/playlist/4rnleEAOdmFAbRcNCgZMpY", "Playlist Spotify rilevata", "aiff"),
            ("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy", "Full Album Spotify rilevato", "mp3"),
            ("https://www.youtube.com/playlist?list=PLGyZUTYD4yVA", "Playlist / Album YouTube rilevato", "mp3"),
            ("https://www.youtube.com/watch?v=ZIHFT1YLmgg", "Video / Full Album YouTube rilevato", "flac"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.sockseek_conf = root / "config" / "sockseek.conf"
            cfg.config_file = root / "config" / "pipeline.json"
            write_setup_configs(cfg, "user_ok", "pass_ok", root / "Music", "flac", True)
            cfg = load_config(cfg.config_file)
            cfg.sockseek_conf = root / "config" / "sockseek.conf"

            fake_export = CollectionExport(
                kind="playlist",
                name="Sample",
                tracks=[Track(artist="A", title="B")],
            )
            for url, expected_label, chosen_fmt in cases:
                with patch.dict("os.environ", {"SLSK_LANG": "it", "SLSK_NO_CAFFEINATE": "1"}, clear=False):
                    with patch("cli.load_config", return_value=cfg):
                        with patch("cli.is_first_run_needed", return_value=False):
                            with patch("cli.ensure_configured", return_value=cfg):
                                with patch("cli._can_use_gui_prompt", return_value=True):
                                    with patch(
                                        "cli._prompt_with_gui",
                                        return_value=(True, url, chosen_fmt),
                                    ) as mock_prompt:
                                        with patch("cli.extract_collection", return_value=fake_export):
                                            with patch("cli.download_collection", return_value=0) as mock_dl:
                                                rc = cli.main(["--no-update", url])
                self.assertEqual(rc, 0)
                mock_prompt.assert_called_once()
                self.assertEqual(mock_prompt.call_args.kwargs.get("label"), expected_label)
                self.assertEqual(mock_dl.call_args.kwargs.get("convert_format"), chosen_fmt)
                self.assertTrue(mock_dl.call_args.kwargs.get("delete_original"))


class DownloaderAndConverterTests(unittest.TestCase):
    def test_write_csv_with_and_without_durations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "with_len.csv"
            exp1 = CollectionExport(
                kind="playlist",
                name="Test",
                tracks=[Track(artist="Artist, One", title="Song, Vol. 1", duration_sec=210)],
            )
            write_tracks_csv(exp1, p1)
            text1 = p1.read_text(encoding="utf-8")
            self.assertIn("Artist,Title,Album,Length", text1)
            self.assertIn('"Artist, One","Song, Vol. 1",,210', text1)

            p2 = Path(tmp) / "no_len.csv"
            exp2 = CollectionExport(
                kind="playlist",
                name="Test2",
                tracks=[Track(artist="Artist", title="Song", duration_sec=None)],
            )
            write_tracks_csv(exp2, p2)
            text2 = p2.read_text(encoding="utf-8")
            self.assertIn("Artist,Title,Album", text2)
            self.assertNotIn("Length", text2)

    def test_youtube_adds_relaxed_length_tolerance_and_album_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config()
            cfg.output_dir = root / "downloads"
            cfg.lists_dir = root / "lists"
            cfg.sockseek_bin = root / "sockseek"
            cfg.sockseek_conf = root / "sockseek.conf"
            cfg.sockseek_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg.sockseek_conf.write_text("username=x\n", encoding="utf-8")

            export = CollectionExport(
                kind="album",
                name="Daft Punk - Discovery",
                artist="Daft Punk",
                album="Discovery",
                total_tracks=2,
                source_platform="youtube",
                tracks=[
                    Track(artist="Daft Punk", title="One More Time", album="Discovery", duration_sec=320),
                    Track(artist="Daft Punk", title="Aerodynamic", album="Discovery", duration_sec=212),
                ],
            )

            calls: list[list[str]] = []

            def fake_run(cmd: list[str]) -> int:
                calls.append(cmd)
                if len(calls) == 2:
                    out_sub = cfg.output_dir / "Daft Punk - Discovery"
                    out_sub.mkdir(parents=True, exist_ok=True)
                    (out_sub / "01 - One More Time.flac").write_bytes(b"A")
                    (out_sub / "02 - Aerodynamic.flac").write_bytes(b"B")
                return 1 if len(calls) == 1 else 0

            with patch("downloader._run_sockseek", side_effect=fake_run):
                with patch("downloader._open_in_finder") as mock_finder:
                    rc = download_collection(export, cfg, open_finder=False, auto_tag=False)

            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("--length-tol", calls[0])
            self.assertIn("15", calls[0])
            self.assertIn("-a", calls[0])
            self.assertTrue(calls[1][1].endswith("Daft Punk - Discovery.csv"))
            mock_finder.assert_not_called()

    def test_album_track_count_check_and_playlist_missing_last_track_rescue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config()
            cfg.output_dir = root / "downloads"
            cfg.lists_dir = root / "lists"
            cfg.sockseek_bin = root / "sockseek"
            cfg.sockseek_conf = root / "sockseek.conf"
            cfg.sockseek_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg.sockseek_conf.write_text("username=x\n", encoding="utf-8")

            # 1) Full Album returns rc=0 with only 2/3 tracks -> must rescue missing 3rd track into same folder
            alb = CollectionExport(
                kind="album",
                name="Cymande - Cymande",
                artist="Cymande",
                album="Cymande",
                total_tracks=3,
                source_platform="spotify",
                tracks=[
                    Track(artist="Cymande", title="Zion I", album="Cymande", duration_sec=210),
                    Track(artist="Cymande", title="One More", album="Cymande", duration_sec=186),
                    Track(artist="Cymande", title="Mighty Heavy Load", album="Cymande", duration_sec=245),
                ],
            )
            alb_calls: list[list[str]] = []
            alb_rescue_csv_contents: list[str] = []

            def fake_alb_run(cmd: list[str]) -> int:
                alb_calls.append(cmd)
                alb_dir = cfg.output_dir / "Cymande - Cymande (1972)"
                alb_dir.mkdir(parents=True, exist_ok=True)
                if len(alb_calls) == 1:
                    (alb_dir / "01 - Zion I.flac").write_bytes(b"1")
                    (alb_dir / "02 - One More.flac").write_bytes(b"2")
                    return 0  # rc=0 even though 3rd track is missing!
                alb_rescue_csv_contents.append(Path(cmd[1]).read_text(encoding="utf-8"))
                (alb_dir / "03 - Mighty Heavy Load.flac").write_bytes(b"3")
                return 0

            with patch("downloader._run_sockseek", side_effect=fake_alb_run):
                with patch("downloader._open_in_finder"):
                    rc_alb = download_collection(alb, cfg, open_finder=False, auto_tag=False)

            self.assertEqual(rc_alb, 0)
            self.assertEqual(len(alb_calls), 2)
            self.assertIn("--desperate", alb_calls[1])
            self.assertIn("Mighty Heavy Load", alb_rescue_csv_contents[0])
            self.assertNotIn("Zion I", alb_rescue_csv_contents[0])
            self.assertNotIn("Length", alb_rescue_csv_contents[0])
            self.assertTrue((cfg.output_dir / "Cymande - Cymande (1972)" / "03 - Mighty Heavy Load.flac").is_file())

            # 2) Playlist misses the LAST track on pass 1 -> must rescue ONLY that last track into same folder
            pl = CollectionExport(
                kind="playlist",
                name="Friend Playlist",
                total_tracks=3,
                source_platform="spotify",
                tracks=[
                    Track(artist="Artist A", title="First Song", duration_sec=200),
                    Track(artist="Artist B", title="Middle Song", duration_sec=210),
                    Track(artist="Artist C", title="Last Song", duration_sec=220),
                ],
            )
            pl_calls: list[list[str]] = []
            pl_rescue_csv_contents: list[str] = []

            def fake_pl_run(cmd: list[str]) -> int:
                pl_calls.append(cmd)
                pl_dir = cfg.output_dir / "Friend Playlist"
                pl_dir.mkdir(parents=True, exist_ok=True)
                if len(pl_calls) == 1:
                    (pl_dir / "01 - First Song.mp3").write_bytes(b"1")
                    (pl_dir / "02 - Middle Song.mp3").write_bytes(b"2")
                    return 0
                pl_rescue_csv_contents.append(Path(cmd[1]).read_text(encoding="utf-8"))
                (pl_dir / "03 - Last Song.mp3").write_bytes(b"3")
                return 0

            with patch("downloader._run_sockseek", side_effect=fake_pl_run):
                with patch("downloader._open_in_finder"):
                    rc_pl = download_collection(pl, cfg, convert_format="mp3", open_finder=False, auto_tag=False)

            self.assertEqual(rc_pl, 0)
            self.assertEqual(len(pl_calls), 2)
            self.assertIn("--desperate", pl_calls[1])
            self.assertIn("Last Song", pl_rescue_csv_contents[0])
            self.assertNotIn("First Song", pl_rescue_csv_contents[0])
            self.assertTrue((cfg.output_dir / "Friend Playlist" / "03 - Last Song.mp3").is_file())
            self.assertFalse((cfg.output_dir / "Friend Playlist" / "BRANI_MANCANTI.txt").exists())

    def test_missing_tracks_report_created_and_preserved_by_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config()
            cfg.output_dir = root / "downloads"
            cfg.lists_dir = root / "lists"
            cfg.sockseek_bin = root / "sockseek"
            cfg.sockseek_conf = root / "sockseek.conf"
            cfg.sockseek_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg.sockseek_conf.write_text("username=x\n", encoding="utf-8")

            pl = CollectionExport(
                kind="playlist",
                name="Rare Vinyl Playlist",
                total_tracks=3,
                source_platform="spotify",
                tracks=[
                    Track(artist="Artist A", title="Found Track", album="Alb A", duration_sec=200),
                    Track(artist="Artist B", title="Missing Rare Gem", album="Rare EP", duration_sec=210),
                    Track(artist="Artist C", title="Also Missing Track", duration_sec=220),
                ],
            )
            pl_dir = cfg.output_dir / "Rare Vinyl Playlist"
            calls = 0

            def fake_run(cmd: list[str]) -> int:
                nonlocal calls
                calls += 1
                pl_dir.mkdir(parents=True, exist_ok=True)
                if calls == 1:
                    (pl_dir / "01 - Found Track.mp3").write_bytes(b"AUDIO")
                    (pl_dir / "cover.jpg").write_bytes(b"JUNK")
                    (pl_dir / "info.nfo").write_text("junk", encoding="utf-8")
                return 0  # Rescue pass also finds nothing

            with patch.dict("os.environ", {"SLSK_LANG": "it"}, clear=False):
                with patch("downloader._run_sockseek", side_effect=fake_run):
                    with patch("downloader._open_in_finder"):
                        rc = download_collection(pl, cfg, convert_format="mp3", open_finder=False, auto_tag=False)

            self.assertEqual(rc, 0)
            self.assertEqual(calls, 2)
            # Non-audio junk MUST still be deleted...
            self.assertFalse((pl_dir / "cover.jpg").exists())
            self.assertFalse((pl_dir / "info.nfo").exists())
            # ...while BRANI_MANCANTI.txt MUST be created and survive cleanup!
            report = pl_dir / "BRANI_MANCANTI.txt"
            self.assertTrue(report.is_file())
            text = report.read_text(encoding="utf-8")
            self.assertIn("Scaricati: 1/3 (mancanti: 2)", text)
            self.assertIn("01. Artist B - Missing Rare Gem [Rare EP]", text)
            self.assertIn("02. Artist C - Also Missing Track", text)
            self.assertNotIn("Found Track", text)

    def test_download_opens_finder_after_flac_to_aiff_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config()
            cfg.output_dir = root / "downloads"
            cfg.lists_dir = root / "lists"
            cfg.sockseek_bin = root / "sockseek"
            cfg.sockseek_conf = root / "sockseek.conf"
            cfg.sockseek_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg.sockseek_conf.write_text("username=x\n", encoding="utf-8")
            if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
                self.skipTest("ffmpeg/ffprobe non disponibili")

            export = CollectionExport(
                kind="playlist",
                name="My Cool Playlist",
                tracks=[Track(artist="Tester", title="Song One")],
            )
            playlist_dir = cfg.output_dir / "My Cool Playlist"

            def fake_run(cmd: list[str]) -> int:
                playlist_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        "-c:a", "flac", str(playlist_dir / "01 - Song One.flac"),
                    ],
                    check=True,
                )
                return 0

            with patch("downloader._run_sockseek", side_effect=fake_run):
                with patch("downloader.tag_downloaded_files", return_value=(1, 0, 0)) as mock_tag:
                    with patch("downloader._open_in_finder") as mock_finder:
                        rc = download_collection(export, cfg, convert_aiff=True, open_finder=True)

            self.assertEqual(rc, 0)
            self.assertTrue((playlist_dir / "01 - Song One.aiff").is_file())
            self.assertFalse((playlist_dir / "01 - Song One.flac").exists())
            mock_tag.assert_called_once()
            self.assertEqual(mock_tag.call_args[0][0], [playlist_dir / "01 - Song One.aiff"])
            mock_finder.assert_called_once_with(playlist_dir, reveal=False)

    def test_real_ffmpeg_converts_flac_to_aiff_deletes_flac_and_keeps_mp3(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "My Playlist"
            folder.mkdir(parents=True)
            flac_file = folder / "01 - Track One.flac"
            mp3_file = folder / "02 - Track Two.mp3"

            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                    "-metadata", "title=Track One", "-metadata", "artist=Tester",
                    "-c:a", "flac", str(flac_file),
                ],
                check=True,
            )
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=0.4",
                    "-c:a", "libmp3lame", "-b:a", "320k", str(mp3_file),
                ],
                check=True,
            )
            index_csv = folder / "_index.csv"
            index_csv.write_text(
                "filepath,artist,album,title\n./01 - Track One.flac,Tester,,Track One\n./02 - Track Two.mp3,Tester,,Track Two\n",
                encoding="utf-8",
            )

            converted, failed = convert_downloaded_flacs_to_aiff(
                [flac_file, mp3_file],
                ffmpeg_bin=cfg.ffmpeg_bin,
                ffprobe_bin=cfg.ffprobe_bin,
            )
            self.assertEqual((converted, failed), (1, 0))
            self.assertFalse(flac_file.exists())
            self.assertTrue(flac_file.with_suffix(".aiff").is_file())
            self.assertTrue(mp3_file.is_file())
            self.assertIn("./01 - Track One.aiff", index_csv.read_text(encoding="utf-8"))

    def test_real_ffmpeg_converts_flac_to_mp3_320_deletes_flac_and_keeps_original_mp3(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "My MP3 Playlist"
            folder.mkdir(parents=True)
            flac_file = folder / "01 - Track One.flac"
            mp3_file = folder / "02 - Already MP3.mp3"

            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                    "-metadata", "title=Track One", "-metadata", "artist=Tester",
                    "-c:a", "flac", str(flac_file),
                ],
                check=True,
            )
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=0.4",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_file),
                ],
                check=True,
            )
            orig_mp3_bytes = mp3_file.read_bytes()
            index_csv = folder / "_index.csv"
            index_csv.write_text(
                "filepath,artist,album,title\n./01 - Track One.flac,Tester,,Track One\n./02 - Already MP3.mp3,Tester,,Already MP3\n",
                encoding="utf-8",
            )

            converted, failed = convert_downloaded_flacs(
                [flac_file, mp3_file],
                target_format="mp3",
                ffmpeg_bin=cfg.ffmpeg_bin,
                ffprobe_bin=cfg.ffprobe_bin,
            )
            out_mp3 = flac_file.with_suffix(".mp3")
            self.assertEqual((converted, failed), (1, 0))
            self.assertFalse(flac_file.exists())
            self.assertTrue(out_mp3.is_file())
            self.assertEqual(mp3_file.read_bytes(), orig_mp3_bytes)
            self.assertIn("./01 - Track One.mp3", index_csv.read_text(encoding="utf-8"))

            probe = subprocess.run(
                [cfg.ffprobe_bin, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,bit_rate", "-of", "csv=p=0", str(out_mp3)],
                capture_output=True,
                text=True,
                check=True,
            )
            codec, bitrate = probe.stdout.strip().split(",")
            self.assertEqual(codec, "mp3")
            self.assertEqual(int(bitrate), 320000)

    def test_keep_original_and_flac_skip_conversion(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "KeepOrig"
            folder.mkdir(parents=True)
            flac_file = folder / "01 - Track One.flac"
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                    "-c:a", "flac", str(flac_file),
                ],
                check=True,
            )

            # target_format='flac' skips conversion completely
            res_skip = convert_downloaded_flacs(
                [flac_file],
                target_format="flac",
                ffmpeg_bin=cfg.ffmpeg_bin,
                ffprobe_bin=cfg.ffprobe_bin,
                delete_original=True,
            )
            self.assertEqual(res_skip, (0, 0))
            self.assertTrue(flac_file.is_file())
            self.assertFalse(flac_file.with_suffix(".mp3").exists())

            # delete_original=False converts to MP3 320 while keeping the original .flac
            res_keep = convert_downloaded_flacs(
                [flac_file],
                target_format="mp3",
                ffmpeg_bin=cfg.ffmpeg_bin,
                ffprobe_bin=cfg.ffprobe_bin,
                delete_original=False,
            )
            self.assertEqual(res_keep, (1, 0))
            self.assertTrue(flac_file.is_file())
            self.assertTrue(flac_file.with_suffix(".mp3").is_file())

    def test_mp3_format_prefers_mp3_320_and_cleans_all_non_audio_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config()
            cfg.output_dir = root / "downloads"
            cfg.lists_dir = root / "lists"
            cfg.sockseek_bin = root / "sockseek"
            cfg.sockseek_conf = root / "sockseek.conf"
            cfg.sockseek_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg.sockseek_conf.write_text("username=x\n", encoding="utf-8")
            cfg.output_dir.mkdir(parents=True, exist_ok=True)
            preexisting = cfg.output_dir / "keep_old_note.txt"
            preexisting.write_text("untouched", encoding="utf-8")

            export = CollectionExport(
                kind="playlist",
                name="Clean Audio Only",
                tracks=[Track(artist="Tester", title="Song One")],
            )
            pl_dir = cfg.output_dir / "Clean Audio Only"
            seen_cmds: list[list[str]] = []

            def fake_run(cmd: list[str]) -> int:
                seen_cmds.append(cmd)
                pl_dir.mkdir(parents=True, exist_ok=True)
                (pl_dir / "01 - Song One.mp3").write_bytes(b"FAKE_MP3")
                (pl_dir / "cover.jpg").write_bytes(b"JUNK")
                (pl_dir / "info.nfo").write_text("junk", encoding="utf-8")
                (pl_dir / "rip.log").write_text("junk", encoding="utf-8")
                (pl_dir / "album.cue").write_text("junk", encoding="utf-8")
                (pl_dir / "_index.csv").write_text("junk", encoding="utf-8")
                artwork_dir = pl_dir / "Artwork"
                artwork_dir.mkdir()
                (artwork_dir / "back.png").write_bytes(b"JUNK")
                return 0

            with patch("downloader._run_sockseek", side_effect=fake_run):
                with patch("downloader._open_in_finder"):
                    rc = download_collection(export, cfg, convert_format="mp3", open_finder=True, auto_tag=False)

            self.assertEqual(rc, 0)
            self.assertEqual(len(seen_cmds), 1)
            cmd = seen_cmds[0]
            self.assertIn("--no-write-index", cmd)
            self.assertEqual(cmd[cmd.index("--pref-format") + 1], "mp3")
            self.assertEqual(cmd[cmd.index("--pref-min-bitrate") + 1], "320")
            self.assertEqual(cmd[cmd.index("--pref-max-bitrate") + 1], "320")
            # Only audio remains in downloaded folder; all junk + empty Artwork dir deleted; preexisting untouched
            self.assertTrue((pl_dir / "01 - Song One.mp3").is_file())
            self.assertFalse((pl_dir / "cover.jpg").exists())
            self.assertFalse((pl_dir / "info.nfo").exists())
            self.assertFalse((pl_dir / "rip.log").exists())
            self.assertFalse((pl_dir / "album.cue").exists())
            self.assertFalse((pl_dir / "_index.csv").exists())
            self.assertFalse((pl_dir / "Artwork").exists())
            self.assertTrue(preexisting.is_file())

    def test_wav_and_aiff_downloaded_by_soulseek_are_converted_to_chosen_mp3_aiff_or_flac(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1) User chose .mp3 -> Soulseek returned .wav, .aiff, and .flac -> ALL must become .mp3 320 kbps
            mp3_dir = root / "mp3_case"
            mp3_dir.mkdir()
            wav_f = mp3_dir / "01 - Track Wav.wav"
            aiff_f = mp3_dir / "02 - Track Aiff.aiff"
            flac_f = mp3_dir / "03 - Track Flac.flac"
            for out_p, codec in ((wav_f, "pcm_s16le"), (aiff_f, "pcm_s24be"), (flac_f, "flac")):
                subprocess.run(
                    [
                        cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        "-c:a", codec, str(out_p),
                    ],
                    check=True,
                )
            conv, fail = convert_downloaded_flacs(
                [wav_f, aiff_f, flac_f],
                target_format="mp3",
                ffmpeg_bin=cfg.ffmpeg_bin,
                ffprobe_bin=cfg.ffprobe_bin,
                delete_original=True,
            )
            self.assertEqual((conv, fail), (3, 0))
            for orig in (wav_f, aiff_f, flac_f):
                self.assertFalse(orig.exists(), f"{orig.name} should be deleted after MP3 conversion")
                self.assertTrue(orig.with_suffix(".mp3").is_file())

            # 2) User chose .aiff -> Soulseek returned .wav -> must become .aiff 24-bit
            aiff_dir = root / "aiff_case"
            aiff_dir.mkdir()
            wav2 = aiff_dir / "01 - Only Wav.wav"
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                    "-c:a", "pcm_s16le", str(wav2),
                ],
                check=True,
            )
            c2, f2 = convert_downloaded_flacs([wav2], "aiff", cfg.ffmpeg_bin, cfg.ffprobe_bin, delete_original=True)
            self.assertEqual((c2, f2), (1, 0))
            self.assertFalse(wav2.exists())
            self.assertTrue(wav2.with_suffix(".aiff").is_file())

            # 3) User chose .flac -> Soulseek returned .wav -> must become .flac lossless
            flac_dir = root / "flac_case"
            flac_dir.mkdir()
            wav3 = flac_dir / "01 - Only Wav.wav"
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                    "-c:a", "pcm_s16le", str(wav3),
                ],
                check=True,
            )
            c3, f3 = convert_downloaded_flacs([wav3], "flac", cfg.ffmpeg_bin, cfg.ffprobe_bin, delete_original=True)
            self.assertEqual((c3, f3), (1, 0))
            self.assertFalse(wav3.exists())
            self.assertTrue(wav3.with_suffix(".flac").is_file())


class AutoUpdaterTests(unittest.TestCase):
    @staticmethod
    def _build_fake_tarball(cli_marker: str) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            files = {
                "repo-main/src/cli.py": f"# {cli_marker}\n".encode("utf-8"),
                "repo-main/src/helper.py": b"VAL = 2\n",
                "repo-main/README.md": b"# Updated README\n",
                "repo-main/Soulseek Batch Pipeline.command": b"#!/bin/bash\necho updated\n",
            }
            for name, content in files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        return buf.getvalue()

    def test_apply_update_preserves_user_configs_and_replaces_src(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "config").mkdir()
            (root / "src" / "cli.py").write_text("# old\n", encoding="utf-8")
            (root / "src" / "slsk_setup.js").write_text("// legacy\n", encoding="utf-8")
            (root / "config" / "sockseek.conf").write_text("username = keepme\npassword = secret\n", encoding="utf-8")
            (root / "config" / "pipeline.json").write_text('{"convert_format": "mp3"}\n', encoding="utf-8")
            (root / "Soulseek Batch Pipeline.command").write_text("#!/bin/bash\necho old\n", encoding="utf-8")

            cfg = load_config(root / "config" / "pipeline.json")
            cfg.project_root = root
            cfg.sockseek_conf = root / "config" / "sockseek.conf"

            tar_bytes = self._build_fake_tarball("v2-updated")
            with patch("updater._recompile_swift_helpers_if_needed"):
                apply_update_from_tarball(cfg, tar_bytes)

            self.assertIn("v2-updated", (root / "src" / "cli.py").read_text(encoding="utf-8"))
            self.assertFalse((root / "src" / "slsk_setup.js").exists())
            self.assertIn("username = keepme", (root / "config" / "sockseek.conf").read_text(encoding="utf-8"))
            self.assertIn('"mp3"', (root / "config" / "pipeline.json").read_text(encoding="utf-8"))
            self.assertIn("echo updated", (root / "Soulseek Batch Pipeline.command").read_text(encoding="utf-8"))

    def test_updater_skips_git_repo_and_checks_every_launch_via_etag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.project_root = root

            with patch("updater.fetch_remote_commit_info") as mock_fetch:
                self.assertFalse(check_and_apply_update(cfg, force=False))
                mock_fetch.assert_not_called()

            # Remove .git -> non-git friend install checks on EVERY launch using conditional ETag (304 = 0 quota)
            (root / ".git").rmdir()
            state_file = root / "config" / ".update_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                f'{{"commit": "{"a" * 40}", "etag": "W/\\"abc\\"", "last_check": {time.time()}}}\n',
                encoding="utf-8",
            )
            with patch("updater.fetch_remote_commit_info", return_value=("not_modified", None, 'W/"abc"')) as mock_fetch:
                self.assertFalse(check_and_apply_update(cfg, force=False))
                mock_fetch.assert_called_once()
                self.assertEqual(mock_fetch.call_args.kwargs.get("etag"), 'W/"abc"')


class AutoTaggerTests(unittest.TestCase):
    def test_deterministic_candidate_prefers_studio_album_over_compilations(self) -> None:
        cands = [
            {
                "src": "itunes",
                "artist": "Daft Punk",
                "album_artist": "Various Artists",
                "album": "Now That's What I Call Classic Hits Vol. 42",
                "title": "Digital Love",
                "track_number": 19,
                "total_tracks": 40,
                "disc_number": 1,
                "date": "2019",
                "duration_sec": 301,
                "cover_url": "https://example.com/comp.jpg",
            },
            {
                "src": "musicbrainz",
                "artist": "Daft Punk",
                "album_artist": "Daft Punk",
                "album": "Best Of Daft Punk",
                "title": "Digital Love",
                "track_number": 5,
                "total_tracks": 18,
                "disc_number": 1,
                "date": "2020",
                "duration_sec": 300,
                "cover_url": "https://example.com/bestof.jpg",
            },
            {
                "src": "itunes",
                "artist": "Daft Punk",
                "album_artist": "Daft Punk",
                "album": "Discovery",
                "title": "Digital Love",
                "track_number": 3,
                "total_tracks": 14,
                "disc_number": 1,
                "date": "2001",
                "duration_sec": 301,
                "cover_url": "https://example.com/discovery.jpg",
            },
        ]
        chosen = _choose_candidate_deterministic(cands, "Daft Punk", "Digital Love", "", 301, "playlist")
        self.assertEqual(chosen, 2)

    def test_album_cohesion_unifies_album_artist_and_total_tracks(self) -> None:
        export = CollectionExport(
            kind="album",
            name="Daft Punk - Discovery",
            artist="Daft Punk",
            album="Discovery",
            total_tracks=14,
            cover_url="https://example.com/discovery.jpg",
        )
        decisions = [
            TagDecision(
                artist="Daft Punk",
                album_artist="Daft Punk",
                album="Discovery",
                title="One More Time",
                track_number=1,
                total_tracks=14,
                disc_number=1,
                date="2001",
                cover_url="https://example.com/discovery.jpg",
            ),
            TagDecision(
                artist="Daft Punk feat. Romanthony",
                album_artist="Daft Punk feat. Romanthony",
                album="",
                title="Too Long",
                track_number=14,
                total_tracks=None,
                disc_number=1,
                date="",
                cover_url="",
            ),
        ]
        _enforce_album_cohesion(decisions, export)
        self.assertEqual(decisions[1].album_artist, "Daft Punk")
        self.assertEqual(decisions[1].album, "Discovery")
        self.assertEqual(decisions[1].total_tracks, 14)
        self.assertEqual(decisions[1].cover_url, "https://example.com/discovery.jpg")
        self.assertEqual(decisions[1].date, "2001")

    def test_real_cross_format_lossless_tagging_and_cover_art_flac_aiff_mp3_m4a(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Generate a real PNG cover image
            cover_png = root / "cover.png"
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x1e88e5:s=64x64:d=0.1",
                    "-frames:v", "1", str(cover_png),
                ],
                check=True,
            )
            cover_bytes = cover_png.read_bytes()

            # Generate dirty/untagged real audio files across all 4 formats
            specs = [
                (root / "03 - Digital Love.flac", ["-c:a", "flac"]),
                (root / "03 - Digital Love.aiff", ["-c:a", "pcm_s24be"]),
                (root / "03 - Digital Love.mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
                (root / "03 - Digital Love.m4a", ["-c:a", "aac", "-b:a", "192k"]),
            ]
            audio_paths: list[Path] = []
            for p, codec_args in specs:
                subprocess.run(
                    [
                        cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        *codec_args,
                        "-metadata", "title=03. Digital Love [FLAC]",
                        str(p),
                    ],
                    check=True,
                )
                audio_paths.append(p)

            export = CollectionExport(
                kind="playlist",
                name="Mixed Test",
                source_platform="spotify",
                tracks=[
                    Track(
                        artist="Daft Punk",
                        title="Digital Love",
                        album="Discovery",
                        album_artist="Daft Punk",
                        track_number=3,
                        total_tracks=14,
                        disc_number=1,
                        release_year="2001",
                        cover_url="https://example.com/discovery.png",
                    )
                    for _ in audio_paths
                ],
            )

            with patch("tagger._try_mlx_tagger_batch", return_value=None):
                with patch("tagger.download_cover_art", return_value=cover_bytes):
                    updated, already_ok, failed = tag_downloaded_files(audio_paths, export, cfg, allow_network=True)

            self.assertEqual((updated, already_ok, failed), (4, 0, 0))

            for p in audio_paths:
                probed = probe_audio_tags(cfg.ffprobe_bin, p)
                self.assertEqual(probed.artist, "Daft Punk", f"artist mismatch on {p.suffix}")
                self.assertEqual(probed.album_artist, "Daft Punk", f"album_artist mismatch on {p.suffix}")
                self.assertEqual(probed.album, "Discovery", f"album mismatch on {p.suffix}")
                self.assertEqual(probed.title, "Digital Love", f"title mismatch on {p.suffix}")
                self.assertEqual(probed.track_number, 3, f"track_number mismatch on {p.suffix}")
                self.assertEqual(probed.total_tracks, 14, f"total_tracks mismatch on {p.suffix}")
                self.assertEqual(probed.disc_number, 1, f"disc_number mismatch on {p.suffix}")
                self.assertEqual(probed.date, "2001", f"date mismatch on {p.suffix}")
                self.assertTrue(probed.has_cover, f"cover missing on {p.suffix}")
                self.assertFalse(tags_need_repair(probed), f"tags_need_repair should be False on {p.suffix}")

            # Second pass must skip every already-complete file without rewriting!
            with patch("tagger.write_audio_tags_lossless") as mock_write:
                u2, ok2, f2 = tag_downloaded_files(audio_paths, export, cfg, allow_network=False)
            self.assertEqual((u2, ok2, f2), (0, 4, 0))
            mock_write.assert_not_called()

    def test_mixed_playlist_option_a_with_mlx_tagger_lora_tags_each_track_with_real_original_album(self) -> None:
        cfg = load_config()
        if not Path(cfg.ffmpeg_bin).is_file() or not Path(cfg.ffprobe_bin).is_file():
            self.skipTest("ffmpeg/ffprobe non disponibili")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover_png = root / "cover.png"
            subprocess.run(
                [
                    cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x43a047:s=64x64:d=0.1",
                    "-frames:v", "1", str(cover_png),
                ],
                check=True,
            )
            cover_bytes = cover_png.read_bytes()

            f1 = root / "01 - Track A.mp3"
            f2 = root / "02 - Track B.mp3"
            for f in (f1, f2):
                subprocess.run(
                    [
                        cfg.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
                        "-c:a", "libmp3lame", "-b:a", "192k", str(f),
                    ],
                    check=True,
                )

            export = CollectionExport(
                kind="playlist",
                name="My Mixed Vibes",
                source_platform="youtube",
                tracks=[
                    Track(artist="Air", title="Track A"),
                    Track(artist="Aphex Twin", title="Track B"),
                ],
            )
            fake_itunes = {
                "track a": [
                    {
                        "src": "itunes",
                        "artist": "Air",
                        "album_artist": "Air",
                        "album": "Moon Safari",
                        "title": "La Femme d'Argent",
                        "track_number": 1,
                        "total_tracks": 10,
                        "disc_number": 1,
                        "date": "1998",
                        "duration_sec": 430,
                        "cover_url": "https://example.com/moon.png",
                    }
                ],
                "track b": [
                    {
                        "src": "itunes",
                        "artist": "Aphex Twin",
                        "album_artist": "Aphex Twin",
                        "album": "Selected Ambient Works 85-92",
                        "title": "Xtal",
                        "track_number": 1,
                        "total_tracks": 13,
                        "disc_number": 1,
                        "date": "1992",
                        "duration_sec": 294,
                        "cover_url": "https://example.com/saw.png",
                    }
                ],
            }
            fake_mlx_response = [
                {
                    "tracks": [
                        {
                            "idx": 0,
                            "candidate_idx": 0,
                            "artist": "Air",
                            "album_artist": "Air",
                            "album": "Moon Safari",
                            "title": "La Femme d'Argent",
                            "track_number": 1,
                            "total_tracks": 10,
                            "disc_number": 1,
                            "date": "1998",
                        },
                        {
                            "idx": 1,
                            "candidate_idx": 0,
                            "artist": "Aphex Twin",
                            "album_artist": "Aphex Twin",
                            "album": "Selected Ambient Works 85-92",
                            "title": "Xtal",
                            "track_number": 1,
                            "total_tracks": 13,
                            "disc_number": 1,
                            "date": "1992",
                        },
                    ]
                }
            ]

            def fake_search(artist: str, title: str, album: str = "", limit: int = 5) -> list[dict]:
                return fake_itunes.get(title.lower(), [])

            with patch("tagger.search_itunes_candidates", side_effect=fake_search):
                with patch("tagger.search_musicbrainz_candidates", return_value=[]):
                    with patch("tagger._try_mlx_tagger_batch", return_value=fake_mlx_response) as mock_mlx:
                        with patch("tagger.download_cover_art", return_value=cover_bytes):
                            u, ok, fail = tag_downloaded_files([f1, f2], export, cfg, allow_network=True)

            self.assertEqual((u, ok, fail), (2, 0, 0))
            mock_mlx.assert_called_once()
            p1 = probe_audio_tags(cfg.ffprobe_bin, f1)
            p2 = probe_audio_tags(cfg.ffprobe_bin, f2)
            # Option A: each playlist track gets its OWN real original album + album_artist + cover art!
            self.assertEqual((p1.album, p1.album_artist, p1.track_number, p1.total_tracks, p1.has_cover),
                             ("Moon Safari", "Air", 1, 10, True))
            self.assertEqual((p2.album, p2.album_artist, p2.track_number, p2.total_tracks, p2.has_cover),
                             ("Selected Ambient Works 85-92", "Aphex Twin", 1, 13, True))


class LinuxSupportTests(unittest.TestCase):
    def test_linux_zenity_prompt_and_headless_guard(self) -> None:
        from linux import slsk_ui

        # Headless SSH session must disable GUI dialogs cleanly
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {"DISPLAY": ":0", "SSH_CONNECTION": "1.2.3.4 22 5.6.7.8 22"}, clear=False):
                self.assertFalse(slsk_ui.has_linux_display())

        # Active X11 desktop with zenity prompt picks chosen format
        fake_proc = subprocess.CompletedProcess(args=["zenity"], returncode=0, stdout=".mp3 (320 kbps)\n", stderr="")
        with patch.object(sys, "platform", "linux"):
            with patch.dict("os.environ", {"DISPLAY": ":0", "SSH_CONNECTION": "", "SSH_TTY": ""}, clear=False):
                with patch("linux.slsk_ui._tk_available", return_value=False):
                    with patch("shutil.which", side_effect=lambda name: "/usr/bin/zenity" if name == "zenity" else None):
                        with patch("subprocess.run", return_value=fake_proc):
                            res = slsk_ui.run_linux_prompt(
                                "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy",
                                "flac",
                                label="Full Album Spotify rilevato",
                            )
        self.assertEqual(res, (True, "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy", "mp3"))

    def test_linux_open_in_file_manager_uses_xdg_open_and_updater_syncs_sh_launcher(self) -> None:
        import downloader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dl_dir = root / "Album"
            dl_dir.mkdir()
            with patch.object(sys, "platform", "linux"):
                with patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False):
                    with patch("shutil.which", side_effect=lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None):
                        with patch("subprocess.Popen") as mock_popen:
                            downloader._open_in_finder(dl_dir, reveal=False)
            mock_popen.assert_called_once()
            self.assertEqual(mock_popen.call_args[0][0], ["/usr/bin/xdg-open", str(dl_dir)])

            # Verify updater syncs Soulseek Batch Pipeline.sh with +x
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                for rel, body in (
                    ("repo-main/src/cli.py", b"# linux-updated\n"),
                    ("repo-main/src/linux/slsk_watcher.py", b"#!/usr/bin/env python3\n"),
                    ("repo-main/Soulseek Batch Pipeline.sh", b"#!/bin/bash\necho linux\n"),
                ):
                    info = tarfile.TarInfo(name=rel)
                    info.size = len(body)
                    tf.addfile(info, io.BytesIO(body))
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.project_root = root
            with patch("updater._recompile_swift_helpers_if_needed"):
                apply_update_from_tarball(cfg, buf.getvalue())
            sh_path = root / "Soulseek Batch Pipeline.sh"
            self.assertTrue(sh_path.is_file())
            self.assertTrue(sh_path.stat().st_mode & 0o111)


class I18nTests(unittest.TestCase):
    def test_all_it_and_en_keys_match_and_language_detection_defaults_to_en_for_non_italian(self) -> None:
        import i18n

        self.assertEqual(set(i18n.STRINGS["it"].keys()), set(i18n.STRINGS["en"].keys()))

        # Italian locales -> 'it'
        for val in ("it", "it_IT.UTF-8", "it-CH", "it_IT:en_US"):
            with patch.dict("os.environ", {"SLSK_LANG": "", "LC_ALL": val, "LANG": ""}, clear=False):
                i18n.reset_lang_cache()
                self.assertEqual(i18n.detect_language(), "it", f"Expected 'it' for {val}")

        # Any non-Italian locale -> 'en'
        for val in ("en_US.UTF-8", "fr_FR.UTF-8", "de_DE.UTF-8", "es_ES.UTF-8", "ja_JP.UTF-8"):
            with patch.dict("os.environ", {"SLSK_LANG": "", "LC_ALL": val, "LANG": ""}, clear=False):
                i18n.reset_lang_cache()
                self.assertEqual(i18n.detect_language(), "en", f"Expected 'en' for {val}")

        # Explicit SLSK_LANG override switches UI strings and missing-tracks filename
        with patch.dict("os.environ", {"SLSK_LANG": "en"}, clear=False):
            self.assertEqual(i18n.get_lang(), "en")
            self.assertEqual(i18n.t("dl_report_filename"), "MISSING_TRACKS.txt")
            self.assertEqual(cli._describe_source_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"), "Spotify Full Album detected")

        with patch.dict("os.environ", {"SLSK_LANG": "it"}, clear=False):
            self.assertEqual(i18n.get_lang(), "it")
            self.assertEqual(i18n.t("dl_report_filename"), "BRANI_MANCANTI.txt")
            self.assertEqual(cli._describe_source_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"), "Full Album Spotify rilevato")

        i18n.reset_lang_cache()


class DownloadQueueTests(unittest.TestCase):
    def test_concurrent_launches_enqueue_and_drain_in_fifo_order_with_per_item_formats(self) -> None:
        from queue_manager import QueueJob, get_queue_dir, list_pending_jobs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.project_root = root

            executed: list[tuple[str, str]] = []
            secondary_results: list[tuple[int, bool]] = []

            def fake_run_single(job: QueueJob, _cfg, export_only: bool = False) -> int:
                executed.append((job.url, job.convert_format))
                # While Job 1 is actively downloading (holding the OS flock), two other windows trigger downloads!
                if len(executed) == 1:
                    r2 = cli._run_or_enqueue_jobs(
                        [QueueJob(url="https://open.spotify.com/album/second", convert_format="mp3")],
                        cfg,
                        use_queue=True,
                    )
                    r3 = cli._run_or_enqueue_jobs(
                        [QueueJob(url="https://open.spotify.com/playlist/third", convert_format="aiff")],
                        cfg,
                        use_queue=True,
                    )
                    secondary_results.extend([r2, r3])
                return 0

            with patch.dict("os.environ", {"SLSK_NO_CAFFEINATE": "1"}, clear=False):
                with patch("cli._run_single_job", side_effect=fake_run_single):
                    rc, enqueued_only = cli._run_or_enqueue_jobs(
                        [QueueJob(url="https://open.spotify.com/album/first", convert_format="flac")],
                        cfg,
                        use_queue=True,
                    )

            self.assertEqual((rc, enqueued_only), (0, False))
            self.assertEqual(secondary_results, [(0, True), (0, True)])
            self.assertEqual(
                executed,
                [
                    ("https://open.spotify.com/album/first", "flac"),
                    ("https://open.spotify.com/album/second", "mp3"),
                    ("https://open.spotify.com/playlist/third", "aiff"),
                ],
            )
            self.assertEqual(list_pending_jobs(get_queue_dir(root)), [])

    def test_overnight_queue_continues_after_error_and_clear_queue_works(self) -> None:
        from queue_manager import QueueJob, clear_pending_jobs, enqueue_job, get_queue_dir, list_pending_jobs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(root / "config" / "pipeline.json")
            cfg.project_root = root
            qdir = get_queue_dir(root)

            # Test clear_pending_jobs
            enqueue_job(qdir, QueueJob(url="https://open.spotify.com/album/cancel1"))
            enqueue_job(qdir, QueueJob(url="https://open.spotify.com/album/cancel2"))
            self.assertEqual(len(list_pending_jobs(qdir)), 2)
            self.assertEqual(clear_pending_jobs(qdir), 2)
            self.assertEqual(list_pending_jobs(qdir), [])

            # Test overnight resilience: Job 1 raises, Jobs 2 & 3 still execute!
            executed: list[str] = []

            def fake_run(job: QueueJob, _cfg, export_only: bool = False) -> int:
                executed.append(job.url)
                if job.url.endswith("/broken"):
                    raise RuntimeError("temporary network failure")
                return 0

            jobs = [
                QueueJob(url="https://open.spotify.com/album/broken", convert_format="flac"),
                QueueJob(url="https://open.spotify.com/album/ok2", convert_format="mp3"),
                QueueJob(url="https://open.spotify.com/album/ok3", convert_format="aiff"),
            ]
            with patch.dict("os.environ", {"SLSK_NO_CAFFEINATE": "1"}, clear=False):
                with patch("cli._run_single_job", side_effect=fake_run):
                    rc, enqueued_only = cli._run_or_enqueue_jobs(jobs, cfg, use_queue=True)

            self.assertEqual(enqueued_only, False)
            self.assertEqual(rc, 1)  # Reports non-zero because 1 item failed
            self.assertEqual(
                executed,
                [
                    "https://open.spotify.com/album/broken",
                    "https://open.spotify.com/album/ok2",
                    "https://open.spotify.com/album/ok3",
                ],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
