package eventbus

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type testEvent struct {
	ID int64
}

func TestPackageSubscribeAndPublish(t *testing.T) {
	resetManagerForTest(t)

	var got atomic.Int64
	_, unsubscribe := Subscribe(func(ctx context.Context, event testEvent) error {
		got.Store(event.ID)
		return nil
	})
	defer unsubscribe()

	if err := Publish(context.Background(), testEvent{ID: 42}); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	if got.Load() != 42 {
		t.Fatalf("expected event ID 42, got %d", got.Load())
	}
}

func TestPublishReturnsJoinedHandlerErrors(t *testing.T) {
	firstErr := errors.New("first")
	secondErr := errors.New("second")
	bus := New[testEvent]()

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		return firstErr
	})
	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		return secondErr
	})

	err := bus.Publish(context.Background(), testEvent{})
	if !errors.Is(err, firstErr) {
		t.Fatalf("expected joined error to include first error, got %v", err)
	}
	if !errors.Is(err, secondErr) {
		t.Fatalf("expected joined error to include second error, got %v", err)
	}
}

func TestPublishCallsHandlersInSubscribeOrder(t *testing.T) {
	bus := New[testEvent]()
	calls := make([]int, 0, 3)

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 1)
		return nil
	})
	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 2)
		return nil
	})
	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 3)
		return nil
	})

	if err := bus.Publish(context.Background(), testEvent{}); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	if !reflect.DeepEqual(calls, []int{1, 2, 3}) {
		t.Fatalf("expected handlers to run in subscribe order, got %#v", calls)
	}
}

func TestUnsubscribeKeepsRemainingHandlerOrder(t *testing.T) {
	bus := New[testEvent]()
	calls := make([]int, 0, 2)

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 1)
		return nil
	})
	_, unsubscribe := bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 2)
		return nil
	})
	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		calls = append(calls, 3)
		return nil
	})

	unsubscribe()

	if err := bus.Publish(context.Background(), testEvent{}); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	if !reflect.DeepEqual(calls, []int{1, 3}) {
		t.Fatalf("expected remaining handlers to keep order, got %#v", calls)
	}
}

func TestPanicReturnsError(t *testing.T) {
	bus := New[testEvent]()

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		panic("boom")
	})

	err := bus.Publish(context.Background(), testEvent{})
	if err == nil {
		t.Fatalf("expected panic to be returned as error")
	}
}

func TestPublishAsyncCallsErrorHandler(t *testing.T) {
	handlerErr := errors.New("async failed")
	var handled atomic.Int64
	done := make(chan struct{})
	bus := New[testEvent](WithErrorHandler[testEvent](func(ctx context.Context, err error) {
		if errors.Is(err, handlerErr) {
			handled.Add(1)
		}
		close(done)
	}))

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		return handlerErr
	})

	bus.PublishAsync(context.Background(), testEvent{})

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatalf("expected async error handler to be called")
	}

	if handled.Load() != 1 {
		t.Fatalf("expected error handler to be called once, got %d", handled.Load())
	}
}

func TestSubscribeOnceConcurrentPublishOnlyCallsOnce(t *testing.T) {
	bus := New[testEvent]()
	var calls atomic.Int64

	bus.SubscribeOnce(func(ctx context.Context, event testEvent) error {
		calls.Add(1)
		time.Sleep(10 * time.Millisecond)
		return nil
	})

	const workers = 32
	var wg sync.WaitGroup
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = bus.Publish(context.Background(), testEvent{})
		}()
	}
	wg.Wait()

	if calls.Load() != 1 {
		t.Fatalf("expected once handler to be called once, got %d", calls.Load())
	}
}

func TestPublishAsyncAllowsReentrantPublishWithConcurrencyLimit(t *testing.T) {
	bus := New[testEvent](WithAsyncConcurrency[testEvent](1))
	done := make(chan struct{})
	var calls atomic.Int64

	bus.Subscribe(func(ctx context.Context, event testEvent) error {
		if calls.Add(1) == 1 {
			bus.PublishAsync(ctx, testEvent{ID: 2})
			return nil
		}
		close(done)
		return nil
	})

	bus.PublishAsync(context.Background(), testEvent{ID: 1})

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatalf("expected reentrant async publish to complete")
	}
}
