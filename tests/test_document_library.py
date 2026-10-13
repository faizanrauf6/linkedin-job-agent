"""Supporting files stay original, private, explicit, and separate from CVs."""
import asyncio
import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from agent.models import Resume
from agent.store import Store


def pdf_bytes(pages=1, encrypted=False):
    output=io.BytesIO()
    pdf=PdfWriter()
    for _ in range(pages):
        pdf.add_blank_page(width=100,height=100)
    if encrypted:
        pdf.encrypt('secret')
    pdf.write(output)
    return output.getvalue()


def docx_bytes():
    output=io.BytesIO()
    document=Document()
    document.add_paragraph('Confirmed nursing certificate original.')
    document.save(output)
    return output.getvalue()


@pytest.fixture
def library(tmp_path):
    from agent.document_library import DocumentLibrary
    return DocumentLibrary(Store(tmp_path/'agent.db'),tmp_path)


def confirm(library,data=b'Original license',filename='license.txt',label='Nursing license',kind='license'):
    record=library.import_document(data,filename,label,kind)
    record.confirmed=True
    return library.save(record)


@pytest.mark.parametrize('filename,data',[
    ('../../license.pdf',pdf_bytes()),
    ('C:\\fakepath\\certificate.docx',docx_bytes()),
    ('cover.txt',b'Dear hiring team, these are my confirmed facts.'),
],ids=['image-pdf','word','text'])
def test_import_roundtrips_original_files_without_extracting_claims(library,filename,data):
    from agent.document_library import DocumentLibrary
    record=library.import_document(data,filename,'Supporting document','certificate')
    assert not record.confirmed
    assert Path(record.path).read_bytes()==data
    assert record.sha256==hashlib.sha256(data).hexdigest()
    assert record.filename in {'license.pdf','certificate.docx','cover.txt'}
    assert 'text' not in record.model_dump()
    restored=DocumentLibrary(library.store,library.runtime).documents()[0]
    assert restored.id==record.id and restored.sha256==record.sha256


@pytest.mark.parametrize('filename,data',[
    ('empty.txt',b''),('fake.pdf',b'Not PDF'),('fake.docx',b'Not DOCX'),
    ('script.py',b'print(1)'),('binary.txt',b'MZ\x00\x01binary'),
    ('invalid.txt',b'\xff\xfe\xfd'),('large.txt',b'x'*(10*1024*1024+1)),
    ('encrypted.pdf',pdf_bytes(encrypted=True)),('long.pdf',pdf_bytes(pages=101)),
],ids=['empty','fake-pdf','fake-docx','unsupported','binary-text','invalid-utf8','oversize','encrypted','page-limit'])
def test_invalid_documents_are_rejected_without_persisting_metadata(library,filename,data):
    with pytest.raises(ValueError):
        library.import_document(data,filename,'Invalid','other')
    assert library.documents()==[]
    assert not library.store.meta('document_generation')


def test_docx_expansion_is_bounded(library):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',b'x'*(25*1024*1024+1))
    with pytest.raises(ValueError):
        library.import_document(output.getvalue(),'bomb.docx','Invalid','other')
    assert library.documents()==[]


def test_confirmation_and_generation_only_change_on_real_updates(library):
    record=library.import_document(b'Original certificate','certificate.txt','BLS','certificate')
    assert library.store.meta('document_generation')=='1'
    library.save(record)
    assert library.store.meta('document_generation')=='1'
    record.confirmed=True
    library.save(record)
    assert library.store.meta('document_generation')=='2'
    library.save(record)
    assert library.store.meta('document_generation')=='2'
    library.delete(record.id)
    assert library.store.meta('document_generation')=='3'
    with pytest.raises(KeyError):
        library.delete(record.id)
    assert library.store.meta('document_generation')=='3'


def test_save_rejects_files_outside_library_even_with_correct_hash(library,tmp_path):
    record=confirm(library)
    outside=tmp_path/'outside.txt'
    outside.write_bytes(b'Original license')
    forged=record.model_copy(update={'path':str(outside)})
    with pytest.raises(ValueError):
        library.save(forged)
    assert library.documents()[0].path==record.path
    assert outside.read_bytes()==b'Original license'


def test_symlink_document_directory_is_rejected(tmp_path):
    from agent.document_library import DocumentLibrary
    outside=tmp_path/'outside'
    outside.mkdir()
    (tmp_path/'documents').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):
        DocumentLibrary(Store(tmp_path/'agent.db'),tmp_path)
    assert list(outside.iterdir())==[]


def test_changed_original_cannot_be_confirmed_or_selected(library):
    from agent.document_library import document_for_field
    record=confirm(library)
    Path(record.path).write_bytes(b'Changed original')
    assert document_for_field('License',library.documents()) is None
    with pytest.raises(ValueError):
        library.save(record)


def test_symlink_original_is_not_read_or_selected(library,tmp_path):
    from agent.document_library import document_for_field,verified_document_path
    record=confirm(library)
    outside=tmp_path/'outside.txt'
    outside.write_bytes(b'Original license')
    Path(record.path).unlink()
    Path(record.path).symlink_to(outside)
    assert document_for_field('License',library.documents()) is None
    with pytest.raises(ValueError):
        verified_document_path(record,library.runtime)
    assert outside.read_bytes()==b'Original license'


def test_selection_uses_unique_confirmation_and_exact_label_disambiguation(library):
    from agent.document_library import document_for_field
    nursing=confirm(library,label='Nursing license')
    driving=confirm(library,label='Driving license')
    assert document_for_field('License',library.documents()) is None
    assert document_for_field('Upload nursing license',library.documents()).id==nursing.id
    assert document_for_field('Driving license *',library.documents()).id==driving.id
    nursing.confirmed=False
    library.save(nursing)
    assert document_for_field('Nursing license',library.documents()) is None
    assert document_for_field('License',library.documents()).id==driving.id


def test_selection_never_reuses_cv_or_guesses_generic_attachment(library):
    from agent.document_library import document_for_field
    record=confirm(library)
    cv=Resume(label='License',filename='cv.pdf',path=record.path,sha256=record.sha256,confirmed=True)
    assert document_for_field('License',[cv]) is None
    assert document_for_field('Resume',library.documents()) is None
    assert document_for_field('Supporting attachment',library.documents()) is None
    assert document_for_field('Cover letter',library.documents()) is None


@pytest.fixture
def client(tmp_path):
    from agent.document_api import register_document_routes
    app=FastAPI()
    store=Store(tmp_path/'agent.db')
    worker=SimpleNamespace(busy=False,run_lock=asyncio.Lock())
    register_document_routes(app,store,worker,tmp_path)
    app.state.store,app.state.worker=store,worker
    with TestClient(app) as client:
        yield client


def upload(client):
    return client.post('/api/documents',files={'file':('license.txt',b'Original license')},data={'label':'Nursing license','kind':'license'})


def test_api_upload_requires_explicit_confirmation_and_hides_local_path(client):
    response=upload(client)
    assert response.status_code==200
    record=response.json()
    assert not record['confirmed'] and 'path' not in record
    assert client.get('/api/documents').json()==[record]
    confirmed=client.patch('/api/documents/'+record['id'],json={'confirmed':True})
    assert confirmed.status_code==200 and confirmed.json()['confirmed']
    assert 'path' not in confirmed.json()
    download=client.get('/api/documents/'+record['id']+'/file')
    assert download.status_code==200 and download.content==b'Original license'
    assert client.delete('/api/documents/'+record['id']).status_code==200
    assert client.get('/api/documents').json()==[]


@pytest.mark.parametrize('state',['unpaused','busy','locked'])
def test_all_api_mutations_require_paused_idle_agent(client,state):
    record=upload(client).json()
    worker,store=client.app.state.worker,client.app.state.store
    if state=='unpaused':
        config=store.config()
        config.settings.paused=False
        store.save_config(config)
    elif state=='busy':
        worker.busy=True
    else:
        asyncio.run(worker.run_lock.acquire())
    generation=store.meta('document_generation')
    assert upload(client).status_code==409
    assert client.patch('/api/documents/'+record['id'],json={'confirmed':True}).status_code==409
    assert client.delete('/api/documents/'+record['id']).status_code==409
    assert store.meta('document_generation')==generation
    if state=='locked':
        worker.run_lock.release()


def test_api_cannot_change_storage_identity_or_accept_cv_document_kind(client):
    record=upload(client).json()
    assert client.patch('/api/documents/'+record['id'],json={'path':'/tmp/secret.txt','confirmed':True}).status_code==422
    assert client.post('/api/documents',files={'file':('cv.txt',b'CV original')},data={'kind':'resume'}).status_code==422
    assert client.delete('/api/documents/missing').status_code==404


def test_cv_named_file_cannot_be_disguised_as_license(library):
    from agent.document_library import document_for_field
    confirm(library,filename='resume.txt',label='My CV',kind='license')
    assert document_for_field('License',library.documents()) is None


def test_nonprinting_text_is_rejected(library):
    with pytest.raises(ValueError):
        library.import_document(b'Hello\x7fworld','binary.txt','Binary','other')


def test_api_confirmation_rechecks_bytes_and_preserves_generation_on_failure(client,tmp_path):
    record=upload(client).json()
    path=tmp_path/'documents'/(record['id']+'.txt')
    path.write_bytes(b'Changed license')
    generation=client.app.state.store.meta('document_generation')
    response=client.patch('/api/documents/'+record['id'],json={'confirmed':True})
    assert response.status_code==409
    assert client.app.state.store.meta('document_generation')==generation
    assert not client.get('/api/documents').json()[0]['confirmed']
    assert client.get('/api/documents/'+record['id']+'/file').status_code==409


def test_import_does_not_write_outside_when_document_directory_is_replaced(library,tmp_path):
    outside=tmp_path/'outside'
    outside.mkdir()
    library.directory.rmdir()
    library.directory.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):
        library.import_document(b'Original','original.txt','Original','other')
    assert list(outside.iterdir())==[]


def test_original_read_rejects_directory_symlink_swap_after_path_check(library,tmp_path,monkeypatch):
    import agent.document_library as module
    record=confirm(library)
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/Path(record.path).name).write_bytes(b'Original license')
    original=module._confined_path
    swapped=False
    def swap_after_check(*args,**kwargs):
        nonlocal swapped
        path=original(*args,**kwargs)
        if not swapped:
            swapped=True
            library.directory.rename(tmp_path/'previous-documents')
            library.directory.symlink_to(outside,target_is_directory=True)
        return path
    monkeypatch.setattr(module,'_confined_path',swap_after_check)
    with pytest.raises(ValueError):
        module.verified_document_bytes(record,library.runtime)
