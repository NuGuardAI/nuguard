// main.bicep — Top-level AZD deployment for FinTech GOAT
// FinTech GOAT — Deliberately Vulnerable AI Application
// targetScope = 'subscription' is required for AZD resource-group based deploys

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the AZD environment (used to generate unique resource names).')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources.')
param location string = 'eastus'

@description('Azure OpenAI model deployment name.')
param openAiModelName string = 'gpt-4o'

@description('Azure OpenAI model version.')
param openAiModelVersion string = '2024-11-20'

// ── Shared tags ────────────────────────────────────────────────────────────
var tags = {
  'azd-env-name': environmentName
  app: 'fintech-goat'
  purpose: 'security-research'
  warning: 'DELIBERATELY-VULNERABLE-DO-NOT-USE-IN-PRODUCTION'
}

// ── Unique token appended to resource names to avoid collisions ──────────
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))

// ── Resource Group ─────────────────────────────────────────────────────────
resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

// ── Module: Log Analytics + Application Insights ──────────────────────────
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-${resourceToken}'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
  }
}

// ── Module: Azure Cache for Redis ──────────────────────────────────────────
module redis 'modules/redis.bicep' = {
  name: 'redis-${resourceToken}'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
  }
}

// ── Module: Azure OpenAI ───────────────────────────────────────────────────
module openai 'modules/openai.bicep' = {
  name: 'openai-${resourceToken}'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    modelName: openAiModelName
    modelVersion: openAiModelVersion
  }
}

// ── Module: Container Apps Environment + the 3 microservices ─────────────
module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'aca-${resourceToken}'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    redisConnectionString: redis.outputs.redisConnectionString
    openAiEndpoint: openai.outputs.endpoint
    openAiApiKey: openai.outputs.apiKey
    openAiDeploymentName: openAiModelName
  }
}

// ── Outputs consumed by azd and downstream services ───────────────────────
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerAppsEnv.outputs.registryEndpoint
output AZURE_CONTAINER_REGISTRY_NAME string = containerAppsEnv.outputs.registryName

output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnv.outputs.environmentId

output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString

output SERVICE_MCP_BANKING_SERVER_URI string = containerAppsEnv.outputs.mcpServerUri
output SERVICE_AGENT_ORCHESTRATOR_URI string = containerAppsEnv.outputs.orchestratorUri
output SERVICE_FRONTEND_UI_URI string = containerAppsEnv.outputs.frontendUri
