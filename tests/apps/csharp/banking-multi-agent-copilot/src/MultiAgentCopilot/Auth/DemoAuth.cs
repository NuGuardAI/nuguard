using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;
using MultiAgentCopilot.Services.InMemory;

namespace MultiAgentCopilot.Auth
{
    /// <summary>
    /// NuGuard fixture: JWT bearer auth for the seeded demo users. POST /auth/token issues a token
    /// whose claims carry the tenant and user; every /tenant/{tenantId}/user/{userId}/... route must
    /// match them. Agent tools are not changed, so any per-account checks they lack remain.
    /// </summary>
    public static class DemoAuth
    {
        public const string TenantClaim = "tenant_id";
        const string Issuer = "banking-multi-agent-copilot";

        public record TokenRequest(string Username, string Password);

        static SymmetricSecurityKey SigningKey(IConfiguration configuration)
        {
            var key = configuration["DemoAuth:SigningKey"];
            if (string.IsNullOrEmpty(key) || Encoding.UTF8.GetByteCount(key) < 32)
                throw new InvalidOperationException("DemoAuth:SigningKey must be set to at least 32 bytes.");
            return new SymmetricSecurityKey(Encoding.UTF8.GetBytes(key));
        }

        public static void AddDemoJwtAuth(this IHostApplicationBuilder builder)
        {
            var signingKey = SigningKey(builder.Configuration);
            builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
                .AddJwtBearer(options =>
                {
                    options.MapInboundClaims = false;
                    options.TokenValidationParameters = new TokenValidationParameters
                    {
                        ValidateIssuer = true,
                        ValidIssuer = Issuer,
                        ValidateAudience = false,
                        ValidateLifetime = true,
                        IssuerSigningKey = signingKey,
                        NameClaimType = JwtRegisteredClaimNames.Sub
                    };
                });
        }

        public static void MapDemoAuth(this WebApplication app)
        {
            var configuration = app.Configuration;
            var signingKey = SigningKey(configuration);
            var password = configuration["DemoAuth:Password"];
            var lifetime = TimeSpan.FromMinutes(configuration.GetValue("DemoAuth:TokenMinutes", 240));

            app.MapPost("/auth/token", (TokenRequest request, IServiceProvider services) =>
            {
                var store = services.GetService<InMemoryStore>();
                if (store == null || string.IsNullOrEmpty(password))
                    return Results.Problem("Demo login needs DataStore:Type=InMemory and DemoAuth:Password.", statusCode: 501);

                var user = store.Users.FirstOrDefault(u => InMemoryStore.Str(u, "id") == request.Username);
                if (user == null || !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(request.Password ?? ""), Encoding.UTF8.GetBytes(password)))
                    return Results.Unauthorized();

                var tenantId = InMemoryStore.Str(user, "tenantId")!;
                var token = new JwtSecurityToken(
                    issuer: Issuer,
                    claims: new[]
                    {
                        new Claim(JwtRegisteredClaimNames.Sub, request.Username),
                        new Claim(TenantClaim, tenantId),
                        new Claim(JwtRegisteredClaimNames.Name, InMemoryStore.Str(user, "name") ?? request.Username)
                    },
                    expires: DateTime.UtcNow.Add(lifetime),
                    signingCredentials: new SigningCredentials(signingKey, SecurityAlgorithms.HmacSha256));

                return Results.Ok(new
                {
                    access_token = new JwtSecurityTokenHandler().WriteToken(token),
                    token_type = "Bearer",
                    tenant_id = tenantId,
                    user_id = request.Username
                });
            }).AllowAnonymous().WithName("DemoLogin");
        }

        public static (string TenantId, string UserId) Caller(ClaimsPrincipal user) =>
            (user.FindFirstValue(TenantClaim) ?? string.Empty, user.FindFirstValue(JwtRegisteredClaimNames.Sub) ?? string.Empty);

        /// <summary>Rejects a request whose {tenantId} or {userId} route value differs from the caller's token.</summary>
        public static async ValueTask<object?> RouteIdentityFilter(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
        {
            var (tenantId, userId) = Caller(context.HttpContext.User);
            var routeValues = context.HttpContext.Request.RouteValues;
            if ((routeValues.TryGetValue("tenantId", out var t) && !string.Equals(t?.ToString(), tenantId, StringComparison.Ordinal)) ||
                (routeValues.TryGetValue("userId", out var u) && !string.Equals(u?.ToString(), userId, StringComparison.Ordinal)))
            {
                return Results.Forbid();
            }
            return await next(context);
        }
    }
}
