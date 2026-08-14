targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment. Used to generate stable resource names.')
param environmentName string

@minLength(1)
@description('Azure region for the deployment. Defaults to East US for the NuGuard-AI Platform subscription.')
param location string = 'eastus'

@minLength(1)
@description('Resource group to create for this environment.')
param resourceGroupName string = 'rg-nuguard-ai-platform-${environmentName}'

@minLength(1)
@description('Azure OpenAI chat model deployment name.')
param openAiModelName string = 'gpt-4o'

@minLength(1)
@description('Azure OpenAI chat model version.')
param openAiModelVersion string = '2024-11-20'

@minValue(1)
@description('Azure OpenAI deployment capacity in thousands of tokens per minute.')
param openAiTpmCapacity int = 50

@secure()
@minLength(16)
@description('JWT signing secret for the orchestrator. Set with: azd env set JWT_SECRET <secret>.')
param jwtSecret string

var tags = {
  'azd-env-name': environmentName
  application: 'Refined-FinTech-App'
  workload: 'nuguard-ai-platform'
  purpose: 'security-research'
}

var resourceToken = toLower(uniqueString(subscription().id, toLower(resourceGroupName), environmentName, location))

resource resourceGroup 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-${resourceToken}'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
  }
}

module openai 'modules/openai.bicep' = {
  name: 'openai-${resourceToken}'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    modelName: openAiModelName
    modelVersion: openAiModelVersion
    tpmCapacity: openAiTpmCapacity
  }
}

module containerApps 'modules/container-apps-env.bicep' = {
  name: 'container-apps-${resourceToken}'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    openAiEndpoint: openai.outputs.endpoint
    openAiApiKey: openai.outputs.apiKey
    openAiDeploymentName: openAiModelName
    jwtSecret: jwtSecret
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = resourceGroup.name

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerApps.outputs.registryEndpoint
output AZURE_CONTAINER_REGISTRY_NAME string = containerApps.outputs.registryName
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerApps.outputs.environmentId

output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsWorkspaceId

output AZURE_OPENAI_ENDPOINT string = openai.outputs.endpoint
output AZURE_OPENAI_DEPLOYMENT string = openAiModelName

output SERVICE_MCP_BANKING_SERVER_URI string = containerApps.outputs.mcpBankingServerUri
output SERVICE_AGENT_ORCHESTRATOR_URI string = containerApps.outputs.orchestratorUri
output SERVICE_FRONTEND_UI_URI string = containerApps.outputs.frontendUri