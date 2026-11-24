"""Recursive-descent parser of the calculator language (ТЗ 8.7.4): text → tree of `nodes`. Nothing is executed.

    or_expr    := and_expr { OR and_expr }
    and_expr   := not_expr { AND not_expr }
    not_expr   := NOT not_expr | comparison
    comparison := additive [ (= | <> | < | <= | > | >=) additive | IN ( literal {, literal} ) ]
    additive   := term { (+ | -) term }
    term       := unary { (* | /) unary }
    unary      := (- | +) unary | power
    power      := primary [ ^ unary ]                  (right-associative: 2^3^2 = 2^9, -2^2 = -4)
    primary    := number | 'string' | field | [field name] | NAME ( or_expr {, or_expr} ) | ( or_expr )

A name followed by `(` must be one of FUNCTIONS (anything else — `__import__('os')` included — is a syntax error at
the name); a name without `(` is a field. Limits: MAX_LENGTH characters, MAX_DEPTH levels (nested parentheses, calls,
unary signs, `^` and the height of the tree), checked while parsing, so a hostile text cannot exhaust the stack.
"""

from __future__ import annotations

from .errors import ExprError, ExprLimitError
from .lexer import COMMA, END, FIELD, IDENT, LPAREN, NUM, OP, RPAREN, STR, Token, tokenize
from .nodes import (
    COMPARE_OPS,
    FUNCTIONS,
    KEYWORDS,
    MAX_DEPTH,
    MAX_LENGTH,
    BinOp,
    BoolOp,
    Call,
    Compare,
    Expr,
    Field,
    In,
    Neg,
    Not,
    Num,
    Str,
    ascii_upper,
)

_FUNCTION_LIST = ", ".join(FUNCTIONS)


def parse(text: str) -> Expr:
    """The tree of `text`. Raises ExprError (ExprLimitError for the length and depth limits), never runs anything."""
    if not isinstance(text, str):
        raise TypeError("Выражение должно быть строкой.")
    if len(text) > MAX_LENGTH:
        raise ExprLimitError(text, MAX_LENGTH, f"Выражение длиннее {MAX_LENGTH} символов",
                             "Разбейте расчёт на два поля: сначала промежуточное, затем итоговое.", "too_long")
    return _Parser(text, tokenize(text)).run()


class _Parser:
    def __init__(self, text: str, tokens: list[Token]) -> None:
        self.text = text
        self.tokens = tokens
        self.i = 0
        self.level = 0
        self._height: dict[int, int] = {}
        self._keep: list[Expr] = []  # every node stays alive, so `id` is a safe key of `_height`

    # -- helpers ---------------------------------------------------------------------------------------------

    def _peek(self) -> Token:
        return self.tokens[self.i]

    def _next(self) -> Token:
        tok = self.tokens[self.i]
        if tok.kind != END:
            self.i += 1
        return tok

    def _is_word(self, tok: Token, word: str) -> bool:
        return tok.kind == IDENT and ascii_upper(str(tok.value)) == word

    def _is_op(self, tok: Token, *ops: str) -> bool:
        return tok.kind == OP and tok.value in ops

    def _fail(self, pos: int, detail: str, hint: str = "", reason: str = "syntax") -> ExprError:
        return ExprError(self.text, pos, detail, hint, reason)

    def _describe(self, tok: Token) -> str:
        if tok.kind == END:
            return "конец выражения"
        return f"«{self.text[tok.pos : tok.end]}»"

    def _enter(self, tok: Token) -> None:
        self.level += 1
        if self.level > MAX_DEPTH:
            raise ExprLimitError(self.text, tok.pos, f"Вложенность глубже {MAX_DEPTH} уровней",
                                 "Упростите выражение или вынесите часть расчёта в промежуточное поле.", "too_deep")

    def _leave(self) -> None:
        self.level -= 1

    def _built(self, node: Expr, op_pos: int) -> Expr:
        """Registers the height of a new node; a tree higher than MAX_DEPTH is refused at the operator."""
        height = 1 + max((self._height.get(id(c), 0) for c in node.children()), default=-1)
        if height > MAX_DEPTH:
            raise ExprLimitError(self.text, op_pos, f"Вложенность глубже {MAX_DEPTH} уровней",
                                 "Слишком длинная цепочка операций: вынесите часть расчёта в промежуточное поле.",
                                 "too_deep")
        self._keep.append(node)
        self._height[id(node)] = height
        return node

    def _unexpected(self, tok: Token, expected: str) -> ExprError:
        if tok.kind == RPAREN:
            return self._fail(tok.pos, "Лишняя закрывающая скобка", "Проверьте, что скобки идут парами.")
        if tok.kind == COMMA:
            return self._fail(tok.pos, "Запятая здесь не нужна",
                              "Запятая разделяет аргументы функции; десятичная дробь пишется через точку: 12.5.")
        return self._fail(tok.pos, f"{expected}, найдено {self._describe(tok)}",
                          "Между значениями нужен оператор, например *; имя поля с пробелами пишется в [квадратных "
                          "скобках].")

    # -- grammar ---------------------------------------------------------------------------------------------

    def run(self) -> Expr:
        if self._peek().kind == END:
            raise self._fail(len(self.text), "Пустое выражение", "Введите выражение, например AU * 2.", "empty")
        node = self._or()
        tok = self._peek()
        if tok.kind != END:
            raise self._unexpected(tok, "Ожидается оператор или конец выражения")
        return node

    def _or(self) -> Expr:
        first = self._peek()
        items = [self._and()]
        while self._is_word(self._peek(), "OR"):
            self._next()
            items.append(self._and())
        if len(items) == 1:
            return items[0]
        return self._built(BoolOp.of("OR", items, first.pos), first.pos)

    def _and(self) -> Expr:
        first = self._peek()
        items = [self._not()]
        while self._is_word(self._peek(), "AND"):
            self._next()
            items.append(self._not())
        if len(items) == 1:
            return items[0]
        return self._built(BoolOp.of("AND", items, first.pos), first.pos)

    def _not(self) -> Expr:
        tok = self._peek()
        if self._is_word(tok, "NOT"):
            self._next()
            self._enter(tok)
            operand = self._not()
            self._leave()
            return self._built(Not(operand, tok.pos), tok.pos)
        return self._comparison()

    def _comparison(self) -> Expr:
        left = self._additive()
        tok = self._peek()
        if self._is_op(tok, *COMPARE_OPS):
            self._next()
            node: Expr = self._built(Compare(str(tok.value), left, self._additive(), left.pos), tok.pos)
        elif self._is_word(tok, "IN"):
            self._next()
            node = self._built(In(left, self._in_list(), left.pos), tok.pos)
        else:
            return left
        nxt = self._peek()
        if self._is_op(nxt, *COMPARE_OPS) or self._is_word(nxt, "IN"):
            raise self._fail(nxt.pos, "Сравнения нельзя соединять в цепочку",
                             "Соедините два условия через AND: AU > 1 AND AU < 5.", "chained_compare")
        return node

    def _in_list(self) -> tuple[str | float, ...]:
        open_tok = self._next()
        if open_tok.kind != LPAREN:
            raise self._fail(open_tok.pos, f"После IN нужна скобка «(», найдено {self._describe(open_tok)}",
                             "Список значений: поле IN ('A', 'B').")
        values: list[str | float] = []
        while True:
            tok = self._next()
            sign = 1.0
            if self._is_op(tok, "-", "+"):
                sign = -1.0 if tok.value == "-" else 1.0
                if self._peek().kind != NUM:
                    raise self._fail(tok.pos, "После знака в списке IN нужно число", "Пример: x IN (-1, 2).")
                tok = self._next()
                values.append(sign * float(tok.value))
            elif tok.kind == NUM:
                values.append(float(tok.value))
            elif tok.kind == STR:
                values.append(str(tok.value))
            else:
                raise self._fail(tok.pos, f"В списке IN допустимы строки и числа, найдено {self._describe(tok)}",
                                 "Пример: поле IN ('A', 'B') или поле IN (1, 2). Поле или выражение в списке не "
                                 "допускается.")
            nxt = self._next()
            if nxt.kind == COMMA:
                continue
            if nxt.kind == RPAREN:
                return tuple(values)
            if nxt.kind == END:
                raise self._fail(open_tok.pos, "Не закрыта скобка списка IN", "Добавьте «)» в конце списка.",
                                 "unclosed_paren")
            raise self._unexpected(nxt, "В списке IN ожидается «,» или «)»")

    def _additive(self) -> Expr:
        left = self._term()
        while self._is_op(self._peek(), "+", "-"):
            tok = self._next()
            left = self._built(BinOp(str(tok.value), left, self._term(), left.pos), tok.pos)
        return left

    def _term(self) -> Expr:
        left = self._unary()
        while self._is_op(self._peek(), "*", "/"):
            tok = self._next()
            left = self._built(BinOp(str(tok.value), left, self._unary(), left.pos), tok.pos)
        return left

    def _unary(self) -> Expr:
        tok = self._peek()
        if self._is_op(tok, "-", "+"):
            self._next()
            self._enter(tok)
            operand = self._unary()
            self._leave()
            if tok.value == "+":
                return operand
            return self._built(Neg(operand, tok.pos), tok.pos)
        return self._power()

    def _power(self) -> Expr:
        base = self._primary()
        tok = self._peek()
        if self._is_op(tok, "^"):
            self._next()
            self._enter(tok)
            exponent = self._unary()
            self._leave()
            return self._built(BinOp("^", base, exponent, base.pos), tok.pos)
        return base

    def _primary(self) -> Expr:
        tok = self._next()
        if tok.kind == NUM:
            return self._built(Num(float(tok.value), tok.pos), tok.pos)
        if tok.kind == STR:
            return self._built(Str(str(tok.value), tok.pos), tok.pos)
        if tok.kind == FIELD:
            return self._built(Field(str(tok.value), tok.pos), tok.pos)
        if tok.kind == LPAREN:
            self._enter(tok)
            inner = self._or()
            closing = self._peek()
            if closing.kind == RPAREN:
                self._next()
            elif closing.kind == END:
                raise self._fail(tok.pos, "Не закрыта скобка", "Добавьте закрывающую скобку «)».", "unclosed_paren")
            else:
                raise self._unexpected(closing, "Ожидается оператор или «)»")
            self._leave()
            return inner
        if tok.kind == IDENT:
            return self._name(tok)
        raise self._operand_expected(tok)

    def _operand_expected(self, tok: Token) -> ExprError:
        if tok.kind == END:
            return self._fail(tok.pos, "Выражение оборвано: ожидается значение",
                              "Допишите поле, число, строку, функцию или скобку.")
        if tok.kind in (RPAREN, COMMA):
            return self._fail(tok.pos, f"Ожидается значение, найдено {self._describe(tok)}",
                              "Допишите поле, число, строку, функцию или скобку.")
        return self._fail(tok.pos, f"Ожидается значение (поле, число, строка, функция, скобка), найдено "
                          f"{self._describe(tok)}", "Перед оператором и после него нужно значение.")

    def _name(self, tok: Token) -> Expr:
        name = str(tok.value)
        word = ascii_upper(name)
        if word in KEYWORDS:
            raise self._fail(tok.pos, f"Ожидается значение, найдено слово «{name}»",
                             f"Поле с таким именем пишется в квадратных скобках: [{name}].")
        if self._peek().kind != LPAREN:
            return self._built(Field(name, tok.pos), tok.pos)
        if word not in FUNCTIONS:
            raise self._fail(tok.pos, f"Неизвестная функция «{name}»", f"Допустимые функции: {_FUNCTION_LIST}.",
                             "unknown_function")
        open_tok = self._next()
        self._enter(open_tok)
        args: list[Expr] = []
        if self._peek().kind != RPAREN:
            args.append(self._or())
            while self._peek().kind == COMMA:
                self._next()
                args.append(self._or())
        closing = self._peek()
        if closing.kind == RPAREN:
            self._next()
        elif closing.kind == END:
            raise self._fail(open_tok.pos, f"Не закрыта скобка функции {word}", "Добавьте закрывающую скобку «)».",
                             "unclosed_paren")
        else:
            raise self._unexpected(closing, "Ожидается «,» или «)»")
        self._leave()
        low, high = FUNCTIONS[word]
        if len(args) < low or (high is not None and len(args) > high):
            need = f"{low}" if high == low else (f"от {low}" if high is None else f"от {low} до {high}")
            raise self._fail(tok.pos, f"Функция {word}: аргументов {len(args)}, нужно {need}",
                             f"Разделяйте аргументы запятой: {word}(…).", "arity")
        return self._built(Call(word, tuple(args), tok.pos), tok.pos)
