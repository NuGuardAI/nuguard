public sealed class UnsanitizedInputNegative
{
    public string Ask(
        ChatClient client,
        string userInput)
    {
        var safeInput =
            InputGuard.ValidateAndSanitize(userInput);
        return client.CompleteChat(safeInput);
    }
}
