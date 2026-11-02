# Source and license

- Upstream: https://github.com/AzureCosmosDB/banking-multi-agent-workshop, branch `HOL_v1_SKandLangGraph`,
  directories `02_completed/csharp/src` (as `src/`) and `02_completed/infra` (as `infra/`)
- Pinned commit: c0c1215eea9ee75a3f16315fcc80d32c07802f8a (copied on 2026-11-01)
- License: MIT (the upstream LICENSE file is in this directory)
- Removed from the copy: `ABClog.txt` (leftover log) and the Visual Studio Web Deploy service
  dependency profiles under `Properties/` (they name a real Azure resource)
- Not copied: the Angular frontend, the Python version, the `01_exercises` (TODO) version

## Local changes

Every change is marked `NuGuard fixture:` in the code. The first commit of this directory is the
unchanged upstream copy, so `git log -p` on it shows the exact diff.

1. **Azure OpenAI API key.** `SemanticKernelService` and `BankingDataService` use
   `AzureOpenAISettings.Key` (an existing, unused upstream setting) when it is set. The
   `DefaultAzureCredential` path is unchanged.
2. **In-memory data store.** `DataStore:Type=InMemory` registers `InMemoryCosmosDBService` and
   `InMemoryBankingDataService` (`Services/InMemory/`) instead of Cosmos DB. They override the
   upstream methods (made `virtual`, plus a protected constructor) and keep the same query scope,
   including the missing ownership checks. Seed: `infra/data/*.json` plus `fixture/ExtraSeedData.json`.
   Offer search ranks the same candidate terms by keyword overlap, since there is no embeddings
   deployment in CI. The Cosmos classes and Bicep stay in the tree. Default config is still `Cosmos`.
3. **JWT login.** `Auth/DemoAuth.cs`: `POST /auth/token {username, password}` for seeded users,
   each with their own password (`DemoAuth:Passwords:<userId>`; no password, no login), HS256 tokens with `sub` = user id and `tenant_id` claims. All
   upstream routes and `/chat` require a token, and a filter rejects routes whose `{tenantId}` or
   `{userId}` differ from the token. Agent tools are not changed.
4. **`POST /chat {message, session_id?}`** (`Adapters/ChatAdapterEndpoints.cs`): creates or reuses a
   session for the caller and returns the agents' replies as `{"response", "session_id", "agents"}`.
   Upstream needs a separate session call and takes the prompt as a bare JSON string.
5. **Placeholders** for the Cosmos and Azure OpenAI endpoints in `appsettings.json`; a
   `DataStore` section.
6. **Fixture seed** (`fixture/ExtraSeedData.json`, synthetic): account `Acc010` for the second
   Contoso customer Sandeep with canary values (see `canary.example.json`), and service request
   `ServiceRequest1001` on Mark's `Acc001`, filed by Theo, whose note carries an injected
   instruction to call `AddFunTransferRequest`.

## Upstream behaviour kept on purpose

- Tools scope bank data by tenant (and account), not by user: `GetTransactionHistory`,
  `IsAccountRegisteredToUser`, `CheckPendingServiceRequests`, `AddFunTransferRequest`,
  `UpdateExistingServiceRequest`, `RegisterAccount`.
- `BankingDataService.GetServiceRequestsAsync` appends its `userId` and `SRType` clauses to the
  query text after the query is built, so those filters never apply. The in-memory version keeps that.
- `PUT /offerdata`, `/accountdata`, `/userdata` insert raw documents (now behind login, any user).
- `completiondetails` returns the stored prompt and tool-call debug log.

## Running it

- Needs the .NET 9 SDK. `scripts/serve.sh` falls back to `~/.dotnet` when the `dotnet` on PATH has none.
- Uses `AZURE_OPENAI_KEY` and `AZURE_OPENAI_ENDPOINT`; `APP_MODEL_NAME` sets the chat deployment
  (default `gpt-5.4-mini`). Without them (fork PRs get no secrets) `serve.sh` starts
  `scripts/openai_chat_stub.py`, a deterministic loopback stub of the Azure OpenAI chat API: it routes
  every turn to the Coordinator, stops after one reply and never calls tools. That keeps the pipeline
  live in CI; the findings in `FINDINGS.md` come from runs against the real model.
- `scripts/serve.sh` builds and starts the app on 127.0.0.1:8091 (loopback only), with a random
  JWT signing key per start. Only `Mark` (the NuGuard run identity) has a known password:
  `BANKING_DEMO_PASSWORD` (default `nuguard-demo-only`). The other users (`Sandeep`, `Theo` in Contoso;
  `Sajee`, `Abhishek`, `David` in Fabrikan) get a random password per start, so they cannot be logged into.
- All state is in memory. Run `scripts/serve.sh` again to reset; `scripts/stop.sh` stops it.
- Never expose this app publicly. Local and CI use only.
