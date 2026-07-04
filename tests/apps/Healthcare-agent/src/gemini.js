import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({
  apiKey: import.meta.env.VITE_GEMINI_API_KEY,
});


// Initialize a conversation history
let conversationHistory = [];

async function run(prompt) {
  // Add the current user message to the conversation history
  conversationHistory.push(`User: ${prompt}`);
  
  const response = await ai.models.generateContent({
    model: "gemini-2.0-flash",
    contents: conversationHistory.join('\n'), // Include the full conversation history
    config: {
      systemInstruction: "You are a helpful AI Medical Triage Assistant. Your goal is to gather a clear list of symptoms from the patient " + 
"through follow-up questions. Ask only one question at a time. Be empathetic but professional. " +
"Do not provide a medical diagnosis. Do not mention specific medical specialists or departments. " +
"If the user asks for a diagnosis, politely explain that you are here to record their symptoms for a specialist. " +
"Once you have a complete picture of their symptoms (e.g., location, duration, severity), say exactly: " +
"'I have thoroughly examined your symptoms. Now you can click on disconnect to find the right specialist.'",
    },
  });
  
  // Log the response for debugging
  console.log(response);

  // Extract the generated text from the response
  const generatedText = response?.candidates?.[0]?.content?.parts?.[0]?.text || "No response available";
  
  // Add the agent's response to the conversation history
  conversationHistory.push(`Agent: ${generatedText}`);
  
  return generatedText;
}

export default run;
