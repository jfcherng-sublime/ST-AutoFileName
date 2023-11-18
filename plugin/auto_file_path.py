from __future__ import annotations

import ctypes
import itertools
import os
import re
import string
import time
from typing import Any

import sublime
import sublime_plugin

from .context import get_context
from .libs.filesize import naturalsize
from .libs.image_info import getImageInfo

g_auto_completions: list[sublime.CompletionItem] = []
MAXIMUM_WAIT_TIME = 0.3


def get_setting(string, view: sublime.View | None = None) -> Any:
    if view and view.settings().get(string):
        return view.settings().get(string)
    else:
        return sublime.load_settings("AutoFilePath.sublime-settings").get(string)


def get_cur_scope_settings(view: sublime.View):
    selection = view.sel()[0].a
    current_scope_str = view.scope_name(selection)

    all_scopes_settings = get_setting("afp_scopes", view)
    for scope_settings in all_scopes_settings:
        if re.search(scope_settings.get("scope"), current_scope_str):
            return scope_settings


def apply_alias_replacements(entered_path, aliases) -> str | None:
    project_root = sublime.active_window().folders()[0]
    replacers = [("<project_root>", project_root)]

    result_path = entered_path
    for alias in aliases:
        alias_regex = re.compile(alias[0])
        alias_target = alias[1]
        if not re.match(alias_regex, result_path):
            continue

        for replacer in replacers:
            alias_target = alias_target.replace(replacer[0], replacer[1])

        result_path = re.sub(alias_regex, alias_target, result_path)

    return result_path if result_path != entered_path else None


def apply_post_replacements(view, insertion_text: str) -> str:
    cur_scope_settings = get_cur_scope_settings(view)
    if cur_scope_settings:
        replace_on_insert_setting = cur_scope_settings.get("replace_on_insert")
        if replace_on_insert_setting:
            for replace in replace_on_insert_setting:
                insertion_text = re.sub(replace[0], replace[1], insertion_text)
    return insertion_text


class AfpShowFilenames(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        FileNameComplete.is_active = True
        self.view.run_command("auto_complete", {"disable_auto_insert": True, "next_completion_if_showing": False})


class AfpSettingsPanel(sublime_plugin.WindowCommand):
    def run(self) -> None:
        use_pr = "✗ Stop using project root" if get_setting("afp_use_project_root") else "✓ Use Project Root"
        use_dim = (
            "✗ Disable HTML Image Dimension insertion"
            if get_setting("afp_insert_dimensions")
            else "✓ Auto-insert Image Dimensions in HTML"
        )
        p_root = get_setting("afp_proj_root")

        menu = [[use_pr, p_root], [use_dim, '<img src="_path_" width = "x" height = "y" >']]
        self.window.show_quick_panel(menu, self.on_done)

    def on_done(self, value) -> None:
        settings = sublime.load_settings("AutoFilePath.sublime-settings")
        if value == 0:
            use_pr = settings.get("afp_use_project_root")
            settings.set("afp_use_project_root", not use_pr)
        if value == 1:
            use_dim = settings.get("afp_use_project_root")
            settings.set("afp_use_project_root", not use_dim)


# Used to remove the / or \ when autocompleting a Windows drive (eg. /C:/path)
class AfpDeletePrefixedSlash(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        selection = self.view.sel()[0].a
        length = 5 if (self.view.substr(sublime.Region(selection - 5, selection - 3)) == "\\\\") else 4
        reg = sublime.Region(selection - length, selection - 3)
        self.view.erase(edit, reg)


# inserts width and height dimensions into img tags. HTML only
class InsertDimensionsCommand(sublime_plugin.TextCommand):
    this_dir = ""

    def insert_dimension(self, edit: sublime.Edit, dim: int, name: str, tag_scope: sublime.Region) -> None:
        view = self.view
        selection = view.sel()[0].a

        if name in view.substr(tag_scope):
            reg = view.find("(?<=" + name + r'=)\s*"\d{1,5}', tag_scope.a)
            view.replace(edit, reg, '"' + str(dim))
        else:
            dimension = str(dim)
            view.insert(edit, selection + 1, " " + name + '="' + dimension + '"')

    def insert_dimensions(self, edit: sublime.Edit, scope: sublime.Region, w: int, h: int) -> None:
        view = self.view

        if get_setting("afp_insert_width_first", view):
            self.insert_dimension(edit, h, "height", scope)
            self.insert_dimension(edit, w, "width", scope)
        else:
            self.insert_dimension(edit, w, "width", scope)
            self.insert_dimension(edit, h, "height", scope)

    # determines if there is a template tag in a given region.  supports HTML and template languages.
    def is_img_tag_in_region(self, region: sublime.Region) -> bool:
        view = self.view

        # handle template languages but template languages like slim may also contain HTML so
        # we do a check for that as well
        return view.substr(region).strip().startswith("img") or "<img" in view.substr(region)

    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        view.run_command("commit_completion")
        selection = view.sel()[0].a

        if "html" not in view.scope_name(selection):
            return
        scope = view.extract_scope(selection - 1)

        # if using a template language, the scope is set to the current line
        tag_scope = (
            view.line(selection) if get_setting("afp_template_languages", view) else view.extract_scope(scope.a - 1)
        )

        path = view.substr(scope)
        if path.startswith(("'", '"', "(")):
            path = path[1:-1]

        path = path[path.rfind(FileNameComplete.sep) :] if FileNameComplete.sep in path else path
        full_path = self.this_dir + path

        if self.is_img_tag_in_region(tag_scope) and path.endswith((".png", ".jpg", ".jpeg", ".gif")):
            with open(full_path, "rb") as r:
                read_data = r.read() if path.endswith((".jpg", ".jpeg")) else r.read(24)
            w, h = getImageInfo(read_data)

            self.insert_dimensions(edit, tag_scope, w, h)


# When backspacing through a path, selects the previous path component
class ReloadAutoCompleteCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        view.run_command("hide_auto_complete")
        view.run_command("left_delete")
        selection = view.sel()[0].a

        scope = view.extract_scope(selection - 1)
        scope_text = view.substr(scope)
        slash_pos = scope_text[: selection - scope.a].rfind(FileNameComplete.sep)
        slash_pos += 1 if slash_pos < 0 else 0

        region = sublime.Region(scope.a + slash_pos + 1, selection)
        view.sel().add(region)


def enable_autocomplete() -> None:
    """
    Used externally by other packages which want to autocomplete file paths
    """
    FileNameComplete.is_forced = True


def disable_autocomplete() -> None:
    """
    Used externally by other packages which want to autocomplete file paths
    """
    FileNameComplete.is_forced = False


class FileNameComplete(sublime_plugin.ViewEventListener):
    is_forced = False
    is_active = False
    sep = "/"

    def __init__(self, view: sublime.View) -> None:
        super().__init__(view)
        self.showing_win_drives = False

    def on_activated(self) -> None:
        self.showing_win_drives = False
        self.sep = "/"
        self.is_active = False

    def on_query_context(self, key: str, operator: str, operand: str, match_all: bool) -> bool:
        view = self.view

        if key == "afp_deleting_slash":  # for reloading autocomplete
            selection = view.sel()[0]
            valid = self.at_path_end(view) and selection.empty() and view.substr(selection.a - 1) == self.sep
            return valid == operand

        if key == "afp_use_keybinding":
            return get_setting("afp_use_keybinding", view) == operand

        return False

    def on_query_completions(
        self,
        prefix: str,
        locations: list[int],
    ) -> tuple[list[sublime.CompletionItem], int] | None:
        view = self.view
        is_always_enabled = not self.get_setting("afp_use_keybinding", view)

        if not (is_always_enabled or self.is_forced or self.is_active):
            return
        caret = view.sel()[0].a

        valid_scopes = self.get_setting("afp_valid_scopes", view)
        blacklist = self.get_setting("afp_blacklist_scopes", view)

        if (
            # ...
            not any(view.match_selector(caret, scope) for scope in valid_scopes)
            or any(view.match_selector(caret, scope) for scope in blacklist)
        ):
            return

        self.view = view
        self.caret = caret

        self.start_time = time.time()
        self.add_completions()

        return g_auto_completions, sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS

    def on_modified_async(self) -> None:
        view = self.view
        selections = view.sel()

        if len(selections) != 1:
            return

        caret = selections[0].a
        prefix = view.substr(sublime.Region(caret - 4, caret))

        if self.showing_win_drives and re.match(r"^/[a-zA-Z]:[/\\]", prefix):
            self.showing_win_drives = False
            view.run_command("afp_delete_prefixed_slash")

    def on_selection_modified_async(self) -> None:
        view = self.view

        if not view.window():
            return

        file_name = view.file_name()

        # Open autocomplete automatically if keybinding mode is used
        if not (self.is_forced or self.is_active):
            return

        if not len(sel := view.sel()):
            return
        region = sel[0]

        # if selection.empty() and self.at_path_end(view):
        if region.empty():
            scope_contents = view.substr(view.extract_scope(region.a - 1))
            extracted_path = scope_contents.replace("\r\n", "\n").split("\n")[0]

            if "\\" in extracted_path and "/" not in extracted_path:
                self.sep = "\\"
            else:
                self.sep = "/"

            if view.substr(region.a - 1) == self.sep or len(view.extract_scope(region.a)) < 3 or not file_name:
                view.run_command("auto_complete", {"disable_auto_insert": True, "next_completion_if_showing": False})

        else:
            self.is_active = False

    def at_path_end(self, view: sublime.View) -> bool:
        selection = view.sel()[0]
        name = view.scope_name(selection.a)

        if selection.empty() and ("string.end" in name or "string.quoted.end.js" in name):
            return True

        if ".css" in name and view.substr(selection.a) == ")":
            return True

        return False

    def prepare_completion(self, view: sublime.View, this_dir: str, directory: str) -> sublime.CompletionItem:
        path = os.path.join(this_dir, directory)

        annotation = ""
        annotation_head = ""
        annotation_head_kind = sublime.KIND_ID_AMBIGUOUS
        details_head = ""
        details_parts = []

        if os.path.isdir(path):
            annotation = "Dir"
            annotation_head = "📁"
            annotation_head_kind = sublime.KIND_ID_MARKUP
            details_head = "Directory"
        elif os.path.isfile(path):
            annotation = "File"
            annotation_head = "📄"
            annotation_head_kind = sublime.KIND_ID_MARKUP
            details_head = "File"
            details_parts.append("Size: " + naturalsize(os.stat(path).st_size))

        if path.endswith((".gif", ".jpeg", ".jpg", ".png")):
            details_head = "Image"

            with open(path, "rb") as f:
                read_data = f.read() if path.endswith((".jpeg", ".jpg")) else f.read(24)

            try:
                w, h = getImageInfo(read_data)
                details_parts.extend((f"Height: {h}", f"Width: {w}"))
            except Exception:
                pass

        return sublime.CompletionItem(
            trigger=directory,
            annotation=annotation,
            completion=apply_post_replacements(view, directory),
            kind=(annotation_head_kind, annotation_head, details_head),
            details=", ".join(details_parts),
        )

    def get_entered_path(self, view: sublime.View, selection: int) -> str:
        scope_contents = view.substr(view.extract_scope(selection - 1)).strip()
        cur_path = scope_contents.replace("\r\n", "\n").split("\n")[0]

        if cur_path.startswith(("'", '"', "(")):
            cur_path = cur_path[1:-1]

        return cur_path

    def get_cur_path(self, view: sublime.View, selection: int) -> str:
        cur_path = self.get_entered_path(view, selection)
        return cur_path[: cur_path.rfind(self.sep) + 1] if self.sep in cur_path else ""

    def get_setting(self, key: str, view: sublime.View | None = None) -> Any:
        if view and view.settings().get(key):
            return view.settings().get(key)

        else:
            return sublime.load_settings("AutoFilePath.sublime-settings").get(key)

    def add_drives(self) -> None:
        if sublime.platform() != "windows":
            return

        drive_bitmask = ctypes.cdll.kernel32.GetLogicalDrives()
        drive_list = list(
            itertools.compress(string.ascii_uppercase, map(lambda x: ord(x) - ord("0"), bin(drive_bitmask)[:1:-1]))
        )

        # Overrides default auto completion
        # https://github.com/BoundInCode/AutoFileName/issues/18
        for driver in drive_list:
            g_auto_completions.append(
                sublime.CompletionItem(
                    trigger=f"{driver}:{self.sep}",
                    annotation="Drive",
                    completion=f"{driver}:{self.sep}",
                    kind=(sublime.KIND_ID_MARKUP, "🖴", "Drive"),
                    details="",
                )
            )

            if time.time() - self.start_time > MAXIMUM_WAIT_TIME:
                return

    def add_completions(self) -> None:
        g_auto_completions.clear()

        ctx = get_context(self.view)
        if not ctx["is_valid"]:
            return

        scope_settings = get_cur_scope_settings(self.view)
        if scope_settings and scope_settings.get("prefixes") and ctx["prefix"]:
            if ctx["prefix"] not in scope_settings.get("prefixes"):
                return

        file_name = self.view.file_name()
        is_proj_rel = self.get_setting("afp_use_project_root", self.view)

        this_dir = ""
        cur_path = os.path.expanduser(self.get_cur_path(self.view, self.caret))

        if cur_path.startswith("\\\\") and not cur_path.startswith("\\\\\\") and sublime.platform() == "windows":
            self.showing_win_drives = True
            self.add_drives()
            return
        elif cur_path.startswith(("/", "\\")):
            if is_proj_rel and file_name:
                proot = self.get_setting("afp_proj_root", self.view)
                if proot:
                    if not file_name and not os.path.isabs(proot):
                        proot = "/"
                    cur_path = os.path.join(proot, cur_path[1:])
                for f in sublime.active_window().folders():
                    if f in file_name:
                        this_dir = os.path.join(f, cur_path.lstrip("/\\"))
        elif not file_name:
            this_dir = cur_path
        else:
            this_dir = os.path.split(file_name)[0]
            this_dir = os.path.join(this_dir, cur_path)

            if scope_settings and scope_settings.get("aliases"):
                entered_path = self.get_entered_path(self.view, self.caret)
                result_path = apply_alias_replacements(entered_path, scope_settings.get("aliases"))
                if result_path:
                    this_dir = re.sub(r"[^/]+$", "", result_path)

        try:
            if os.path.isabs(cur_path) and (not is_proj_rel or not this_dir):
                if sublime.platform() == "windows" and len(self.view.extract_scope(self.caret)) < 4:
                    self.showing_win_drives = True
                    self.add_drives()
                    return

                if sublime.platform() != "windows":
                    this_dir = cur_path

            self.showing_win_drives = False
            dir_files = os.listdir(this_dir)

            now = time.time()

            for directory in dir_files:
                if directory.startswith("."):
                    continue

                if "." not in directory:
                    directory += self.sep

                g_auto_completions.append(self.prepare_completion(self.view, this_dir, directory))
                InsertDimensionsCommand.this_dir = this_dir

                if now - self.start_time > MAXIMUM_WAIT_TIME:
                    return

        except OSError:
            pass
