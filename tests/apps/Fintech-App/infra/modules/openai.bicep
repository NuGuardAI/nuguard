// modules/openai.bicep — Azure OpenAI Service + model deployments

param location string
param tags object
param resourceToken string

@description('OpenAI model name for GPT-4o deployment.')
param modelName string = 'gpt-4o'

@description('OpenAI model version.')
param modelVersion string = '2024-11-20'

@description('Tokens per minute capacity (thousands).')
param tpmCapacity int = 10

// ── Azure OpenAI Account ───────────────────────────────────────────────────
resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'oai-${resourceToken}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: 'oai-${resourceToken}'
  }
}

// ── GPT-4o Deployment (used by both Triage Agent and Wealth Advisor) ───────
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAiAccount
  name: modelName
  sku: {
    name: 'Standard'
    capacity: tpmCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────
output openAiName string = openAiAccount.name
output endpoint string = openAiAccount.properties.endpoint
@secure()
output apiKey string = openAiAccount.listKeys().key1
output deploymentName string = chatDeployment.name
