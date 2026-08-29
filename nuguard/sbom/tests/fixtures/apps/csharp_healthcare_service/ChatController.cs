using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using OpenAI.Chat;

namespace HealthcareService.Controllers;

public record ChatRequest(string Message);
public record ChatResponse(string Answer);

[ApiController]
[Route("api/[controller]")]
public class ChatController : ControllerBase
{
    private readonly ChatClient client = new ChatClient(
        model: "gpt-4-turbo",
        apiKey: "unused");

    [HttpPost("complete")]
    [Authorize]
    public async Task<ActionResult<ChatResponse>> Complete(
        ChatRequest request)
    {
        var answer = await client.CompleteChatAsync(
            request.Message);
        return new ChatResponse(answer);
    }

    [HttpGet("{id}/triage")]
    public async Task<ActionResult<ChatResponse>> Triage(string id)
    {
        var answer = await client.CompleteChatAsync(id);
        return new ChatResponse(answer);
    }
}
