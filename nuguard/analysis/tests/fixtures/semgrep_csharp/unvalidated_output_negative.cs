public sealed class UnvalidatedOutputNegative
{
    public void Execute(ChatClient client)
    {
        var response =
            client.CompleteChat("Return one shell command");
        var command =
            OutputGuard.ValidateLlmOutput(
                response.Value.Content[0].Text);

        System.Diagnostics.Process.Start(command);
    }
}
