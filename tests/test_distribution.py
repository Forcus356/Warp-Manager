import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from build_release import build,source_files
from check_project import check


def bootstrap():
    text=(ROOT/'install_warp_manager.sh').read_text()
    code=text.split("<<'PYBOOT'\n",1)[1].split('\nPYBOOT\n',1)[0]
    module=types.ModuleType('bootstrap_under_test')
    exec(compile(code,'bootstrap_under_test.py','exec'),module.__dict__)
    return module


def source_zip(path,prefix='source'):
    names=['warp_install.py','warp_pool.py','warp_capacity.py','warp_ctl.py','warp_profiles.py',
           'worker.sh','Dockerfile','LICENSE','install.sh']
    with zipfile.ZipFile(path,'w') as out:
        for name in names:out.writestr(prefix+'/'+name,'# test fixture\n')


class Distribution(unittest.TestCase):
    def test_documentation_links_examples_and_shells_are_consistent(self):
        self.assertGreater(check(),40)

    def test_release_is_repeatable_and_manifest_covers_every_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            first,digest=build(ROOT,output)
            before=first.read_bytes()
            second,again=build(ROOT,output)
            self.assertEqual(digest,again);self.assertEqual(before,second.read_bytes())
            with zipfile.ZipFile(second) as archive:
                manifest=archive.read('Warp-Manager/MANIFEST.sha256').decode()
                covered=set()
                for line in manifest.splitlines():
                    checksum,name=line.split('  ',1);covered.add('Warp-Manager/'+name)
                    self.assertEqual(hashlib.sha256(archive.read('Warp-Manager/'+name)).hexdigest(),checksum)
                self.assertEqual(set(archive.namelist())-{'Warp-Manager/MANIFEST.sha256'},covered)
                self.assertFalse(any('__pycache__' in n or n.endswith('.pyc') for n in archive.namelist()))
                self.assertEqual(archive.read('Warp-Manager/README_RU.md'),archive.read('Warp-Manager/README.ru_RU.md'))

    def test_release_refuses_runtime_registration_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'reg.json').write_text('{}')
            with self.assertRaises(ValueError):source_files(root)

    def test_release_refuses_source_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'link').symlink_to('/etc/hostname')
            with self.assertRaises(ValueError):source_files(root)

    def test_tag_version_mismatch_is_rejected(self):
        from build_release import version
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'VERSION').write_text('0.4.1\n')
            (root/'warp_pool.py').write_text('VERSION="0.4.0"\n')
            with self.assertRaises(ValueError):version(root)


class Bootstrap(unittest.TestCase):
    def test_invalid_repository_cannot_select_an_arbitrary_host(self):
        b=bootstrap()
        for value in ['https://example.test/repo','owner/../repo','owner/repo/extra','../repo']:
            with self.subTest(value=value),self.assertRaises(ValueError):b.repository(value)

    def test_commit_pin_does_not_call_ref_api(self):
        b=bootstrap()
        with patch.object(b,'urlopen') as fetch:
            self.assertEqual(b.resolve('Forcus356/Warp-Manager','a'*40),'a'*40)
        fetch.assert_not_called()

    def test_ref_is_encoded_and_result_must_be_a_commit(self):
        b=bootstrap()
        with patch.object(b,'urlopen',return_value=io.BytesIO(json.dumps({'sha':'b'*40}).encode())) as fetch:
            self.assertEqual(b.resolve('Forcus356/Warp-Manager','feature/docs'),'b'*40)
            self.assertIn('/commits/feature%2Fdocs',fetch.call_args.args[0].full_url)
        with patch.object(b,'urlopen',return_value=io.BytesIO(b'{"sha":"not a commit"}')):
            with self.assertRaises(ValueError):b.resolve('Forcus356/Warp-Manager','main')

    def test_download_digest_mismatch_is_rejected(self):
        b=bootstrap()
        with tempfile.TemporaryDirectory() as folder,patch.object(b,'urlopen',return_value=io.BytesIO(b'bad source')):
            with self.assertRaises(ValueError):b.download('Forcus356/Warp-Manager','a'*40,Path(folder)/'source.zip','0'*64)

    def test_safe_complete_tree_is_extracted(self):
        b=bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source_zip(root/'source.zip')
            self.assertEqual(b.unpack(root/'source.zip',root/'out'),root/'out/source')

    def test_traversal_symlink_and_duplicate_members_are_rejected(self):
        b=bootstrap()
        for kind in ['traversal','symlink','duplicate']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);archive=root/'source.zip'
                with zipfile.ZipFile(archive,'w') as out,warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    if kind=='traversal':out.writestr('../outside','bad')
                    elif kind=='symlink':
                        info=zipfile.ZipInfo('source/link');info.external_attr=(stat.S_IFLNK|0o777)<<16
                        out.writestr(info,'/etc/hostname')
                    else:out.writestr('same','a');out.writestr('same','b')
                with self.assertRaises(ValueError):b.unpack(archive,root/'out')
                self.assertFalse((root/'outside').exists())

    def test_size_and_ambiguous_source_limits_are_enforced(self):
        b=bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source_zip(root/'source.zip')
            with patch.object(b,'MAX_UNPACKED',1):
                with self.assertRaises(ValueError):b.unpack(root/'source.zip',root/'out')
            with zipfile.ZipFile(root/'source.zip','a') as out:
                out.writestr('other/warp_install.py','# test\n');out.writestr('other/install.sh','# test\n')
            with self.assertRaises(ValueError):b.unpack(root/'source.zip',root/'ambiguous')

    def test_installer_options_are_forwarded_without_shell_interpolation(self):
        b=bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder);(source/'warp_pool.py').write_text('VERSION="0.4.1"\n')
            with patch.object(b.os,'geteuid',return_value=0),patch.object(b,'resolve',return_value='a'*40),\
                 patch.object(b,'download',return_value='b'*64),patch.object(b,'unpack',return_value=source),\
                 patch.object(b.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as execute,\
                 contextlib.redirect_stdout(io.StringIO()):
                result=b.main(['--source-ref','v0.4.1','--instance','2','--role','youtube','--target-gbps','0.1'])
            self.assertEqual(result,0)
            self.assertEqual(execute.call_args.args[0],['bash',str(source/'install.sh'),'--instance','2','--role','youtube','--target-gbps','0.1'])


if __name__=='__main__':unittest.main()
