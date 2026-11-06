namespace  MultiAgentCopilot.Models.Chat;

public record Message
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

    public DateTime TimeStamp { get; set; }

    public string Sender { get; set; }

    public string SenderRole { get; set; }

    public string Text { get; set; }

    public string? DebugLogId { get; set; }

    public bool? Rating { get; set; }


    public Message(string TenantId, string UserId,string SessionId, string Sender, string SenderRole, string Text, string? Id = null, string? DebugLogId = null)
    {
        this.SessionId = SessionId;
        this.TenantId = TenantId;
        this.UserId = UserId;
        this.Id = Id ?? Guid.NewGuid().ToString();
        if (DebugLogId != null)
            this.DebugLogId = DebugLogId;
        Type = nameof(Message);
        this.Sender = Sender;
        this.SenderRole = SenderRole;
        this.Text = Text;
        this.TimeStamp = DateTime.UtcNow; 
    }
}
