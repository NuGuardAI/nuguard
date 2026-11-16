APP_GOCACHE := $(CURDIR)/.cache/go-build

.PHONY: dev down test go-test web-test web-build integration-test eval e2e seed

dev:
	docker compose up --build

down:
	docker compose down

test: go-test web-test

go-test:
	GOCACHE=$(APP_GOCACHE) go test ./...

web-test:
	cd apps/web && npm test

web-build:
	cd apps/web && npm run build

integration-test:
	GOCACHE=$(APP_GOCACHE) go test ./...

eval:
	mkdir -p docs/evaluation
	GIT_COMMIT_SHA=$$(git rev-parse --short HEAD) GOCACHE=$(APP_GOCACHE) go run ./cmd/eval -dataset seed/eval/support-golden.jsonl -mode mock -output docs/evaluation/mock-baseline.json

e2e:
	cd apps/web && npx playwright test e2e/support-flow.spec.ts

seed:
	curl -sS -F "file=@seed/knowledge/subscription-activation.md;type=text/markdown" http://localhost:3000/api/v1/knowledge/documents
