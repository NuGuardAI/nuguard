using System.Text.Json.Nodes;
using MultiAgentCopilot.Models.Banking;

namespace MultiAgentCopilot.Services.InMemory
{
    /// <summary>
    /// NuGuard fixture: BankingDataService over <see cref="InMemoryStore"/>. Each method keeps the
    /// query scope of the Cosmos DB original (partition = tenant + account), including its missing
    /// ownership checks, so the agent tools behave as upstream.
    /// </summary>
    public class InMemoryBankingDataService : BankingDataService
    {
        private readonly InMemoryStore _store;

        public InMemoryBankingDataService(InMemoryStore store, ILoggerFactory loggerFactory)
            : base(loggerFactory)
        {
            _store = store;
        }

        private static bool InPartition(JsonObject doc, string tenantId, string accountId) =>
            InMemoryStore.Str(doc, "tenantId") == tenantId && (InMemoryStore.Str(doc, "accountId") ?? string.Empty) == accountId;

        private static bool OfType(JsonObject doc, string type) => InMemoryStore.Str(doc, "type") == type;

        public override Task<BankUser> GetUserAsync(string tenantId, string userId)
        {
            lock (_store.Gate)
            {
                var doc = _store.Users.FirstOrDefault(d => InMemoryStore.Str(d, "id") == userId && InMemoryStore.Str(d, "tenantId") == tenantId);
                return Task.FromResult(doc == null ? null! : InMemoryStore.To<BankUser>(doc));
            }
        }

        public override Task<List<BankAccount>> GetUserRegisteredAccountsAsync(string tenantId, string userId)
        {
            lock (_store.Gate)
            {
                return Task.FromResult(_store.AccountData
                    .Where(d => OfType(d, nameof(BankAccount)) && InMemoryStore.Str(d, "tenantId") == tenantId && InMemoryStore.Str(d, "userId") == userId)
                    .Select(InMemoryStore.To<BankAccount>).ToList());
            }
        }

        // Like upstream, reads by account id within the tenant; userId is not checked.
        public override Task<BankAccount> GetAccountDetailsAsync(string tenantId, string userId, string accountId)
        {
            lock (_store.Gate)
            {
                var doc = _store.AccountData.FirstOrDefault(d => OfType(d, nameof(BankAccount)) && InMemoryStore.Str(d, "id") == accountId && InPartition(d, tenantId, accountId));
                return Task.FromResult(doc == null ? null! : InMemoryStore.To<BankAccount>(doc));
            }
        }

        public override Task<List<BankTransaction>> GetTransactionsAsync(string tenantId, string accountId, DateTime startDate, DateTime endDate)
        {
            lock (_store.Gate)
            {
                return Task.FromResult(_store.AccountData
                    .Where(d => OfType(d, nameof(BankTransaction)) && InPartition(d, tenantId, accountId))
                    .Select(InMemoryStore.To<BankTransaction>)
                    .Where(t => t.TransactionDateTime >= startDate && t.TransactionDateTime <= endDate)
                    .ToList());
            }
        }

        public override Task<ServiceRequest> CreateFundTransferRequestAsync(string tenantId, string accountId, string userId, string requestAnnotation, string recipientEmail, string recipientPhone, decimal debitAmount) =>
            Add(new ServiceRequest(ServiceRequestType.FundTransfer, tenantId, accountId, userId, requestAnnotation, recipientEmail, recipientPhone, debitAmount, DateTime.MinValue, null));

        public override Task<ServiceRequest> CreateTeleBankerRequestAsync(string tenantId, string accountId, string userId, string requestAnnotation, DateTime scheduledDateTime) =>
            Add(new ServiceRequest(ServiceRequestType.TeleBankerCallBack, tenantId, accountId, userId, requestAnnotation, string.Empty, string.Empty, 0, scheduledDateTime, null));

        public override Task<ServiceRequest> CreateComplaintAsync(string tenantId, string accountId, string userId, string requestAnnotation) =>
            Add(new ServiceRequest(ServiceRequestType.Complaint, tenantId, accountId, userId, requestAnnotation, string.Empty, string.Empty, 0, DateTime.MinValue, null));

        public override Task<ServiceRequest> CreateFulfilmentRequestAsync(string tenantId, string accountId, string userId, string requestAnnotation, Dictionary<string, string> fulfilmentDetails) =>
            Add(new ServiceRequest(ServiceRequestType.Fulfilment, tenantId, accountId, userId, requestAnnotation, string.Empty, string.Empty, 0, DateTime.MinValue, fulfilmentDetails));

        private Task<ServiceRequest> Add(ServiceRequest req)
        {
            lock (_store.Gate)
            {
                _store.AccountData.Add(InMemoryStore.From(req));
            }
            return Task.FromResult(req);
        }

        public override Task<List<ServiceRequest>> GetServiceRequestsAsync(string tenantId, string accountId, string? userId = null, ServiceRequestType? SRType = null)
        {
            lock (_store.Gate)
            {
                return Task.FromResult(_store.AccountData
                    .Where(d => OfType(d, nameof(ServiceRequest)) && InPartition(d, tenantId, accountId))
                    .Select(InMemoryStore.To<ServiceRequest>)
                    .Where(r => (string.IsNullOrEmpty(userId) || r.UserId == userId) && (!SRType.HasValue || r.SRType == SRType))
                    .ToList());
            }
        }

        public override Task<bool> AddServiceRequestDescriptionAsync(string tenantId, string accountId, string requestId, string annotationToAdd)
        {
            lock (_store.Gate)
            {
                var doc = _store.AccountData.FirstOrDefault(d => InMemoryStore.Str(d, "id") == requestId && InPartition(d, tenantId, accountId));
                if (doc == null)
                    return Task.FromResult(false);

                if (doc["requestAnnotations"] is not JsonArray annotations)
                    doc["requestAnnotations"] = annotations = new JsonArray();
                annotations.Add($"[{DateTime.Now.ToUniversalTime()}] : {annotationToAdd}");
                return Task.FromResult(true);
            }
        }

        // Upstream runs a vector search over the Savings terms of the tenant (the account type filter is
        // hard-coded). Without an embeddings deployment, rank the same candidates by keyword overlap.
        public override Task<List<OfferTerm>> SearchOfferTermsAsync(string tenantId, AccountType accountType, string requirementDescription)
        {
            var words = requirementDescription.ToLowerInvariant()
                .Split(new[] { ' ', ',', '.', ';', ':', '?', '!' }, StringSplitOptions.RemoveEmptyEntries)
                .Where(w => w.Length > 2).ToHashSet();

            lock (_store.Gate)
            {
                return Task.FromResult(_store.OfferData
                    .Where(d => OfType(d, "Term") && InMemoryStore.Str(d, "tenantId") == tenantId && InMemoryStore.Str(d, "accountType") == "Savings")
                    .Select(d =>
                    {
                        var copy = d.DeepClone().AsObject();
                        copy.Remove("vector");
                        return InMemoryStore.To<OfferTerm>(copy);
                    })
                    .OrderByDescending(t => words.Count(w => t.Text.Contains(w, StringComparison.OrdinalIgnoreCase)))
                    .Take(10)
                    .ToList());
            }
        }

        public override Task<Offer> GetOfferDetailsAsync(string tenantId, string offerId)
        {
            lock (_store.Gate)
            {
                var doc = _store.OfferData.FirstOrDefault(d => InMemoryStore.Str(d, "id") == offerId && InMemoryStore.Str(d, "tenantId") == tenantId);
                return Task.FromResult(doc == null ? null! : InMemoryStore.To<Offer>(doc));
            }
        }
    }
}
