using Microsoft.SemanticKernel;
using System.Text.Json;

namespace MultiAgentCopilot.Logs
{
    public sealed class AutoFunctionInvocationLoggingFilter : IAutoFunctionInvocationFilter
    {
        private readonly ILogger<AutoFunctionInvocationLoggingFilter> _logger;

        public AutoFunctionInvocationLoggingFilter(ILogger<AutoFunctionInvocationLoggingFilter> logger)
        {
            _logger = logger;

        }

        public async Task OnAutoFunctionInvocationAsync(AutoFunctionInvocationContext context, Func<AutoFunctionInvocationContext, Task> next)
        {

            var functionCalls = FunctionCallContent.GetFunctionCalls(context.ChatHistory.Last()).ToList();

            if (_logger.IsEnabled(LogLevel.Trace))
            {
                functionCalls.ForEach(functionCall
                    => _logger.LogTrace(
                        "Function call requests: {PluginName}-{FunctionName}({Arguments})",
                        functionCall.PluginName,
                        functionCall.FunctionName,
                        JsonSerializer.Serialize(functionCall.Arguments)));
            }

            await next(context);
        }
    }
}
