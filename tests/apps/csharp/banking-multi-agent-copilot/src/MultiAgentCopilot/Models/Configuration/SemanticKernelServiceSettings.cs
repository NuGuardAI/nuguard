
namespace  MultiAgentCopilot.Models.Configuration
{
    public record SemanticKernelServiceSettings
    {        
        public AzureOpenAISettings AzureOpenAISettings { get; init; }
        //public required CosmosDBSettings CosmosDBVectorStoreSettings { get; init; }
    }
}