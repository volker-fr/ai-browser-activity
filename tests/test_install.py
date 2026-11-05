import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class InstallTests(unittest.TestCase):
    def test_systemd_install_only_sets_up_units_and_open_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            bindir = base / 'bin'; bindir.mkdir()
            log = base / 'calls'
            for name, body in {
                'systemctl': 'printf "systemctl %s\\n" "$*" >> "$CALL_LOG"',
                'xdg-open': 'printf "open %s\\n" "$*" >> "$CALL_LOG"',
                'uname': 'printf "Linux\\n"',
            }.items():
                command = bindir / name
                command.write_text('#!/bin/sh\n' + body + '\n'); command.chmod(0o755)
            env = dict(os.environ, XDG_CONFIG_HOME=str(base / 'config'), CALL_LOG=str(log),
                       DISPLAY=':test', PATH=str(bindir) + os.pathsep + os.environ['PATH'])
            subprocess.run(['make', '-j', 'systemd-install'], cwd=ROOT, env=env, check=True, capture_output=True)
            self.assertEqual(log.read_text().splitlines(), [
                'systemctl --user daemon-reload', 'systemctl --user enable --now ai-browser-overlay.socket',
                'systemctl --user try-restart ai-browser-overlay.service'])
            subprocess.run(['make', 'open'], cwd=ROOT, env=env, check=True, capture_output=True)
            self.assertEqual(log.read_text().splitlines()[-1], 'open http://127.0.0.1:17391/')
            env.pop('DISPLAY', None); env.pop('WAYLAND_DISPLAY', None)
            result = subprocess.run(['make', 'open'], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
            self.assertIn('http://127.0.0.1:17391/', result.stdout)
            self.assertIn('No desktop browser opener', result.stdout)
            self.assertEqual(len(log.read_text().splitlines()), 4)

    def test_systemd_targets_fail_before_changes_on_non_linux(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            bindir = base / 'bin'; bindir.mkdir()
            log = base / 'calls'
            for name, body in {
                'systemctl': 'printf "systemctl %s\\n" "$*" >> "$CALL_LOG"',
                'uname': 'printf "Darwin\\n"',
            }.items():
                command = bindir / name
                command.write_text('#!/bin/sh\n' + body + '\n'); command.chmod(0o755)
            env = dict(os.environ, XDG_CONFIG_HOME=str(base / 'config'), CALL_LOG=str(log),
                       PATH=str(bindir) + os.pathsep + os.environ['PATH'])
            for target in ('systemd-install', 'systemd-uninstall'):
                result = subprocess.run(['make', target], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'{target} requires Linux', result.stderr)
                self.assertFalse((base / 'config').exists())
                self.assertFalse(log.exists())

    def test_installer_replaces_symlink_and_enables_only_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            bindir = base / 'bin'; bindir.mkdir()
            log = base / 'calls'
            systemctl = bindir / 'systemctl'
            systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
            systemctl.chmod(0o755)
            config = base / 'config/systemd/user'; config.mkdir(parents=True)
            victim = base / 'victim'; victim.write_text('unchanged')
            (config / 'ai-browser-overlay.service').symlink_to(victim)
            directory = base / 'directory'; directory.mkdir()
            (config / 'ai-browser-overlay.socket').symlink_to(directory)
            env = dict(os.environ, XDG_CONFIG_HOME=str(base / 'config'), CALL_LOG=str(log),
                       PATH=str(bindir) + os.pathsep + os.environ['PATH'])
            subprocess.run(['make', 'systemd-install'], cwd=ROOT, env=env, check=True)
            self.assertEqual(victim.read_text(), 'unchanged')
            self.assertFalse((config / 'ai-browser-overlay.socket').is_symlink())
            self.assertEqual(list(directory.iterdir()), [])
            service = (config / 'ai-browser-overlay.service').read_text()
            self.assertIn(str(ROOT / 'overlay_server.py'), service)
            self.assertIn('ConditionPathExists=' + str(ROOT / 'overlay_server.py').replace('%', '%%'), service)
            self.assertIn('AI_OVERLAY_IDLE_MINUTES=5', service)
            self.assertIn('127.0.0.1:17391', (config / 'ai-browser-overlay.socket').read_text())
            self.assertEqual(log.read_text().splitlines(), ['--user daemon-reload', '--user enable --now ai-browser-overlay.socket', '--user try-restart ai-browser-overlay.service'])
            subprocess.run(['make', 'systemd-install'], cwd=ROOT, env=env, check=True)
            self.assertEqual((config / 'ai-browser-overlay.service').read_text(), service)

    def test_empty_xdg_config_home_falls_back_to_home_config(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            home = base / "home"; home.mkdir()
            bindir = base / "bin"; bindir.mkdir()
            fake = bindir / "systemctl"; fake.write_text("#!/bin/sh\nexit 0\n"); fake.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME="",
                       PATH=str(bindir) + os.pathsep + os.environ["PATH"])
            subprocess.run(["make", "systemd-install"], cwd=ROOT, env=env, check=True)
            config = home / ".config/systemd/user"
            self.assertTrue((config / "ai-browser-overlay.service").is_file())
            self.assertTrue((config / "ai-browser-overlay.socket").is_file())

    def test_dollar_path_is_literal_in_rendered_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            checkout = base / '${HOME}' / '$$ activity% " quote\\slash'
            checkout.mkdir(parents=True)
            shutil.copy(ROOT / 'Makefile', checkout / 'Makefile')
            shutil.copytree(ROOT / 'systemd', checkout / 'systemd')
            (checkout / 'overlay_server.py').touch()
            bindir = base / 'bin'; bindir.mkdir()
            fake = bindir / 'systemctl'; fake.write_text('#!/bin/sh\nexit 0\n'); fake.chmod(0o755)
            env = dict(os.environ, XDG_CONFIG_HOME=str(base / 'config'),
                       PATH=str(bindir) + os.pathsep + os.environ['PATH'])
            subprocess.run(['make', 'systemd-install'], cwd=checkout, env=env, check=True)
            unit = (base / 'config/systemd/user/ai-browser-overlay.service').read_text()
            server_path = str(checkout / 'overlay_server.py')
            escaped = server_path.replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"')
            self.assertIn('ExecStart=:/usr/bin/python3 "' + escaped + '"', unit)
            self.assertIn('ConditionPathExists=' + server_path.replace('%', '%%'), unit)
            if shutil.which('systemd-analyze'):
                subprocess.run(['systemd-analyze', 'verify', str(base / 'config/systemd/user/ai-browser-overlay.service')], check=True, capture_output=True)

if __name__ == '__main__': unittest.main()
