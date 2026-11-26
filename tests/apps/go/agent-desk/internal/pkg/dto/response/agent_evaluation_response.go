package response

type AgentEvaluationResultResponse struct {
	CaseID      string `json:"caseId"`
	Category    string `json:"category"`
	Passed      bool   `json:"passed"`
	ReplyText   string `json:"replyText"`
	Interrupted bool   `json:"interrupted"`
	Error       string `json:"error,omitempty"`
	Finding     string `json:"finding,omitempty"`
}

type AgentEvaluationReportResponse struct {
	Total   int                             `json:"total"`
	Passed  int                             `json:"passed"`
	Results []AgentEvaluationResultResponse `json:"results"`
	CSV     string                          `json:"csv"`
}
