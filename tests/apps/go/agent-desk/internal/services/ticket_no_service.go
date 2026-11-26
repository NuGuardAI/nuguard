package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var TicketNoSequenceService = newTicketNoSequenceService()

func newTicketNoSequenceService() *ticketNoSequenceService {
	return &ticketNoSequenceService{}
}

type ticketNoSequenceService struct {
	ticketNoSQLiteMu sync.Mutex
}

func (s *ticketNoSequenceService) Next(now time.Time) (string, error) {
	s.ticketNoSQLiteMu.Lock()
	defer s.ticketNoSQLiteMu.Unlock()

	return s.nextWithRetry(sqls.DB(), now)
}

func (s *ticketNoSequenceService) nextWithRetry(tx *gorm.DB, now time.Time) (string, error) {
	dateKey := now.Format("20060102")
	for attempt := 0; attempt < 100; attempt++ {
		current, err := repositories.TicketNoSequenceRepository.GetByDateKeyForUpdate(tx, dateKey)
		if err != nil {
			if isRetriableTicketNoError(tx, err) {
				sleepTicketNoRetry(attempt)
				continue
			}
			return "", err
		}
		if current == nil {
			item := &models.TicketNoSequence{
				DateKey:   dateKey,
				NextSeq:   2,
				CreatedAt: now,
				UpdatedAt: now,
			}
			err := repositories.TicketNoSequenceRepository.Create(tx, item)
			if err == nil {
				return formatTicketNo(dateKey, 1), nil
			}
			if !isRetriableTicketNoError(tx, err) {
				return "", err
			}

			current, err = repositories.TicketNoSequenceRepository.GetByDateKeyForUpdate(tx, dateKey)
			if err != nil {
				if isRetriableTicketNoError(tx, err) {
					sleepTicketNoRetry(attempt)
					continue
				}
				return "", err
			}
			if current == nil {
				sleepTicketNoRetry(attempt)
				continue
			}
		}
		allocated := current.NextSeq
		ok, err := repositories.TicketNoSequenceRepository.UpdateNextSeq(tx, current.ID, allocated, allocated+1, now)
		if err != nil {
			if isRetriableTicketNoError(tx, err) {
				sleepTicketNoRetry(attempt)
				continue
			}
			return "", err
		}
		if ok {
			return formatTicketNo(dateKey, allocated), nil
		}
		sleepTicketNoRetry(attempt)
	}
	return "", fmt.Errorf("failed to allocate ticket number")
}

func sleepTicketNoRetry(attempt int) {
	delay := time.Duration(attempt+1) * 10 * time.Millisecond
	if delay > 200*time.Millisecond {
		delay = 200 * time.Millisecond
	}
	time.Sleep(delay)
}

func formatTicketNo(dateKey string, seq int64) string {
	return fmt.Sprintf("TK%s%05d", dateKey, seq)
}

func isDuplicateKeyError(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "duplicate") || strings.Contains(message, "unique") || strings.Contains(message, "constraint failed")
}

func isRetriableTicketNoError(tx *gorm.DB, err error) bool {
	if isDuplicateKeyError(err) {
		return true
	}
	return tx != nil && tx.Dialector.Name() == "sqlite" && isSQLiteDatabaseLockedError(err)
}

func isSQLiteDatabaseLockedError(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "database is locked") ||
		strings.Contains(message, "database table is locked") ||
		strings.Contains(message, "database is busy")
}
