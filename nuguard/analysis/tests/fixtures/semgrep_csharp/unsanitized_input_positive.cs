public sealed class UnsanitizedInputPositive
{
    public string Ask(
        ChatClient client,
        string userInput)
    {
        return client.CompleteChat(userInput);
    }
}
