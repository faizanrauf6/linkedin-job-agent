"""Optional local runtime controls; startup activation requires an explicit call."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = 'com.orbit.local-agent'


def startup_status(root: Path) -> dict:
    root = Path(root).resolve()
    generated = root / '.runtime' / (LABEL + '.plist')
    installed = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    supported = sys.platform == 'darwin'
    matches = supported and generated.is_file() and installed.is_file() and generated.read_bytes() == installed.read_bytes()
    return {'supported': supported, 'enabled': bool(matches), 'config_path': str(generated),
            'launch_agent_path': str(installed),
            'message': 'macOS login startup' if supported else 'Login startup is supported on macOS only'}


def configure_startup(root: Path, enabled: bool) -> dict:
    """Generate an inspectable launchd plist, then activate on explicit control use."""
    root = Path(root).resolve()
    status = startup_status(root)
    if not status['supported']:
        return status
    generated, installed = Path(status['config_path']), Path(status['launch_agent_path'])
    generated.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {'Label': LABEL, 'ProgramArguments': ['/bin/bash', str(root / 'scripts' / 'start.sh')],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': False, 'Umask': 0o077,
            'StandardOutPath': str(root / '.runtime' / 'startup.log'),
            'StandardErrorPath': str(root / '.runtime' / 'startup-error.log')}
    generated.write_bytes(plistlib.dumps(data))
    generated.chmod(0o600)
    domain = 'gui/' + str(os.getuid())
    if enabled:
        installed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if installed.exists():
            # Do not overwrite a launch agent owned by another checkout.
            prior = plistlib.loads(installed.read_bytes())
            if prior.get('WorkingDirectory') != str(root):
                raise ValueError('Orbit startup is already configured for another project directory')
            if installed.read_bytes() == generated.read_bytes():
                return startup_status(root)
            subprocess.run(['launchctl', 'bootout', domain, str(installed)], check=True, capture_output=True, timeout=15)
        installed.write_bytes(generated.read_bytes())
        installed.chmod(0o600)
        try:
            subprocess.run(['launchctl', 'bootstrap', domain, str(installed)], check=True, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            installed.unlink(missing_ok=True)
            raise
    elif installed.exists():
        prior = plistlib.loads(installed.read_bytes())
        if prior.get('WorkingDirectory') != str(root):
            raise ValueError('Orbit startup is configured for another project directory')
        subprocess.run(['launchctl', 'bootout', domain, str(installed)], check=True, capture_output=True, timeout=15)
        installed.unlink()
    return startup_status(root)


class SleepPrevention:
    def __init__(self):
        self.process = None
        self.message = ''

    def status(self) -> dict:
        return {'supported': sys.platform == 'darwin',
                'active': self.process is not None and self.process.poll() is None,
                'message': self.message or ('macOS caffeinate' if sys.platform == 'darwin' else 'Sleep prevention is supported on macOS only')}

    def update(self, enabled: bool, running: bool) -> dict:
        if not enabled or not running or sys.platform != 'darwin':
            self.stop()
        elif self.process is None or self.process.poll() is not None:
            try:
                self.process = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)
                self.message = ''
            except OSError as exc:
                self.process = None
                self.message = 'Sleep prevention could not start (' + type(exc).__name__ + ')'
        return self.status()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None
