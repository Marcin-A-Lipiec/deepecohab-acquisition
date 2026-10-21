> Historical contract. For current behavior see [compatibility](../COMPATIBILITY.md) and [config v8](../CONFIG_V8.md).

# Preprocessing diagnostic v11

`<experiment>.diagnostic.json` resides beside the main config by default. The report starts with `definitions`: terminology, units, evidence limits and counting rules. Top-level experiment_output_name, status and outputs remain compatible with publication ownership checks. The application still recognizes completed older manifests during replacement.

| Section | Meaning |
| --- | --- |
| processing | Effective options, config fingerprints, data formats, reference clock, stitching windows and boundary reporting policy. |
| experiment_timing | Selected/automatic bounds, their evidence, recovery status and manual-override preservation. |
| recordings | One row per selected board folder or file; source/part, raw-file count, bytes, fingerprint and observed first/last record. |
| clocks | Clock epochs, scales, counter/reset evidence and references to logical recording IDs. |
| recording_boundaries | Observed gaps and confirmed acquisition inactivity; overlapping descriptions combine into one record where possible. |
| acquisition_evidence / device_evidence | Available acquisition starts/stops, abrupt-end uncertainty, health/errors and other device evidence. |
| transition_diagnostic | Complete mouse × main-antenna matrix: antenna, animal, correct, interpolated, rejected. |
| transition_medians | Every configured/accepted mouse and both directions of each adjacent tunnel pair; median_ms, samples, eligible. |
| cohort_medians | Optional shared fallback estimates, referenced by zero-based cohort_fallback indexes. A fallback never replaces the mouse’s observed median. |
| read_diagnostic | Raw accepted/rejected totals per RFID/TTL input and number of distinct rejected tag identities. |
| device_diagnostic | Observed device RFID periods per animal, outside movement statistics. |
| duration_summary | Observed antenna period duration statistics, milliseconds. |
| inference_methods / interpolations | Shared inference explanations and every inserted output row with observed anchors. |
| issues | Nonempty warnings/rejection reasons, bounded examples and unattributable transitions. |
| outputs | Exact published files, stream/group membership, counts and hashes. |

`correct` counts observed stitched RFID periods, not raw reads or independent proof of a physical path. `rejected` counts identified antenna-insertion candidates that failed for any reason, once per mouse/observed transition/candidate antenna. Ambiguous alternative routes contribute each distinct internal candidate once; they do not prove all candidates were visited. Whitelist failures are separate rejected_tag_reads. With interpolation disabled there are no attempted insertions or rejected candidates. The candidate-reason total reconciles with the sum of rejected across transition_diagnostic.

Folders intentionally have no routine per-member inventory or internal boundary list, including internal gaps exceeding three minutes. Real stops, errors and clock resets remain meaningful evidence. Separate recording gaps use elapsed instants per board and the default inclusive threshold of 180 seconds. Overlapping recording ranges do not create gaps. Silence alone is not proof of failure. An abrupt/unknown stop remains unknown; a last detection is not substituted for its failure time.

Folder fingerprint version 1: sort internal `[relative_path, byte_count, content_sha256]` rows by forward-slash relative_path; serialize as a UTF-8 JSON array with ensure_ascii=false and separators=(',', ':'); hash those bytes with SHA-256. Only selected supported raw files are included. This records reproducibility without emitting every filename.

Each output stream identifies one destination/part with one or both CSV/Parquet representations in outputs. Each `interpolations` record identifies `stream` and zero-based `row` (CSV header excluded), an index into inference_methods, and previous/following observed row references. An integer anchor denotes the same stream; an object specifies another stream and row. `previous_actual_end` and, when available, previous_actual_board_end retain actual observed end times before duration rounding or minimum padding. Never reconstruct those anchors by adding the padded output duration.

Shared methods retain route, standard/extended inference, timing anchor, duration basis/sample count and a zero-based mouse_median or cohort_median reference. The five data columns themselves supply the inserted row’s antenna, animal, datetime, duration and internal board timestamp. Output hashes bind all references to exact files. Interpolations are not truncated to examples. Methods and row arrays use one compact record per line.

Parquet schema is unchanged. Diagnostic version 11 does not imply a new data-file schema.
