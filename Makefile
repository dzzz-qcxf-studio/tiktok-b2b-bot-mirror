.PHONY: help install seed run api ui down logs clean status

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:       ## Install backend deps + frontend deps
	pip install -e .
	cd tiktok_bot_console/ui && npm install

seed:          ## Seed SQLite with mock data (mirrors frontend mock.ts)
	python -m tiktok_bot_api.seed

api:           ## Run FastAPI backend on :8000 (auto-reload)
	uvicorn tiktok_bot_api.main:app --reload --port 8000

ui:            ## Run Vue frontend on :5173
	cd tiktok_bot_console/ui && npm run dev

status:        ## Show DB row counts
	python -m tiktok_bot_api.seed --status

logs:           ## Tail docker compose logs
	docker compose logs -f

down:          ## Stop docker stack
	docker compose down

clean:         ## Remove DB + node_modules (careful!)
	rm -f data/tiktok_bot.db
	rm -rf tiktok_bot_console/ui/node_modules
	rm -rf tiktok_bot_console/ui/dist

# Docker one-shot (replaces all of the above)
up:            ## Docker stack: build + start + seed (one command)
	docker compose up -d --build
	docker compose exec tiktok-bot python -m tiktok_bot_api.seed
	@echo ""
	@echo "✅ UI:    http://localhost:8080"
	@echo "✅ API:   http://localhost:8000/docs"