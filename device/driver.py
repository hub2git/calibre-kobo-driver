# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:fdm=indent:ai

"""The main driver for the KoboTouchExtended driver. Everything starts here."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

__license__ = "GPL v3"
__copyright__ = "2013, Joel Goguen <jgoguen@jgoguen.ca>"
__docformat__ = "markdown en"

import json
import os
import re
import shutil
from datetime import datetime

try:
    # Python 3
    from configparser import SafeConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser

from calibre.constants import config_dir
from calibre.devices.kobo.driver import KOBOTOUCH
from calibre.ebooks.oeb.polish.errors import DRMError

from calibre_plugins.kobotouch_extended.common import log
from calibre_plugins.kobotouch_extended.common import modify_epub
from calibre_plugins.kobotouch_extended.common import plugin_minimum_calibre_version
from calibre_plugins.kobotouch_extended.common import plugin_version
from calibre_plugins.kobotouch_extended.container import KEPubContainer

# Support load_translations() without forcing calibre 1.9+
try:
    load_translations()
except NameError:
    pass

EPUB_EXT = ".epub"
KEPUB_EXT = ".kepub"


class InvalidEPub(ValueError):
    """InvalidEpub wraps ValueError and ensures book information is present."""

    def __init__(self, name, author, message, fname=None, lineno=None):
        """Construct a new InvalidEpub."""
        self.name = name
        self.author = author
        self.message = message
        self.fname = fname
        self.lineno = lineno
        ValueError.__init__(
            self,
            _(  # noqa: F821
                "Failed to parse '{book}' by '{author}' with error: '{error}' "
                "(file: {filename}, line: {lineno})"
            ).format(
                book=name, author=author, error=message, filename=fname, lineno=lineno
            ),
        )


class KOBOTOUCHEXTENDED(KOBOTOUCH):
    """Extended driver for Kobo Touch, Kobo Glo, and Kobo Mini devices.

    This driver automatically modifies ePub files to include extra information
    used by Kobo devices to enable annotations and display of chapter names and
    page numbers on a per-chapter basis. Files are also transferred using the
    'kepub' designation ({name}.kepub.{ext}) automatically to trigger the Kobo
    device to enable these features. This also enabled more detailed reading
    statistics accessible within each book.
    """

    name = "KoboTouchExtended"
    gui_name = "Kobo Touch/Glo/Mini/Aura HD/Aura"
    author = "Joel Goguen"
    description = _(  # noqa: F821
        "Communicate with the Kobo Touch, Glo, Mini, Aura HD, and Aura "
        "firmwares and enable extended Kobo ePub features."
    )
    configdir = os.path.join(config_dir, "plugins")
    reference_kepub = os.path.join(configdir, "reference.kepub.epub")
    FORMATS = ["kepub", "epub", "cbr", "cbz", "pdf", "txt"]

    minimum_calibre_version = plugin_minimum_calibre_version
    version = plugin_version

    content_types = {"main": 6, "content": 9, "toc": 899}

    EXTRA_CUSTOMIZATION_MESSAGE = KOBOTOUCH.EXTRA_CUSTOMIZATION_MESSAGE[:]
    EXTRA_CUSTOMIZATION_DEFAULT = KOBOTOUCH.EXTRA_CUSTOMIZATION_DEFAULT[:]

    skip_renaming_files = set([])
    kobo_js_re = re.compile(r".*/?kobo.*\.js$", re.IGNORECASE)
    invalid_filename_chars_re = re.compile(
        r"[\/\\\?%\*:;\|\"\'><\$!]", re.IGNORECASE | re.UNICODE
    )

    def modifying_epub(self):
        """Determine if this epub will be modified."""
        return (
            self.modifying_css()
            or self.clean_markup
            or self.extra_features
            or self.skip_failed
            or self.smarten_punctuation
            or self.disable_hyphenation
        )

    @classmethod
    def settings(cls):
        """Initialize settings for the driver."""
        opts = super(KOBOTOUCHEXTENDED, cls).settings()
        log.debug("KoboTouchExtended:settings: settings=", opts)
        # Make sure that each option is actually the right type
        for idx in range(0, len(cls.EXTRA_CUSTOMIZATION_DEFAULT)):
            if not isinstance(
                opts.extra_customization[idx],
                type(cls.EXTRA_CUSTOMIZATION_DEFAULT[idx]),
            ):
                opts.extra_customization[idx] = cls.EXTRA_CUSTOMIZATION_DEFAULT[idx]
        return opts

    @classmethod
    def config_widget(cls):
        """Create and populate the driver settings config widget."""
        from calibre.gui2.device_drivers.configwidget import ConfigWidget

        cw = super(KOBOTOUCHEXTENDED, cls).config_widget()
        if isinstance(cw, ConfigWidget):
            log.warning("KoboTouchExtended:config_widget: Have old style config.")
            try:
                from PyQt5.QtCore import QCoreApplication
                from PyQt5.QtWidgets import QScrollArea
            except ImportError:
                from PyQt4.Qt import QCoreApplication
                from PyQt4.Qt import QScrollArea
            qsa = QScrollArea()
            qsa.setWidgetResizable(True)
            qsa.setWidget(cw)
            qsa.validate = cw.validate
            desktop_geom = QCoreApplication.instance().desktop().availableGeometry()
            if desktop_geom.height() < 800:
                qsa.setBaseSize(qsa.size().width(), desktop_geom.height() - 100)
            cw = qsa
        else:
            log.info("KoboTouchExtended:config_widget: Have new style config.")
            cls.current_friendly_name = cls.gui_name

            from calibre_plugins.kobotouch_extended.device.koboextended_config import (
                KOBOTOUCHEXTENDEDConfig,
            )

            cw = KOBOTOUCHEXTENDEDConfig(
                cls.settings(),
                cls.FORMATS,
                cls.SUPPORTS_SUB_DIRS,
                cls.MUST_READ_METADATA,
                cls.SUPPORTS_USE_AUTHOR_SORT,
                cls.EXTRA_CUSTOMIZATION_MESSAGE,
                cls,
                extra_customization_choices=cls.EXTRA_CUSTOMIZATION_CHOICES,
            )
        return cw

    @classmethod
    def save_settings(cls, config_widget):
        """Ensure settings are properly saved between old and new config styles."""
        try:
            config_widget = config_widget.widget()
            log.warning("KoboTouchExtended:save_settings: Have old style config.")
        except Exception:
            log.info("KoboTouchExtended:save_settings: Have new style config.")

        super(KOBOTOUCHEXTENDED, cls).save_settings(config_widget)

    def _modify_epub(self, infile, metadata, container=None):
        if not infile.endswith(EPUB_EXT):
            if not infile.endswith(KEPUB_EXT):
                self.skip_renaming_files.add(metadata.uuid)
            else:
                log.info(
                    "KoboTouchExtended:_modify_epub:Skipping all "
                    "processing for calibre-converted KePub file "
                    "{0}".format(infile)
                )
            return super(KOBOTOUCHEXTENDED, self)._modify_epub(
                infile, metadata, container
            )

        log.info(
            "KoboTouchExtended:_modify_epub:Adding basic Kobo features to "
            "{0} by {1}".format(metadata.title, " and ".join(metadata.authors))
        )

        opts = self.settings()
        skip_failed = self.skip_failed
        if skip_failed:
            log.info(
                "KoboTouchExtended:_modify_epub:Failed conversions will be skipped"
            )
        else:
            log.info(
                "KoboTouchExtended:_modify_epub:Failed conversions will raise "
                "exceptions"
            )

        is_encumbered_book = False
        try:
            if container is None:
                container = KEPubContainer(infile, log)
            else:
                is_encumbered_book = container.is_drm_encumbered
        except DRMError:
            log.warning(
                "KoboTouchExtended:_modify_epub:ERROR: ePub is "
                "DRM-encumbered, not modifying"
            )
            is_encumbered_book = True

        if is_encumbered_book:
            self.skip_renaming_files.add(metadata.uuid)
            if self.upload_encumbered:
                return super(KOBOTOUCHEXTENDED, self)._modify_epub(
                    infile, metadata, container
                )
            else:
                return False

        try:
            # Add the conversion info file
            calibre_details_file = self.normalize_path(
                os.path.join(self._main_prefix, "driveinfo.calibre")
            )
            log.debug(
                "KoboTouchExtended:_modify_epub:Calibre details file :: "
                "{0}".format(calibre_details_file)
            )
            o = {}
            if os.path.isfile(calibre_details_file):
                with open(calibre_details_file, "rb") as f:
                    o = json.loads(f.read())
                for prop in (
                    "device_store_uuid",
                    "prefix",
                    "last_library_uuid",
                    "location_code",
                ):
                    del o[prop]
            else:
                log.warning(
                    "KoboTouchExtended:_modify_file:Calibre details file does "
                    "not exist!"
                )
            o["kobotouchextended_version"] = ".".join([str(n) for n in self.version])
            o["kobotouchextended_options"] = str(opts.extra_customization)
            o["kobotouchextended_currenttime"] = datetime.utcnow().ctime()
            kte_data_file = self.temporary_file("_KoboTouchExtendedDriverInfo")
            log.debug(
                "KoboTouchExtended:_modify_epub:Driver data file :: {0}".format(
                    kte_data_file.name
                )
            )
            kte_data_file.write(json.dumps(o).encode("UTF-8"))
            kte_data_file.close()
            container.copy_file_to_container(
                kte_data_file.name, name="driverinfo.kte", mt="application/json"
            )

            modify_epub(
                container,
                infile,
                metadata=metadata,
                opts={
                    "clean_markup": self.clean_markup,
                    "hyphenate": self.hyphenate and not self.disable_hyphenation,
                    "no-hyphens": self.disable_hyphenation,
                    "smarten_punctuation": self.smarten_punctuation,
                    "extended_kepub_features": self.extra_features,
                },
            )
        except Exception as e:
            log.exception(
                "Failed to process {0} by {1}: {2}".format(
                    metadata.title, " and ".join(metadata.authors), e.message,
                )
            )

            if not skip_failed:
                raise

            self.skip_renaming_files.add(metadata.uuid)
            return super(KOBOTOUCHEXTENDED, self)._modify_epub(
                infile, metadata, container
            )

        if not self.extra_features:
            self.skip_renaming_files.add(metadata.uuid)

        dpath = self.file_copy_dir or ""
        if dpath != "":
            dpath = os.path.expanduser(dpath).strip()
            dpath = self.create_upload_path(dpath, metadata, metadata.kte_calibre_name)
            log.info(
                "KoboTouchExtended:_modify_epub:Generated KePub file copy "
                "path: {0}".format(dpath)
            )
            shutil.copy(infile, dpath)

        retval = super(KOBOTOUCHEXTENDED, self)._modify_epub(
            infile, metadata, container
        )
        if retval:
            container.commit(outpath=infile)
        return retval

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        """Process sending the book to the Kobo device."""
        if self.modifying_css():
            log.info(
                "KoboTouchExtended:upload_books:Searching for device-specific "
                "CSS file"
            )
            device_css_file_name = self.KOBO_EXTRA_CSSFILE
            try:
                if self.isAuraH2O():
                    device_css_file_name = "kobo_extra_AURAH2O.css"
                elif self.isAuraHD():
                    device_css_file_name = "kobo_extra_AURAHD.css"
                elif self.isAura():
                    device_css_file_name = "kobo_extra_AURA.css"
                elif self.isGlo():
                    device_css_file_name = "kobo_extra_GLO.css"
                elif self.isGloHD():
                    device_css_file_name = "kobo_extra_GLOHD.css"
                elif self.isMini():
                    device_css_file_name = "kobo_extra_MINI.css"
                elif self.isTouch():
                    device_css_file_name = "kobo_extra_TOUCH.css"
            except AttributeError:
                log.warning(
                    "KoboTouchExtended:upload_books:Calibre version too old "
                    "to handle some specific devices, falling back to "
                    "generic file {0}".format(device_css_file_name)
                )
            device_css_file_name = os.path.join(self.configdir, device_css_file_name)
            if os.path.isfile(device_css_file_name):
                log.info(
                    "KoboTouchExtended:upload_books:Found device-specific "
                    "file {0}".format(device_css_file_name)
                )
                shutil.copy(
                    device_css_file_name,
                    os.path.join(self._main_prefix, self.KOBO_EXTRA_CSSFILE),
                )
            else:
                log.info(
                    "KoboTouchExtended:upload_books:No device-specific CSS "
                    "file found (expecting {0})".format(device_css_file_name)
                )

        kobo_config_file = os.path.join(
            self._main_prefix, ".kobo", "Kobo", "Kobo eReader.conf"
        )
        if os.path.isfile(kobo_config_file):
            cfg = SafeConfigParser(allow_no_value=True)
            cfg.optionxform = str
            cfg.read(kobo_config_file)

            if not cfg.has_section("FeatureSettings"):
                cfg.add_section("FeatureSettings")
            log.info(
                "KoboTouchExtended:upload_books:Setting FeatureSettings."
                "FullBookPageNumbers to {0}".format(
                    "true" if self.full_page_numbers else "false"
                )
            )
            cfg.set(
                "FeatureSettings",
                "FullBookPageNumbers",
                "true" if self.full_page_numbers else "false",
            )
            with open(kobo_config_file, "w") as cfgfile:
                cfg.write(cfgfile)

        return super(KOBOTOUCHEXTENDED, self).upload_books(
            files, names, on_card, end_session, metadata
        )

    def filename_callback(self, path, mi):
        """Ensure the filename on the device is correct."""
        if self.extra_features:
            log.debug("KoboTouchExtended:filename_callback:Path - {0}".format(path))
            if path.endswith(KEPUB_EXT):
                path += EPUB_EXT
            elif path.endswith(EPUB_EXT) and mi.uuid not in self.skip_renaming_files:
                path = path[: -len(EPUB_EXT)] + KEPUB_EXT + EPUB_EXT

            log.debug("KoboTouchExtended:filename_callback:New path - {0}".format(path))
        return path

    def sanitize_path_components(self, components):
        """Perform any sanitization of path components."""
        return [self.invalid_filename_chars_re.sub("_", x) for x in components]

    def sync_booklists(self, booklists, end_session=True):
        """Synchronize book lists between calibre and the Kobo device."""
        if self.upload_covers:
            log.info("KoboTouchExtended:sync_booklists:Setting ImageId fields")

            select_query = (
                "SELECT ContentId FROM content WHERE "
                + "ContentType = ? AND "
                + "(ImageId IS NULL OR ImageId = '')"
            )
            update_query = "UPDATE content SET ImageId = ? WHERE ContentId = ?"
            try:
                db = self.device_database_connection()
            except AttributeError:
                import apsw

                db = apsw.Connection(self.device_database_path())

            def __rows_needing_imageid():
                """Map row ContentID entries needing an ImageID.

                Returns a dict object with keys being the ContentID of a row
                without an ImageID.
                """
                c = db.cursor()
                d = {}
                log.debug(
                    "KoboTouchExtended:sync_booklists:About to call query: "
                    "{0}".format(select_query)
                )
                c.execute(select_query, (self.content_types["main"],))
                for row in c:
                    d[row[0]] = 1
                return d

            all_nulls = __rows_needing_imageid()
            log.debug(
                "KoboTouchExtended:sync_booklists:Got {0:d} rows to "
                "update".format(len(list(all_nulls.keys())))
            )
            nulls = []
            for booklist in booklists:
                for b in booklist:
                    if b.application_id is not None and b.contentID in all_nulls:
                        nulls.append(
                            (self.imageid_from_contentid(b.contentID), b.contentID)
                        )
            del all_nulls

            cursor = db.cursor()
            while nulls[:100]:
                log.debug(
                    "KoboTouchExtended:sync_booklists:Updating {0:d} "
                    "ImageIDs...".format(len(nulls[:100]))
                )
                cursor.executemany(update_query, nulls[:100])
                del nulls[:100]
            cursor.close()
            db.close()
            log.debug("KoboTouchExtended:sync_booklists:done setting ImageId fields")

        super(KOBOTOUCHEXTENDED, self).sync_booklists(booklists, end_session)

    @classmethod
    def _config(cls):
        c = super(KOBOTOUCHEXTENDED, cls)._config()

        c.add_opt("extra_features", default=True)
        c.add_opt("upload_encumbered", default=False)
        c.add_opt("skip_failed", default=False)
        c.add_opt("hyphenate", default=False)
        c.add_opt("smarten_punctuation", default=False)
        c.add_opt("clean_markup", default=False)
        c.add_opt("full_page_numbers", default=False)
        c.add_opt("disable_hyphenation", default=False)
        c.add_opt("file_copy_dir", default="")

        # remove_opt verifies the preference is present first
        c.remove_opt("replace_lang")

        return c

    @classmethod
    def migrate_old_settings(cls, settings):
        """Migrate old settings to the new format."""
        log.debug("KoboTouchExtended::migrate_old_settings - start")
        settings = super(KOBOTOUCHEXTENDED, cls).migrate_old_settings(settings)
        log.debug(
            "KoboTouchExtended::migrate_old_settings - end",
            settings.extra_customization,
        )

        count_options = 0
        opt_extra_features = count_options
        count_options += 1
        opt_upload_encumbered = count_options
        count_options += 1
        opt_skip_failed = count_options
        count_options += 1
        opt_hypnenate = count_options
        count_options += 1
        opt_smarten_punctuation = count_options
        count_options += 1
        opt_clean_markup = count_options
        count_options += 1
        opt_full_page_numbers = count_options
        count_options += 1
        opt_file_copy_dir = count_options
        count_options += 1
        opt_disable_hyphenation = count_options

        if len(settings.extra_customization) >= count_options:
            log.warning(
                "KoboTouchExtended::migrate_old_settings - settings need to "
                "be migrated"
            )
            try:
                settings.extra_features = settings.extra_customization[
                    opt_extra_features
                ]
            except IndexError:
                pass
            try:
                settings.upload_encumbered = settings.extra_customization[
                    opt_upload_encumbered
                ]
            except IndexError:
                pass
            try:
                settings.skip_failed = settings.extra_customization[opt_skip_failed]
            except IndexError:
                pass
            try:
                settings.hyphenate = settings.extra_customization[opt_hypnenate]
            except IndexError:
                pass
            try:
                settings.smarten_punctuation = settings.extra_customization[
                    opt_smarten_punctuation
                ]
            except IndexError:
                pass
            try:
                settings.clean_markup = settings.extra_customization[opt_clean_markup]
            except IndexError:
                pass
            try:
                settings.file_copy_dir = settings.extra_customization[opt_file_copy_dir]
                if not isinstance(settings.file_copy_dir, str):
                    settings.file_copy_dir = None
            except IndexError:
                pass
            try:
                settings.full_page_numbers = settings.extra_customization[
                    opt_full_page_numbers
                ]
            except IndexError:
                pass
            try:
                settings.disable_hyphenation = settings.extra_customization[
                    opt_disable_hyphenation
                ]
            except IndexError:
                pass

            settings.extra_customization = settings.extra_customization[
                count_options + 1 :  # noqa:E203 - thanks Black formatting!
            ]
            log.info(
                "KoboTouchExtended::migrate_old_settings - end",
                settings.extra_customization,
            )

        return settings

    @property
    def extra_features(self):
        """Determine if extra Kobo features are being applied."""
        return self.get_pref("extra_features")

    @property
    def upload_encumbered(self):
        """Determine if DRM-encumbered files will be uploaded."""
        return self.get_pref("upload_encumbered")

    @property
    def skip_failed(self):
        """Determine if failed conversions will be skipped."""
        return self.get_pref("skip_failed")

    @property
    def hyphenate(self):
        """Determine if hyphenation will be enabled."""
        return self.get_pref("hyphenate")

    @property
    def smarten_punctuation(self):
        """Determine if punctuation will be made into smart punctuation."""
        return self.get_pref("smarten_punctuation")

    @property
    def clean_markup(self):
        """Determine if additional cleanup will be done on the book contents."""
        return self.get_pref("clean_markup")

    @property
    def full_page_numbers(self):
        """Determine if the device should display book page numbers."""
        return self.get_pref("full_page_numbers")

    @property
    def disable_hyphenation(self):
        """Determine if hyphenation should be disabled."""
        return self.get_pref("disable_hyphenation")

    @property
    def file_copy_dir(self):
        """Determine where to copy converted books to."""
        return self.get_pref("file_copy_dir")
