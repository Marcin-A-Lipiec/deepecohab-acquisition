# Appearance

Choose **Theme** in the bottom-right status bar:

- **System** follows the desktop's light or dark appearance. This is the default.
- **Light** uses the reference's pale background, white cards and teal accents.
- **Dark** uses its dark green-gray background, lighter text and teal accents.

The choice takes effect immediately and is saved per user. It applies across
the existing ten pages, Qt dialogs, tables, toolbars and custom graphics. It
does not restart acquisition, discard edits or reload the experiment. Manual
layout zoom, marker positions and label offsets are retained. The camera image
and its dark viewing area are preserved in both modes.

Geist is the interface font; Geist Mono is used for logs and plain-text editors.
Desktop font files, icons, indicator SVGs and stylesheets are included in the
wheel and source archive. They require no internet connection, reference checkout,
system font installation or additional dependency. If Qt cannot load a bundled
font, startup logs the problem and uses a desktop font fallback.

## Preferences

Appearance and video-tool paths are independent sections in the existing
per-user `preferences.json`:

```json
{
  "format": "deepecohab_application_preferences",
  "version": 1,
  "video_tools": {"ffmpeg": "", "ffprobe": ""},
  "appearance": {"theme": "system"}
}
```

Run `deepecohab-acquisition-cli paths` to find the application-data folder.
Updating or reinstalling the tool preserves these settings. They never enter
experiment config, GUI-state, diagnostic or Parquet files. Unknown preference
keys survive updates. A malformed preferences file is retained for repair;
startup uses System and explains the problem in the log. A failed preference
write leaves the current theme and selector unchanged.

## Extend the visual system

- Add semantic colors to **both** palettes in `assets/theme.json`. The core roles
  match the supplied DeepEcoHab 0.6.0 `app.css`; additional roles distinguish
  acquisition antennas, device markers, errors and camera surroundings.
- Style standard widgets in `assets/theme.qss`, using `@role` placeholders.
  Set `role="primary"` or `role="stop"` on buttons, `tone="muted"`, `"success"`,
  `"warning"` or `"error"` on labels, and `monospace=true` where needed. Set these
  properties before displaying a widget; repolish it if a property changes later.
- Custom painters use `ui.theme.color(role)` and `font(...)`. Subscribe a bound
  widget method to `manager().changed` to repaint or recolor items. Keep that
  method independent of configuration loading, device commands and graph edits.
  Use `ui.theme.ThemedCalendar` for date controls to retain themed weekend colors.
- Use `ui.theme.icon(name)` for reference SVG icons. Icons are cached by theme
  and role, with explicit normal, active, selected and disabled colors.
- After changing indicator colors, run
  `python tools/generate_theme_indicators.py`. This helper works offline.
  `tools/fetch_theme_assets.py` is an optional development helper for refreshing
  the pinned upstream fonts and copying supplied reference icons; it is not an
  installation step. Preserve the notices in `THIRD_PARTY_NOTICES.md`.

Theme work changes appearance within the current navigation. The later sidebar
and workspace-header stage is separate.
