// modules/redis.bicep — Azure Cache for Redis (Standard C1)
// Upgraded from deprecated Basic C0 → Standard C1, API version 2024-11-01.

param location string
param tags object
param resourceToken string

// ── Redis Cache (Standard C1 — minimum recommended after Basic deprecation) ─
resource redisCache 'Microsoft.Cache/redis@2024-11-01' = {
  name: 'redis-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'Standard'
      family: 'C'
      capacity: 1
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      'maxmemory-policy': 'allkeys-lru'
    }
    publicNetworkAccess: 'Enabled'
  }
}

// ── Connection string (Celery/redis-py compatible — TLS port 6380) ─────────
var redisHostName = redisCache.properties.hostName
var redisPrimaryKey = redisCache.listKeys().primaryKey
var redisPort = '6380'

// ── Outputs ────────────────────────────────────────────────────────────────
output redisName string = redisCache.name
output redisHostName string = redisHostName
@secure()
output redisConnectionString string = 'rediss://:${redisPrimaryKey}@${redisHostName}:${redisPort}/0'
