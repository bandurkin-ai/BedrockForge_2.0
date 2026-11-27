import { expect, vi } from "vitest";
import { answerConfirm, confirmStore, type ConfirmView } from "../src/modules/assistant/confirm";

/**
 * The confirmation of a call from Claude is a card in the chat (Э23, 23.15), not a dialog of the shell: waits for the card and answers it
 * (`true` — «Разрешить», `false` — «Отклонить»). Returns the card (the title, the description, the arguments).
 */
export async function answerClaude(allow: boolean, commandId?: string): Promise<ConfirmView> {
  await vi.waitFor(() => expect(confirmStore.get().queue.length).toBeGreaterThan(0));
  const card = commandId ? confirmStore.get().queue.find((c) => c.commandId === commandId)! : confirmStore.get().queue[0];
  expect(card).toBeDefined();
  expect(answerConfirm(card.id, allow)).toBe(true);
  return card;
}

/** No card of Claude is waiting. */
export const noClaudeCards = (): void => expect(confirmStore.get().queue).toEqual([]);
