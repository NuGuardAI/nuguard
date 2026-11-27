package lark

import "strings"

// LarkChannelDomainLark is the international Lark Suite endpoint.
const LarkChannelDomainLark = "lark"

// LarkChannelDomainFeishu is the mainland-China Feishu endpoint.
const LarkChannelDomainFeishu = "feishu"

// Event im.message.receive_v1 - a message was sent to the app or a chat the
// app is in.
const EventTypeImMessageReceiveV1 = "im.message.receive_v1"

// BaseURLForDomain maps a channel-config domain value to its API base URL.
// Empty or unknown values fall back to international Lark.
func BaseURLForDomain(domain string) string {
	switch strings.ToLower(domain) {
	case LarkChannelDomainFeishu:
		return "https://open.feishu.cn"
	default:
		return "https://open.larksuite.com"
	}
}

// EventV2 is the envelope of a Lark v2.0 event callback. Header.Token carries
// the app's Verification Token, which is the only credential a webhook payload
// provides for proving it was sent by Lark.
type EventV2 struct {
	Schema string        `json:"schema"`
	Header EventV2Header `json:"header"`
	Event  EventV2Body   `json:"event"`
}

type EventV2Header struct {
	EventID    string `json:"event_id"`
	EventType  string `json:"event_type"`
	CreateTime string `json:"create_time"`
	Token      string `json:"token"`
	AppID      string `json:"app_id"`
	TenantKey  string `json:"tenant_key"`
}

type EventV2Body struct {
	Sender  EventV2Sender  `json:"sender"`
	Message EventV2Message `json:"message"`
}

type EventV2Sender struct {
	SenderID   EventV2SenderID `json:"sender_id"`
	SenderType string          `json:"sender_type"`
	TenantKey  string          `json:"tenant_key"`
}

type EventV2SenderID struct {
	OpenID  string `json:"open_id"`
	UnionID string `json:"union_id"`
	UserID  string `json:"user_id"`
}

type EventV2Message struct {
	MessageID   string `json:"message_id"`
	RootID      string `json:"root_id"`
	ParentID    string `json:"parent_id"`
	ChatID      string `json:"chat_id"`
	ChatType    string `json:"chat_type"`
	MessageType string `json:"message_type"`
	// Content is a JSON-encoded string whose shape depends on MessageType;
	// for text messages it is {"text":"..."}.
	Content  string           `json:"content"`
	Mentions []EventV2Mention `json:"mentions"`
}

type EventV2Mention struct {
	Key  string          `json:"key"`
	ID   EventV2SenderID `json:"id"`
	Name string          `json:"name"`
}

// URLVerification is the handshake Lark sends when an event request URL is
// first configured; the endpoint must echo the challenge back.
type URLVerification struct {
	Challenge string `json:"challenge"`
	Token     string `json:"token"`
	Type      string `json:"type"`
}

// TenantTokenResponse is the envelope of
// /open-apis/auth/v3/tenant_access_token/internal. Lark answers HTTP 200 and
// reports application-level failures through the code field.
type TenantTokenResponse struct {
	Code              int    `json:"code"`
	Msg               string `json:"msg"`
	TenantAccessToken string `json:"tenant_access_token"`
	Expire            int    `json:"expire"`
}

// SendMessageRequest is the im/v1/messages payload. Content must be a
// JSON-encoded string whose shape matches MsgType (for text: {"text":"..."}).
type SendMessageRequest struct {
	ReceiveID string `json:"receive_id"`
	MsgType   string `json:"msg_type"`
	Content   string `json:"content"`
}

// SendMessageResponse is the im/v1/messages envelope.
type SendMessageResponse struct {
	Code int    `json:"code"`
	Msg  string `json:"msg"`
	Data struct {
		MessageID string `json:"message_id"`
	} `json:"data"`
}
