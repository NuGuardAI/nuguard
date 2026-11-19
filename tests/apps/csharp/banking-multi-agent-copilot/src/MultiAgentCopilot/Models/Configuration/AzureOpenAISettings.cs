
namespace MultiAgentCopilot.Models.Configuration
{
    public record AzureOpenAISettings
    {
        public required string Endpoint { get; init; }

        public  string? Key { get; init; }           

        public required string CompletionsDeployment { get; init; }

        public required string EmbeddingsDeployment { get; init; }
        
        public required string UserAssignedIdentityClientID { get; init; }
    }
}
