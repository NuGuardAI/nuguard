# Source and license

- Upstream: https://github.com/langchain4j/langchain4j-examples, directory customer-support-agent-example
- Pinned commit: 9d441958edc7f5a91a716271b4f8748af2c854ec (copied on 2026-10-06)
- License: Apache-2.0 (the upstream LICENSE file is in this directory)
- Local changes to the application source: none
- Added: .gitignore (build output, reports, SBOM files)
- Removed from the copy: mvnw, mvnw.cmd and .mvn (the wrapper includes a binary jar; use a system Maven)

## Running it

- Needs Java 17+ and Maven.
- Uses `OPENAI_API_KEY`, or the Azure variables documented in `scripts/serve.sh`, for live-model
  runs. When neither is available, `serve.sh` starts a deterministic loopback OpenAI-compatible
  stub so fork CI still exercises the complete HTTP and NuGuard pipeline without credentials.
- Bind it to loopback only: SERVER_ADDRESS=127.0.0.1
- cancelBooking deletes the only seeded booking, held in memory. Restart the app before every run to reseed it.
- Never expose this app publicly. Local and CI use only.
- The example reads its terms-of-use file with getFile(), which fails inside a packaged jar. Run it from the classes directory (scripts/serve.sh does this) or with mvn spring-boot:run.
