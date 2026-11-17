/**
 * Errors of the 3D window's pure modules: `{code, message, hint}` like the core (ТЗ 17), the message
 * and hint in Russian for the user, the code for programs and tests.
 */
export class VizError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly hint: string,
    /** HTTP status when the error came from the core (0 — no answer, undefined — not a request) */
    readonly status?: number,
  ) {
    super(message);
    this.name = "VizError";
  }

  /** Message and hint in one line, for logs and Claude. */
  get full(): string {
    return this.hint ? `${this.message} ${this.hint}` : this.message;
  }
}
