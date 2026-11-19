using Microsoft.SemanticKernel;
using System.ComponentModel;
using  MultiAgentCopilot.Models.Banking;
using MultiAgentCopilot.Services;

namespace MultiAgentCopilot.Plugins
{

    public class BasePlugin
    {
        protected readonly ILogger<BasePlugin> _logger;
        protected readonly BankingDataService _bankService;
        protected readonly string _userId;
        protected readonly string _tenantId;

        public BasePlugin(ILogger<BasePlugin> logger, BankingDataService bankService, string tenantId, string userId)
        {
            _logger = logger;
            _tenantId = tenantId;
            _userId = userId;
            _bankService = bankService;
        }


        [KernelFunction("GetLoggedInUser")]
        [Description("Get the current logged-in BankUser")]
        public async Task<BankUser> GetLoggedInUser()
        {
            _logger.LogTrace($"Get Logged In User for Tenant:{_tenantId}  User:{_userId}");
            return await _bankService.GetUserAsync(_tenantId, _userId);

        }


        [KernelFunction("GetCurrentDateTime")]
        [Description("Get the current date time in UTC")]
        public DateTime GetCurrentDateTime()
        {
            _logger.LogTrace($"Get Datetime: {System.DateTime.Now.ToUniversalTime()}");
            return System.DateTime.Now.ToUniversalTime();
        }

        [KernelFunction("GetUserRegisteredAccounts")]
        [Description("Get user registered accounts")]
        public async Task<List<BankAccount>> GetUserRegisteredAccounts()
        {
            _logger.LogTrace($"Fetching accounts for Tenant: {_tenantId} User ID: {_userId}");
            return await _bankService.GetUserRegisteredAccountsAsync(_tenantId, _userId);
        }

        /*
     [KernelFunction]
     [Description("Analyzes the feedback text to determine the sentiment as \"Happy\" or \"Sad,\" and the satisfaction status, represented as a boolean (true or false).")]
     public static (string Sentiment, bool IsSatisfied) AnalyzeFeedback(string userId, string feedbackText, string chatHistory)
     {
         _logger.LogTrace($"Analyzing feedback for User: {userId}");
         _logger.LogTrace($"Feedback Text: {feedbackText}");
         _logger.LogTrace($"Chat History: {chatHistory}");

         // Dummy logic to analyze feedback
         string sentiment = feedbackText.Contains("great") || feedbackText.Contains("good") ? "Happy" : "Sad";
         bool isSatisfied = feedbackText.Contains("great") || feedbackText.Contains("good");

         _logger.LogTrace($"Sentiment Analysis Complete: Sentiment = {sentiment}, Satisfied = {isSatisfied}");
         return (sentiment, isSatisfied);
     }
     */

        /*
       [KernelFunction]
       [Description("Summarizes the JSON into a natural language description.")]
       public static string ConvertJSONToNaturalLanguage(
           [Description("")]
               string requestJSON
       )
       {
           _logger.LogTrace($"Using LLM to convert request details to natural language: {requestJSON}");
           // Simulated natural language generation
           return $"Please {requestJSON} for my account."; // Dummy generated description
       }
       */
    }
}
 