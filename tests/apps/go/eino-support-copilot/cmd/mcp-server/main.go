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

	"github.com/guiyangyuan/eino-support-copilot/internal/config"
	"github.com/guiyangyuan/eino-support-copilot/internal/mcpserver"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatal(err)
	}

	connectCtx, cancelConnect := context.WithTimeout(context.Background(), 10*time.Second)
	pool, err := database.Open(connectCtx, cfg.DatabaseURL)
	cancelConnect()
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.Handle("/mcp", mcpserver.NewHTTPHandler(mcpserver.NewService(pool)))
	server := &http.Server{
		Addr:              cfg.MCPAddr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	serverErrors := make(chan error, 1)
	go func() {
		log.Printf("read-only MCP server listening on %s", cfg.MCPAddr)
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
