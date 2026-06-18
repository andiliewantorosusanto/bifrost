# Bifröst

Watch any YouTube stream in English. Paste a link — a live stream or a regular video —
and Bifröst transcribes the audio **on-device**, translates it to English, and brings
the live chat across with you. Single-user, personal tool for macOS (Apple Silicon).

## Architecture

One stream in, three independent pipelines out — they only meet in the browser:

```
            YouTube
           ╱   │   ╲
   ┌──────╴    │    ╶──────────┐
   │ IFrame    │ yt-dlp        │ YouTube Data API
   │ player    │ (audio only)  │ (live chat)
   │ (watch)   ▼               ▼
   │       ffmpeg → 10s WAV   DeepL (text → EN)
   │           ▼               │
   │       whisper.cpp         │
   │       (audio → EN + JA)   │
   ╲           │               │
    ╲──────────┴───────────────┘
            browser UI (WebSocket)
```

```
┌────────────────────── Docker container ──────────────────────┐
│  FastAPI + WebSocket (port 7842)                             │
│  backend/app/                                                │
│  ├─ audio.py     yt-dlp → ffmpeg → 16 kHz WAV chunks,        │
│  │               VOD stall-resume, live reconnect            │
│  ├─ whisper_client.py  HTTP client → whisper-server          │
│  ├─ chat.py      YouTube Data API v3 live-chat poller        │
│  ├─ translate.py DeepL (chat text → English)                 │
│  ├─ library.py   offline media + cached transcripts          │
│  ├─ session.py   orchestrator (wires everything, no logic)   │
│  └─ main.py      FastAPI entry, /ws, static mounts           │
│  frontend/       Vite + React 19 + TypeScript + Tailwind v4  │
└──────────────────────────────────────────────┬───────────────┘
                                               ▼ host.docker.internal:8178
                    ┌──────────────────────────────────────────┐
                    │  whisper-server — NATIVE on macOS        │
                    │  whisper.cpp · Metal GPU · model loaded  │
                    │  once · --translate · Silero VAD         │
                    └──────────────────────────────────────────┘
```

Whisper runs **outside** Docker on purpose: containers on macOS are Linux VMs with no
Metal GPU access, and CPU-only Whisper is ~3× slower — too slow to keep up with a live
stream on a fanless M4. Everything else is containerized for portability.

## Setup (once)

```sh
brew install whisper-cpp                 # native Whisper engine (Metal)
./whisper/download-models.sh             # large-v3 (~3.1 GB) + medium (~1.5 GB) + Silero VAD
cp config.example.toml config.toml       # then add your API keys
```

Keys (both optional — audio translation works with none):
- `youtube_api_key` — [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) key, for the live-chat panel.
- `deepl_api_key` — [DeepL API free](https://www.deepl.com/pro-api) key, to translate chat into English.

Docker Desktop (or OrbStack/colima) must be running.

## Run

```sh
./run.sh         # starts native whisper-server, then the app container
# or:
make run         # same; `make help` lists everything (rebuild/stop/logs/status/clean)
```

Open **http://localhost:7842**, paste a YouTube URL, press *Translate to English*.

Frontend development with hot reload (backend keeps running in Docker):

```sh
cd frontend && npm install && npm run dev    # Vite dev server, proxies /ws + /library
```

## Features

- **Live streams & videos** — live captions lag by ~chunk length; video transcripts
  build faster than real-time and the caption overlay follows your playhead.
- **Dual captions** — English + the original Japanese under it (second Whisper pass;
  `dual_transcript` in config). Toggle under *Captions* menu.
- **Custom player** — chromeless YouTube embed with Bifröst controls. Captions are
  draggable (double-click to reset), sized S/M/L/XL, and survive fullscreen.
- **Live lag meter** — `~21s behind · now 21:27:51` chip computed from capture
  timestamps vs the player's distance to the live edge; also shown in fullscreen.
- **Download monitor** — `dl 0.3× · throttled` chip shows real capture speed so you
  can tell YouTube throttling from a slow machine. Tooltip has whisper per-chunk time.
- **Offline library** — *Save offline* downloads the video (mp4) and plays it in a
  native player (also the escape hatch for embed-blocked videos). Transcripts are
  cached automatically — including **partial progress when you stop midway**, which
  resumes where it left off next time you open the video; *Regenerate transcript*
  re-runs Whisper; trash icon removes an entry. Lives in `library/`, survives rebuilds.
- **`t=` aware** — a URL with `?t=269s` starts transcription there (partial sessions
  aren't cached).
- **Stall resilience** — VOD downloads resume from the last completed chunk
  (YouTube throttling/timeouts are routine); live capture reconnects with backoff.
- **Chat translation** — live chat polled via the Data API, translated by DeepL
  (ASCII-only messages skip the API to save quota), timestamps in your local time.
- **Members-only videos** — run `make cookies` once (`BROWSER=chrome` default;
  safari/firefox/edge/brave supported) to export your YouTube login into
  `cookies.txt`; yt-dlp then accesses member content your account can watch.
  The *embed* usually still refuses members videos — use *Save offline & play
  here*, or read captions while watching on YouTube. Members-only chat needs
  OAuth and is not supported. Re-run `make cookies` if access stops working
  (YouTube rotates cookies). The jar stays on your machine (mounted read-only,
  never copied into the image).

## Configuration

`config.toml` (or `BIFROST_*` env vars — env wins):

| key | default | notes |
|---|---|---|
| `whisper_model` | `large-v3` | `medium` = ~2.5× less GPU, rougher output. Restart `run.sh` to apply. |
| `chunk_seconds` | 15 | 10–20. Smaller = lower caption lag, less context per chunk. |
| `dual_transcript` | `true` | second Whisper pass for the original-language line; `false` halves GPU work |
| `youtube_api_key` | — | chat panel |
| `deepl_api_key` | — | chat translation |
| `whisper_url` | `http://127.0.0.1:8178` | container overrides via compose to `host.docker.internal` |

**Never use `large-v3-turbo`** — it was fine-tuned without translation data and returns
Japanese when asked for English. The run script refuses it, and the app warns if output
stops looking like English.

## What to expect

- Japanese → English is **gist-level**, not broadcast subtitling. Names, slang, and
  fast gaming/vtuber speech will occasionally come out mangled — model, not bug.
- First video after a container (re)start takes ~30–50 s to begin (yt-dlp caches
  YouTube's player challenge); after that, probes are seconds.
- Whisper hallucinations on music/silence ("Please subscribe…") are suppressed by
  Silero VAD + non-speech filtering, but rare ones can still slip through.
- Sustained GPU load warms a fanless Air. Cheapest relief: `dual_transcript = false`;
  biggest: `whisper_model = "medium"`. The Docker VM also drains battery while it
  runs — `make stop` + quit Docker Desktop when done.
