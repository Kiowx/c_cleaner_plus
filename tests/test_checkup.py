import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import cleaner_core as core
import health_check as health
import main
from checkup_ui import CheckupPage, FindingCard, ToolDialog
from maintenance_ui import PreviewDialog


class CheckupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        old = core.current_store()
        self.addCleanup(self.restore_store, old)
        self.store = core.configure(str(self.root/'config'))
        self.data = self.root/'data'
        self.data.mkdir()
        self.now = time.time()

    def restore_store(self, old):
        core._store = old
        core._policy_cache = None
        core._policy_cache_at = 0

    def file(self, path, count=16, age=0):
        path = self.data/path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x'*count)
        os.utime(path, (self.now-age*86400, self.now-age*86400))
        return path

    def rule(self, name='Chrome Cache', subdir='cache', enabled=False, kind='dir', pattern=''):
        return (name, str(self.data/subdir), kind, enabled, 'test', False, pattern)

    def scan(self, rules=(), builtins=(), **kwargs):
        defaults = dict(installed=[], data_roots=[], download_roots=[], scopes=[str(self.data)], now=self.now, large_threshold=1)
        defaults.update(kwargs)
        return health.scan_checkup(rules, builtins, **defaults)

    def test_common_cache_is_discovered_even_if_manual_rule_unchecked(self):
        self.file('cache/file', 128)
        report = self.scan(builtins=[self.rule()])
        row = next(row for row in report['rows'] if row['category']=='cache')
        self.assertTrue(row['selected'])
        self.assertEqual(report['recommended_bytes'], 128)
        self.assertIn('密码和书签', row['impact'])
        self.assertEqual(next(row for row in report['apps'] if row['name']=='Google Chrome')['cache'], 128)

    def test_custom_rule_cannot_grant_itself_recommended_status(self):
        self.file('cache/file')
        report = self.scan(rules=[self.rule(enabled=True)])
        row = next(row for row in report['rows'] if row['category']=='cache')
        self.assertFalse(row['selected'])
        self.assertFalse(row['recommended'])
        self.assertTrue(row['cleanable'])

    def test_disabled_custom_rule_is_not_offered(self):
        self.file('cache/file')
        report = self.scan(rules=[self.rule(enabled=False)])
        self.assertFalse(any(row['cleanable'] for row in report['rows']))

    def test_temporary_files_keep_last_seven_days(self):
        recent = self.file('temp/new.log', age=1)
        old = self.file('temp/old.log', age=20)
        report = self.scan(builtins=[self.rule('用户临时文件','temp')])
        plan = health.reviewed_plan([row for row in report['rows'] if row['selected']])
        self.assertEqual([item['path'] for item in plan['items']], [core.canonical(str(old))])

    def test_existing_rule_conditions_preserved_in_checkup(self):
        self.file('cache/new', 32, age=1)
        old = self.file('cache/old', 32, age=50)
        self.file('cache/tiny', 1, age=60)
        rule = self.rule()
        conditions={core.rule_id(rule):dict(min_age_days=30,min_size_bytes=10,keep_latest=1)}
        report=self.scan(builtins=[rule],conditions=conditions)
        self.assertEqual([item['path'] for row in report['rows'] if row['selected'] for item in row['files']], [core.canonical(str(old))])

    def test_exclusions_apply_to_inventory_and_cleanup_candidates(self):
        keep = self.file('cache/keep.psd')
        remove = self.file('cache/log.txt')
        report = self.scan(builtins=[self.rule()], policy=core.Policy(['*.psd']))
        self.assertEqual(report['file_count'], 1)
        self.assertEqual(report['recommended_bytes'], 16)
        self.assertNotIn(str(keep), str(report))

    def test_download_suggestions_are_explicitly_opt_in(self):
        old = self.file('Downloads/installer.exe', 64, age=50)
        self.file('Downloads/new.zip', 32, age=1)
        self.file('Downloads/keep.docx', 100, age=90)
        self.file('Downloads/archive/important.zip', 100, age=90)
        report = self.scan(download_roots=[str(self.data/'Downloads')])
        row = next(row for row in report['rows'] if row['category']=='download')
        self.assertFalse(row['selected'])
        self.assertEqual([f['path'] for f in row['files']], [core.canonical(str(old))])
        self.assertEqual(report['recommended_bytes'], 0)

    def test_personal_data_and_installations_are_never_cleanup_targets(self):
        self.file('chat/messages.db', 50)
        self.file('app/program.exe', 100)
        report = self.scan(data_roots=[('聊天记录',str(self.data/'chat'),'微信')],
                           installed=[dict(name='Test App',location=str(self.data/'app'))])
        self.assertFalse(any(row['cleanable'] for row in report['rows']))
        self.assertFalse(health.reviewed_plan(report['rows'])['items'])
        app = next(row for row in report['apps'] if row['name']=='Test App')
        self.assertEqual(app['install'],100)

    def test_overlapping_sources_do_not_double_count_files_or_offer_twice(self):
        self.file('app/cache/file',100)
        rules = [self.rule('Chrome Cache','app/cache'), self.rule('Other','app',True)]
        report = self.scan(builtins=rules[:1],rules=rules[1:],data_roots=[('App',str(self.data/'app'),'Google Chrome')])
        plan = health.reviewed_plan(report['rows'])
        self.assertEqual(report['total'],100)
        self.assertEqual(len(plan['items']),1)
        self.assertEqual(sum(row['size'] for row in report['apps']),100)

    def test_boundary_does_not_include_sibling_with_shared_prefix(self):
        self.file('cache/file',10)
        self.file('cache-other/file',99)
        report=self.scan(builtins=[self.rule()],scopes=[str(self.data/'cache')])
        self.assertEqual(report['total'],10)

    def test_scan_limit_is_partial_and_has_no_auto_selection(self):
        for i in range(8):self.file(f'cache/{i}',10)
        report=self.scan(builtins=[self.rule()],max_files=2)
        self.assertFalse(report['complete'])
        self.assertTrue(report['errors'])
        self.assertEqual(report['recommended_bytes'],0)
        self.assertFalse(any(row['selected'] for row in report['rows']))

    def test_cancelled_scan_does_not_offer_automatic_cleanup(self):
        stop=threading.Event();stop.set()
        report=self.scan(builtins=[self.rule()],stop=stop)
        self.assertTrue(report['cancelled'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['recommended_bytes'],0)

    def test_file_changed_since_scan_is_preserved(self):
        path=self.file('cache/file')
        report=self.scan(builtins=[self.rule()])
        plan=health.reviewed_plan([row for row in report['rows'] if row['selected']])
        path.write_bytes(b'new contents changed after scan')
        result=core.execute_plan(plan,main.delete_path,True)
        self.assertEqual(result['skipped'],1)
        self.assertTrue(path.exists())

    def test_retry_contains_only_failed_items_and_keeps_original_evidence(self):
        self.file('cache/one');self.file('cache/two')
        report=self.scan(builtins=[self.rule()])
        plan=health.reviewed_plan(report['rows'])
        result=dict(items=[dict(path=plan['items'][0]['path'],status='failed'),dict(path=plan['items'][1]['path'],status='skipped')])
        retry=health.retry_plan(plan,result)
        self.assertEqual(len(retry['items']),1)
        self.assertEqual(retry['items'][0]['signature'],plan['items'][0]['signature'])

    def test_file_identity_avoids_counting_hardlinks_as_extra_storage(self):
        items=[dict(path='a',size=100,identity=(1,2)),dict(path='alias',size=100,identity=(1,2)),dict(path='b',size=20,identity=(1,3))]
        self.assertEqual(health.unique_size(items),120)

    def test_cleanup_failures_keep_the_actual_error_for_retry_review(self):
        self.file('cache/file')
        report=self.scan(builtins=[self.rule()]);plan=health.reviewed_plan(report['rows'])
        def fail(path, permanent, log):
            log('Access denied: file is locked by browser')
            return False
        result=core.execute_plan(plan,fail,False)
        self.assertEqual(result['failed'],1)
        self.assertIn('locked by browser',result['items'][0]['reason'])

    def test_unrelated_unreadable_folder_does_not_hide_completed_cache_recommendation(self):
        self.file('cache/file',128)
        bad=self.data/'unreadable';bad.mkdir()
        original=core.os.scandir
        def scandir(path):
            if core.canonical(str(path))==core.canonical(str(bad)):
                raise PermissionError('denied')
            return original(path)
        with mock.patch.object(core.os,'scandir',side_effect=scandir):
            report=self.scan(builtins=[self.rule()],data_roots=[('Locked',str(bad),'Locked')])
        self.assertFalse(report['complete'])
        self.assertEqual(report['recommended_bytes'],128)

    def test_reparse_point_is_not_counted_or_offered_for_cleanup(self):
        self.file('cache/file',128)
        original=core.link_like
        cache=core.canonical(str(self.data/'cache'))
        def reparse(path,st=None):
            return core.canonical(str(path))==cache or original(path,st)
        with mock.patch.object(core,'link_like',side_effect=reparse):
            report=self.scan(builtins=[self.rule()])
        self.assertEqual(report['file_count'],0)
        self.assertFalse(any(row['cleanable'] for row in report['rows']))

    def test_software_names_are_not_grouped_by_coincidental_substrings(self):
        self.assertEqual(health.application_name('Ledger Live'),'Ledger Live')
        self.assertEqual(health.application_name('Media Codecs'),'Media Codecs')
        self.assertEqual(health.application_name('Microsoft Edge'),'Microsoft Edge')
        self.assertEqual(health.application_name('Edge Code'),'Microsoft Edge')
        self.assertEqual(health.application_name('Visual Studio Code'),'Visual Studio Code')


class CheckupWidgetTests(unittest.TestCase):
    setUp=CheckupTests.setUp
    restore_store=CheckupTests.restore_store
    file=CheckupTests.file
    rule=CheckupTests.rule
    scan=CheckupTests.scan

    @classmethod
    def setUpClass(cls):
        cls.app=main.QApplication.instance() or main.QApplication([])

    def dispose(self,widget):
        from PySide6.QtCore import QCoreApplication,QEvent
        widget.close()
        worker=getattr(widget,'worker',None)
        if worker:worker.join(timeout=5)
        widget.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()

    def host_window(self):
        return types.SimpleNamespace(_targets_lock=threading.Lock(),targets=[],pg_clean=types.SimpleNamespace(_sync=lambda:None),
                                      apply_language_to_widget=lambda widget:None)

    def test_recommendation_cards_and_software_ranking_render(self):
        self.file('cache/file',128)
        report=self.scan(builtins=[self.rule()])
        page=CheckupPage(self.host_window(),main)
        self.addCleanup(self.dispose,page)
        page.resize(1100,800);page.show();page.on_finished('scan',report,'');self.app.processEvents()
        self.assertTrue(page.cards)
        self.assertTrue(page.clean_button.isEnabled())
        self.assertFalse(page.grab().isNull())
        page.cards[0].check.setChecked(False)
        self.assertFalse(page.clean_button.isEnabled())
        page.select_recommended()
        self.assertTrue(page.clean_button.isEnabled())
        page.mode.setCurrentIndex(1);self.app.processEvents()
        self.assertTrue(page.app_model.rows)

    def test_preview_uses_scanned_evidence_without_rescanning(self):
        self.file('cache/file')
        report=self.scan(builtins=[self.rule()]);plan=health.reviewed_plan(report['rows'])
        with mock.patch.object(core,'build_plan',side_effect=AssertionError('unexpected second scan')):
            dialog=PreviewDialog([],main,plan=plan)
        self.addCleanup(self.dispose,dialog)
        self.assertEqual(len(dialog.plan['items']),1)

    def test_relocated_settings_only_construct_requested_sections(self):
        for sections in ([0],[1,2],[3],[4],[5],[6],[7]):
            window=main.QWidget()
            window.targets=[];window._targets_lock=threading.Lock();window.apply_language_to_widget=lambda widget:None
            dialog=ToolDialog(window,main,sections)
            self.assertEqual(dialog.page.stack.count(),len(sections))
            self.dispose(dialog);self.dispose(window)

    def test_cleanup_uses_recycle_bin_and_exposes_failure_retry(self):
        self.file('cache/file')
        page=CheckupPage(self.host_window(),main)
        self.addCleanup(self.dispose,page)
        report=self.scan(builtins=[self.rule()]);page.on_finished('scan',report,'')
        fake=types.SimpleNamespace(exec=lambda:True,deleteLater=lambda:None)
        captured={}
        def run(key,callback):
            captured['key']=key
            return callback()
        with (mock.patch('checkup_ui.PreviewDialog',return_value=fake),mock.patch.object(page,'run',side_effect=run),
              mock.patch.object(core,'execute_plan',return_value={}) as execute):
            page.clean_selected()
        self.assertIs(execute.call_args.args[2],False)
        self.assertEqual(captured['key'],'clean')

    def test_scan_worker_preserves_manual_selections_and_produces_findings(self):
        from PySide6.QtTest import QTest
        self.file('cache/file',128)
        rule=self.rule()
        window=self.host_window();window.targets=[rule]
        page=CheckupPage(window,main);self.addCleanup(self.dispose,page)
        page.scope_paths.append(str(self.data));page.scope.addItem(str(self.data));page.scope.setCurrentIndex(len(page.scope_paths)-1)
        with (mock.patch.object(main,'default_clean_targets',return_value=[rule]),
              mock.patch.object(main,'scan_installed_software_entries',return_value=([],[],0)),
              mock.patch.object(main,'default_download_dirs',return_value=[]),
              mock.patch.object(health,'default_data_roots',return_value=[])):
            page.start_scan()
            deadline=time.monotonic()+5
            while page.busy and time.monotonic()<deadline:
                QTest.qWait(10)
        self.assertFalse(page.busy)
        self.assertIsNotNone(page.report)
        self.assertEqual(page.report['recommended_bytes'],128)
        self.assertFalse(window.targets[0][3])

    def test_cleanup_results_remove_processed_files_and_enable_retry_only_for_failures(self):
        self.file('cache/one');self.file('cache/two')
        report=self.scan(builtins=[self.rule()]);plan=health.reviewed_plan(report['rows'])
        page=CheckupPage(self.host_window(),main);self.addCleanup(self.dispose,page)
        page.on_finished('scan',report,'');page.last_plan=plan
        result=dict(success=1,failed=1,skipped=0,cancelled=False,free_delta=0,estimated_bytes=16,
                    free_before={str(self.data):100},free_after={str(self.data):100},items=[
                    dict(path=plan['items'][0]['path'],status='deleted',reason=''),
                    dict(path=plan['items'][1]['path'],status='failed',reason='file locked')])
        page.on_finished('clean',result,'')
        self.assertTrue(page.retry_button.isEnabled())
        self.assertIn('file locked',page.result_detail.toPlainText())
        remaining=health.reviewed_plan(page.report['rows'])['items']
        self.assertEqual(len(remaining),1)
        with mock.patch.object(page,'preview_rows') as preview:
            page.retry_failed()
        self.assertEqual(preview.call_args.args[1]['items'][0]['path'],plan['items'][1]['path'])
