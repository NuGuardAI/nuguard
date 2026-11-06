using Newtonsoft.Json;

namespace  MultiAgentCopilot.Models.Chat;

public record Session
{
    /// <summary>
    /// Unique identifier
    /// </summary>
    public string Id { get; set; }

    public string Type { get; set; }

    /// <summary>
    /// Partition key
    /// </summary>
    public string SessionId { get; set; }

    public string TenantId { get; set; }

    public string UserId { get; set; }

    public string Name { get; set; }

    [JsonIgnore]
    public List<Message> Messages { get; set; }

    public Session(string tenantId, string userId)
    {
        Id = Guid.NewGuid().ToString();
        TenantId = tenantId;
        UserId = userId;
        Type = nameof(Session);
        SessionId = Id;
        Name = "New Chat";
        Messages = new List<Message>();
    }

    public void AddMessage(Message message)
    {
        Messages.Add(message);
    }

    public void UpdateMessage(Message message)
    {
        var match = Messages.Single(m => m.Id == message.Id);
        var index = Messages.IndexOf(match);
        Messages[index] = message;
    }
}