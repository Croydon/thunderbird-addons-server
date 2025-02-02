import mock
import os
import pytest
import tempfile
from datetime import datetime

from django.conf import settings
from django.test.utils import override_settings

from freezegun import freeze_time

from olympia import amo
from olympia.activity.models import ActivityLog
from olympia.addons import cron
from olympia.addons.models import AddonCategory, MigratedLWT, Addon
from olympia.addons.tasks import (
    add_static_theme_from_lwt, create_persona_preview_images,
    migrate_legacy_dictionary_to_webextension, migrate_lwts_to_static_themes,
    save_persona_image, migrate_addons_that_require_sensitive_data_access)
from olympia.amo.storage_utils import copy_stored_file
from olympia.amo.tests import addon_factory, TestCase, user_factory
from olympia.amo.tests.test_helpers import get_image_path
from olympia.amo.utils import image_size
from olympia.applications.models import AppVersion
from olympia.constants import licenses
from olympia.constants.categories import CATEGORIES
from olympia.files.models import FileUpload
from olympia.ratings.models import Rating
from olympia.stats.models import ThemeUpdateCount, UpdateCount
from olympia.tags.models import Tag
from olympia.users.models import UserProfile
from olympia.versions.models import License


class TestPersonaImageFunctions(TestCase):
    @mock.patch('olympia.addons.tasks.pngcrush_image')
    def test_create_persona_preview_image(self, pngcrush_image_mock):
        addon = addon_factory()
        addon.modified = self.days_ago(41)
        # Given an image, a 680x100 and a 32x32 thumbnails need to be generated
        # and processed with pngcrush.
        expected_dst1 = tempfile.NamedTemporaryFile(
            mode='r+w+b', suffix=".png", delete=False, dir=settings.TMP_PATH)
        expected_dst2 = tempfile.NamedTemporaryFile(
            mode='r+w+b', suffix=".png", delete=False, dir=settings.TMP_PATH)
        create_persona_preview_images(
            src=get_image_path('persona-header.jpg'),
            full_dst=[expected_dst1.name, expected_dst2.name],
            set_modified_on=addon.serializable_reference(),
        )
        # pngcrush_image should have been called twice, once for each
        # destination thumbnail.
        assert pngcrush_image_mock.call_count == 2
        assert pngcrush_image_mock.call_args_list[0][0][0] == (
            expected_dst1.name)
        assert pngcrush_image_mock.call_args_list[1][0][0] == (
            expected_dst2.name)

        assert image_size(expected_dst1.name) == (680, 100)
        assert image_size(expected_dst2.name) == (32, 32)

        addon.reload()
        self.assertCloseToNow(addon.modified)

    @mock.patch('olympia.addons.tasks.pngcrush_image')
    def test_save_persona_image(self, pngcrush_image_mock):
        # save_persona_image() simply saves an image as a png to the
        # destination file. The image should be processed with pngcrush.
        expected_dst = tempfile.NamedTemporaryFile(
            mode='r+w+b', suffix=".png", delete=False, dir=settings.TMP_PATH)
        save_persona_image(
            get_image_path('persona-header.jpg'),
            expected_dst.name
        )
        # pngcrush_image should have been called once.
        assert pngcrush_image_mock.call_count == 1
        assert pngcrush_image_mock.call_args_list[0][0][0] == expected_dst.name

    @mock.patch('olympia.addons.tasks.pngcrush_image')
    def test_save_persona_image_not_an_image(self, pngcrush_image_mock):
        # If the source is not an image, save_persona_image() should just
        # return early without writing the destination or calling pngcrush.
        expected_dst = tempfile.NamedTemporaryFile(
            mode='r+w+b', suffix=".png", delete=False, dir=settings.TMP_PATH)
        save_persona_image(
            get_image_path('non-image.png'),
            expected_dst.name
        )
        # pngcrush_image should not have been called.
        assert pngcrush_image_mock.call_count == 0
        # the destination file should not have been written to.
        assert os.stat(expected_dst.name).st_size == 0


@pytest.mark.django_db
@mock.patch('olympia.addons.tasks.add_static_theme_from_lwt')
def test_migrate_lwts_to_static_themes(add_static_theme_from_lwt_mock):
    persona_a = addon_factory(type=amo.ADDON_PERSONA, slug='theme_a')
    persona_b = addon_factory(type=amo.ADDON_PERSONA, slug='theme_b')
    addon_a = addon_factory(type=amo.ADDON_STATICTHEME)
    addon_b = addon_factory(type=amo.ADDON_STATICTHEME)
    add_static_theme_from_lwt_mock.side_effect = [addon_a, addon_b]

    # call the migration task, as the command would:
    migrate_lwts_to_static_themes([persona_a.id, persona_b.id])

    assert MigratedLWT.objects.all().count() == 2

    persona_a.reload()
    addon_a.reload()
    assert persona_a.status == amo.STATUS_DELETED
    assert MigratedLWT.objects.get(
        lightweight_theme=persona_a).static_theme == addon_a
    assert addon_a.slug == 'theme_a'

    persona_b.reload()
    addon_a.reload()
    assert persona_b.status == amo.STATUS_DELETED
    assert MigratedLWT.objects.get(
        lightweight_theme=persona_b).static_theme == addon_b
    assert addon_b.slug == 'theme_b'


@override_settings(ENABLE_ADDON_SIGNING=True)
class TestAddStaticThemeFromLwt(TestCase):
    create_date = datetime(2000, 1, 1, 1, 1, 1)
    modify_date = datetime(2008, 8, 8, 8, 8, 8)
    update_date = datetime(2009, 9, 9, 9, 9, 9)

    def setUp(self):
        super(TestAddStaticThemeFromLwt, self).setUp()
        self.call_signing_mock = self.patch(
            'olympia.lib.crypto.packaged.call_signing')
        self.build_mock = self.patch(
            'olympia.addons.tasks.build_static_theme_xpi_from_lwt')
        self.build_mock.side_effect = self._mock_xpi_side_effect
        self.call_signing_mock.return_value = 'abcdefg1234'
        AppVersion.objects.get_or_create(
            application=amo.FIREFOX.id, version='53.0')
        AppVersion.objects.get_or_create(
            application=amo.FIREFOX.id, version='*')

    def _mock_xpi_side_effect(self, lwt, upload_path):
        xpi_path = os.path.join(
            settings.ROOT, 'src/olympia/devhub/tests/addons/static_theme.zip')
        copy_stored_file(xpi_path, upload_path)
        assert not os.path.isdir(upload_path)
        return mock.DEFAULT

    def _check_result(self, static_theme, authors, tags, categories, license_,
                      ratings):
        # metadata is correct
        assert list(static_theme.authors.all()) == authors
        assert list(static_theme.tags.all()) == tags
        assert [cat.name for cat in static_theme.all_categories] == [
            cat.name for cat in categories]
        assert static_theme.current_version.license.builtin == license_
        # status is good
        assert static_theme.status == amo.STATUS_PUBLIC
        current_file = static_theme.current_version.files.get()
        assert current_file.status == amo.STATUS_PUBLIC
        # Ratings were migrated
        assert list(Rating.unfiltered.filter(addon=static_theme)) == ratings
        log_entries = ActivityLog.objects.filter(
            action=amo.LOG.ADD_RATING.id, addonlog__addon=static_theme)
        assert log_entries.count() == len(ratings)
        for rating, log_entry in zip(ratings, log_entries):
            arguments = log_entry.arguments
            assert rating in arguments
            assert static_theme in arguments
        # UpdateCounts were copied.
        assert UpdateCount.objects.filter(
            addon_id=static_theme.id).count() == 2
        # xpi was signed
        self.call_signing_mock.assert_called_with(current_file)
        assert current_file.cert_serial_num == 'abcdefg1234'
        assert static_theme.created == self.create_date
        assert static_theme.modified == self.modify_date
        cron.addon_last_updated()  # Make sure the last_updated change stuck.
        assert static_theme.reload().last_updated == self.update_date

    def test_add_static_theme_from_lwt(self):
        author = user_factory()
        persona = addon_factory(type=amo.ADDON_PERSONA, users=[author])
        persona.update(
            created=self.create_date, modified=self.modify_date,
            last_updated=self.update_date)
        persona.persona.license = licenses.LICENSE_CC_BY_ND.id
        Tag.objects.create(tag_text='themey').save_tag(persona)
        License.objects.create(builtin=licenses.LICENSE_CC_BY_ND.builtin)
        rating_user = user_factory()
        rating = Rating.objects.create(
            addon=persona, version=persona.current_version, user=rating_user,
            rating=2, body=u'fooooo', user_responsible=rating_user)
        ThemeUpdateCount.objects.create(
            addon_id=persona.id, date=datetime(2018, 1, 1), count=123)
        ThemeUpdateCount.objects.create(
            addon_id=persona.id, date=datetime(2018, 2, 1), count=456)
        # Create a count for an addon that shouldn't be migrated too.
        ThemeUpdateCount.objects.create(
            addon_id=addon_factory().id, date=datetime(2018, 2, 1), count=45)

        static_theme = add_static_theme_from_lwt(persona)

        self._check_result(
            static_theme, [author], list(persona.tags.all()),
            persona.all_categories, licenses.LICENSE_CC_BY_ND.builtin,
            [rating])

    def test_add_static_theme_broken_lwt(self):
        """What if no author or license or category?"""
        persona = addon_factory(type=amo.ADDON_PERSONA)
        persona.update(
            created=self.create_date, modified=self.modify_date,
            last_updated=self.update_date)

        assert list(persona.authors.all()) == []  # no author
        persona.persona.license = None  # no license
        AddonCategory.objects.filter(addon=persona).delete()
        assert persona.all_categories == []  # no category
        License.objects.create(builtin=licenses.LICENSE_COPYRIGHT_AR.builtin)
        rating_user = user_factory()
        rating = Rating.objects.create(
            addon=persona, version=persona.current_version, user=rating_user,
            rating=2, body=u'fooooo', user_responsible=rating_user)
        rating.delete()  # delete the rating - should still be migrated.
        # Add 2 more Ratings for different addons that shouldn't be copied.
        Rating.objects.create(
            addon=addon_factory(), user=rating_user,
            rating=3, body=u'tgd', user_responsible=rating_user)
        Rating.objects.create(
            addon=addon_factory(), user=rating_user,
            rating=4, body=u'tgffd', user_responsible=rating_user)
        ThemeUpdateCount.objects.create(
            addon_id=persona.id, date=datetime(2018, 1, 1), count=123)
        ThemeUpdateCount.objects.create(
            addon_id=persona.id, date=datetime(2018, 2, 1), count=456)
        # Create a count for an addon that shouldn't be migrated too.
        ThemeUpdateCount.objects.create(
            addon_id=addon_factory().id, date=datetime(2018, 2, 1), count=45)

        static_theme = add_static_theme_from_lwt(persona)

        default_author = UserProfile.objects.get(
            email=settings.MIGRATED_LWT_DEFAULT_OWNER_EMAIL)
        default_category = (
            CATEGORIES[amo.THUNDERBIRD.id][amo.ADDON_STATICTHEME]['other'])
        self._check_result(
            static_theme, [default_author], [], [default_category],
            licenses.LICENSE_COPYRIGHT_AR.builtin, [rating])
        # Double check its the exact category we want.
        assert static_theme.all_categories == [default_category]


@override_settings(ENABLE_ADDON_SIGNING=True)
class TestMigrateLegacyDictionaryToWebextension(TestCase):
    def setUp(self):
        self.user = user_factory(
            id=settings.TASK_USER_ID, username='taskuser',
            email='taskuser@mozilla.com')
        with freeze_time('2017-07-27 07:00'):
            self.addon = addon_factory(
                type=amo.ADDON_DICT,
                guid='@my-dict',  # Same id used in dict-webext.xpi.
                version_kw={'version': '6.3'})

        AppVersion.objects.get_or_create(
            application=amo.FIREFOX.id, version='61.0')
        AppVersion.objects.get_or_create(
            application=amo.FIREFOX.id, version='*')

        self.call_signing_mock = self.patch(
            'olympia.lib.crypto.packaged.call_signing')
        self.call_signing_mock.return_value = 'abcdefg1234'

        self.build_mock = self.patch(
            'olympia.addons.tasks.build_webext_dictionary_from_legacy')
        self.build_mock.side_effect = self._mock_xpi_side_effect

    def _mock_xpi_side_effect(self, addon, destination):
        xpi_path = os.path.join(
            settings.ROOT, 'src/olympia/files/fixtures/files/dict-webext.xpi')
        copy_stored_file(xpi_path, destination)
        assert not os.path.isdir(destination)
        return mock.DEFAULT

    def test_basic(self):
        assert not FileUpload.objects.exists()
        assert not ActivityLog.objects.filter(
            action=amo.LOG.ADD_VERSION.id).exists()
        old_version = self.addon.current_version

        self.build_mock.return_value = 'fake-locale'

        with freeze_time('2018-08-28 08:00'):
            self.migration_date = datetime.now()
            migrate_legacy_dictionary_to_webextension(self.addon)

        self.build_mock.assert_called_once_with(self.addon, mock.ANY)
        assert FileUpload.objects.exists()
        self.addon.reload()
        assert self.addon.target_locale == 'fake-locale'
        assert self.addon.current_version != old_version
        activity_log = ActivityLog.objects.filter(
            action=amo.LOG.ADD_VERSION.id).get()
        assert activity_log.arguments == [
            self.addon.current_version, self.addon
        ]

        assert self.addon.last_updated == self.migration_date

        current_file = self.addon.current_version.all_files[0]
        assert current_file.datestatuschanged == self.migration_date
        assert current_file.status == amo.STATUS_PUBLIC

        self.call_signing_mock.assert_called_with(current_file)
        assert current_file.cert_serial_num == 'abcdefg1234'


# migrate_addons_that_require_sensitive_data_access
class TestMigrateAddonsThatRequireSensitiveDataAccess(TestCase):

    def setUp(self):
        self.user = user_factory(
            id=settings.TASK_USER_ID, username='taskuser',
            email='taskuser@mozilla.com')

        # Commented out until test fixes are merged
        #create_default_webext_appversion()
        # Inlined until test fixes are merged
        from olympia.applications.models import AppVersion
        AppVersion.objects.get_or_create(
            application=amo.THUNDERBIRD.id, version='60.0')  # Default Min Version
        AppVersion.objects.get_or_create(
            application=amo.THUNDERBIRD.id, version='102.0') # Targeted Max Version
        AppVersion.objects.get_or_create(
            application=amo.THUNDERBIRD.id, version='*')


    def test_addon_with_sda(self):
        """Test addon with sensitive permissions. This should cause both the Addon and AddonReviewer Flags to be True"""
        addon_with_sda = addon_factory(
            version_kw={'application': amo.THUNDERBIRD.id},
            file_kw={
                'is_webextension': True,
                'permissions': [
                    'messagesRead'
                ]
        })

        assert addon_with_sda.requires_sensitive_data_access is False
        assert not addon_with_sda.needs_sensitive_data_access_review

        migrate_addons_that_require_sensitive_data_access([addon_with_sda.pk])

        # Reload the addon
        addon_with_sda = Addon.objects.get(pk=addon_with_sda.pk)

        assert addon_with_sda.requires_sensitive_data_access is True
        assert addon_with_sda.needs_sensitive_data_access_review is True

    def test_addon_without_sda(self):
        """Test addon without sensitive permissions. No flags should be set to True"""
        addon_without_sda = addon_factory(
            version_kw={'application': amo.THUNDERBIRD.id},
            file_kw={
                'is_webextension': True,
                'permissions': [
                    'tabs'
                ]
        })

        assert addon_without_sda.requires_sensitive_data_access is False
        assert not addon_without_sda.needs_sensitive_data_access_review

        migrate_addons_that_require_sensitive_data_access([addon_without_sda.pk])

        # Reload the addon
        addon_without_sda = Addon.objects.get(pk=addon_without_sda.pk)

        assert addon_without_sda.requires_sensitive_data_access is False
        assert not addon_without_sda.needs_sensitive_data_access_review

    def test_addon_with_sda_and_sensitive_data_upload(self):
        """Test addon with sensitive permissions, but also an explicitly set `sensitiveDataUpload` permission. The Addon Flag should be True, but the AddonReviewer Flag should not"""
        addon_with_sda_and_sensitive_data_upload = addon_factory(
            version_kw={'application': amo.THUNDERBIRD.id},
            file_kw={
                'is_webextension': True,
                'permissions': [
                    'messagesRead',
                    'sensitiveDataUpload'
                ]
        })

        assert addon_with_sda_and_sensitive_data_upload.requires_sensitive_data_access is False
        assert not addon_with_sda_and_sensitive_data_upload.needs_sensitive_data_access_review

        migrate_addons_that_require_sensitive_data_access([addon_with_sda_and_sensitive_data_upload.pk])

        # Reload the addon
        addon_with_sda_and_sensitive_data_upload = Addon.objects.get(pk=addon_with_sda_and_sensitive_data_upload.pk)

        assert addon_with_sda_and_sensitive_data_upload.requires_sensitive_data_access is True
        assert not addon_with_sda_and_sensitive_data_upload.needs_sensitive_data_access_review
