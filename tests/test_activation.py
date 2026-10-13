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

    def archive(self, directory, entries):
        path=Path(directory)/'links.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for name,kind,target in entries:
                member=tarfile.TarInfo(name);member.type=kind;member.linkname=target
                archive.addfile(member)
        return path

    def test_legitimate_node_links_and_root_relative_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path=self.archive(directory,[
                ('externals/node24/lib/npm.js',tarfile.REGTYPE,''),
                ('externals/node24/bin/npm',tarfile.SYMTYPE,'../lib/npm.js'),
                ('externals/node24/bin/npx',tarfile.SYMTYPE,'npm'),
                ('nested/hard',tarfile.LNKTYPE,'externals/node24/lib/npm.js')])
            activation.verify_archive(path)

    def test_escape_cycles_and_link_pivots_rejected(self):
        cases=[
            [('a/link',tarfile.SYMTYPE,'../../outside')],
            [('a',tarfile.SYMTYPE,'b'),('b',tarfile.SYMTYPE,'a')],
            [('dir/link',tarfile.SYMTYPE,'../alias/../../outside'),('alias',tarfile.SYMTYPE,'.')],
            [('link',tarfile.SYMTYPE,'inside'),('link',tarfile.SYMTYPE,'../outside')],
            [('later/link',tarfile.SYMTYPE,'../safe'),('later',tarfile.SYMTYPE,'.')],
            # A hardlink to a symlink copies its inode: the same ../ target can
            # escape when interpreted from the new hardlink's parent.
            [('safe',tarfile.REGTYPE,''),('dir/link',tarfile.SYMTYPE,'../safe'),
             ('hard',tarfile.LNKTYPE,'dir/link')],
            [('dir/hard',tarfile.LNKTYPE,'../safe'),('safe',tarfile.REGTYPE,'')],
            [('dir/link',tarfile.SYMTYPE,'/outside')],
            [('alias',tarfile.SYMTYPE,'real'),('alias/x',tarfile.SYMTYPE,'../top'),
             ('y',tarfile.SYMTYPE,'alias/x/../../outside')]]
        with tempfile.TemporaryDirectory() as directory:
            for entries in cases:
                with self.subTest(entries=entries):
                    with self.assertRaises(ValueError): activation.verify_archive(self.archive(directory,entries))

    def test_forward_link_checked_against_final_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            path=self.archive(directory,[('link',tarfile.SYMTYPE,'alias/../safe'),
                                         ('alias',tarfile.SYMTYPE,'.'),('safe',tarfile.REGTYPE,'')])
            # Lexically alias/../safe is in-root, but alias -> . makes .. escape.
            with self.assertRaises(ValueError): activation.verify_archive(path)

    def test_host_resume_does_not_run_installer_or_write_guard(self):
        from unittest.mock import patch,MagicMock
        def completed(command):
            return __import__('subprocess').CompletedProcess(command,1 if '-L' in command else 0)
        guard=MagicMock();guard.read_text.return_value='a'*64+'\n'
        def check_output(command,**kwargs):
            return 'nexarm-runner\n' if command[0]=='id' else '0 644\n'
        with patch.object(activation.subprocess,'run',side_effect=completed), \
             patch.object(activation.subprocess,'check_output',side_effect=check_output), \
             patch.object(activation,'run') as run, \
             patch.object(activation,'Path',return_value=guard):
            activation.ensure_host_setup(Path('/tmp/stage'),'a'*64)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][-1],'probe')
            guard.write_text.assert_not_called()

    def test_changed_host_files_fail_without_overwrite(self):
        from unittest.mock import patch
        import subprocess
        def command(args):
            return subprocess.CompletedProcess(args,1 if args[1] in ['cmp'] or '-L' in args else 0)
        with patch.object(activation.subprocess,'run',side_effect=command),patch.object(activation,'run') as run:
            with self.assertRaises(ValueError): activation.ensure_host_setup(Path('/tmp/stage'),'a'*64)
            run.assert_not_called()

    def test_links_through_in_root_directory_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path=self.archive(directory,[('alias',tarfile.SYMTYPE,'real'),
                                         ('real/file',tarfile.REGTYPE,''),
                                         ('alias/x',tarfile.SYMTYPE,'file'),
                                         ('hard',tarfile.LNKTYPE,'alias/file')])
            activation.verify_archive(path)

    def test_accepted_archive_extracts_links_inside_root(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            path=self.archive(directory,[('node/lib/npm.js',tarfile.REGTYPE,''),
                                         ('node/bin/npm',tarfile.SYMTYPE,'../lib/npm.js'),
                                         ('hard',tarfile.LNKTYPE,'node/lib/npm.js')])
            activation.verify_archive(path)
            root=Path(directory)/'extracted';root.mkdir()
            subprocess.run(['tar','-xzf',str(path),'-C',str(root)],check=True,capture_output=True)
            self.assertEqual((root/'node/bin/npm').resolve(),root/'node/lib/npm.js')
            self.assertEqual((root/'hard').stat().st_ino,(root/'node/lib/npm.js').stat().st_ino)

    def residue(self, directory):
        root=Path(directory)/'actions-runner';root.mkdir()
        (root/'config.sh').write_bytes(b'official package')
        (root/'.env').write_text('LANG=C\n')
        (root/'.path').write_text('/usr/bin\n')
        (root/'_diag').mkdir();(root/'_diag/Runner.log').write_text('failed registration')
        import hashlib
        manifest={'config.sh':('file',hashlib.sha256(b'official package').hexdigest())}
        proc=Path(directory)/'proc';proc.mkdir()
        def mapped(path):
            if str(path)=='/var/lib/nexarm-runner/actions-runner': return root
            if str(path)=='/proc': return proc
            return Path(path)
        return root,manifest,mapped

    def test_exact_unconfigured_residue_removed_only(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,mapped=self.residue(directory)
            neighbor=Path(directory)/'protected-host-file';neighbor.write_text('preserve')
            with patch.object(activation,'Path',side_effect=mapped):
                activation.recover_unconfigured_runner(manifest)
            self.assertFalse(root.exists())
            self.assertEqual(neighbor.read_text(),'preserve')

    def test_configured_credentials_unknown_or_modified_residue_preserved(self):
        from unittest.mock import patch
        for marker in ['.runner','.credentials','.credentials_rsaparams','.service','_work','unknown.txt','modified']:
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as directory:
                root,manifest,mapped=self.residue(directory)
                if marker=='modified': (root/'config.sh').write_text('changed')
                else: (root/marker).write_text('keep')
                with patch.object(activation,'Path',side_effect=mapped):
                    with self.assertRaises(ValueError): activation.recover_unconfigured_runner(manifest)
                self.assertTrue(root.exists())
                self.assertTrue((root/'config.sh').exists())

    def test_residue_symlink_refused(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,mapped=self.residue(directory)
            (root/'_diag/escape').symlink_to('/tmp')
            with patch.object(activation,'Path',side_effect=mapped):
                with self.assertRaises(ValueError): activation.recover_unconfigured_runner(manifest)
            self.assertTrue(root.exists())

    def test_active_service_prevents_cleanup(self):
        from unittest.mock import patch
        with patch.object(activation.subprocess,'check_output',return_value='active\n'),patch.object(activation,'run') as run:
            with self.assertRaises(ValueError): activation.recover_runner_residue(Path('/tmp/archive'),'/var/lib/nexarm-runner/actions-runner')
            run.assert_not_called()
