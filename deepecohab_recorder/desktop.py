"""Per-user Windows shortcuts; Unicode and spaces are preserved as path data."""
import argparse
from importlib import resources
import os
from pathlib import Path
import shutil
import sys
import sysconfig

from . import APP_NAME
from .runtime_paths import app_data_directory, asset

DESCRIPTION = "Launch DeepEcoHab Acquisition"
COMMAND = "deepecohab-acquisition.exe"


def installed_launcher():
    # Prefer this environment over an unrelated installation on PATH.
    candidate = Path(sysconfig.get_path("scripts")) / COMMAND
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError("The installed GUI launcher is missing. Run uv tool install --python 3.12 . from the application folder.")


def shortcut_locations(shell, directory=None, start_menu=False):
    if directory is not None:
        if start_menu:
            raise ValueError("--directory cannot be combined with --start-menu.")
        folders = [Path(directory).expanduser().resolve()]
    else:
        folders = [Path(shell.SpecialFolders("Desktop"))]
        if start_menu:
            folders.append(Path(shell.SpecialFolders("Programs")))
    return [folder / f"{APP_NAME}.lnk" for folder in folders]


def shell_link(path=None):
    """Use IShellLinkW/IPersistFile so paths never pass through an ANSI codec."""
    import pythoncom
    from win32com.shell import shell

    link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IShellLink,
    )
    if path is not None:
        link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path))
    return link


def change_shortcuts(*, directory=None, start_menu=False, remove=False, shell=None):
    if sys.platform != "win32":
        raise RuntimeError("Desktop shortcut installation is currently supported on Windows. Launch with deepecohab-acquisition on other systems.")
    if shell is None and directory is None:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    paths = shortcut_locations(shell, directory, start_menu)
    for path in paths:
        if path.exists():
            existing = shell_link(path)
            if existing.GetDescription() != DESCRIPTION or Path(existing.GetPath(0)[0]).name.lower() != COMMAND:
                raise FileExistsError(f"A different shortcut already exists: {path}")
    if remove:
        for path in paths:
            path.unlink(missing_ok=True)
        return paths
    target = installed_launcher()
    directory = app_data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    icon = directory / "acquisition.ico"
    with resources.as_file(asset("acquisition.ico")) as source:
        temporary = directory / "acquisition.ico.tmp"
        shutil.copyfile(source, temporary)
        os.replace(temporary, icon)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        shortcut = shell_link()
        shortcut.SetPath(str(target))
        shortcut.SetArguments("")
        shortcut.SetWorkingDirectory(str(directory))
        shortcut.SetIconLocation(str(icon), 0)
        shortcut.SetDescription(DESCRIPTION)
        shortcut.SetShowCmd(1)
        import pythoncom
        shortcut.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a DeepEcoHab Acquisition desktop icon.")
    parser.add_argument("--start-menu", action="store_true", help="Also add/remove the Start menu entry.")
    parser.add_argument("--directory", type=Path, help="Use a specific shortcut folder instead of the Desktop.")
    parser.add_argument("--remove", action="store_true", help="Remove shortcuts only; retain application and experiment files.")
    args = parser.parse_args(argv)
    try:
        paths = change_shortcuts(directory=args.directory, start_menu=args.start_menu, remove=args.remove)
    except Exception as exc:
        print(f"Shortcut setup failed: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(f"{'Removed' if args.remove else 'Created'}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
