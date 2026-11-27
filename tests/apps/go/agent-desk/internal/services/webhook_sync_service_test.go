package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"testing"
	"time"
)

func TestWebhookDOSOrgSync_OrgEvents(t *testing.T) {
	db := setupAuthServiceTestDB(t)
	svc := newWebhookSyncService()

	// 1. Test org.created
	err := svc.HandleDOSOrgSync(request.DOSOrgSyncWebhookRequest{
		Event:     "org.created",
		Timestamp: time.Now().Format(time.RFC3339),
		Data: request.DOSOrgSyncEventData{
			OrgID:   "org_tingee_001",
			OrgName: "Tingee Corporation",
			Plan:    "pro",
		},
	})
	if err != nil {
		t.Fatalf("HandleDOSOrgSync org.created failed: %v", err)
	}

	org := repositories.OrganizationRepository.GetByCode(db, "org_tingee_001")
	if org == nil || org.Name != "Tingee Corporation" || org.Plan != "pro" || org.Status != enums.StatusOk {
		t.Fatalf("unexpected created org: %+v", org)
	}

	// 2. Test org.updated
	err = svc.HandleDOSOrgSync(request.DOSOrgSyncWebhookRequest{
		Event:     "org.updated",
		Timestamp: time.Now().Format(time.RFC3339),
		Data: request.DOSOrgSyncEventData{
			OrgID:   "org_tingee_001",
			OrgName: "Tingee Global Corp",
			Plan:    "enterprise",
		},
	})
	if err != nil {
		t.Fatalf("HandleDOSOrgSync org.updated failed: %v", err)
	}

	org = repositories.OrganizationRepository.GetByCode(db, "org_tingee_001")
	if org == nil || org.Name != "Tingee Global Corp" || org.Plan != "enterprise" {
		t.Fatalf("unexpected updated org: %+v", org)
	}

	// 3. Test org.deleted
	err = svc.HandleDOSOrgSync(request.DOSOrgSyncWebhookRequest{
		Event:     "org.deleted",
		Timestamp: time.Now().Format(time.RFC3339),
		Data: request.DOSOrgSyncEventData{
			OrgID: "org_tingee_001",
		},
	})
	if err != nil {
		t.Fatalf("HandleDOSOrgSync org.deleted failed: %v", err)
	}

	org = repositories.OrganizationRepository.GetByCode(db, "org_tingee_001")
	if org == nil || org.Status != enums.StatusDeleted {
		t.Fatalf("expected org to be deleted, got: %+v", org)
	}
}

func TestWebhookDOSOrgSync_MemberEvents(t *testing.T) {
	db := setupAuthServiceTestDB(t)
	svc := newWebhookSyncService()

	// 1. Test org.member_added
	err := svc.HandleDOSOrgSync(request.DOSOrgSyncWebhookRequest{
		Event:     "org.member_added",
		Timestamp: time.Now().Format(time.RFC3339),
		Data: request.DOSOrgSyncEventData{
			OrgID:     "org_tingee_002",
			OrgName:   "Tingee R&D",
			UserID:    "usr_dos_001",
			UserEmail: "member@tingee.com",
			UserName:  "Nguyen Van A",
			Role:      "ADMIN",
		},
	})
	if err != nil {
		t.Fatalf("HandleDOSOrgSync org.member_added failed: %v", err)
	}

	org := repositories.OrganizationRepository.GetByCode(db, "org_tingee_002")
	if org == nil {
		t.Fatalf("expected org to be auto-provisioned")
	}

	user := repositories.UserRepository.GetByEmail(db, "member@tingee.com")
	if user == nil || user.Nickname != "Nguyen Van A" {
		t.Fatalf("expected user to be created: %+v", user)
	}

	member := repositories.OrganizationMemberRepository.GetByOrgAndUser(db, org.ID, user.ID)
	if member == nil || member.Role != "ADMIN" || member.Status != enums.StatusOk {
		t.Fatalf("unexpected member record: %+v", member)
	}

	if user.ActiveOrgID != org.ID {
		t.Fatalf("expected active_org_id = %d, got %d", org.ID, user.ActiveOrgID)
	}

	// 2. Test org.member_removed
	err = svc.HandleDOSOrgSync(request.DOSOrgSyncWebhookRequest{
		Event:     "org.member_removed",
		Timestamp: time.Now().Format(time.RFC3339),
		Data: request.DOSOrgSyncEventData{
			OrgID:     "org_tingee_002",
			UserID:    "usr_dos_001",
			UserEmail: "member@tingee.com",
		},
	})
	if err != nil {
		t.Fatalf("HandleDOSOrgSync org.member_removed failed: %v", err)
	}

	member = repositories.OrganizationMemberRepository.GetByOrgAndUser(db, org.ID, user.ID)
	if member == nil || member.Status != enums.StatusDeleted {
		t.Fatalf("expected member to be marked deleted, got: %+v", member)
	}
}

func TestOrganizationService_SwitchAndList(t *testing.T) {
	db := setupAuthServiceTestDB(t)
	user := createAuthTestUser(t, db, "testuser", "secret")

	org1 := &models.Organization{Code: "org1", Name: "Org 1", Status: enums.StatusOk}
	org2 := &models.Organization{Code: "org2", Name: "Org 2", Status: enums.StatusOk}
	_ = repositories.OrganizationRepository.Create(db, org1)
	_ = repositories.OrganizationRepository.Create(db, org2)

	_ = repositories.OrganizationMemberRepository.Create(db, &models.OrganizationMember{
		OrganizationID: org1.ID,
		UserID:         user.ID,
		Role:           "OWNER",
		Status:         enums.StatusOk,
	})
	_ = repositories.OrganizationMemberRepository.Create(db, &models.OrganizationMember{
		OrganizationID: org2.ID,
		UserID:         user.ID,
		Role:           "MEMBER",
		Status:         enums.StatusOk,
	})

	_ = repositories.UserRepository.UpdateColumn(db, user.ID, "active_org_id", org1.ID)

	// List
	res, err := OrganizationService.GetUserOrganizations(user.ID)
	if err != nil {
		t.Fatalf("GetUserOrganizations failed: %v", err)
	}
	if len(res.Organizations) != 2 || res.CurrentOrganizationID != org1.ID {
		t.Fatalf("unexpected user org list: %+v", res)
	}

	// Switch
	switched, err := OrganizationService.SwitchActiveOrganization(user.ID, org2.ID)
	if err != nil {
		t.Fatalf("SwitchActiveOrganization failed: %v", err)
	}
	if switched == nil || switched.ID != org2.ID {
		t.Fatalf("unexpected switched org: %+v", switched)
	}

	updatedUser := repositories.UserRepository.Get(db, user.ID)
	if updatedUser.ActiveOrgID != org2.ID {
		t.Fatalf("expected active_org_id %d, got %d", org2.ID, updatedUser.ActiveOrgID)
	}
}
