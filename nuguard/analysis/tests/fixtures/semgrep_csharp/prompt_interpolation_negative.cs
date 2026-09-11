public sealed class PromptInterpolationNegative
{
    public string Ask(
        ChatClient client,
        string userInput)
    {
        var safeInput =
            InputGuard.SanitizeInput(userInput);
        var prompt = $"User request: {safeInput}";
        return client.CompleteChat(prompt);
    }
}
