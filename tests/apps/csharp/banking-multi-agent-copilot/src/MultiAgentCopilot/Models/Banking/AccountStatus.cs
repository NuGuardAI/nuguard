namespace MultiAgentCopilot.MultiAgentCopilot.Models.Banking
{
    public enum AccountStatus
    {
        Active = 0,        // Account is operational and can transact
        Dormant = 1,       // Inactive for a long period, limited activity
        Locked = 2,        // Temporarily blocked (e.g., suspicious activity, wrong PIN)
        Closed = 3,        // Permanently closed by customer or bank
        Frozen = 4,        // Suspended by bank/legal order, no transactions allowed
        PendingApproval = 5, // Newly created, waiting for KYC/verification
        Suspended = 6,     // Temporarily deactivated by bank (different from Locked)
        Restricted = 7     // Limited functionality (e.g., only deposits allowed)
    }
}
