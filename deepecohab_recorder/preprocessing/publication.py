"""Recoverable replacement of one experiment's files inside an existing folder."""
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from deepecohab_recorder.utils import atomic_write_json, read_json


class Publication:
    def __init__(self, root, output, name):
        self.root, self.output, self.name = Path(root).resolve(), Path(output).resolve(), name
        self.allowed = {self.root, self.output}
        self.lock_path = self.root / f'.{name}.preprocessing.lock'
        self.journal = self.root / f'.{name}.preprocessing.pending'
        self.temporary = []

    def __enter__(self):
        for root in self.allowed:
            root.mkdir(parents=True, exist_ok=True)
        self.lock = self.lock_path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.lock.seek(0); self.lock.write(b'0'); self.lock.flush(); self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if os.fstat(self.lock.fileno()).st_ino != self.lock_path.stat().st_ino:
                    raise OSError("Obsolete lock file; retry the job.")
        except OSError:
            self.lock.close()
            raise ValueError('This experiment is already being preprocessed. Wait for its current job to finish.')
        try:
            self.recover()
            for root in self.allowed:
                for folder in root.iterdir():
                    if not folder.is_dir() or not folder.name.startswith(f'.{self.name}.processing-'):
                        continue
                    marker = folder / '.owner'
                    if marker.is_file() and marker.read_text() == self.name:
                        shutil.rmtree(folder)
            self.staging = self.workspace(self.output)
            self.metadata_staging = self.staging if self.output == self.root else self.workspace(self.root)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def workspace(self, root):
        path = Path(tempfile.mkdtemp(prefix=f'.{self.name}.processing-', dir=root))
        (path / '.owner').write_text(self.name)
        self.temporary.append(path)
        return path

    def recover(self):
        if not self.journal.exists():
            return
        data = read_json(self.journal)
        if data.get('experiment') != self.name:
            raise ValueError('Unrecognized preprocessing recovery journal.')
        for entry in reversed(data['files']):
            target, backup = Path(entry['target']), Path(entry['backup'])
            if target.parent not in self.allowed or backup.parent != target.parent or not backup.name.startswith('.' + target.name + '.previous-'):
                raise ValueError('Recovery requires the original experiment and output directories.')
            if data['status'] == 'committed':
                backup.unlink(missing_ok=True)
            elif backup.exists():
                os.replace(backup, target)
            elif not entry['existed']:
                target.unlink(missing_ok=True)
        self.journal.unlink()

    def publish(self, changes, guard=lambda: None):
        """None retires an old generated file. New files must already be complete."""
        token = uuid.uuid4().hex
        entries = []
        for target, source in changes.items():
            target = Path(target).resolve()
            if target.parent not in self.allowed:
                raise ValueError('Publication target is outside the selected directories.')
            entries.append(dict(target=str(target), source=str(source) if source else None,
                                backup=str(target.with_name('.' + target.name + '.previous-' + token)),
                                existed=target.exists()))
        guard()
        data = dict(experiment=self.name, output=str(self.output), status='prepared', files=entries)
        atomic_write_json(self.journal, data)
        try:
            for entry in entries:
                target, backup = Path(entry['target']), Path(entry['backup'])
                if entry['existed']:
                    os.replace(target, backup)
                if entry['source']:
                    os.replace(entry['source'], target)
            data['status'] = 'committed'
            atomic_write_json(self.journal, data)
        except Exception:
            self.recover()
            raise
        self.recover()

    def __exit__(self, *_):
        # A failed rollback remains recoverable through the durable journal.
        if not self.journal.exists():
            for directory in self.temporary:
                shutil.rmtree(directory, ignore_errors=True)
        if getattr(self, 'lock', None) and not self.lock.closed:
            if os.name == 'nt':
                import msvcrt
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                self.lock_path.unlink(missing_ok=True)
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            if os.name == 'nt':
                try:
                    self.lock_path.unlink(missing_ok=True)
                except OSError:
                    pass


def recover_for_config(path):
    """Restore a interrupted result set before its metadata is read or discovered."""
    path = Path(path).resolve()
    for journal in path.parent.glob('.*.preprocessing.pending'):
        data = read_json(journal)
        if not any(Path(entry['target']) == path for entry in data.get('files', [])):
            continue
        name = data.get('experiment', '')
        from deepecohab_recorder.experiment_files import experiment_stem
        experiment_stem(name)
        if journal.name != f'.{name}.preprocessing.pending':
            raise ValueError('Unrecognized preprocessing recovery journal.')
        with Publication(path.parent, data['output'], name):
            pass
