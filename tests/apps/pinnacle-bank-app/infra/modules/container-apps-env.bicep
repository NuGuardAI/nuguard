param location string
param tags object
param resourceToken string
param logAnalyticsWorkspaceId string

@secure()
param appInsightsConnectionString string

param openAiEndpoint string

@secure()
param openAiApiKey string

param openAiDeploymentName string

@secure()
param jwtSecret string

var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var frontendOrigin = 'https://frontend-ui.${acaEnv.properties.defaultDomain}'

var sharedEnvVars = [
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
  {
    name: 'OTEL_SERVICE_NAMESPACE'
    value: 'Refined-FinTech-App'
  }
  {
    name: 'AZURE_OPENAI_ENDPOINT'
    value: openAiEndpoint
  }
  {
    name: 'AZURE_OPENAI_DEPLOYMENT'
    value: openAiDeploymentName
  }
  {
    name: 'AZURE_OPENAI_API_VERSION'
    value: '2024-12-01-preview'
  }
]

var registrySecret = {
  name: 'acr-password'
  value: acr.listCredentials().passwords[0].value
}

var registryConfig = [
  {
    server: acr.properties.loginServer
    username: acr.listCredentials().username
    passwordSecretRef: registrySecret.name
  }
]

var commonMcpEnvVars = concat(sharedEnvVars, [
  {
    name: 'MCP_TRANSPORT'
    value: 'sse'
  }
  {
    name: 'PORT'
    value: '8080'
  }
])

var mcpServices = [
  'mcp-accounts'
  'mcp-payments'
  'mcp-cards'
  'mcp-loans'
  'mcp-investments'
  'mcp-fraud'
  'mcp-market-data'
  'mcp-fx'
  'mcp-crypto'
  'mcp-kyc'
  'mcp-aml'
  'mcp-compliance'
  'mcp-notifications'
  'mcp-audit'
  'mcp-reporting'
  'mcp-documents'
  'mcp-admin'
  'mcp-data-export'
  'mcp-internal-bridge'
  'mcp-scheduler'
]

var orchestratorMcpUrls = [
  {
    name: 'MCP_SERVER_URL'
    value: 'http://mcp-banking-server'
  }
  {
    name: 'MCP_ACCOUNTS_URL'
    value: 'http://mcp-accounts'
  }
  {
    name: 'MCP_PAYMENTS_URL'
    value: 'http://mcp-payments'
  }
  {
    name: 'MCP_CARDS_URL'
    value: 'http://mcp-cards'
  }
  {
    name: 'MCP_LOANS_URL'
    value: 'http://mcp-loans'
  }
  {
    name: 'MCP_INVESTMENTS_URL'
    value: 'http://mcp-investments'
  }
  {
    name: 'MCP_FRAUD_URL'
    value: 'http://mcp-fraud'
  }
  {
    name: 'MCP_MARKET_DATA_URL'
    value: 'http://mcp-market-data'
  }
  {
    name: 'MCP_FX_URL'
    value: 'http://mcp-fx'
  }
  {
    name: 'MCP_CRYPTO_URL'
    value: 'http://mcp-crypto'
  }
  {
    name: 'MCP_KYC_URL'
    value: 'http://mcp-kyc'
  }
  {
    name: 'MCP_AML_URL'
    value: 'http://mcp-aml'
  }
  {
    name: 'MCP_COMPLIANCE_URL'
    value: 'http://mcp-compliance'
  }
  {
    name: 'MCP_NOTIFICATIONS_URL'
    value: 'http://mcp-notifications'
  }
  {
    name: 'MCP_AUDIT_URL'
    value: 'http://mcp-audit'
  }
  {
    name: 'MCP_REPORTING_URL'
    value: 'http://mcp-reporting'
  }
  {
    name: 'MCP_DOCUMENTS_URL'
    value: 'http://mcp-documents'
  }
  {
    name: 'MCP_ADMIN_URL'
    value: 'http://mcp-admin'
  }
  {
    name: 'MCP_DATA_EXPORT_URL'
    value: 'http://mcp-data-export'
  }
  {
    name: 'MCP_INTERNAL_BRIDGE_URL'
    value: 'http://mcp-internal-bridge'
  }
  {
    name: 'MCP_SCHEDULER_URL'
    value: 'http://mcp-scheduler'
  }
]

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acrng${resourceToken}'
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
  }
}

resource mcpBankingServer 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'mcp-banking-server'
  location: location
  tags: union(tags, {
    'azd-service-name': 'mcp-banking-server'
  })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
      }
      registries: registryConfig
      secrets: [
        registrySecret
        {
          name: 'openai-api-key'
          value: openAiApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-banking-server'
          image: placeholderImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(commonMcpEnvVars, [
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'openai-api-key'
            }
            {
              name: 'DATABASE_URL'
              value: 'sqlite:///./cipher_bank.db'
            }
            {
              name: 'DEFAULT_SOURCE_ACCOUNT'
              value: 'ACCT-GLOBAL-POOL'
            }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8080, scheme: 'HTTP' }
              initialDelaySeconds: 15
              periodSeconds: 30
              failureThreshold: 3
              timeoutSeconds: 5
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8080, scheme: 'HTTP' }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
              timeoutSeconds: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1  // Keep warm — avoid cold-start on the critical tool-call path
        maxReplicas: 2
      }
    }
  }
}

resource mcpApps 'Microsoft.App/containerApps@2023-05-01' = [for serviceName in mcpServices: {
  name: serviceName
  location: location
  tags: union(tags, {
    'azd-service-name': serviceName
  })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
      }
      registries: registryConfig
      secrets: [
        registrySecret
      ]
    }
    template: {
      containers: [
        {
          name: serviceName
          image: placeholderImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: commonMcpEnvVars
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}]

resource agentOrchestrator 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'agent-orchestrator'
  location: location
  tags: union(tags, {
    'azd-service-name': 'agent-orchestrator'
  })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8001
        transport: 'http'
        allowInsecure: false
        corsPolicy: {
          allowedOrigins: [
            frontendOrigin
          ]
          allowedMethods: [
            'GET'
            'POST'
            'PATCH'
            'DELETE'
            'OPTIONS'
          ]
          allowedHeaders: [
            'Authorization'
            'Content-Type'
          ]
        }
      }
      registries: registryConfig
      secrets: [
        registrySecret
        {
          name: 'openai-api-key'
          value: openAiApiKey
        }
        {
          name: 'jwt-secret'
          value: jwtSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-orchestrator'
          image: placeholderImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(sharedEnvVars, orchestratorMcpUrls, [
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'openai-api-key'
            }
            {
              name: 'JWT_SECRET'
              secretRef: 'jwt-secret'
            }
            {
              name: 'ALLOWED_ORIGINS'
              value: frontendOrigin
            }
            {
              name: 'ORCHESTRATOR_URL'
              value: 'http://agent-orchestrator'
            }
            {
              name: 'PORT'
              value: '8001'
            }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8001, scheme: 'HTTP' }
              initialDelaySeconds: 15
              periodSeconds: 30
              failureThreshold: 3
              timeoutSeconds: 5
            }
            {
              type: 'Readiness'
              httpGet: { path: '/api/health', port: 8001, scheme: 'HTTP' }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
              timeoutSeconds: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1  // Keep warm — external traffic-facing service
        maxReplicas: 5
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
}

resource frontendUi 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'frontend-ui'
  location: location
  tags: union(tags, {
    'azd-service-name': 'frontend-ui'
  })
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'http'
        allowInsecure: false
      }
      registries: registryConfig
      secrets: [
        registrySecret
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend-ui'
          image: placeholderImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'ORCHESTRATOR_UPSTREAM'
              value: 'agent-orchestrator'
            }
            {
              name: 'ORCHESTRATOR_URL'
              value: 'https://${agentOrchestrator.properties.configuration.ingress.fqdn}'
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

output registryName string = acr.name
output registryEndpoint string = acr.properties.loginServer
output environmentId string = acaEnv.id
output environmentName string = acaEnv.name
output mcpBankingServerUri string = 'https://${mcpBankingServer.properties.configuration.ingress.fqdn}'
output orchestratorUri string = 'https://${agentOrchestrator.properties.configuration.ingress.fqdn}'
output frontendUri string = 'https://${frontendUi.properties.configuration.ingress.fqdn}'