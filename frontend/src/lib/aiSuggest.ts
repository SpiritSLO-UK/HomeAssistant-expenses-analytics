import { approveAiRequest, classifyWithAi } from "../api/client";

export interface AiSuggestion {
  categoryId: number | null;
  country: string | null; // ISO-3166-1 alpha-2, when the AI inferred one
}

/**
 * Ask the AI to suggest a category (and, when it can tell, the country) for a
 * transaction. Never auto-applies — the caller applies what's returned.
 *
 * Cloud-manual mode previews the redacted payload and asks for approval before
 * anything leaves the device (spec §22.5); then the suggestion itself is shown
 * for a final confirm. Shared by the Transactions list and the Review Queue so
 * the privacy/approval flow stays identical everywhere.
 *
 * @returns the suggestion the user agreed to apply (category id and/or country),
 *   or `null` if they declined at any step / the AI had nothing to offer.
 * @throws on transport errors (caller should surface the message).
 */
export async function suggestForTransaction(transactionId: number): Promise<AiSuggestion | null> {
  let res = await classifyWithAi(transactionId);
  if (res.status === "approval_required") {
    const preview = JSON.stringify(res.payload ?? {}, null, 2);
    if (!globalThis.confirm(`Cloud AI needs approval. Only this redacted payload is sent:\n\n${preview}\n\nApprove?`)) {
      return null;
    }
    res = await approveAiRequest(res.ai_request_id);
  }
  const country = res.country ?? null;
  if (res.status === "ok" && (res.category_id || country)) {
    const pct = res.confidence == null ? "" : ` (${Math.round(res.confidence * 100)}%)`;
    const lines = [
      res.category_id ? `Category: ${res.category_name}${pct}` : null,
      country ? `Country: ${country}` : null,
      res.rationale || null,
    ].filter(Boolean);
    if (globalThis.confirm(`AI suggests:\n\n${lines.join("\n")}\n\nApply?`)) {
      return { categoryId: res.category_id, country };
    }
    return null;
  }
  globalThis.alert("AI couldn't suggest anything for this transaction.");
  return null;
}
