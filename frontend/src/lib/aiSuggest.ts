import { approveAiRequest, classifyWithAi } from "../api/client";

/**
 * Ask the AI to suggest a category for a transaction (never auto-applies).
 *
 * Cloud-manual mode previews the redacted payload and asks for approval before
 * anything leaves the device (spec §22.5); then the suggestion itself is shown
 * for a final confirm. Shared by the Transactions list and the Review Queue so
 * the privacy/approval flow stays identical everywhere.
 *
 * @returns the chosen category id once the user confirms applying it, or `null`
 *   if they declined at any step / the AI had no suggestion.
 * @throws on transport errors (caller should surface the message).
 */
export async function suggestCategory(transactionId: number): Promise<number | null> {
  let res = await classifyWithAi(transactionId);
  if (res.status === "approval_required") {
    const preview = JSON.stringify(res.payload ?? {}, null, 2);
    if (!globalThis.confirm(`Cloud AI needs approval. Only this redacted payload is sent:\n\n${preview}\n\nApprove?`)) {
      return null;
    }
    res = await approveAiRequest(res.ai_request_id);
  }
  if (res.status === "ok" && res.category_id) {
    const pct = res.confidence == null ? "" : ` (${Math.round(res.confidence * 100)}%)`;
    if (globalThis.confirm(`AI suggests: ${res.category_name}${pct}\n${res.rationale ?? ""}\n\nApply this category?`)) {
      return res.category_id;
    }
    return null;
  }
  globalThis.alert("AI couldn't suggest a category for this transaction.");
  return null;
}
