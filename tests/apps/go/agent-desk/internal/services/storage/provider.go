package storage

import (
	"agent-desk/internal/pkg/config"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"io"
)

var providers = make(map[enums.AssetProvider]FileStorageProvider)

type FileStorageProvider interface {
	ProviderType() enums.AssetProvider
	Upload(reader io.Reader, key string, info UploadInfo) (*StoredFile, error)
	GetURL(key string) string
	GetSignedURL(key string) string
	Delete(key string) error
	Read(key string) (io.ReadCloser, error)
}

func GetDefault() (FileStorageProvider, error) {
	return NewProvider(config.Current().Storage.Default)
}

func GetProvider(providerType enums.AssetProvider) (FileStorageProvider, error) {
	if provider, exists := providers[providerType]; exists {
		return provider, nil
	}
	provider, err := NewProvider(providerType)
	if err != nil {
		return nil, err
	}

	providers[providerType] = provider

	return provider, nil
}

func NewProvider(provider enums.AssetProvider) (FileStorageProvider, error) {
	cfg := config.Current().Storage

	switch provider {
	case "", enums.AssetProviderLocal:
		return NewLocalStorage(cfg.Local), nil
	case enums.AssetProviderOSS:
		return NewOSSStorage(cfg.OSS), nil
	default:
		return nil, errorsx.InvalidParamI18n("error.e0082")
	}
}
