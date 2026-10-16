"""Build Windows artifacts into dist/<APP_VERSION>/ using the bundled spec."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from perovskite_studio.app_info import APP_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', action='store_true', help='Build the portable folder instead of one EXE')
    parser.add_argument('--no-clean', action='store_true', help='Reuse the build cache')
    args = parser.parse_args()
    output = ROOT / 'dist' / APP_VERSION
    work = ROOT / 'build' / ('release-' + APP_VERSION)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
               '--distpath', str(output), '--workpath', str(work)]
    if not args.no_clean:
        command.append('--clean')
    command.append(str(ROOT / 'packaging' / 'PerovskiteDeviceStudio.spec'))
    env = dict(os.environ)
    env['PDS_BUILD_LAYOUT'] = 'folder' if args.folder else ''
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
    target = output / ('PerovskiteDeviceStudio-portable' if args.folder else 'PerovskiteDeviceStudio.exe')
    print('Build ready: ' + str(target), flush=True)


if __name__ == '__main__':
    main()
