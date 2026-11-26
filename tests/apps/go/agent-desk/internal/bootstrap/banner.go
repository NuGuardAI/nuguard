package bootstrap

import (
	"fmt"
	"io"
	"os"

	"agent-desk/internal/pkg/config"
)

func printBanner() {
	printBannerTo(os.Stdout, config.Current())
}

func printBannerTo(w io.Writer, cfg config.Config) {
	_, _ = fmt.Fprint(w, renderBanner(cfg))
}

func renderBanner(cfg config.Config) string {
	port := cfg.Server.Port
	if port <= 0 {
		port = 8080
	}
	dbType := cfg.DB.Type
	if dbType == "" {
		dbType = "unknown"
	}

	return fmt.Sprintf(`
    _    ____ _____ _   _ _____   ____  _____ ____  _  __
   / \  / ___| ____| \ | |_   _| |  _ \| ____/ ___|| |/ /
  / _ \| |  _|  _| |  \| | | |   | | | |  _| \___ \| ' /
 / ___ \ |_| | |___| |\  | | |   | |_| | |___ ___) | . \
/_/   \_\____|_____|_| \_| |_|   |____/|_____|____/|_|\_\

:: AGENT DESK ::

Port     : %d
DB       : %s
Address  : http://127.0.0.1:%d

`, port, dbType, port)
}
