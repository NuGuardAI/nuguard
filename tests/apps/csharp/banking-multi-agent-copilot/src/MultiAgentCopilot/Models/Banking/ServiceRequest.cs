using System.Text.Json.Serialization;

namespace MultiAgentCopilot.Models.Banking
{
    public class ServiceRequest
    {
        public string Id { get; set; }
        public string TenantId { get; set; }
        public string UserId { get; set; }
        public string Type { get; set; }
        public DateTime RequestedOn { get; set; }
        public DateTime ScheduledDateTime { get; set; }
        public string AccountId { get; set; }
        [JsonConverter(typeof(JsonStringEnumConverter))]
        public ServiceRequestType SRType { get; set; }
        public string? RecipientEmail { get; set; }
        public string? RecipientPhone { get; set; }
        public decimal? DebitAmount { get; set; }
        public bool IsComplete { get; set; }
        public List<string> RequestAnnotations { get; set; }
        public Dictionary<string, string> FulfilmentDetails { get; set; }

        public ServiceRequest(ServiceRequestType serviceRequestType, string tenantId, string accountId, string userId, string requestAnnotation, string recipientEmail, string recipientPhone, decimal debitAmount, DateTime scheduledDateTime, Dictionary<string, string>? fulfilmentDetails)
        {
            Id = Guid.NewGuid().ToString();
            TenantId = tenantId;
            Type = nameof(ServiceRequest);
            SRType = serviceRequestType;
            RequestedOn = DateTime.Now;
            AccountId = accountId;
            UserId = userId;
            RequestAnnotations = new List<string> { requestAnnotation };
            RecipientEmail = recipientEmail;
            RecipientPhone = recipientPhone;
            DebitAmount = debitAmount;
            if (scheduledDateTime != DateTime.MinValue)
                ScheduledDateTime = scheduledDateTime;
            IsComplete = false;
            FulfilmentDetails = fulfilmentDetails ?? new Dictionary<string, string>();
        }

        [JsonConstructor]
        public ServiceRequest(
        string id,
        string tenantId,
        string userId,
        string type,
        DateTime requestedOn,
        DateTime scheduledDateTime,
        string accountId,
        ServiceRequestType srType,
        string? recipientEmail,
        string? recipientPhone,
        decimal? debitAmount,
        bool isComplete,
        List<string> requestAnnotations,
        Dictionary<string, string> fulfilmentDetails)
        {
            Id = id;
            TenantId = tenantId;
            UserId = userId;
            Type = type;
            RequestedOn = requestedOn;
            ScheduledDateTime = scheduledDateTime;
            AccountId = accountId;
            SRType = srType;
            RecipientEmail = recipientEmail;
            RecipientPhone = recipientPhone;
            DebitAmount = debitAmount;
            IsComplete = isComplete;
            RequestAnnotations = requestAnnotations ?? new List<string>();
            FulfilmentDetails = fulfilmentDetails ?? new Dictionary<string, string>();
        }
    }
}



