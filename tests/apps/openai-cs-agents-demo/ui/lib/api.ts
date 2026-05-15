// Helper to call the server
export async function callLoginAPI(username: string, password: string) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE ?? "";
    const url = baseUrl ? `${baseUrl}/login` : "/login";
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(`Login API error: ${res.status}`);
    return res.json();
  } catch (err) {
    console.error("Error logging in:", err);
    return null;
  }
}

export async function callLogoutAPI(token: string) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE ?? "";
    const url = baseUrl ? `${baseUrl}/logout` : "/logout";
    await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // best-effort
  }
}

export async function callChatAPI(message: string, conversationId: string, token?: string) {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE ?? "";
    const url = baseUrl ? `${baseUrl}/chat` : "/chat";
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ conversation_id: conversationId, message }),
    });
    if (!res.ok) throw new Error(`Chat API error: ${res.status}`);
    return res.json();
  } catch (err) {
    console.error("Error sending message:", err);
    return null;
  }
}
