import nuguard.common.bootstrap as _bs
_bs.BOOTSTRAP_TIMEOUT = 120.0

from nuguard.cli.main import app

app([
    "redteam",
    "--config", "tests/apps/pinnacle-bank-app/nuguard-sbom-azure.yaml",
    "--format", "markdown",
    "--output", "tests/apps/pinnacle-bank-app/reports/3333-redteam.md",
    "--profile", "full",
])
