import asyncio
import struct
import unittest
from importlib.resources import files
from xml.etree import ElementTree

from still import app


class AssetTest(unittest.TestCase):
    def test_favicon_vector_is_self_contained(self):
        source = files('still').joinpath('static/favicon.svg').read_text()
        root = ElementTree.fromstring(source)
        self.assertEqual(root.attrib['viewBox'], '0 0 32 32')
        self.assertIn('satchel', source)
        self.assertNotIn('<script', source)
        self.assertNotIn('href=', source)

    def test_ico_contains_three_correctly_sized_pngs_with_provenance(self):
        data = files('still').joinpath('static/favicon.ico').read_bytes()
        self.assertEqual(struct.unpack_from('<HHH', data), (0, 1, 3))
        for index, expected in enumerate((16, 32, 48)):
            width, height, colors, reserved, planes, bits, size, offset = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
            self.assertEqual((width, height, planes, bits), (expected, expected, 1, 32))
            png = data[offset:offset + size]
            self.assertEqual(len(png), size)
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack_from('>II', png, 16), (expected, expected))
            self.assertIn(b'Origin: original Still satchel', png)

    def test_favicons_are_served_by_existing_static_mount(self):
        static = next(route.app for route in app.routes if getattr(route, 'path', None) == '/static')
        for filename, types in (
            ('favicon.svg', ('image/svg+xml',)),
            ('favicon.ico', ('image/vnd.microsoft.icon', 'image/x-icon')),
        ):
            with self.subTest(filename=filename):
                response = asyncio.run(static.get_response(filename, {'type':'http', 'method':'GET', 'headers':[]}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(response.media_type, types)
