import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('activation',ROOT/'tools/activate_pi_runner.py')
activation=importlib.util.module_from_spec(spec);spec.loader.exec_module(activation)


class ActivationTests(unittest.TestCase):
    def metadata(self):
        return {'tag_name':'v2.330.0','assets':[{'name':'actions-runner-linux-arm64-2.330.0.tar.gz',
                 'browser_download_url':'https://github.com/actions/runner/releases/download/v2.330.0/actions-runner-linux-arm64-2.330.0.tar.gz',
                 'digest':'sha256:'+'a'*64}]}

    def test_requires_official_url_and_digest(self):
        data=self.metadata()
        self.assertEqual(activation.release_metadata(data)[1],'a'*64)
        for field,value in [('digest',None),('browser_download_url','https://other.invalid/package')]:
            data=self.metadata();data['assets'][0][field]=value
            with self.assertRaises(ValueError): activation.release_metadata(data)

    def test_archive_rejects_traversal_links_and_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.tar.gz'
            for name,kind,target in [('../escape',tarfile.REGTYPE,''),('link',tarfile.SYMTYPE,'/etc/shadow'),
                                     ('device',tarfile.CHRTYPE,'')]:
                with tarfile.open(path,'w:gz') as archive:
                    member=tarfile.TarInfo(name);member.type=kind;member.linkname=target
                    archive.addfile(member)
                with self.assertRaises(ValueError): activation.verify_archive(path)

    def test_safe_archive_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.tar.gz'
            with tarfile.open(path,'w:gz') as archive:
                archive.addfile(tarfile.TarInfo('config.sh'))
            activation.verify_archive(path)

    def test_standalone_embeds_all_host_files(self):
        import ast,subprocess
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'activate.py'
            subprocess.run(['python3',str(ROOT/'tools/build_activation_script.py'),str(output)],check=True,capture_output=True)
            tree=ast.parse(output.read_text())
            assignment=next(node for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PAYLOAD' for t in node.targets))
            payload=ast.literal_eval(assignment.value)
            self.assertEqual(set(payload),set(activation.FILES))
            for name,contents in payload.items(): self.assertEqual(contents,(ROOT/name).read_text())
