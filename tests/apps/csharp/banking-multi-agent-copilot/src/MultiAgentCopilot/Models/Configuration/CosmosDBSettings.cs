using Microsoft.Azure.Cosmos;


namespace  MultiAgentCopilot.Models.Configuration
{
    public record CosmosDBSettings
    {
        public required string CosmosUri { get; init; }

        public string? CosmosKey { get; init; }

        public required string Database { get; init; }

        public bool EnableTracing { get; init; }

        public required string ChatDataContainer { get; init; }

        public required string UserDataContainer { get; init; }

        public required string AccountsContainer { get; init; }

        public required string RequestDataContainer { get; init; }

        public required string OfferDataContainer { get; init; }

        public VectorEmbeddingPolicy? VectorEmbeddingPolicy { get; init; }

        public IndexingPolicy? VectorIndexingPolicy { get; init; }

        public required string UserAssignedIdentityClientID { get; init; }
    }
}
