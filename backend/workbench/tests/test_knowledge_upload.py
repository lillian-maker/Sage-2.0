import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from backend.workbench.models import KnowledgeDocument
from backend.workbench.tests.base import API
from backend.workbench.tests.test_experts_knowledge import KnowledgeFixtures


class KnowledgeUploadTests(KnowledgeFixtures):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix='sage-upload-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        override = override_settings(KNOWLEDGE_UPLOAD_DIR=self.dir)
        override.enable()
        self.addCleanup(override.disable)

    def upload(self, name='竞品清单.xlsx', content=b'PK\x03\x04 data', **fields):
        data = {'knowledge_base_id': self.ws_kb.public_id, **fields}
        if name is not None:
            data['file'] = SimpleUploadedFile(name, content, content_type='application/octet-stream')
        return self.client.post(API + 'app/documents/upload', data, **self._headers(self.client, None, {}))

    def stored_files(self):
        return [p for p in Path(self.dir).rglob('*') if p.is_file()]

    def test_upload_creates_queued_document_and_download_round_trips(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.upload(), 201)['document']
        self.assertEqual((body['title'], body['source_kind'], body['index_state']), ('竞品清单.xlsx', 'upload', 'queued'))
        self.assertEqual(body['size_bytes'], len(b'PK\x03\x04 data'))
        self.assertEqual(body['uploaded_by']['name'], 'Lillian Li')
        files = self.stored_files()
        self.assertEqual(len(files), 1)
        self.assertNotIn('竞品清单', files[0].name)
        self.assertTrue(files[0].name.endswith('.xlsx'))
        response = self.client.get(API + 'app/documents/%s/download' % body['id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'PK\x03\x04 data')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_custom_title(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.upload(title='竞品价格带'), 201)['document']
        self.assertEqual(body['title'], '竞品价格带')

    def test_path_traversal_name_stays_inside_upload_dir(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.upload(name='../../../etc/evil.pdf'), 201)['document']
        self.assertEqual(body['title'], 'evil.pdf')
        files = self.stored_files()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].resolve().is_relative_to(Path(self.dir).resolve()))

    def test_rejects_disallowed_type_and_oversize(self):
        self.login(self.lillian, self.business)
        before = KnowledgeDocument.objects.count()
        self.assert_error(self.upload(name='run.exe'), 422, 'VALIDATION_FAILED')
        self.assert_error(self.upload(name='noext'), 422, 'VALIDATION_FAILED')
        with override_settings(KNOWLEDGE_UPLOAD_MAX_BYTES=4):
            self.assert_error(self.upload(), 422, 'VALIDATION_FAILED')
        self.assert_error(self.upload(content=b''), 422, 'VALIDATION_FAILED')
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(KnowledgeDocument.objects.count(), before)

    def test_request_shape(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.upload(name=None), 422, 'INVALID_REQUEST')
        self.assert_error(self.upload(extra='x'), 422, 'INVALID_REQUEST')
        self.assert_error(self.post('app/documents/upload', {'knowledge_base_id': self.ws_kb.public_id}),
                          422, 'INVALID_REQUEST')

    def test_requires_edit_rights(self):
        self.login(self.mark, self.business)
        self.assert_error(self.upload(), 403, 'PERMISSION_DENIED')
        self.assertEqual(self.stored_files(), [])
