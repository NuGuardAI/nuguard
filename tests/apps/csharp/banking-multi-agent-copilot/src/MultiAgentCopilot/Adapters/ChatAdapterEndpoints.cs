using System.Collections.Concurrent;
using MultiAgentCopilot.Auth;
using MultiAgentCopilot.Services;

namespace MultiAgentCopilot.Adapters
{
    /// <summary>
    /// NuGuard fixture: single-call chat for test harnesses. The upstream API needs a session to be
    /// created first and takes the prompt as a bare JSON string; POST /chat {message, session_id?}
    /// does both for the authenticated caller and returns the agents' replies as one text field.
    /// </summary>
    public static class ChatAdapterEndpoints
    {
        public record ChatRequest(string Message, string? Session_Id);

        // (tenant, user, client session id) -> app session id
        static readonly ConcurrentDictionary<(string, string, string), string> Sessions = new();

        public static void MapChatAdapter(this IEndpointRouteBuilder app)
        {
            app.MapPost("/chat", async (ChatRequest request, HttpContext http, ChatService chatService) =>
            {
                if (string.IsNullOrWhiteSpace(request.Message))
                    return Results.BadRequest(new { error = "message is required" });

                var (tenantId, userId) = DemoAuth.Caller(http.User);
                var clientSession = string.IsNullOrEmpty(request.Session_Id) ? Guid.NewGuid().ToString() : request.Session_Id;

                var key = (tenantId, userId, clientSession);
                List<Models.Chat.Message> messages;
                try
                {
                    if (!Sessions.TryGetValue(key, out var sessionId))
                    {
                        var session = await chatService.CreateNewChatSessionAsync(tenantId, userId);
                        sessionId = Sessions.GetOrAdd(key, session.SessionId);
                    }

                    messages = await chatService.GetChatCompletionAsync(tenantId, userId, sessionId, request.Message);
                }
                catch (Exception)
                {
                    return Results.Problem("chat backend failed", statusCode: StatusCodes.Status502BadGateway);
                }

                var replies = messages.Where(m => m.SenderRole != "User").ToList();
                var replyText = string.Join("\n\n", replies.Select(m => m.Text).Where(t => !string.IsNullOrWhiteSpace(t)));
                if (replies.Any(m => m.SenderRole == "Error") || string.IsNullOrWhiteSpace(replyText))
                    return Results.Problem("chat backend returned no assistant reply", statusCode: StatusCodes.Status502BadGateway);

                return Results.Ok(new
                {
                    response = replyText,
                    session_id = clientSession,
                    agents = replies.Select(m => new { sender = m.Sender, text = m.Text })
                });
            }).RequireAuthorization().WithName("ChatAdapter");
        }
    }
}
