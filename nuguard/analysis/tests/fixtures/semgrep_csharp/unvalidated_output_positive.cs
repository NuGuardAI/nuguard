public sealed class UnvalidatedOutputPositive
{
    public void Execute(ChatClient client)
    {
        var response =
            client.CompleteChat("Return one shell command");
        var command =
            response.Value.Content[0].Text;

        System.Diagnostics.Process.Start(command);
    }
}
