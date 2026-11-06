using Microsoft.SemanticKernel;
using System.ComponentModel;
using MultiAgentCopilot.Models.Banking;
using MultiAgentCopilot.Services;

namespace MultiAgentCopilot.Plugins
{
    public class TransactionPlugin : BasePlugin
    {
        public TransactionPlugin(ILogger<BasePlugin> logger, BankingDataService bankService, string tenantId, string userId)
         : base(logger, bankService, tenantId, userId)
        {
        }

        [KernelFunction]
        [Description("Adds a new Account Transaction request")]
        public async Task<ServiceRequest> AddFunTransferRequest(
            string debitAccountId,
            decimal amount,
            string requestAnnotation,
            string? recipientPhoneNumber = null,
            string? recipientEmailId = null)
        {
            _logger.LogTrace("Adding AccountTransaction request for User ID: {UserId}, Debit Account: {DebitAccountId}", _userId, debitAccountId);

            // Ensure non-null values for recipientEmailId and recipientPhoneNumber
            string emailId = recipientEmailId ?? string.Empty;
            string phoneNumber = recipientPhoneNumber ?? string.Empty;

            return await _bankService.CreateFundTransferRequestAsync(_tenantId, debitAccountId, _userId, requestAnnotation, emailId, phoneNumber, amount);
        }

        [KernelFunction]
        [Description("Get the transactions history between 2 dates")]
        public async Task<List<BankTransaction>> GetTransactionHistory(string accountId, DateTime startDate, DateTime endDate)
        {
            _logger.LogTrace("Fetching AccountTransaction history for Account: {AccountId}, From: {StartDate} To: {EndDate}", accountId, startDate, endDate);
            return await _bankService.GetTransactionsAsync(_tenantId, accountId, startDate, endDate);
        }
    }
}
