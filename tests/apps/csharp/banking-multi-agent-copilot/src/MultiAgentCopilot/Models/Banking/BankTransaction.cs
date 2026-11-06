namespace  MultiAgentCopilot.Models.Banking
{
    public class BankTransaction
    {
        public required string Id { get; set; }
        public required string TenantId { get; set; }
        public required string AccountId { get; set; }
        public required int DebitAmount { get; set; }
        public required int CreditAmount { get; set; }
        public required long AccountBalance { get; set; }
        public required string Details { get; set; }
        public required DateTime TransactionDateTime { get; set; }
    }
}
