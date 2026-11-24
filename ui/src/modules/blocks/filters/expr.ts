/**
 * The language of 8.7.4 on the client (ТЗ 8.7.4; ADR 0023 §7; core — `calc/{lexer, parser, nodes, errors}.py`): the same
 * scanner, the same recursive descent and the same canonical record, so that the constructor of a filter and its text
 * go to and from each other without loss (`constructor.ts`) before the core is asked. Nothing is executed: a text is
 * only parsed. Positions are offsets in code points (as the core counts them), `line` and `column` start with 1.
 * The numbers are written as Python writes them (`repr`: `1e-07`, `1e+20`) — the text the core stores is the same.
 * Checked against `core/tests/data/blocks/expr_texts.json` (44 texts with their canonical form, 55 refusals with the
 * place) and the texts of the real answers of the core (`ui/test/filters-constructor.test.ts`). No React.
 */

export const MAX_LENGTH = 4000;
export const MAX_DEPTH = 64;

/** name → [fewest arguments, most arguments or null for any number] */
export const FUNCTIONS: Readonly<Record<string, readonly [number, number | null]>> = {
  ABS: [1, 1],
  SQRT: [1, 1],
  LN: [1, 1],
  LOG10: [1, 1],
  EXP: [1, 1],
  MIN: [2, null],
  MAX: [2, null],
  ROUND: [2, 2],
  FLOOR: [1, 1],
  CEIL: [1, 1],
  CLIP: [3, 3],
  IF: [3, 3],
  ISNULL: [1, 1],
  COALESCE: [2, null],
};
const KEYWORDS = new Set(["AND", "OR", "NOT", "IN"]);
const COMPARE_OPS = ["=", "<>", "<", "<=", ">", ">="];

export type Ex =
  | { k: "num"; v: number }
  | { k: "str"; v: string }
  | { k: "field"; name: string }
  | { k: "neg"; a: Ex }
  | { k: "not"; a: Ex }
  | { k: "bin"; op: string; l: Ex; r: Ex }
  | { k: "cmp"; op: string; l: Ex; r: Ex }
  | { k: "bool"; op: "AND" | "OR"; items: Ex[] }
  | { k: "in"; a: Ex; values: (string | number)[] }
  | { k: "call"; name: string; args: Ex[] };

/** A wrong text: the place and the words of the core (`expr_syntax`: message, hint, pos, line, column, char, reason). */
export interface ExprIssue {
  code: "expr_syntax";
  message: string;
  hint: string;
  detail: string;
  pos: number;
  line: number;
  column: number;
  char: string;
  reason: string;
}

class ExprFail extends Error {
  constructor(readonly issue: ExprIssue) {
    super(issue.message);
  }
}

const isAscii = (s: string) => /^[\x00-\x7f]*$/.test(s);
/** Keywords and function names are matched in ASCII case only (`ın` is not IN). */
export const asciiUpper = (w: string): string => (isAscii(w) ? w.toUpperCase() : w);

function showChar(ch: string): string {
  if (ch === "") return "";
  if (ch === "\n") return "\\n";
  if (ch === "\r") return "\\r";
  if (ch === "\t") return "\\t";
  if (/\s|[\p{C}\p{Zl}\p{Zp}\p{Zs}]/u.test(ch)) return `U+${(ch.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, "0")}`;
  return ch;
}

function issue(cp: readonly string[], posIn: number, detail: string, hint = "", reason = "syntax"): ExprFail {
  const pos = Math.max(0, Math.min(posIn, cp.length));
  const before = cp.slice(0, pos);
  const line = before.filter((c) => c === "\n").length + 1;
  const column = pos - (before.lastIndexOf("\n") + 1) + 1;
  const char = pos < cp.length ? cp[pos] : "";
  const where = char ? `строка ${line}, столбец ${column}, символ «${showChar(char)}»` : `строка ${line}, столбец ${column}, конец выражения`;
  return new ExprFail({ code: "expr_syntax", message: `${detail} (${where}).`, hint, detail, pos, line, column, char, reason });
}

// ---- the scanner ----

type Kind = "num" | "str" | "ident" | "field" | "op" | "(" | ")" | "," | "end";
interface Token {
  kind: Kind;
  value: string | number;
  pos: number;
  end: number;
}

const VALID = "Допустимы числа, поля, строки в одинарных кавычках, операторы + − * / ^ = <> < <= > >=, скобки и запятая.";
const BAD_CHAR_HINTS: Record<string, string> = {
  '"': "Строки пишутся в одинарных кавычках: 'руда'.",
  ";": "Аргументы функций разделяются запятой.",
  "!": "Неравенство пишется «<>», отрицание — NOT.",
  "&": "Логическое «и» пишется AND.",
  "|": "Логическое «или» пишется OR.",
  "{": "Имя поля с пробелами пишется в квадратных скобках: [имя поля].",
  "}": "Имя поля с пробелами пишется в квадратных скобках: [имя поля].",
  ".": "Точка допустима только в числе: 0.5. Имя поля с точкой пишется в квадратных скобках.",
  "%": "Остаток от деления не поддерживается.",
};
const OPERATORS = ["<=", ">=", "<>", "<", ">", "=", "+", "-", "*", "/", "^"];
const NUMBER = /^(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?/;
const isAlpha = (c: string) => /\p{L}/u.test(c);
const isDecimal = (c: string) => /\p{Nd}/u.test(c);
const identStart = (c: string) => isAlpha(c) || c === "_";
const identPart = (c: string) => isAlpha(c) || c === "_" || isDecimal(c);
const isDigit = (c: string | undefined) => c !== undefined && c >= "0" && c <= "9";
const isSpace = (c: string) => /\s/u.test(c);

function tokenize(cp: readonly string[]): Token[] {
  const out: Token[] = [];
  const n = cp.length;
  let i = 0;
  const startsWith = (s: string, at: number) => s.split("").every((ch, k) => cp[at + k] === ch);
  while (i < n) {
    const ch = cp[i];
    if (isSpace(ch)) {
      i++;
    } else if (isDigit(ch) || (ch === "." && isDigit(cp[i + 1]))) {
      const m = NUMBER.exec(cp.slice(i).join(""));
      const len = Array.from(m ? m[0] : "").length;
      const j = i + len;
      if (j < n && (identPart(cp[j]) || cp[j] === ".")) throw issue(cp, i, "Некорректное число", "Число пишется с точкой: 12.5 или 1e-3. Между числом и полем нужен оператор, например 2 * AU.", "bad_number");
      const value = Number(m ? m[0] : "0");
      if (!Number.isFinite(value)) throw issue(cp, i, "Число слишком велико", "Допустимы значения до 1.8e308.", "bad_number");
      out.push({ kind: "num", value, pos: i, end: j });
      i = j;
    } else if (identStart(ch)) {
      let j = i + 1;
      while (j < n && identPart(cp[j])) j++;
      out.push({ kind: "ident", value: cp.slice(i, j).join(""), pos: i, end: j });
      i = j;
    } else if (ch === "'") {
      let j = i + 1;
      const parts: string[] = [];
      for (;;) {
        let k = j;
        while (k < n && cp[k] !== "'") k++;
        if (k >= n) throw issue(cp, i, "Не закрыта строка в одинарных кавычках", "Закройте строку кавычкой «'». Кавычка внутри строки удваивается: 'it''s'.", "unclosed_string");
        parts.push(cp.slice(j, k).join(""));
        if (k + 1 < n && cp[k + 1] === "'") {
          parts.push("'");
          j = k + 2;
          continue;
        }
        j = k + 1;
        break;
      }
      out.push({ kind: "str", value: parts.join(""), pos: i, end: j });
      i = j;
    } else if (ch === "[") {
      let k = i + 1;
      while (k < n && cp[k] !== "]") k++;
      if (k >= n) throw issue(cp, i, "Не закрыта квадратная скобка в имени поля", "Имя поля с пробелами пишется целиком: [Имя поля].", "unclosed_bracket");
      const name = cp.slice(i + 1, k).join("");
      if (!name.trim()) throw issue(cp, i, "Пустое имя поля в квадратных скобках", "Впишите имя поля: [Имя поля].", "syntax");
      out.push({ kind: "field", value: name, pos: i, end: k + 1 });
      i = k + 1;
    } else if (ch === "(" || ch === ")" || ch === ",") {
      out.push({ kind: ch, value: ch, pos: i, end: i + 1 });
      i++;
    } else if (ch === "−") {
      out.push({ kind: "op", value: "-", pos: i, end: i + 1 }); // U+2212, the minus sign of the ТЗ
      i++;
    } else {
      const two = cp.slice(i, i + 2).join("");
      if (two === "==") throw issue(cp, i, "Двойной знак «==» не поддерживается", "Равенство пишется одним знаком: AU = 1.", "bad_char");
      if (two === "!=") throw issue(cp, i, "Знак «!=» не поддерживается", "Неравенство пишется «<>»: AU <> 1.", "bad_char");
      if (two === "**") throw issue(cp, i, "Знак «**» не поддерживается", "Степень пишется знаком ^: AU ^ 2.", "bad_char");
      const op = OPERATORS.find((o) => startsWith(o, i));
      if (!op) throw issue(cp, i, "Недопустимый символ", BAD_CHAR_HINTS[ch] ?? VALID, "bad_char");
      out.push({ kind: "op", value: op, pos: i, end: i + op.length });
      i += op.length;
    }
  }
  out.push({ kind: "end", value: "", pos: n, end: n });
  return out;
}

// ---- the parser ----

const FUNCTION_LIST = Object.keys(FUNCTIONS).join(", ");

class Parser {
  private i = 0;
  private level = 0;
  private height = new Map<Ex, number>();

  constructor(
    private cp: readonly string[],
    private tokens: Token[],
  ) {}

  private peek = (): Token => this.tokens[this.i];
  private next(): Token {
    const t = this.tokens[this.i];
    if (t.kind !== "end") this.i++;
    return t;
  }
  private isWord = (t: Token, w: string) => t.kind === "ident" && asciiUpper(String(t.value)) === w;
  private isOp = (t: Token, ...ops: string[]) => t.kind === "op" && ops.includes(String(t.value));
  private fail = (pos: number, detail: string, hint = "", reason = "syntax") => issue(this.cp, pos, detail, hint, reason);
  private describe = (t: Token) => (t.kind === "end" ? "конец выражения" : `«${this.cp.slice(t.pos, t.end).join("")}»`);

  private enter(t: Token): void {
    this.level++;
    if (this.level > MAX_DEPTH) throw this.fail(t.pos, `Вложенность глубже ${MAX_DEPTH} уровней`, "Упростите выражение или вынесите часть расчёта в промежуточное поле.", "too_deep");
  }
  private leave = () => void this.level--;

  private kids(n: Ex): Ex[] {
    switch (n.k) {
      case "neg":
      case "not":
        return [n.a];
      case "bin":
      case "cmp":
        return [n.l, n.r];
      case "bool":
        return n.items;
      case "in":
        return [n.a];
      case "call":
        return n.args;
      default:
        return [];
    }
  }

  /** Registers the height of a new node; a tree higher than MAX_DEPTH is refused at the operator. */
  private built<T extends Ex>(node: T, opPos: number): T {
    const ks = this.kids(node);
    const height = 1 + Math.max(-1, ...ks.map((c) => this.height.get(c) ?? 0));
    if (height > MAX_DEPTH) throw this.fail(opPos, `Вложенность глубже ${MAX_DEPTH} уровней`, "Слишком длинная цепочка операций: вынесите часть расчёта в промежуточное поле.", "too_deep");
    this.height.set(node, height);
    return node;
  }

  private unexpected(t: Token, expected: string): ExprFail {
    if (t.kind === ")") return this.fail(t.pos, "Лишняя закрывающая скобка", "Проверьте, что скобки идут парами.");
    if (t.kind === ",") return this.fail(t.pos, "Запятая здесь не нужна", "Запятая разделяет аргументы функции; десятичная дробь пишется через точку: 12.5.");
    return this.fail(t.pos, `${expected}, найдено ${this.describe(t)}`, "Между значениями нужен оператор, например *; имя поля с пробелами пишется в [квадратных скобках].");
  }

  run(): Ex {
    if (this.peek().kind === "end") throw this.fail(this.cp.length, "Пустое выражение", "Введите выражение, например AU * 2.", "empty");
    const node = this.or();
    const t = this.peek();
    if (t.kind !== "end") throw this.unexpected(t, "Ожидается оператор или конец выражения");
    return node;
  }

  private or(): Ex {
    const first = this.peek();
    const items = [this.and()];
    while (this.isWord(this.peek(), "OR")) {
      this.next();
      items.push(this.and());
    }
    return items.length === 1 ? items[0] : this.built(boolOf("OR", items), first.pos);
  }

  private and(): Ex {
    const first = this.peek();
    const items = [this.not()];
    while (this.isWord(this.peek(), "AND")) {
      this.next();
      items.push(this.not());
    }
    return items.length === 1 ? items[0] : this.built(boolOf("AND", items), first.pos);
  }

  private not(): Ex {
    const t = this.peek();
    if (this.isWord(t, "NOT")) {
      this.next();
      this.enter(t);
      const a = this.not();
      this.leave();
      return this.built({ k: "not", a }, t.pos);
    }
    return this.comparison();
  }

  private comparison(): Ex {
    const left = this.additive();
    const t = this.peek();
    let node: Ex;
    if (this.isOp(t, ...COMPARE_OPS)) {
      this.next();
      node = this.built({ k: "cmp", op: String(t.value), l: left, r: this.additive() }, t.pos);
    } else if (this.isWord(t, "IN")) {
      this.next();
      node = this.built({ k: "in", a: left, values: this.inList() }, t.pos);
    } else {
      return left;
    }
    const nxt = this.peek();
    if (this.isOp(nxt, ...COMPARE_OPS) || this.isWord(nxt, "IN")) throw this.fail(nxt.pos, "Сравнения нельзя соединять в цепочку", "Соедините два условия через AND: AU > 1 AND AU < 5.", "chained_compare");
    return node;
  }

  private inList(): (string | number)[] {
    const open = this.next();
    if (open.kind !== "(") throw this.fail(open.pos, `После IN нужна скобка «(», найдено ${this.describe(open)}`, "Список значений: поле IN ('A', 'B').");
    const values: (string | number)[] = [];
    for (;;) {
      let t = this.next();
      if (this.isOp(t, "-", "+")) {
        const sign = t.value === "-" ? -1 : 1;
        if (this.peek().kind !== "num") throw this.fail(t.pos, "После знака в списке IN нужно число", "Пример: x IN (-1, 2).");
        t = this.next();
        values.push(sign * Number(t.value));
      } else if (t.kind === "num") {
        values.push(Number(t.value));
      } else if (t.kind === "str") {
        values.push(String(t.value));
      } else {
        throw this.fail(t.pos, `В списке IN допустимы строки и числа, найдено ${this.describe(t)}`, "Пример: поле IN ('A', 'B') или поле IN (1, 2). Поле или выражение в списке не допускается.");
      }
      const nxt = this.next();
      if (nxt.kind === ",") continue;
      if (nxt.kind === ")") return values;
      if (nxt.kind === "end") throw this.fail(open.pos, "Не закрыта скобка списка IN", "Добавьте «)» в конце списка.", "unclosed_paren");
      throw this.unexpected(nxt, "В списке IN ожидается «,» или «)»");
    }
  }

  private additive(): Ex {
    let left = this.term();
    while (this.isOp(this.peek(), "+", "-")) {
      const t = this.next();
      left = this.built({ k: "bin", op: String(t.value), l: left, r: this.term() }, t.pos);
    }
    return left;
  }

  private term(): Ex {
    let left = this.unary();
    while (this.isOp(this.peek(), "*", "/")) {
      const t = this.next();
      left = this.built({ k: "bin", op: String(t.value), l: left, r: this.unary() }, t.pos);
    }
    return left;
  }

  private unary(): Ex {
    const t = this.peek();
    if (this.isOp(t, "-", "+")) {
      this.next();
      this.enter(t);
      const a = this.unary();
      this.leave();
      return t.value === "+" ? a : this.built({ k: "neg", a }, t.pos);
    }
    return this.power();
  }

  private power(): Ex {
    const base = this.primary();
    const t = this.peek();
    if (this.isOp(t, "^")) {
      this.next();
      this.enter(t);
      const exp = this.unary();
      this.leave();
      return this.built({ k: "bin", op: "^", l: base, r: exp }, t.pos);
    }
    return base;
  }

  private primary(): Ex {
    const t = this.next();
    if (t.kind === "num") return this.built({ k: "num", v: Number(t.value) }, t.pos);
    if (t.kind === "str") return this.built({ k: "str", v: String(t.value) }, t.pos);
    if (t.kind === "field") return this.built({ k: "field", name: String(t.value) }, t.pos);
    if (t.kind === "(") {
      this.enter(t);
      const inner = this.or();
      const closing = this.peek();
      if (closing.kind === ")") this.next();
      else if (closing.kind === "end") throw this.fail(t.pos, "Не закрыта скобка", "Добавьте закрывающую скобку «)».", "unclosed_paren");
      else throw this.unexpected(closing, "Ожидается оператор или «)»");
      this.leave();
      return inner;
    }
    if (t.kind === "ident") return this.name(t);
    throw this.operandExpected(t);
  }

  private operandExpected(t: Token): ExprFail {
    if (t.kind === "end") return this.fail(t.pos, "Выражение оборвано: ожидается значение", "Допишите поле, число, строку, функцию или скобку.");
    if (t.kind === ")" || t.kind === ",") return this.fail(t.pos, `Ожидается значение, найдено ${this.describe(t)}`, "Допишите поле, число, строку, функцию или скобку.");
    return this.fail(t.pos, `Ожидается значение (поле, число, строка, функция, скобка), найдено ${this.describe(t)}`, "Перед оператором и после него нужно значение.");
  }

  private name(t: Token): Ex {
    const name = String(t.value);
    const word = asciiUpper(name);
    if (KEYWORDS.has(word)) throw this.fail(t.pos, `Ожидается значение, найдено слово «${name}»`, `Поле с таким именем пишется в квадратных скобках: [${name}].`);
    if (this.peek().kind !== "(") return this.built({ k: "field", name }, t.pos);
    if (!(word in FUNCTIONS)) throw this.fail(t.pos, `Неизвестная функция «${name}»`, `Допустимые функции: ${FUNCTION_LIST}.`, "unknown_function");
    const open = this.next();
    this.enter(open);
    const args: Ex[] = [];
    if (this.peek().kind !== ")") {
      args.push(this.or());
      while (this.peek().kind === ",") {
        this.next();
        args.push(this.or());
      }
    }
    const closing = this.peek();
    if (closing.kind === ")") this.next();
    else if (closing.kind === "end") throw this.fail(open.pos, `Не закрыта скобка функции ${word}`, "Добавьте закрывающую скобку «)».", "unclosed_paren");
    else throw this.unexpected(closing, "Ожидается «,» или «)»");
    this.leave();
    const [low, high] = FUNCTIONS[word];
    if (args.length < low || (high !== null && args.length > high)) {
      const need = high === low ? `${low}` : high === null ? `от ${low}` : `от ${low} до ${high}`;
      throw this.fail(t.pos, `Функция ${word}: аргументов ${args.length}, нужно ${need}`, `Разделяйте аргументы запятой: ${word}(…).`, "arity");
    }
    return this.built({ k: "call", name: word, args }, t.pos);
  }
}

/** AND / OR of two or more operands; a nested one with the same op is merged (both are associative). */
export function boolOf(op: "AND" | "OR", operands: readonly Ex[]): Ex {
  const items: Ex[] = [];
  for (const o of operands) {
    if (o.k === "bool" && o.op === op) items.push(...o.items);
    else items.push(o);
  }
  return { k: "bool", op, items };
}

/** The tree of `text`; a wrong text — `{issue}` with the place (never thrown, nothing is executed). */
export function parseExpr(text: string): { ok: true; node: Ex } | { ok: false; issue: ExprIssue } {
  try {
    const cp = Array.from(text);
    if (cp.length > MAX_LENGTH) {
      throw issue(cp, MAX_LENGTH, `Выражение длиннее ${MAX_LENGTH} символов`, "Разбейте расчёт на два поля: сначала промежуточное, затем итоговое.", "too_long");
    }
    return { ok: true, node: new Parser(cp, tokenize(cp)).run() };
  } catch (e) {
    if (e instanceof ExprFail) return { ok: false, issue: e.issue };
    throw e;
  }
}

// ---- the canonical record ----

/** A number as the parser reads it back, written as Python's `repr` does (1e-07, 1e+20, 0.001, 1.5). */
export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) throw new Error("Число в выражении должно быть конечным.");
  const v = Math.abs(value);
  if (Number.isInteger(v) && v < 1e15) return String(v);
  const [mant, e] = v.toExponential().split("e");
  const exp = Number(e);
  const digits = mant.replace(".", "");
  if (exp >= -4 && exp < 16) {
    if (exp < 0) return `0.${"0".repeat(-exp - 1)}${digits}`;
    const int = digits.padEnd(exp + 1, "0").slice(0, exp + 1);
    const frac = digits.slice(exp + 1);
    return frac ? `${int}.${frac}` : `${int}.0`;
  }
  const m = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
  return `${m}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
}

export const formatString = (v: string): string => `'${v.replace(/'/g, "''")}'`;

function plainName(name: string): boolean {
  const cp = Array.from(name);
  if (!cp.length || !identStart(cp[0])) return false;
  if (!cp.every(identPart)) return false;
  const w = asciiUpper(name);
  return !KEYWORDS.has(w) && !(w in FUNCTIONS);
}

/** A field as the text writes it: the name itself or `[name]`; a name with «]» or an empty one has no record (null). */
export function formatField(name: string): string | null {
  if (plainName(name)) return name;
  if (name.includes("]") || !name.trim()) return null;
  return `[${name}]`;
}

// precedence, low → high; a child with a lower precedence than its place needs parentheses
const [P_OR, P_AND, P_NOT, P_CMP, P_ADD, P_MUL, P_UNARY, P_POW, P_ATOM] = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const BINARY_PREC: Record<string, readonly [number, number, number]> = {
  "+": [P_ADD, P_ADD, P_MUL],
  "-": [P_ADD, P_ADD, P_MUL],
  "*": [P_MUL, P_MUL, P_UNARY],
  "/": [P_MUL, P_MUL, P_UNARY],
  "^": [P_POW, P_ATOM, P_UNARY],
};

const signed = (v: number): string => (v < 0 || Object.is(v, -0) ? "-" : "") + formatNumber(v);

function render(n: Ex): [string, number] {
  switch (n.k) {
    case "num":
      return n.v < 0 || Object.is(n.v, -0) ? [`-${formatNumber(n.v)}`, P_UNARY] : [formatNumber(n.v), P_ATOM];
    case "str":
      return [formatString(n.v), P_ATOM];
    case "field": {
      const f = formatField(n.name);
      if (f === null) throw new Error("Имя поля нельзя записать в выражении: нет «]» и пустых имён.");
      return [f, P_ATOM];
    }
    case "neg":
      return [`-${emit(n.a, P_UNARY)}`, P_UNARY];
    case "not":
      return [`NOT ${emit(n.a, P_NOT)}`, P_NOT];
    case "bin": {
      const [own, left, right] = BINARY_PREC[n.op];
      return [`${emit(n.l, left)} ${n.op} ${emit(n.r, right)}`, own];
    }
    case "cmp":
      return [`${emit(n.l, P_ADD)} ${n.op} ${emit(n.r, P_ADD)}`, P_CMP];
    case "bool": {
      const own = n.op === "OR" ? P_OR : P_AND;
      return [n.items.map((o) => emit(o, own + 1)).join(` ${n.op} `), own];
    }
    case "in":
      return [`${emit(n.a, P_ADD)} IN (${n.values.map((v) => (typeof v === "string" ? formatString(v) : signed(v))).join(", ")})`, P_CMP];
    case "call":
      return [`${n.name}(${n.args.map((a) => emit(a, 0)).join(", ")})`, P_ATOM];
  }
}

function emit(n: Ex, need: number): string {
  const [text, prec] = render(n);
  return prec < need ? `(${text})` : text;
}

/** The canonical text: upper-case words, single spaces, the fewest parentheses; it parses back to the same tree. */
export const exprText = (n: Ex): string => emit(n, 0);

/** Distinct field names in the order of first use (names compare ignoring case, the first spelling is kept). */
export function exprFields(root: Ex): string[] {
  const seen = new Map<string, string>();
  const stack: Ex[] = [root];
  while (stack.length) {
    const n = stack.pop() as Ex;
    if (n.k === "field") {
      const key = n.name.toLowerCase();
      if (!seen.has(key)) seen.set(key, n.name);
    }
    const ks = n.k === "neg" || n.k === "not" ? [n.a] : n.k === "bin" || n.k === "cmp" ? [n.l, n.r] : n.k === "bool" ? n.items : n.k === "in" ? [n.a] : n.k === "call" ? n.args : [];
    for (let i = ks.length - 1; i >= 0; i--) stack.push(ks[i]);
  }
  return [...seen.values()];
}

/** The height of the tree: a leaf is 0, any other node is 1 + the deepest child (at most MAX_DEPTH in a parsed tree). */
export function exprDepth(n: Ex): number {
  const ks = n.k === "neg" || n.k === "not" ? [n.a] : n.k === "bin" || n.k === "cmp" ? [n.l, n.r] : n.k === "bool" ? n.items : n.k === "in" ? [n.a] : n.k === "call" ? n.args : [];
  return ks.length ? 1 + Math.max(...ks.map(exprDepth)) : 0;
}
