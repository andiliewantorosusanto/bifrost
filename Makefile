# Bifröst — common tasks. `make help` lists targets.

COMPOSE := docker compose
WHISPER_PORT ?= 8178
TRANSLATE_PORT ?= 8180

BROWSER ?= firefox

.DEFAULT_GOAL := help
.PHONY: help run start stop restart build rebuild clean clean-all logs status models whisper cookies translate translate-model

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

run: ## Start everything: native whisper-server (Metal) + app container (foreground)
	./run.sh

start: ## Start the app container only, detached (assumes whisper-server is running)
	@touch cookies.txt
	$(COMPOSE) up -d

cookies: ## Export YouTube cookies from your browser for members-only videos (BROWSER=chrome|safari|firefox|edge|brave)
	@echo "Exporting YouTube cookies from $(BROWSER) (Chrome/Edge/Brave trigger a Keychain prompt — allow it)…"
	@rm -f cookies.txt  # yt-dlp tries to LOAD an existing jar; the empty mount placeholder isn't valid
	@yt-dlp -q --simulate --cookies-from-browser $(BROWSER) --cookies cookies.txt \
	  "https://www.youtube.com/watch?v=eyXmS8ozKXc" \
	  || { touch cookies.txt; echo "Export failed — see error above."; exit 1; }
	@chmod 600 cookies.txt
	@grep -qi "youtube" cookies.txt && echo "✓ cookies.txt written — just retry the video, no restart needed" \
	  || echo "No YouTube cookies found — are you logged in to YouTube in $(BROWSER)?"

stop: ## Stop the app container and the native whisper/translate servers
	$(COMPOSE) down
	-pkill -f "whisper-server --model" 2>/dev/null || true
	-pkill -f "llama-server --model" 2>/dev/null || true

restart: stop run ## Stop everything, then start again

build: ## Build the Docker image
	$(COMPOSE) build

rebuild: ## Rebuild the image and restart the running container
	$(COMPOSE) up -d --build

clean: ## Stop and remove containers, the built image, and Python caches
	$(COMPOSE) down --rmi local --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .venv

clean-all: clean ## clean + delete the downloaded Whisper models (~4.3 GB)
	rm -f models/ggml-*.bin models/*.part

logs: ## Follow app container logs
	$(COMPOSE) logs -f bifrost

status: ## Show container, whisper-server, translate-server, and UI health
	@$(COMPOSE) ps --format '{{.Name}}  {{.Status}}' 2>/dev/null || true
	@curl -fsS -o /dev/null http://127.0.0.1:$(WHISPER_PORT)/ 2>/dev/null && echo "whisper-server   :$(WHISPER_PORT)  up" || echo "whisper-server   :$(WHISPER_PORT)  down"
	@curl -fsS -o /dev/null http://127.0.0.1:$(TRANSLATE_PORT)/health 2>/dev/null && echo "translate-server :$(TRANSLATE_PORT)  up" || echo "translate-server :$(TRANSLATE_PORT)  down (DeepL/auto still works)"
	@curl -fsS -o /dev/null http://127.0.0.1:7842/ 2>/dev/null && echo "app UI           :7842  up" || echo "app UI           :7842  down"

models: ## Download Whisper models (large-v3 + medium)
	./whisper/download-models.sh

whisper: ## Start only the native whisper-server (foreground)
	./whisper/run-whisper-server.sh

translate-model: ## Download the local chat-translation model (Qwen2.5-3B GGUF, ~1.9GB)
	./llm/download-model.sh

translate: ## Start only the native translate-server / llama-server (foreground)
	./llm/run-llm-server.sh
