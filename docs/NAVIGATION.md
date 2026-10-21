# Navigation and experiment header

The sidebar groups the existing pages without changing their names or controls:

| Group | Pages |
|---|---|
| Experiment | Setup, Animal details, EcoHAB layout, Environmental settings, Events |
| Devices | Acquisition, Camera control, Hardware trigger |
| Workflow | Launch |

**Data preprocessing** is pinned at the bottom, immediately above the sidebar
collapse/expand button, separate from the experiment pages. It follows Launch
in keyboard navigation and appears last in the compact page menu. Internal page
keys and legacy numeric indices remain unchanged.

Each page is created once. Switching pages preserves unsaved fields, table
selection, habitat graphics and manual zoom/pan, camera previews, preprocessing
progress and running recordings. Device operations continue while their page
is hidden. Shared identity/hardware locks apply when returning to a page.

The sidebar is 232 logical pixels wide when expanded and 64 when collapsed.
Use its bottom button to collapse or expand it; this choice survives restart.
Below 1100 logical pixels of window width, navigation automatically becomes an
icon rail to keep room for acquisition controls. Hover or focus a page button
for its name; the bottom **Choose page** button opens all full names, grouped as
above. Navigation scrolls when the window is short. Resizing restores the wider
layout according to your saved collapse choice.

Dense pages retain their internal tables/canvases and gain page scrollbars when
their controls cannot fit in the available workspace. This keeps lower controls
reachable on a 640×480 window while the header and global log stay visible.

The mouse wheel scrolls the surrounding form when it is over a numeric,
date/time or selection editor, including a focused editor. Change values by
typing, clicking or using keyboard controls. Open selection lists retain their
normal list scrolling.

## Header and actions

The persistent header shows the current page, experiment name, save directory,
last configuration action and activity. Long text is shortened to fit; tooltips
and accessible names retain the full text. While recording or matching owns
resources, identity comes from the locked experiment context. Status includes
starting/running/stopping acquisition devices, tag matching, lighting connections,
preprocessing tasks and System check.

**Experiment** opens Load/Save experiment configuration, Check configuration
and System check. The existing Load/Save buttons on Setup and the individual
page controls remain available. Configuration status reports the last load/save,
save-target notice or error. Individual pages may still contain unsaved edits;
use **Save experiment configuration** to save the whole current draft.

**Stop all** stays visible on every page, including small windows. It stops all
acquisition devices through the existing stop/drain path. Independently powered
lighting and standalone lickometers continue operating, as before. Preprocessing
and System check have their own Cancel controls. Closing the GUI retains the
existing confirmation and waits for acquisition/background work to release files.

The recording activity/error log remains visible below the page; its splitter
can resize it down to 40 logical pixels. Errors remain there when navigating.
**Theme: System / Light / Dark** remains in the bottom-right status bar.

## Keyboard

| Shortcut/control | Action |
|---|---|
| Ctrl+Tab / Ctrl+Shift+Tab | Next/previous page in sidebar order, wrapping at either end |
| Ctrl+B | Collapse/expand the sidebar; open Choose page in a narrow window |
| Ctrl+S | Save the full experiment configuration |
| Ctrl+O | Load a configuration through the existing chooser and activity checks |
| Sidebar Up/Down, Home/End | Move focus between page buttons |
| Sidebar Enter or Space | Activate the focused page |
| Tab / Shift+Tab | Move keyboard focus through the visible controls |

Shortcuts apply to the main window and its child widgets. Form arrow keys retain
their normal editing behavior. Match and readiness dialogs keep their own controls.

## Settings and extension

`preferences.json` stores `"navigation": {"collapsed": false}` alongside appearance
and video-tool settings in per-user application storage. Automatic compact mode
does not save anything. Navigation never adds fields to experiment configuration,
GUI state, diagnostics or scientific output. Unknown preference keys survive saves;
failed writes retain the current layout, and malformed files remain available for
repair with a notice in the activity log.

Developers register a stable key, title, factory, scrolling policy, group and icon
in `ui/registry.py`. `ui/shell.py` contains the presentation components. Existing
numeric page indices and registry aliases remain supported; new code should use
`window.pages.set_current_key(key)`. See [../ARCHITECTURE.md](../ARCHITECTURE.md).
