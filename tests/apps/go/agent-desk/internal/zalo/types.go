package zalo

// WebhookEvent represents an incoming webhook event from Zalo Official Account.
type WebhookEvent struct {
	EventName string         `json:"event_name"`
	AppID     string         `json:"app_id"`
	Sender    UserRef        `json:"sender"`
	Recipient UserRef        `json:"recipient"`
	Message   *EventMessage  `json:"message,omitempty"`
	Info      map[string]any `json:"info,omitempty"`
	Timestamp string         `json:"timestamp"`
}

type UserRef struct {
	ID string `json:"id"`
}

type EventMessage struct {
	MsgID       string            `json:"msg_id"`
	Text        string            `json:"text,omitempty"`
	Attachments []EventAttachment `json:"attachments,omitempty"`
}

type EventAttachment struct {
	Type    string `json:"type"`
	Payload any    `json:"payload"`
}

// SendMessageRequest represents payload for Zalo OA Customer Support message API (/v3.0/oa/message/cs).
type SendMessageRequest struct {
	Recipient UserRef     `json:"recipient"`
	Message   SendContent `json:"message"`
}

type SendContent struct {
	Text string `json:"text"`
}

// APIResponse represents standard Zalo OpenAPI response.
type APIResponse struct {
	Error   int    `json:"error"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// UserProfileResponse represents Zalo OA get profile API response.
type UserProfileResponse struct {
	Error   int         `json:"error"`
	Message string      `json:"message"`
	Data    UserProfile `json:"data"`
}

type UserProfile struct {
	UserID      string `json:"user_id"`
	DisplayName string `json:"display_name"`
	UserGender  string `json:"user_gender"`
	Avatar      string `json:"avatar"`
	IsSensitive bool   `json:"is_sensitive"`
}
