// Optional Vision-LLM captioning hook.
//
// By default this returns null so the Odoo-side deterministic caption is kept.
// To enable AI captions, set DOC_LLM_ENDPOINT (and DOC_LLM_API_KEY) and adapt
// the request body to your provider. The function receives the PNG buffer and
// a short context string (screen name + target model + field labels).

export async function annotate(imageBuffer, contextText) {
  const endpoint = process.env.DOC_LLM_ENDPOINT;
  if (!endpoint) {
    return null; // captions disabled -> caller keeps the deterministic text
  }

  const apiKey = process.env.DOC_LLM_API_KEY || "";
  const imageB64 = imageBuffer.toString("base64");

  // Generic multimodal chat-completions style payload. Adjust per provider.
  const body = {
    model: process.env.DOC_LLM_MODEL || "vision-default",
    messages: [
      {
        role: "system",
        content:
          "You write concise end-user documentation captions for Odoo screens. " +
          "Describe what the user sees and what they can do, in 2-3 sentences.",
      },
      {
        role: "user",
        content: [
          { type: "text", text: contextText || "Describe this Odoo screen." },
          {
            type: "image_url",
            image_url: { url: `data:image/png;base64,${imageB64}` },
          },
        ],
      },
    ],
  };

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      console.warn(`LLM caption HTTP ${res.status}; falling back.`);
      return null;
    }
    const data = await res.json();
    return data?.choices?.[0]?.message?.content?.trim() || null;
  } catch (err) {
    console.warn("LLM caption failed; falling back:", err.message);
    return null;
  }
}
