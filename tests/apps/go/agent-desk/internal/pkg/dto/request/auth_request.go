package request

type LoginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type UpdateProfileRequest struct {
	Nickname string  `json:"nickname"`
	Avatar   string  `json:"avatar"`
	Email    *string `json:"email"`
}

type WxWorkExchangeRequest struct {
	Ticket string `json:"ticket"`
}

type OIDCExchangeRequest struct {
	Ticket string `json:"ticket"`
}
