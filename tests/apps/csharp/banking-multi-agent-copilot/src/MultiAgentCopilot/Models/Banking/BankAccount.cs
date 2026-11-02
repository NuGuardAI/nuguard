
using MultiAgentCopilot.MultiAgentCopilot.Models.Banking;
using System.Text.Json.Serialization;

namespace MultiAgentCopilot.Models.Banking
{
    public class BankAccount
    {
        public required string Id { get; set; } = string.Empty;
        public required string TenantId { get; set; } = string.Empty;
        public required string Name { get; set; } = string.Empty;
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public required AccountType AccountType { get; set; }
        public long CardNumber { get; set; }
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public AccountStatus AccountStatus { get; set; }
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public CardType CardType { get; set; }        
        public long Balance { get; set; }
        public long Limit { get; set; }
        public int InterestRate { get; set; }
        public required string ShortDescription { get; set; }
    }
}
