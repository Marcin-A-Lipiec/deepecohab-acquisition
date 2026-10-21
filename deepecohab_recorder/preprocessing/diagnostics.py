"""Compact summaries and streamed, lossless inserted-row identification."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo
from .compact_diagnostics import compact_header, compact_medians, public_fields
from .models import Cancelled, iso


class Diagnostics:
    EXAMPLE_LIMIT = 5

    def __init__(self, directory, zone, filename="diagnostic_log.json"):
        self.zone, self.directory, self.filename = zone, directory, filename
        self.counts, self.detail_counts = Counter(), Counter()
        self.rejected, self.rejection_reasons = Counter(), Counter()
        self.examples = defaultdict(list)
        self._inserts_path = directory / 'interpolation_provenance.tmp'
        self._inserts = self._inserts_path.open('w', encoding='utf-8')
        self.info = dict(diagnostics_version=12,
                         created_at=datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).isoformat(),
                         recording_timezone=zone)
        for name in ('interpolated_periods', 'interpolated_standard_periods', 'interpolated_extended_periods'):
            self.counts[name] = 0

    def write(self, record_type, **values):
        if record_type == 'output_record':
            if values.get('is_interpolated'):
                self._inserts.write(json.dumps(values, ensure_ascii=False, allow_nan=False) + '\n')
            elif values.get('spans_output_boundary'):
                self.count('observed_periods_spanning_output_boundary')
                self.write('observed_period_spanning_boundary', **values)
            return
        key = record_type + (':' + str(values['reason']) if 'reason' in values else '')
        self.detail_counts[key] += 1
        if len(self.examples[key]) < self.EXAMPLE_LIMIT:
            self.examples[key].append(dict(type=record_type, **values))

    def count(self, name, value=1):
        self.counts[name] += value

    def reject(self, tag, antennas, reason):
        # A call represents one observed transition and one primary reason.
        for antenna in set(antennas):
            self.rejected[antenna, tag] += 1
            self.rejection_reasons[reason] += 1

    def transition_rows(self, db):
        totals = {(r['antenna'], r['tag']): r for r in db.execute(
            "SELECT antenna,tag,SUM(interpolated=0) AS correct,SUM(interpolated) AS interpolated FROM passes WHERE kind!='ttl' GROUP BY antenna,tag")}
        tags = set(self.info.get('configured_animal_tags', [])) | {tag for _, tag in totals if tag}
        antennas = getattr(self, 'main_antennas', sorted({a for a, _ in totals}))
        for antenna in antennas:
            for tag in sorted(tags):
                row = totals.get((antenna, tag), {})
                yield dict(antenna=antenna, animal=tag, correct=row['correct'] if row else 0,
                           interpolated=row['interpolated'] if row else 0, rejected=self.rejected[antenna, tag])

    def compact_inserts(self, db, mouse_lookup, cohort_lookup):
        methods, indices = [], {}
        target = self.directory / 'compact_interpolations.tmp'
        with self._inserts_path.open(encoding='utf-8') as source, target.open('w', encoding='utf-8') as out:
            for number, line in enumerate(source):
                if number % 1000 == 0 and getattr(self, 'cancelled', lambda: False)():
                    raise Cancelled()
                raw = json.loads(line)
                method = raw.get('method') or {}
                def anchor(key):
                    ident = method.get(key)
                    row = db.execute('SELECT destination,output_rows.part,row_number,pc_end,board_end FROM output_rows JOIN passes USING(id) WHERE id=?', (ident,)).fetchone() if db is not None and ident is not None else None
                    if row is None:
                        raise ValueError('An interpolation anchor is missing from the exported rows.')
                    stream = f"{row['destination']}:{row['part']}"
                    return (row['row_number'] if stream == raw['stream'] else dict(stream=stream, row=row['row_number'])), row
                previous, observed = anchor('previous_observed_id')
                following, _ = anchor('next_observed_id')
                shared = {k:method[k] for k in ('inference_type','route_method','selected_route','anchor','duration_method','duration_samples') if k in method}
                route = method.get('selected_route', [])
                if raw.get('antenna') in route:
                    i = route.index(raw['antenna'])
                    pair = (route[i-1], route[i]) if method.get('anchor') == 'previous_end' else (route[i], route[i+1])
                    key = pair if method.get('median_scope') == 'cohort' else (raw['animal_id'], *pair)
                    lookup = cohort_lookup if method.get('median_scope') == 'cohort' else mouse_lookup
                    if key in lookup:
                        shared['cohort_median' if method.get('median_scope') == 'cohort' else 'mouse_median'] = lookup[key]
                    else:
                        shared['timing_estimate'] = {k:method[k] for k in ('median_samples','median_scope','transit_ms') if k in method}
                encoded = json.dumps(shared, sort_keys=True, ensure_ascii=False)
                if encoded not in indices:
                    indices[encoded] = len(methods)
                    methods.append(shared)
                row = dict(stream=raw['stream'], row=raw['row_number'], method=indices[encoded],
                           previous=previous, following=following,
                           previous_actual_end=iso(observed['pc_end'], self.zone))
                if observed['board_end'] is not None:
                    row['previous_actual_board_end'] = iso(observed['board_end'], self.zone)
                out.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
        return methods, target

    def finish(self, db, status='completed'):
        self._inserts.flush()
        self._inserts.close()
        self.info['status'] = status
        medians, cohort, mouse_lookup, cohort_lookup = compact_medians(self.info, include_tunnel=not bool(getattr(self, 'linked_config', None)))
        methods, compact_path = self.compact_inserts(db, mouse_lookup, cohort_lookup)
        partial = self.directory / (self.filename + '.partial')
        with partial.open('wb') as handle:
            def write(value): handle.write(value.encode('utf-8'))
            write(json.dumps(compact_header(self), indent=2, ensure_ascii=False, allow_nan=False)[:-2])
            self._header_bytes = handle.tell()
            def array(name, rows):
                write(',\n  ' + json.dumps(name) + ': [')
                for i, row in enumerate(rows):
                    write(('\n    ' if i == 0 else ',\n    ') + json.dumps(public_fields(dict(row)), ensure_ascii=False, separators=(',', ':'), allow_nan=False))
                write('\n  ]')
            if db is not None:
                array('transition_diagnostic', self.transition_rows(db))
                array('read_diagnostic', db.execute("SELECT antenna,kind,SUM(reads) AS raw_reads,SUM(CASE WHEN accepted=1 THEN reads ELSE 0 END) AS accepted_reads,SUM(CASE WHEN accepted=0 THEN reads ELSE 0 END) AS rejected_tag_reads,COUNT(DISTINCT CASE WHEN accepted=0 THEN tag END) AS rejected_tag_count FROM raw_counts GROUP BY antenna,kind ORDER BY antenna"))
                devices = getattr(getattr(self, 'settings', None), '_device_sources', set())
                if devices:
                    array('device_diagnostic', (dict(antenna=r['antenna'], animal=r['tag'], periods=r['periods']) for r in db.execute("SELECT antenna,tag,COUNT(*) AS periods FROM passes WHERE kind!='ttl' AND interpolated=0 GROUP BY antenna,tag ORDER BY antenna,tag") if r['antenna'] in devices))
                array('duration_summary', db.execute("SELECT antenna,COUNT(*) AS periods,MIN(duration) AS minimum_ms,AVG(duration) AS mean_ms,MAX(duration) AS maximum_ms FROM passes WHERE kind!='ttl' AND interpolated=0 GROUP BY antenna ORDER BY antenna"))
            array('transition_medians', medians)
            if cohort: array('cohort_medians', cohort)
            array('inference_methods', methods)
            with compact_path.open(encoding='utf-8') as inserts:
                array('interpolations', (json.loads(line) for line in inserts))
            write('\n}\n')
        partial.replace(self.directory / self.filename)
        compact_path.unlink(missing_ok=True)
        self._inserts_path.unlink(missing_ok=True)

    def rewrite_summary(self, directory):
        """Replace bounded header metadata without loading row provenance."""
        import os
        import shutil
        path = directory / self.filename
        temporary = path.with_suffix('.json.partial')
        header = json.dumps(compact_header(self), indent=2, ensure_ascii=False, allow_nan=False)[:-2].encode('utf-8')
        with path.open('rb') as source, temporary.open('wb') as destination:
            destination.write(header)
            source.seek(self._header_bytes)
            shutil.copyfileobj(source, destination, 1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        self._header_bytes = len(header)
