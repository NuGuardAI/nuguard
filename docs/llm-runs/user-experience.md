# User Experience for NuGuard OSS

## CLI only usage
0. Install NuGuard: `pip install nuguard`
- This provides the `nuguard` command in your terminal for running scans and managing configurations.
- `nuguard --help` for usage instructions and available commands. It mentions the `init` command to set up a new project and the `scan` command to run all checks. It also references the `nuguard.yaml` config file for customizing scan behavior and thresholds. It mentions the optional `--llm` flag to include AI-specific checks in the scan.
1. Initialize NuGuard in your project: `nuguard init`
- This generates a `nuguard.yaml` config file and sets up necessary infrastructure for scanning.
2. Run a scan: `nuguard scan`
- This executes all configured checks (IaC, container, code, NGA) and outputs a unified report in the terminal (default) or in the output file if specified.
- If nugard scan or nuguard sbom or nuguard analyze commands are run without a nuguard.yaml file present, it will automatically run `nuguard init` to create a default config before proceeding with the scan. This ensures a smooth onboarding experience for new users. It will record the cli arguments used to run the scan in the nuguard.yaml file for future runs.
3. Review results and fix issues: The report includes detailed findings with severity levels, descriptions, and remediation guidance for each issue detected across your IaC, container images, code, and AI workloads

## Github Action usage


## IDE usage


## Claude Code Skill usage

