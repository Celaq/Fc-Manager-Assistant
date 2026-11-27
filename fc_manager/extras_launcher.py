"""Find the single visible FC Online start button inside Nexon Plug using UIA."""

import re

_GAME = re.compile(r"FC (?:ONLINE|온라인)", re.IGNORECASE)
_START = re.compile(r"(?:(FC (?:ONLINE|온라인))\s*)?게임\s*시작", re.IGNORECASE)
_OTHER_START = re.compile(r"게임\s*시작$")


def _fc_button(root, button):
    name = button.element_info.name
    match = _START.fullmatch(name)
    if not match or not button.is_visible():
        return False
    if match.group(1):
        return True
    card = button
    for _ in range(3):
        card = card.parent()
        if card is None or card == root:
            break
        if card.element_info.control_type not in ("Group", "Pane", "Custom"):
            continue
        starts = [item for item in card.descendants(control_type="Button")
                  if item.is_visible() and _OTHER_START.search(item.element_info.name)]
        if len(starts) != 1:
            break
        if _GAME.fullmatch(card.element_info.name):
            return True
        if any(item.is_visible() and _GAME.fullmatch(item.element_info.name)
               for item in card.children(control_type="Text")):
            return True
    return False


def _select_start(roots):
    candidates = [button for root in roots for button in root.descendants(control_type="Button")
                  if _fc_button(root, button)]
    if len(candidates) == 1 and candidates[0].is_enabled():
        return candidates[0]
    return None


def invoke_nexon_start(launch_blocked):
    import psutil
    import pythoncom

    pythoncom.CoInitialize()
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        roots = [window for process in psutil.process_iter(["name"])
                 if (process.info["name"] or "").casefold() == "nexonplug.exe"
                 for window in desktop.windows(process=process.pid, visible_only=True)]
        button = _select_start(roots)
        if button is None or not button.is_visible() or not button.is_enabled() or launch_blocked():
            return False
        button.iface_invoke.Invoke()
        return True
    finally:
        pythoncom.CoUninitialize()
