# CLAUDE.md — Bifröst

Local YouTube → English translator (audio + live chat) for macOS Apple Silicon (M4
fanless Air, 16 GB). Single user, runs at http://localhost:7842. The original build
brief is `../youtube_live_translator_build_prompt.md`; treat its hard decisions as
settled (English-only output, chunked pipeline not low-latency, YouTube only).

## Architecture in one paragraph

The Docker container (FastAPI, port 7842) runs everything EXCEPT the two Metal
services: `whisper-server` (whisper.cpp, `:8178`) and — optionally —
`llama-server` (llama.cpp, `:8180`, local chat translation) run natively on the
host because Docker-on-Mac has no Metal GPU. Container reaches them at
`host.docker.internal:{8178,8180}`. Three independent pipelines: the user
*watches* a chromeless YouTube IFrame embed (or a saved mp4 in a native
`<video>`); *captions* come from a parallel yt-dlp audio download chunked by
ffmpeg into WAVs and sent to whisper-server twice per chunk (translate→EN +
transcribe→JA when `dual_transcript`); *chat* is polled from the YouTube Data API
and translated by DeepL or the local LLM (per `chat_translate`). They meet only in
the browser over `/ws`.

## File map

- `backend/app/session.py` — orchestrator. Session lifecycle, WS event fan-out (Hub),
  caption events (`t0/t1/captured_at`), library cache-hit replay, auto-cache guard,
  download/regenerate/delete actions, pipeline monitor (capture speed broadcast).
- `backend/app/audio.py` — probe (`yt-dlp -J`, skips HLS/DASH manifests) +
  ChunkStream (yt-dlp|ffmpeg via os.pipe, segment WAVs, VOD stall-resume with
  `--download-sections`, live reconnect, `captured_seconds` progress for the monitor).
- `backend/app/whisper_client.py` — POST /inference; per-request `translate` override;
  non-speech/hallucination filters; turbo-trap guard (`looks_non_english`).
- `backend/app/chat.py` / `translate.py` — Data API poller / chat translation.
  `translate.py` has two backends behind `Translator`: DeepL (sync, `to_thread`)
  and `LocalLLMTranslator` (httpx → llama-server `/v1/chat/completions`, one JSON-
  array request per batch, per-line fallback). `chat_translate` = auto|deepl|local.
- `backend/app/library.py` — `library/<video_id>/{meta.json,captions.json,media.mp4}`.
- `frontend/src/` — Vite + React 19 + TS strict + Tailwind v4 (own theme in
  `index.css`, NO design-system dependency). `useBifrost.ts` (WS + prefs + now-clock),
  `players.ts` (YT IFrame API + native video hooks, same shape), `components/Stage.tsx`
  (player chrome, draggable caption overlay, status chip, Captions popover).
- `whisper/run-whisper-server.sh` — `--translate --language auto --suppress-nst` +
  Silero VAD; refuses turbo models. `download-models.sh` fetches large-v3/medium/VAD.
- `llm/run-llm-server.sh` — `llama-server -ngl 99 --jinja` for local chat
  translation (default Qwen2.5-3B-Instruct Q4_K_M in `models/`).
  `download-model.sh` fetches it (~1.9GB). `make translate{,-model}`.
- `Makefile` — run/start/stop/rebuild/clean/logs/status. `run.sh` = whisper + compose
  (its EXIT trap runs `docker compose down` — that's why the container "disappears"
  after Ctrl-C).

## Hard-won gotchas — do NOT re-debug these

1. **Whisper must stay native** (Metal). Brew `whisper-cpp` ships `whisper-server`.
   60 s JA audio → EN in ~9.8 s on large-v3/M4.
2. **turbo models can't translate** (return source language). Guarded in run script +
   `looks_non_english` heuristic → UI warning.
3. **yt-dlp in Docker needs a JS runtime** or YouTube's n-challenge throttles to
   ~KB/s: image copies deno from `denoland/deno:bin` + `yt-dlp[default]` (pulls
   yt-dlp-ejs). pip in `python:3.12-slim` must be upgraded first or resolution fails.
4. **asyncio can't chain subprocess stdout→stdin** — use `os.pipe()` (audio.py).
5. **yt-dlp extraction is slow**: ~30–50 s cold per container start (player JS +
   challenge solver caching), seconds warm. Probe skips `hls,dash` manifests —
   for a 2 h stream replay that's 84 s → 2 s, because manifests list every segment.
   LIVE capture needs manifests, so live downloads re-extract instead of reusing the
   probe's info JSON (VODs reuse it via `--load-info-json`). An in-progress live has
   ONLY hls/dash formats, so `skip=hls,dash` leaves the probe with zero formats and
   yt-dlp aborts ("No video formats found!") — the probe carries
   `--ignore-no-formats-error` so `-J` still emits `is_live`/metadata; the live
   download re-extracts without the skip and gets its formats. (Without the flag,
   every live URL fails at probe and the session never starts.)
6. **Downloads stall routinely** (read-timeouts to googlevideo; player streams fine
   while yt-dlp crawls — YouTube anti-download policy, not bandwidth). VODs resume
   from last full chunk; never trust EOF as completion — check coverage vs duration.
   A stalled download once cached 90 s of a 139-min video as "complete" (poisoned
   cache); the completeness guard in `_audio_loop` prevents that now.
7. **Whisper hallucinates on music/silence** ("ご視聴ありがとうございました" /
   "Please subscribe to my channel!", repetition loops). Mitigated by Silero VAD at
   the server + `--suppress-nst` + `_is_non_speech` filter. EN and JA passes
   hallucinate *independently* — mismatched lines on silence are the tell.
8. **Embeds can be blocked** (error 101/150, common for Content-ID'd music) while
   yt-dlp still downloads fine — two different access paths. UI falls back to an
   explanation panel + "Save offline & play here" (native player avoids embeds
   entirely). `playable_in_embed` from yt-dlp is not reliable.
9. **Live lag math**: caption lag = (now − chunk `captured_at`) − (player
   `duration − currentTime`) + chunk/2 (mid-chunk reference — users read whole
   chunks, so "end-aligned" looked falsely in-sync). `captured_at` is stamped in
   audio.py when the chunk file completes, NOT when the session loop picks it up
   (Whisper backlog made captions look fresher than reality). Live-edge sync: the
   YT IFrame autoplays a live embed at the OLDEST seekable point (start of the ~1h
   DVR window), while yt-dlp captures at the live edge — so the raw lag reads
   "~Ns ahead" with N≈DVR length (~3500s). `useYouTubePlayer(videoId, isLive)`
   one-shot-seeks the embed to `getDuration()` (the edge) when it loads >60s behind
   (one-shot so a manual scrub-back sticks); Stage shows "syncing to live…" while
   `playerBehind > 60` instead of the bogus number.
10. **Timestamps**: container is UTC — never format times server-side. Send epochs
    (`captured_at`) or ISO (`published_at`); the browser formats local HH:MM:SS.
    VOD captions use media position (`t0`), live uses wall clock.
11. **`networkidle` never fires in Playwright here** (open WebSocket) — use
    `wait_until="load"`. Headless Esc doesn't exit JS fullscreen; click the button.
12. **Partial transcripts persist + resume**: stopping a VOD session mid-video
    saves progress (cancel-path + every 12 chunks + EOF) with
    `captions_covered_s` / `captions_complete` in meta.json; reopening replays
    the cached part and resumes processing at `covered_s` (local media file via
    `-ss` when saved offline, else `--download-sections`). Legacy caches without
    the meta keys are treated as complete. `t=` sessions don't cache to the
    library; live uses a *separate* recovery cache (see #14, not the VOD path).
14. **Live session recovery**: live captions/chat ARE persisted, but to
    `library/<id>/live.json` (NOT the VOD `captions.json`/`meta.json` path — so
    they never show in the library list and never hit VOD resume; t0 is
    capture-relative, there's no media to scrub). `session._save_live_session()`
    writes it every chunk (audio loop) + every chat batch, atomically (tmp+rename,
    two writer tasks). On `start()` of a still-live URL whose `live.json` is
    <12h old (`LIVE_CACHE_TTL`), the captions+chat replay and `_caption_seq`
    resumes, so a refresh/backend-restart continues the feed instead of blanking.
    Frontend persists the active URL to `localStorage` (`bf-active-url`) so a full
    page reload can re-issue `start` and trigger the restore; cleared on stop/end.
    Display orders live lines by `captured_at`, so restored + new lines interleave
    even though new `t0` restarts at 0.
13. **Members-only videos**: `cookies.txt` (Netscape jar) at repo root, mounted RO
    into the container (`BIFROST_COOKIES`); `audio.cookie_args()` adds `--cookies`
    to every yt-dlp call when the file is non-empty. Exported on the HOST via
    `make cookies` (`--cookies-from-browser` can't run in the container — Chrome
    encrypts its store with the macOS Keychain). Empty file = feature off; the
    file must exist or the compose bind mount creates a directory. Members embeds
    still fail (expected → fallback panel); members chat would need OAuth (absent).
15. **Live chat is quota-metered by POLLING, not messages**: `liveChatMessages.list`
    costs 1 unit/call regardless of message volume, and YouTube chat must be polled.
    At a 2s poll that's ~1,800 units/h → a 10k/day key dies in ~5.5h (a multi-hour
    stream blows the budget). Two guards: the poll interval is floored at
    `config.chat_poll_seconds` (default **10s** ≈ 360/h ≈ 28h; clamped ≥1s; YouTube's
    suggested interval wins when longer) and the poller is **gated by an `_active`
    event** — the frontend
    sends `{action:"chat_active", active}` = (panel open && tab visible), so a
    closed panel / backgrounded tab spends ZERO units. Resume restarts from "now"
    (drops the paused backlog). `session.set_chat_active()` persists it so a poller
    spawned later (recovery restart) starts in the right state.

16. **WS `send` must check `readyState === OPEN`**: `ws.send()` while the socket is
    CONNECTING throws `InvalidStateError`. `useBifrost.send` is called from a
    mount-time effect (`setChatActive`), so an unguarded send threw during render
    → uncaught → **whole app blank** (no console error, just a pageerror; `#root`
    empty). The effect re-fires on `connected` change, so dropping the early send
    is safe. Don't remove the guard.
17. **Static-file caching (`SPAStatic` in main.py)**: `/assets/*` (content-hashed)
    get `immutable` long cache; everything else (index.html) gets `no-cache` so the
    browser revalidates and always picks up a rebuild's new asset hashes. A 304 on
    `/` is correct/healthy (revalidation hit), NOT a bug. Plain `StaticFiles` sets
    no Cache-Control → heuristic caching → stale index.html → blank after a rebuild.
18. **Local chat translation (`translate-server`)**: a SECOND native Metal service
    (`llama-server`, `:8180`, brew `llama.cpp`), mirroring whisper-server — same
    `host.docker.internal` + run-script + model-download pattern. Captions are NOT
    affected (Whisper translates those). `chat_translate=auto` uses DeepL then
    falls back to the local LLM on a DeepL quota/error (sets `_deepl_down`, stops
    retrying DeepL). OPT-IN by model presence: run.sh starts it only if the GGUF
    exists, so plain `auto` keeps using DeepL until `make translate-model`. If the
    server is down when local is needed, chat passes originals through (logs a
    hint). The batch prompt asks for `{"out":[...]}` but the model often returns a
    bare array — `_parse_array` accepts both; length mismatch → per-line retry.
    A 3B misses slang (草 "lol" came out "awkward"); fine for gist.

## Testing recipes (no test suite — verify against reality)

- Dev venv for tests: `.venv` with `websockets playwright httpx`
  (`python -m playwright install chromium --only-shell`). `make clean` deletes it.
- Drive a session over WS, assert events:
  `{"action":"start","url":...}` → expect `status:probing → source → status:running →
  caption*` → `ended`. Other actions: `stop`, `download`, `regenerate`,
  `delete_item {video_id}`.
- Short JA test video: `eyXmS8ozKXc` (62 s TBS news). Long replay: `cYHlzEvlosM`
  (139 min). 24/7 live JP speech: ANN news `coYw-eVU0Ks`.
- UI: Playwright headless against :7842 with
  `--autoplay-policy=no-user-gesture-required`; screenshot to /tmp and Read it.
- After frontend changes: `cd frontend && npm run build` (tsc strict gates), then
  `docker compose up -d --build` (multi-stage: node:22 builds dist → python image).
  IDE diagnostics in this setup often lag edits — trust `npm run build`.

## Current state / open ends

- `config.toml` is user-owned: as of 2026-06-12 they set `whisper_model = "medium"`,
  `chunk_seconds = 10`, `dual_transcript = false` (battery/heat on the fanless Air) —
  always read it, don't assume defaults. JA original lines won't appear in new
  sessions while dual_transcript is off.
- Chat panel is wired but the user had no API keys configured as of last session
  (`youtube_api_key` / `deepl_api_key` empty → panel shows "disabled" message).
- Live captions/chat are NOT in the library cache, but ARE persisted to
  `library/<id>/live.json` for refresh/restart recovery (gotcha #14). Sessions
  started via `t=` aren't cached (partial).
- The old `../Bifröst Design System/` folder is no longer referenced by the app
  (frontend was rewritten standalone at the user's request); the user's live design
  source is on claude.ai/design (no API — they re-export manually).
- User context: technical, Japanese-stream watcher (VTubers/news), cares about
  battery/heat on the fanless Air, prefers honest status over optimistic labels.
