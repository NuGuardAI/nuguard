package builders

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/services"
	"time"
)

func BuildCustomer(item *models.Customer) *response.CustomerResponse {
	if item == nil {
		return nil
	}
	return &response.CustomerResponse{
		ID:            item.ID,
		Name:          item.Name,
		Gender:        item.Gender,
		CompanyID:     item.CompanyID,
		Company:       BuildCompany(services.CompanyService.Get(item.CompanyID)),
		LastActiveAt:  utils.FormatTimePtr(item.LastActiveAt),
		PrimaryMobile: item.PrimaryMobile,
		PrimaryEmail:  item.PrimaryEmail,
		Status:        item.Status,
		Remark:        item.Remark,
		CreatedAt:     item.CreatedAt.Format(time.DateTime),
		UpdatedAt:     item.UpdatedAt.Format(time.DateTime),
	}
}

func BuildCustomerList(list []models.Customer) []response.CustomerResponse {
	results := make([]response.CustomerResponse, 0, len(list))
	for _, item := range list {
		if customer := BuildCustomer(&item); customer != nil {
			results = append(results, *customer)
		}
	}
	return results
}
