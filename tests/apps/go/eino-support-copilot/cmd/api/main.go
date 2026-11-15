package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/guiyangyuan/eino-support-copilot/internal/agent"
	"github.com/guiyangyuan/eino-support-copilot/internal/config"
	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
	"github.com/guiyangyuan/eino-support-copilot/internal/knowledge"
	"github.com/guiyangyuan/eino-support-copilot/internal/mcpserver"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/guiyangyuan/eino-support-copilot/internal/retrieval"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/guiyangyuan/eino-support-copilot/internal/ticket"
	"github.com/guiyangyuan/eino-support-copilot/internal/transport/httpapi"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatal(err)
	}
	startupCtx, cancelStartup := context.WithTimeout(context.Background(), 30*time.Second)
	pool, err := database.Open(startupCtx, cfg.DatabaseURL)
	if err != nil {
		cancelStartup()
		log.Fatal(err)
	}
	// NuGuard fixture: NUGUARD_RESET_DB=true drops and recreates the public schema so every start
	// reseeds from scratch (refunds, conversations and runs from earlier scans are discarded).
	if os.Getenv("NUGUARD_RESET_DB") == "true" {
		if _, err := pool.Exec(startupCtx, `drop schema public cascade; create schema public`); err != nil {
			cancelStartup()
			pool.Close()
			log.Fatal(err)
		}
	}
	if err := database.Migrate(startupCtx, pool, "migrations"); err != nil {
		cancelStartup()
		pool.Close()
		log.Fatal(err)
	}
	seed, err := os.ReadFile("seed/business.sql")
	if err != nil {
		cancelStartup()
		pool.Close()
		log.Fatal(err)
	}
	if _, err := pool.Exec(startupCtx, string(seed)); err != nil {
		cancelStartup()
		pool.Close()
		log.Fatal(err)
	}
	// NuGuard fixture: optional extra seed (second customer, canary data, injected incident).
	if fixtureSeedPath := os.Getenv("NUGUARD_FIXTURE_SEED"); fixtureSeedPath != "" {
		fixtureSeed, err := os.ReadFile(fixtureSeedPath)
		if err != nil {
			cancelStartup()
			pool.Close()
			log.Fatal(err)
		}
		if _, err := pool.Exec(startupCtx, string(fixtureSeed)); err != nil {
			cancelStartup()
			pool.Close()
			log.Fatal(err)
		}
	}
	cancelStartup()
	defer pool.Close()

	embedder := retrieval.NewMockEmbedder(1536)
	searcher := retrieval.NewSearcher(retrieval.NewStore(pool), embedder, 0.02)
	guard := agent.NewGuard(.70)
	var engine agent.Engine = agent.NewMockEngine(searcher, mcpserver.NewService(pool), guard)
	var closeEngine func() error
	if cfg.AppMode == config.ModeOpenAICompatible {
		model, modelErr := agent.NewOpenAICompatibleModel(context.Background(), agent.OpenAIModelConfig{
			BaseURL: cfg.ModelBaseURL,
			APIKey:  cfg.ModelAPIKey,
			Model:   cfg.ModelName,
			Timeout: 30 * time.Second,
		})
		if modelErr != nil {
			log.Fatal(modelErr)
		}
		toolset, toolErr := agent.LoadMCPTools(context.Background(), cfg.MCPURL)
		if toolErr != nil {
			log.Fatal(toolErr)
		}
		einoEngine, engineErr := agent.NewEinoEngine(context.Background(), agent.EinoDependencies{
			Model: model,
			Tools: toolset.Tools,
			Guard: guard,
			Close: toolset.Close,
		})
		if engineErr != nil {
			_ = toolset.Close()
			log.Fatal(engineErr)
		}
		engine = einoEngine
		closeEngine = einoEngine.Close
	}
	if closeEngine != nil {
		defer func() {
			if err := closeEngine(); err != nil {
				log.Printf("close agent runtime: %v", err)
			}
		}()
	}

	knowledgeService := knowledge.NewService(
		knowledge.NewPostgresRepository(pool),
		retrieval.NewChunker(600, .15),
		embedder,
	)
	conversationService := conversation.NewServiceWithMode(
		conversation.NewRepository(pool),
		runtrace.NewRepository(pool),
		engine,
		cfg.AppMode,
	)
	ticketService := ticket.NewService(pool)
	runService := runtrace.NewService(pool, func(ctx context.Context, input runtrace.ReplayInput, sink runtrace.Sink) (domain.Draft, error) {
		return engine.Run(ctx, agent.Input{
			ConversationID: input.ConversationID,
			MessageID:      input.MessageID,
			RunID:          input.RunID,
			CustomerID:     input.CustomerID,
			Text:           input.Text,
		}, sink)
	}, cfg.AppMode)
	evalDataset, err := evaluation.LoadJSONL("seed/eval/support-golden.jsonl")
	if err != nil {
		log.Fatal(err)
	}
	commitSHA := os.Getenv("GIT_COMMIT_SHA")
	if commitSHA == "" {
		commitSHA = "local"
	}
	evaluationService := evaluation.NewManager(pool, evaluation.NewRunner(
		evaluation.ExpectedExecutor{}, evaluation.RunConfig{CommitSHA: commitSHA, Mode: cfg.AppMode},
	), evalDataset)

	// NuGuard fixture: JWT bearer auth replaces the upstream X-Demo-User header (see httpapi/auth.go).
	// Only ada (the identity NuGuard tests as) has a known default password. bob and lin get theirs
	// from the environment (scripts/serve.sh generates random ones); unset means no login.
	adaPassword := os.Getenv("DEMO_USER_PASSWORD")
	if adaPassword == "" {
		adaPassword = "nuguard-demo-only"
	}
	jwtAuth, err := httpapi.NewJWTAuth(pool, os.Getenv("JWT_SIGNING_KEY"))
	if err != nil {
		log.Fatal(err)
	}
	if err := httpapi.SeedDemoPasswords(context.Background(), pool, map[string]string{
		"ada": adaPassword,
		"bob": os.Getenv("DEMO_PASSWORD_BOB"),
		"lin": os.Getenv("DEMO_PASSWORD_LIN"),
	}); err != nil {
		log.Fatal(err)
	}

	server := &http.Server{
		Addr: cfg.HTTPAddr,
		Handler: httpapi.NewServer(httpapi.Dependencies{
			Mode:          cfg.AppMode,
			Knowledge:     knowledgeService,
			Conversations: conversationService,
			Tickets:       ticketService,
			Runs:          runService,
			Evaluations:   evaluationService,
			Identity:      jwtAuth, // NuGuard fixture: was httpapi.NewPostgresIdentityResolver(pool)
			Auth:          jwtAuth,
		}),
		ReadHeaderTimeout: 5 * time.Second,
	}

	serverErrors := make(chan error, 1)
	go func() {
		log.Printf("support copilot API listening on %s in %s mode", cfg.HTTPAddr, cfg.AppMode)
		serverErrors <- server.ListenAndServe()
	}()

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(signals)

	select {
	case err := <-serverErrors:
		if !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	case <-signals:
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			log.Printf("graceful shutdown failed: %v", err)
		}
	}
}
