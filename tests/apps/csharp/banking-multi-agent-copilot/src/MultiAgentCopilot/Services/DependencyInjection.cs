using  MultiAgentCopilot.Models.Configuration;
using MultiAgentCopilot.Services;
using MultiAgentCopilot.Services.InMemory;

namespace MultiAgentCopilot
{
    /// <summary>
    /// General purpose dependency injection extensions.
    /// </summary>
    public static partial class DependencyInjection
    {
        
        public static void AddSemanticKernelService(this IHostApplicationBuilder builder)
        {
            builder.Services.AddOptions<SemanticKernelServiceSettings>()
                .Bind(builder.Configuration.GetSection("SemanticKernelServiceSettings"));
            builder.Services.AddSingleton<SemanticKernelService>();
        }

        public static void AddCosmosDBService(this IHostApplicationBuilder builder)
        {
            builder.Services.AddOptions<CosmosDBSettings>()
                .Bind(builder.Configuration.GetSection("CosmosDBSettings"));

            // NuGuard fixture: DataStore:Type=InMemory swaps Cosmos DB for a seeded in-process store.
            if (string.Equals(builder.Configuration["DataStore:Type"], "InMemory", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine("Adding CosmosDBService: in-memory store");
                builder.Services.AddSingleton<InMemoryStore>();
                builder.Services.AddSingleton<CosmosDBService, InMemoryCosmosDBService>();
                return;
            }

            Console.WriteLine("Adding CosmosDBService:" + builder.Configuration["CosmosDBSettings:CosmosUri"]);
            builder.Services.AddSingleton<CosmosDBService>();
        }

        public static void AddChatService(this IHostApplicationBuilder builder)
        {
            builder.Services.AddOptions<CosmosDBSettings>()
                .Bind(builder.Configuration.GetSection("CosmosDBSettings"));
            builder.Services.AddSingleton<ChatService>();
        }

    }
}
