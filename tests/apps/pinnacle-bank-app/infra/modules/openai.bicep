param location string
param tags object
param resourceToken string

@minLength(1)
@description('Azure OpenAI chat model deployment name.')
param modelName string = 'gpt-4o'

@minLength(1)
@description('Azure OpenAI chat model version.')
param modelVersion string = '2024-11-20'

@minValue(1)
@description('Azure OpenAI deployment capacity in thousands of tokens per minute.')
param tpmCapacity int = 50

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'oai-${resourceToken}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: 'oai-${resourceToken}'
    publicNetworkAccess: 'Enabled'
  }
}

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

output openAiName string = openAiAccount.name
output endpoint string = openAiAccount.properties.endpoint
@secure()
output apiKey string = openAiAccount.listKeys().key1
output deploymentName string = chatDeployment.name