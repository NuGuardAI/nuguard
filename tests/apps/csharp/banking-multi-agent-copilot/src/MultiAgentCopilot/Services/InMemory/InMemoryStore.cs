using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using MultiAgentCopilot.Models.Chat;
using MultiAgentCopilot.Models.Debug;

namespace MultiAgentCopilot.Services.InMemory
{
    /// <summary>
    /// NuGuard fixture: process-local replacement for the Cosmos DB containers, seeded from
    /// infra/data/*.json at startup. Restart the app to reset all state.
    /// </summary>
    public class InMemoryStore
    {
        public static readonly JsonSerializerOptions JsonOptions = new()
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = true,
            Converters = { new JsonStringEnumConverter() }
        };

        public object Gate { get; } = new();

        // Raw documents, like the Cosmos containers: accounts, transactions and service requests share one list.
        public List<JsonObject> Users { get; } = new();
        public List<JsonObject> AccountData { get; } = new();
        public List<JsonObject> OfferData { get; } = new();

        public List<Session> Sessions { get; } = new();
        public List<Message> Messages { get; } = new();
        public List<DebugLog> DebugLogs { get; } = new();

        public InMemoryStore(IConfiguration configuration, ILogger<InMemoryStore> logger)
        {
            var seedDir = configuration["DataStore:SeedDirectory"] ?? "../../infra/data";
            Load(Path.Combine(seedDir, "UserData.json"), Users);
            Load(Path.Combine(seedDir, "AccountsData.json"), AccountData);
            Load(Path.Combine(seedDir, "OffersData.json"), OfferData);

            // Extra fixture records (second customer, canary data); optional.
            var extra = configuration["DataStore:ExtraSeedFile"];
            if (!string.IsNullOrEmpty(extra) && File.Exists(extra))
            {
                foreach (var doc in ReadArray(extra))
                {
                    var type = doc["type"]?.GetValue<string>();
                    (type == "BankUser" ? Users : type is "Offer" or "Term" ? OfferData : AccountData).Add(doc);
                }
            }

            logger.LogInformation("In-memory store seeded: {Users} users, {Accounts} account documents, {Offers} offer documents.",
                Users.Count, AccountData.Count, OfferData.Count);
        }

        private static void Load(string path, List<JsonObject> target) => target.AddRange(ReadArray(path));

        private static IEnumerable<JsonObject> ReadArray(string path)
        {
            var array = JsonNode.Parse(File.ReadAllText(path))!.AsArray();
            return array.Select(n => n!.AsObject().DeepClone().AsObject());
        }

        public static string? Str(JsonObject doc, string name) =>
            doc[name] is JsonValue v && v.TryGetValue<string>(out var s) ? s : doc[name]?.ToString();

        public static T To<T>(JsonObject doc) => doc.Deserialize<T>(JsonOptions)!;

        public static JsonObject From<T>(T item) => JsonSerializer.SerializeToNode(item, JsonOptions)!.AsObject();
    }
}
