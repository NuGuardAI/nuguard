package response

type CustomerSessionCustomerResponse struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type CustomerSessionExchangeResponse struct {
	CustomerSessionToken string                          `json:"customerSessionToken"`
	ExpiresAt            string                          `json:"expiresAt"`
	IdentityKey          string                          `json:"identityKey"`
	Customer             CustomerSessionCustomerResponse `json:"customer"`
}
