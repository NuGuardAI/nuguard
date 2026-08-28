using Microsoft.SemanticKernel;
using Microsoft.SemanticKernel.Planning.Handlebars;

namespace HealthcareService;

public class WeatherPlugin
{
    [KernelFunction]
    public string GetWeather() => "sunny";
}

public static class KernelSetup
{
    public static void Run()
    {
        var builder = Kernel.CreateBuilder();
        builder.AddAzureOpenAIChatCompletion(
            deploymentName: "gpt-4o",
            endpoint: "https://example.openai.azure.com",
            apiKey: "unused");
        var kernel = builder.Build();
        kernel.Plugins.AddFromType<WeatherPlugin>(
            "Weather");
        var planner = new HandlebarsPlanner();
        var systemPrompt = "You are a health triage assistant.";
    }
}
