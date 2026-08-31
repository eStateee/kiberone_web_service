"""
Склонение русских имён в родительный падеж.

Нужно для фраз вида «у Валерии День Рождения»: без склонения получается
«у Валерия», и текст читается как ошибка.

Правила покрывают распространённые русские имена. Если имя не подходит
ни под одно правило (заимствованное, несклоняемое, необычное окончание),
оно возвращается без изменений — лучше исходная форма, чем неверная.
"""

# После шипящих и заднеязычных вместо «ы» пишется «и»:
# Ольга -> Ольги, Даша -> Даши, но Анна -> Анны.
_HISSING_AND_BACK = "гкхжчшщ"

# Имена на эти буквы не склоняются: Отто, Мари, Нелли, Дэвиду и т.п.
_INDECLINABLE_ENDINGS = ("о", "е", "ё", "э", "у", "ю", "и", "ы")

# Женские имена на мягкий знак склоняются как существительные 3-го склонения:
# Любовь -> Любови, а не Любовя.
_SOFT_SIGN_FEMININE = frozenset({"любовь", "нинель", "адель", "рахиль", "эсфирь", "ассоль"})

# Имена с беглой гласной — общее правило на них не работает.
_IRREGULAR = {
    "пётр": "петра",
    "петр": "петра",
    "лев": "льва",
    "павел": "павла",
}


def _restore_case(source: str, result: str) -> str:
    """Возвращает результату регистр первой буквы исходного имени."""
    if source[:1].isupper():
        return result[:1].upper() + result[1:]
    return result


def _is_cyrillic(word: str) -> bool:
    """Правила рассчитаны на кириллицу, латиницу склонять нельзя."""
    return all("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in word)


def _one_word_to_genitive(word: str) -> str:
    """Склоняет одно слово. Логика вынесена отдельно ради составных имён."""
    if len(word) < 2 or not _is_cyrillic(word):
        return word

    lower = word.lower()

    if lower in _IRREGULAR:
        return _restore_case(word, _IRREGULAR[lower])

    if lower.endswith(_INDECLINABLE_ENDINGS):
        return word

    stem, last = word[:-1], lower[-1]

    if last == "а":
        # Ольга -> Ольги, Анна -> Анны
        ending = "и" if stem[-1:].lower() in _HISSING_AND_BACK else "ы"
        return stem + ending

    if last == "я":
        # Валерия -> Валерии, Илья -> Ильи, Настя -> Насти
        return stem + "и"

    if last == "й":
        # Андрей -> Андрея, Дмитрий -> Дмитрия
        return stem + "я"

    if last == "ь":
        # Любовь -> Любови, но Игорь -> Игоря
        return stem + ("и" if lower in _SOFT_SIGN_FEMININE else "я")

    # Оканчивается на согласную: Иван -> Ивана, Максим -> Максима
    return word + "а"


def to_genitive(name: str) -> str:
    """
    Переводит имя в родительный падеж: «Валерия» -> «Валерии».

    Составные имена через дефис склоняются по частям:
    «Анна-Мария» -> «Анны-Марии».
    """
    if not name:
        return name

    return "-".join(_one_word_to_genitive(part) for part in name.split("-"))


def get_first_name(full_name: str) -> str:
    """
    Достаёт имя из ФИО. В CRM данные хранятся как «Фамилия Имя Отчество»,
    поэтому имя — второе слово.

    Если слов меньше двух, возвращает строку целиком: пусть лучше в
    сообщении будет как есть, чем задача упадёт на неполных данных.
    """
    parts = (full_name or "").split()
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else ""
