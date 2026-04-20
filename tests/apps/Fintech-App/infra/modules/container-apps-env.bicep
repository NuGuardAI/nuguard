// modules/container-apps-env.bicep
// Provisions:
//   - Azure Container Registry
//   - ACA Managed Environment (linked to Log Analytics)
//   - Container App: mcp-banking-server  (internal ingress only)
//   - Container App: agent-orchestrator  (external ingress)
//   - Container App: frontend-ui          (external ingress)

param location string
param tags object
param resourceToken string
param logAnalyticsWorkspaceId string
@secure()
param appInsightsConnectionString string
@secure()
param redisConnectionString string
param openAiEndpoint string
@secure()
param openAiApiKey string
param openAiDeploymentName string

// ── Azure Container Registry ───────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acrrg${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

// ── ACA Managed Environment ────────────────────────────────────────────────
resource acaEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'acaenv-${resourceToken}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2022-10-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2022-10-01').primarySharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
  }
}

// ── Shared environment variables (non-secret) ──────────────────────────────
var sharedEnvVars = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'OTEL_SERVICE_NAMESPACE', value: 'fintech-goat' }
  { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
  { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiDeploymentName }
  { name: 'AZURE_OPENAI_API_VERSION', value: '2024-12-01-preview' }
]

// ── Container App: mcp-banking-server (INTERNAL — only reachable by orchestrator) ─────
resource mcpBankingServer 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-banking-server'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-banking-server' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: false       // Internal only — still vulnerable to auth bypass internally
        targetPort: 8080
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'redis-conn', value: redisConnectionString }
        { name: 'openai-api-key', value: openAiApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-banking-server'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: union(sharedEnvVars, [
            { name: 'REDIS_URL', secretRef: 'redis-conn' }
            { name: 'CELERY_BROKER_URL', secretRef: 'redis-conn' }
            { name: 'MCP_TRANSPORT', value: 'sse' }
            { name: 'PORT', value: '8080' }
            // VULN-01: DEFAULT_SOURCE_ACCOUNT used when no ownership check performed
            { name: 'DEFAULT_SOURCE_ACCOUNT', value: 'ACCT-GLOBAL-POOL' }
          ])
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

// ── Container App: agent-orchestrator ─────────────────────────────────────
resource agentOrchestrator 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'agent-orchestrator'
  location: location
  tags: union(tags, { 'azd-service-name': 'agent-orchestrator' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8001
        transport: 'http'
        corsPolicy: {
          allowedOrigins: ['*']   // VULN: Permissive CORS
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['*']
        }
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'openai-api-key', value: openAiApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-orchestrator'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: union(sharedEnvVars, [
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-api-key' }
            { name: 'MCP_SERVER_URL', value: 'http://mcp-banking-server' }
            { name: 'MCP_ACCOUNTS_URL', value: 'http://mcp-accounts' }
            { name: 'MCP_FRAUD_URL', value: 'http://mcp-fraud' }
            { name: 'MCP_MARKET_DATA_URL', value: 'http://mcp-market-data' }
            { name: 'MCP_NOTIFICATIONS_URL', value: 'http://mcp-notifications' }
            { name: 'MCP_ADMIN_URL', value: 'http://mcp-admin' }
            { name: 'MCP_DATA_EXPORT_URL', value: 'http://mcp-data-export' }
            { name: 'MCP_INTERNAL_BRIDGE_URL', value: 'http://mcp-internal-bridge' }
            { name: 'MCP_COMPLIANCE_URL', value: 'http://mcp-compliance' }
            { name: 'PORT', value: '8001' }
          ])
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

// ── Container App: frontend-ui ─────────────────────────────────────────────
resource frontendUi 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'frontend-ui'
  location: location
  tags: union(tags, { 'azd-service-name': 'frontend-ui' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 80
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend-ui'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            {
              name: 'ORCHESTRATOR_URL'
              value: 'https://${agentOrchestrator.properties.configuration.ingress.fqdn}'
            }
            {
              // ACA internal DNS: reach agent-orchestrator on port 80 within same env
              name: 'ORCHESTRATOR_UPSTREAM'
              value: 'agent-orchestrator'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

// ── Helper: internal MCP secrets ───────────────────────────────────────────
var mcpSecrets = [
  { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
]

// ── Internal MCP microservices (no external ingress) ──────────────────────
// Each is a separate Container App reachable only within the ACA environment.
// VULN: All MCP services accept unauthenticated requests — no service mesh mTLS.

resource mcpAccounts 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-accounts'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-accounts' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-accounts', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpFraud 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-fraud'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-fraud' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-fraud', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpMarketData 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-market-data'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-market-data' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-market-data', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpNotifications 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-notifications'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-notifications' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-notifications', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpAdmin 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-admin'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-admin' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-admin', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpDataExport 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-data-export'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-data-export' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-data-export', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpInternalBridge 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-internal-bridge'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-internal-bridge' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-internal-bridge', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource mcpCompliance 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-compliance'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp-compliance' })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' }]
      secrets: mcpSecrets
    }
    template: {
      containers: [{ name: 'mcp-compliance', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest', resources: { cpu: json('0.25'), memory: '0.5Gi' }, env: sharedEnvVars }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

// ── Update orchestrator to know all new internal MCP service URLs ─────────
// (The agent-orchestrator resource already exists above; we pass URLs via env vars)

// ── Outputs ────────────────────────────────────────────────────────────────
output registryName string = acr.name
output registryEndpoint string = acr.properties.loginServer
output environmentId string = acaEnv.id
output environmentName string = acaEnv.name
output mcpServerUri string = 'https://${mcpBankingServer.properties.configuration.ingress.fqdn}'
output orchestratorUri string = 'https://${agentOrchestrator.properties.configuration.ingress.fqdn}'
output frontendUri string = 'https://${frontendUi.properties.configuration.ingress.fqdn}'
