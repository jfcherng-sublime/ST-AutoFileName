# Shamefully stolen from https://github.com/sagold/FuzzyFilePath

from typing import Any, Dict
import re
import sublime

ID = "Context"

NEEDLE_SEPARATOR = r">\"\'\(\)\{\}"
NEEDLE_SEPARATOR_BEFORE = r"\"\'\(\{"
NEEDLE_SEPARATOR_AFTER = r"^\"\'\)\}"
NEEDLE_CHARACTERS = r"\.A-Za-z0-9\-\_$"
NEEDLE_INVALID_CHARACTERS = r"\"\'\)=\:\(<>\n\{\}"
DELIMITER = r"\s\:\(\[\=\{"


def get_context(view: sublime.View) -> Dict[str, Any]:
    error = False
    valid = True
    valid_needle = True

    selection = view.sel()
    position = selection[0].begin() if selection else ""

    # regions
    line_region = view.line(position)
    word_region = view.word(position)
    # word (folder/file name) can start with @, ~ or other symbols
    while re.match(r"[^\/\\\s\n\w\'\"`]", view.substr(sublime.Region(word_region.a - 1, word_region.a))):
        word_region = sublime.Region(word_region.a - 1, word_region.b)
    pre_region = sublime.Region(line_region.a, word_region.a)
    post_region = sublime.Region(word_region.b, line_region.b)

    # text
    line = view.substr(line_region)
    word = view.substr(word_region)
    pre = view.substr(pre_region)
    post = view.substr(post_region)

    # word can equal "('./')" or "'./'" when the path contains only a special characters, like require('./')
    enquoted_symbols_match = re.search(r'(\(?[\'"`])(\W+)([\'"`]\)?)', word)
    if enquoted_symbols_match:
        word = enquoted_symbols_match.group(2)
        pre = pre + enquoted_symbols_match.group(1)
        post = post + enquoted_symbols_match.group(3)

    word = word.rstrip("\"';)]}")

    error = re.search("[" + NEEDLE_INVALID_CHARACTERS + "]", word)

    # grab everything in 'separators'
    needle = ""
    separator = False
    pre_match = ""
    # search for a separator before current word, i.e. <">path/to/<position>
    pre_quotes = re.search("([" + NEEDLE_SEPARATOR_BEFORE + "])([^" + NEEDLE_SEPARATOR + "]*)$", pre)

    if pre_quotes:
        needle += pre_quotes.group(2) + word
        separator = pre_quotes.group(1)
        pre_match = pre_quotes.group(2)
    else:
        # use whitespace as separator
        pre_quotes = re.search(r"(\s)([^" + NEEDLE_SEPARATOR + r"\s]*)$", pre)
        if pre_quotes:
            needle = pre_quotes.group(2) + word
            separator = pre_quotes.group(1)
            pre_match = pre_quotes.group(2)

    if pre_quotes:
        post_quotes = re.search("^([" + NEEDLE_SEPARATOR_AFTER + "]*)", post)
        if post_quotes:
            needle += post_quotes.group(1)
        else:
            valid = False
    elif not re.search("[" + NEEDLE_INVALID_CHARACTERS + "]", needle):
        needle = pre + word
    else:
        needle = word

    # grab prefix
    prefix_region = sublime.Region(line_region.a, pre_region.b - len(pre_match) - 1)
    prefix_line = view.substr(prefix_region)

    # define? (["...", "..."]) -> before?
    # before: ABC =:([
    prefix = re.search(r"\s*([" + NEEDLE_CHARACTERS + "]+)[" + DELIMITER + "]*$", prefix_line)
    if prefix is None:
        # validate array, like define(["...", ".CURSOR."])
        prefix = re.search(r"^\s*([" + NEEDLE_CHARACTERS + "]+)[" + DELIMITER + "]+", prefix_line)

    if prefix:
        prefix = prefix.group(1)

    if separator is False:
        valid_needle = False
        valid = False
    elif re.search("[" + NEEDLE_INVALID_CHARACTERS + "]", needle):
        valid_needle = False
        valid = False
    elif prefix is None and separator.strip() == "":
        valid = False

    return {
        "is_valid": valid and valid_needle and not error,
        "word": word,
        "prefix": prefix,
    }
