namespace MultiAgentCopilot.Models.Banking
{
    public class BankUser
    {
        public required string Id { get; set; } = string.Empty;
        public required string TenantId { get; set; } = string.Empty;
        public required string Name { get; set; } = string.Empty;
        public required string Email { get; set; }
        public required string PhoneNumber { get; set; }
        public required Dictionary<string,object> Attributes { get; set; }
    }
}
