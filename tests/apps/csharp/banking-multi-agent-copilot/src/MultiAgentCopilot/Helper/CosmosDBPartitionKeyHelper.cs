using Microsoft.Azure.Cosmos;
using PartitionKey = Microsoft.Azure.Cosmos.PartitionKey;

namespace  MultiAgentCopilot.Helper
{

    public static class PartitionManager
    {
        public static PartitionKey GetChatDataFullPK(string tenantId, string userId, string sessionId)
        {
            PartitionKey partitionKey = new PartitionKeyBuilder()
                .Add(tenantId)
                .Add(userId)
                .Add(sessionId)
                .Build();
            return partitionKey;
        }

        public static PartitionKey GetChatDataPartialPK(string tenantId, string userId)
        {
            PartitionKey partitionKey = new PartitionKeyBuilder()
                .Add(tenantId)
                .Add(userId)
                .Build();
            return partitionKey;
        }

        public static PartitionKey GetAccountsDataFullPK(string tenantId,string accountId)
        {
            PartitionKey partitionKey = new PartitionKeyBuilder()
                .Add(tenantId)
                .Add(accountId)
                .Build();
            return partitionKey;
        }

        public static PartitionKey GetAccountsPartialPK(string tenantId)
        {
            PartitionKey partitionKey = new PartitionKeyBuilder()
                .Add(tenantId)
                .Build();
            return partitionKey;
        }

        public static PartitionKey GetUserDataFullPK(string tenantId)
        {
            PartitionKey partitionKey = new PartitionKeyBuilder()
                .Add(tenantId)
                .Build();
            return partitionKey;
        }
    }

}
