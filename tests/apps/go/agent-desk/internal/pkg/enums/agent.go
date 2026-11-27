package enums

type AgentRunResolutionStatus string

const (
	AgentRunResolutionStatusUnknown    AgentRunResolutionStatus = "unknown"
	AgentRunResolutionStatusResolved   AgentRunResolutionStatus = "resolved"
	AgentRunResolutionStatusUnresolved AgentRunResolutionStatus = "unresolved"
)

var AgentRunResolutionStatusValues = []AgentRunResolutionStatus{
	AgentRunResolutionStatusUnknown,
	AgentRunResolutionStatusResolved,
	AgentRunResolutionStatusUnresolved,
}

type AgentRunEvidenceStatus string

const (
	AgentRunEvidenceStatusUnknown     AgentRunEvidenceStatus = "unknown"
	AgentRunEvidenceStatusSupported   AgentRunEvidenceStatus = "supported"
	AgentRunEvidenceStatusUnsupported AgentRunEvidenceStatus = "unsupported"
)

var AgentRunEvidenceStatusValues = []AgentRunEvidenceStatus{
	AgentRunEvidenceStatusUnknown,
	AgentRunEvidenceStatusSupported,
	AgentRunEvidenceStatusUnsupported,
}

type ServiceStatus int

const (
	ServiceStatusIdle ServiceStatus = 0
	ServiceStatusBusy ServiceStatus = 1
)

var ServiceStatusValues = []ServiceStatus{
	ServiceStatusIdle,
	ServiceStatusBusy,
}

var serviceStatusLabelMap = map[ServiceStatus]string{
	ServiceStatusIdle: "空闲",
	ServiceStatusBusy: "忙碌",
}

func IsValidServiceStatus(status ServiceStatus) bool {
	for _, item := range ServiceStatusValues {
		if item == status {
			return true
		}
	}
	return false
}

func GetServiceStatusLabel(status ServiceStatus) string {
	return serviceStatusLabelMap[status]
}
