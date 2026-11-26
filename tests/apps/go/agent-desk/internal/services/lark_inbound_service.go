package services

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/lark"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/openidentity"

	"github.com/mlogclub/simple/sqls"
)

var LarkInboundService = newLarkInboundService()

func newLarkInboundService() *larkInboundService {
	return &larkInboundService{}
}

type larkInboundService struct{}

// HandleWebhook processes a Lark event-callback delivery. The URL-verification
// handshake is answered first because Lark sends it once while the request URL
// is being configured, before any channel context exists.
func (s *larkInboundService) HandleWebhook(ctx context.Context, channelID string, rawPayload []byte) (*string, error) {
	var verification lark.URLVerification
	if err := json.Unmarshal(rawPayload, &verification); err == nil && verification.Type == "url_verification" {
		challenge := strings.TrimSpace(verification.Challenge)
		if challenge == "" {
			return nil, fmt.Errorf("url_verification payload missing challenge")
		}
		return &challenge, nil
	}

	var event lark.EventV2
	if err := json.Unmarshal(rawPayload, &event); err != nil {
		return nil, fmt.Errorf("unmarshal lark event failed: %w", err)
	}
	if event.Header.EventType == "" {
		return nil, fmt.Errorf("lark payload missing event header")
	}

	if event.Header.EventType != lark.EventTypeImMessageReceiveV1 {
		return nil, nil // Ignore non-message callbacks
	}

	var channel *models.Channel
	channelID = strings.TrimSpace(channelID)
	if channelID != "" {
		channel = ChannelService.Take("channel_id = ? AND channel_type = ? AND status = ?", channelID, enums.ChannelTypeLark, enums.StatusOk)
	}
	if channel == nil && event.Header.AppID != "" {
		channel = findEnabledLarkChannelByAppID(event.Header.AppID)
	}
	if channel == nil {
		return nil, errorsx.InvalidParam("lark channel not found or disabled")
	}

	cfg, err := ChannelService.ParseLarkChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil {
		return nil, errorsx.InvalidParam("lark channel config invalid")
	}

	// Fail closed: Lark echoes the app's verification token in every v2 event
	// header, so a payload whose token is missing or wrong was not sent by
	// Lark. A channel with no token configured cannot prove anything either,
	// and is rejected the same way.
	if cfg.VerificationToken == "" || event.Header.Token != cfg.VerificationToken {
		return nil, errorsx.UnauthorizedI18n("error.auth.invalidSignature")
	}

	ev := event.Event
	if ev.Sender.SenderType == "app" {
		return nil, nil // Ignore app/bot loops
	}

	messageID := strings.TrimSpace(ev.Message.MessageID)
	if messageID == "" {
		return nil, fmt.Errorf("lark event missing message_id")
	}

	var text string
	if ev.Message.MessageType == "text" {
		text = lark.TextFromEventContent(ev.Message.Content)
	}
	if text == "" {
		return nil, nil // Ignore non-text messages for now
	}

	// 1. Resolve customer identity
	openID := strings.TrimSpace(ev.Sender.SenderID.OpenID)
	externalUser := openidentity.ExternalUser{
		ExternalSource: enums.ExternalSourceLark,
		ExternalID:     openID,
		ExternalName:   fmt.Sprintf("Lark User %s", openID),
	}

	// 2. Create or match Conversation
	conversation, err := ConversationService.Create(externalUser, channel.ID, channel.AIAgentID)
	if err != nil {
		return nil, fmt.Errorf("create lark conversation failed: %w", err)
	}

	// 3. Send message through MessageService (message_id keys dedupe)
	chatID := strings.TrimSpace(ev.Message.ChatID)
	chatType := strings.TrimSpace(ev.Message.ChatType)
	clientMsgID := fmt.Sprintf("lark_%s", messageID)
	payloadMap := map[string]any{
		"lark_chat_id":    chatID,
		"lark_chat_type":  chatType,
		"lark_message_id": messageID,
		"lark_open_id":    openID,
	}
	payloadBytes, _ := json.Marshal(payloadMap)

	_, err = MessageService.SendCustomerMessage(
		conversation.ID,
		clientMsgID,
		enums.IMMessageTypeText,
		text,
		string(payloadBytes),
		externalUser,
	)
	if err != nil {
		return nil, fmt.Errorf("send customer message failed: %w", err)
	}

	return nil, nil
}

// findEnabledLarkChannelByAppID resolves the channel for an event that did not
// arrive on a channel-scoped webhook path, by matching the app id the event
// was issued for.
func findEnabledLarkChannelByAppID(appID string) *models.Channel {
	channels := ChannelService.Find(sqls.NewCnd().
		Eq("channel_type", enums.ChannelTypeLark).
		Eq("status", enums.StatusOk).
		Asc("id"))
	for i := range channels {
		cfg, err := ChannelService.ParseLarkChannelConfig(channels[i].ConfigJSON)
		if err == nil && cfg != nil && cfg.AppID == appID {
			return &channels[i]
		}
	}
	return nil
}
