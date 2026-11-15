using MultiAgentCopilot.Services;

namespace MultiAgentCopilot.Plugins
{
    public class CoordinatorPlugin: BasePlugin
    {

        public CoordinatorPlugin(ILogger<BasePlugin> logger, BankingDataService bankService, string tenantId, string userId )
           : base(logger, bankService, tenantId, userId)
        {
        }             

    }
}
