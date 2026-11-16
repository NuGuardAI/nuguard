
namespace MultiAgentCopilot.Models.ChatInfoFormats
{
    internal class TerminationInfo
    {
        public bool ShouldContinue { get; set; }
        public string Reason { get; set; } = string.Empty;
    }
}
