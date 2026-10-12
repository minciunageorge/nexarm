import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrameworkTests(unittest.TestCase):
    def test_rgb_row_padding(self):
        camera = load('camera_capture')
        self.assertEqual(camera.ppm_bytes(1, 2, 'rgb8', 4, bytes([1, 2, 3, 99, 4, 5, 6, 99])),
                         b'P6\n1 2\n255\n\x01\x02\x03\x04\x05\x06')

    def test_bgr_and_mono(self):
        camera = load('camera_capture')
        self.assertTrue(camera.ppm_bytes(1, 1, 'bgr8', 3, bytes([1, 2, 3])).endswith(bytes([3, 2, 1])))
        self.assertTrue(camera.ppm_bytes(1, 1, 'mono8', 1, bytes([42])).endswith(bytes([42]*3)))

    def test_invalid_images_fail(self):
        camera = load('camera_capture')
        for args in [(0, 1, 'rgb8', 3, b'123'), (1, 1, '16UC1', 2, b'12'),
                     (2, 1, 'rgb8', 3, b'123'), (1, 2, 'rgb8', 3, b'123')]:
            with self.assertRaises(ValueError):
                camera.ppm_bytes(*args)

    def test_ssh_security_and_tunnel(self):
        cli = load('nexarm')
        command = cli.ssh_command('127.0.0.1', 2222, None, 'probe')
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('BatchMode=yes', command)
        self.assertIn('pi@127.0.0.1', command)
        self.assertIn('2222', command)
        for host in ['-oProxyCommand=bad', 'pi@host', 'host; touch /tmp/bad', 'https://host']:
            with self.assertRaises(ValueError):
                cli.ssh_command(host, 22, None, 'probe')


if __name__ == '__main__':
    unittest.main()
