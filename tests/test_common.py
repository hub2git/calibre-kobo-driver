# vim: fileencoding=UTF-8:expandtab:autoindent:ts=4:sw=4:sts=4
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

# To import from calibre, some things need to be added to `sys` first. Do not import
# anything from calibre or the plugins yet.
import glob
import os
import sys
import unittest

is_py2 = sys.version_info.major == 2

try:
    from unittest import mock
except ImportError:
    # Python 2
    test_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(test_dir)
    test_libdir = os.path.join(
        src_dir, "pylib", "python{major}".format(major=sys.version_info.major)
    )
    sys.path += glob.glob(os.path.join(test_libdir, "*.zip"))
    import mock

from calibre_plugins.kobotouch_extended import common


LANGUAGES = ("en_CA", "fr_CA", "fr_FR", "de_DE", "ar_EG", "ru_RU")
TEST_STRINGS = [
    {
        "encodings": {"UTF-8", "CP1252"},
        "test_strings": [
            common.unicode_type(s)
            for s in ["Hello, World!", "J'ai trouvé mon livre préféré"]
        ],
    },
    {
        "encodings": {"UTF-8", "CP1256"},
        "test_strings": [common.unicode_type(s) for s in ["مرحبا بالعالم"]],
    },
    {
        "encodings": {"UTF-8", "CP1251"},
        "test_strings": [common.unicode_type(s) for s in ["Привет мир"]],
    },
    {
        "encodings": {"UTF-8", "CP932"},
        "test_strings": [common.unicode_type(s) for s in ["こんにちは世界"]],
    },
]
TEST_TIME = "2020-04-01 01:02:03"


def gen_lang_code():
    encodings = set()
    for o in TEST_STRINGS:
        encodings |= o["encodings"]

    for enc in encodings:
        yield enc


class TestLogger(unittest.TestCase):
    def setUp(self):
        self.orig_lang = os.environ.get("LANG", None)

    def tearDown(self):
        if self.orig_lang is None:
            if "LANG" in os.environ:
                del os.environ["LANG"]
        else:
            os.environ["LANG"] = self.orig_lang
        self.orig_lang = None

    def test_logger_log_level(self):
        for envvar in ("CALIBRE_DEVELOP_FROM", "CALIBRE_DEBUG"):
            if envvar in os.environ:
                del os.environ[envvar]
        logger = common.Logger()
        self.assertEqual(logger.log_level, "INFO")

        os.environ["CALIBRE_DEVELOP_FROM"] = "true"
        logger = common.Logger()
        self.assertEqual(logger.log_level, "DEBUG")
        del os.environ["CALIBRE_DEVELOP_FROM"]

        os.environ["CALIBRE_DEBUG"] = "1"
        logger = common.Logger()
        self.assertEqual(logger.log_level, "DEBUG")
        del os.environ["CALIBRE_DEBUG"]

    def _run_logger_unicode_test(self, as_bytes):
        for o in TEST_STRINGS:
            for enc in o["encodings"]:
                with mock.patch(
                    "calibre_plugins.kobotouch_extended.common.preferred_encoding", enc
                ), mock.patch(
                    "calibre_plugins.kobotouch_extended.common.time.strftime",
                    mock.MagicMock(return_value=TEST_TIME),
                ):
                    logger = common.Logger()

                    for msg in o["test_strings"]:
                        test_tagged = logger._tag_args("DEBUG", msg)
                        self.assertListEqual(
                            test_tagged,
                            [
                                "{timestr} [{level}] {msg}".format(
                                    timestr=TEST_TIME, level="DEBUG", msg=msg
                                ),
                            ],
                        )

    def test_logger_ensure_unicode_from_bytes(self):
        self._run_logger_unicode_test(True)
        self._run_logger_unicode_test(False)


if __name__ == "__main__":
    unittest.main(module="test_common", verbosity=2)
