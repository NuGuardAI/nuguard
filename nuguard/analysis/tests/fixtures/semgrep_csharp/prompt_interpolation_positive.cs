public sealed class PromptInterpolationPositive
{
    public string Ask(
        ChatClient client,
        string userInput)
    {
        var prompt = $"User request: {userInput}";
        return client.CompleteChat(prompt);
    }
}
