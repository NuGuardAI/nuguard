using System.Text.Json;
using System.Text.Json.Nodes;
using MultiAgentCopilot.Models.Banking;
using MultiAgentCopilot.Models.Chat;
using MultiAgentCopilot.Models.Debug;

namespace MultiAgentCopilot.Services.InMemory
{
    /// <summary>
    /// NuGuard fixture: CosmosDBService over <see cref="InMemoryStore"/>. Chat sessions, messages and
    /// debug logs are keyed like the Cosmos hierarchical partition key (tenant, user, session).
    /// </summary>
    public class InMemoryCosmosDBService : CosmosDBService
    {
        public InMemoryStore Store { get; }

        public InMemoryCosmosDBService(InMemoryStore store, ILogger<CosmosDBService> logger)
            : base(logger)
        {
            Store = store;
        }

        private static bool Owned(string tenantId, string userId, string t, string u) => t == tenantId && u == userId;

        public override Task<List<Session>> GetUserSessionsAsync(string tenantId, string userId)
        {
            lock (Store.Gate)
                return Task.FromResult(Store.Sessions.Where(s => Owned(tenantId, userId, s.TenantId, s.UserId)).ToList());
        }

        public override Task<Session> GetSessionAsync(string tenantId, string userId, string sessionId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.Sessions.FirstOrDefault(s => s.SessionId == sessionId && Owned(tenantId, userId, s.TenantId, s.UserId))
                    ?? throw new KeyNotFoundException($"Session {sessionId} not found."));
            }
        }

        public override Task<List<Message>> GetSessionMessagesAsync(string tenantId, string userId, string sessionId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.Messages
                    .Where(m => m.SessionId == sessionId && Owned(tenantId, userId, m.TenantId, m.UserId))
                    .OrderBy(m => m.TimeStamp).ToList());
            }
        }

        public override Task<Session> InsertSessionAsync(Session session)
        {
            lock (Store.Gate)
                Store.Sessions.Add(session);
            return Task.FromResult(session);
        }

        public override Task<Message> InsertMessageAsync(Message message)
        {
            lock (Store.Gate)
                Store.Messages.Add(message);
            return Task.FromResult(message);
        }

        public override Task<Message> UpdateMessageAsync(Message message)
        {
            lock (Store.Gate)
            {
                Store.Messages.RemoveAll(m => m.Id == message.Id && m.SessionId == message.SessionId);
                Store.Messages.Add(message);
            }
            return Task.FromResult(message);
        }

        public override Task<Message> UpdateMessageRatingAsync(string tenantId, string userId, string sessionId, string messageId, bool? rating)
        {
            lock (Store.Gate)
            {
                var message = Store.Messages.FirstOrDefault(m => m.Id == messageId && m.SessionId == sessionId && Owned(tenantId, userId, m.TenantId, m.UserId))
                    ?? throw new KeyNotFoundException($"Message {messageId} not found.");
                message.Rating = rating;
                return Task.FromResult(message);
            }
        }

        public override Task<Session> UpdateSessionAsync(Session session)
        {
            lock (Store.Gate)
            {
                Store.Sessions.RemoveAll(s => s.SessionId == session.SessionId);
                Store.Sessions.Add(session);
            }
            return Task.FromResult(session);
        }

        public override async Task<Session> UpdateSessionNameAsync(string tenantId, string userId, string sessionId, string name)
        {
            var session = await GetSessionAsync(tenantId, userId, sessionId);
            lock (Store.Gate)
                session.Name = name;
            return session;
        }

        public override Task UpsertSessionBatchAsync(List<Message> messages, List<DebugLog> debugLogs, Session session)
        {
            if (messages.Select(m => m.SessionId).Distinct().Count() > 1 || session.SessionId != messages.Select(m => m.SessionId).FirstOrDefault())
                throw new ArgumentException("All items must have the same partition key.");

            lock (Store.Gate)
            {
                foreach (var message in messages)
                {
                    Store.Messages.RemoveAll(m => m.Id == message.Id);
                    Store.Messages.Add(message);
                }
                foreach (var log in debugLogs)
                {
                    Store.DebugLogs.RemoveAll(l => l.Id == log.Id);
                    Store.DebugLogs.Add(log);
                }
                Store.Sessions.RemoveAll(s => s.SessionId == session.SessionId);
                Store.Sessions.Add(session);
            }
            return Task.CompletedTask;
        }

        public override Task DeleteSessionAndMessagesAsync(string tenantId, string userId, string sessionId)
        {
            lock (Store.Gate)
            {
                Store.Messages.RemoveAll(m => m.SessionId == sessionId && Owned(tenantId, userId, m.TenantId, m.UserId));
                Store.DebugLogs.RemoveAll(l => l.SessionId == sessionId && Owned(tenantId, userId, l.TenantId, l.UserId));
                Store.Sessions.RemoveAll(s => s.SessionId == sessionId && Owned(tenantId, userId, s.TenantId, s.UserId));
            }
            return Task.CompletedTask;
        }

        public override Task<DebugLog> GetChatCompletionDebugLogAsync(string tenantId, string userId, string sessionId, string debugLogId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.DebugLogs.FirstOrDefault(l => l.Id == debugLogId && l.SessionId == sessionId && Owned(tenantId, userId, l.TenantId, l.UserId))
                    ?? throw new KeyNotFoundException($"Debug log {debugLogId} not found."));
            }
        }

        // Same contract as upstream: any caller can insert a raw document; duplicates are ignored.
        public override Task<bool> AddDocument(string containerName, JsonElement document)
        {
            var doc = JsonNode.Parse(document.GetRawText()) as JsonObject;
            if (doc == null || !doc.ContainsKey("id"))
                return Task.FromResult(false);

            var target = containerName switch
            {
                "OfferData" => Store.OfferData,
                "AccountData" => Store.AccountData,
                "UserData" => Store.Users,
                _ => null
            };
            if (target == null)
                return Task.FromResult(false);

            lock (Store.Gate)
            {
                var id = InMemoryStore.Str(doc, "id");
                if (target.Any(d => InMemoryStore.Str(d, "id") == id))
                    return Task.FromResult(false);
                target.Add(doc);
            }
            return Task.FromResult(true);
        }

        public override Task<List<ServiceRequest>> GetServiceRequestsAsync(string tenantId)
            => GetServiceRequestsAsync(tenantId, null);

        public Task<List<ServiceRequest>> GetServiceRequestsAsync(string tenantId, string? userId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.AccountData
                    .Where(d => InMemoryStore.Str(d, "type") == nameof(ServiceRequest) && InMemoryStore.Str(d, "tenantId") == tenantId
                        && (userId == null || InMemoryStore.Str(d, "userId") == userId))
                    .Select(InMemoryStore.To<ServiceRequest>)
                    .OrderByDescending(r => r.RequestedOn).Take(10).ToList());
            }
        }

        public override Task<List<BankAccount>> GetUserRegisteredAccountsAsync(string tenantId, string userId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.AccountData
                    .Where(d => InMemoryStore.Str(d, "type") == nameof(BankAccount) && InMemoryStore.Str(d, "tenantId") == tenantId && InMemoryStore.Str(d, "userId") == userId)
                    .Select(InMemoryStore.To<BankAccount>).ToList());
            }
        }

        // Like upstream, scoped by tenant and account only.
        public override Task<List<BankTransaction>> GetAccountTransactionsAsync(string tenantId, string userId, string accountId)
        {
            lock (Store.Gate)
            {
                return Task.FromResult(Store.AccountData
                    .Where(d => InMemoryStore.Str(d, "type") == nameof(BankTransaction) && InMemoryStore.Str(d, "tenantId") == tenantId && InMemoryStore.Str(d, "accountId") == accountId)
                    .Select(InMemoryStore.To<BankTransaction>)
                    .OrderBy(t => t.TransactionDateTime).Take(10).ToList());
            }
        }
    }
}
