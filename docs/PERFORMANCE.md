# Working with large recordings

Raw-file discovery runs in the background and caches unchanged previews. The GUI
can remain usable while a large recording folder is being inspected. The full
preprocessing job still reads the selected raw records.

- Use **Rescan experiment** after adding, changing or removing raw files.
- Use one board folder per continuous source and verify board numbers and formats.
- Allow preprocessing to finish or cancel it before loading another experiment.
- Keep sufficient free disk space for raw data, video and processed outputs.
- Reduce camera preview FPS when display load is high; preview FPS is separate
  from recording FPS.
- On small screens, use the page, table and form scrollbars to reach all controls.

RFID and TTL totals refresh periodically. Hidden camera pages do not require
continuous preview rendering. These display choices do not change recorded
detections or scientific export formats.

Recording throughput depends on hardware, connection quality, camera settings,
encoder and disk performance. Check a representative sample on the acquisition
PC before starting a long experiment. See [System and data checks](../TESTING.md).
