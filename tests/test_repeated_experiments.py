import copy
import csv
import json

import pytest
from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtTest import QTest

from ace_scheduler.core.repeat_demo import demo_study
from ace_scheduler.core.repeated_experiments import RepeatedStudy, METRICS
from ace_scheduler.ui.repeated_experiments import RepeatedExperimentsDialog
from tests.test_ui import app


@pytest.fixture(scope="module")
def fixture_study():
    return demo_study()


@pytest.fixture
def study(fixture_study):
    return copy.deepcopy(fixture_study)


def primary(report):
    return next(g for g in report['groups'] if g['basis']['application_version'] == '1.0')


def test_pairs_equal_weight_separate_version_and_explicit_synthetic(study):
    report = study.report()
    assert report['synthetic_rounds'] == 6
    group = primary(report)
    assert group['valid_rounds'] == 3 and group['excluded_rounds'] == 2
    assert group['median_delta'] == 0 and group['median_absolute_deviation'] == 4
    assert (group['delta_min'], group['delta_max']) == (-4, 4)
    assert group['median_relative_percent'] == 0
    assert group['enough_rounds']
    assert not report['groups'][1]['enough_rounds']
    counts = [study.records[key]['session']['frame_attachment']['stream']['rows'] for key in study.selected[:3]]
    assert len(set(counts)) == 3  # Different frame counts still contribute exactly one pair each.
    assert [r['delta'] for r in report['rounds'][:3]] == [-4, 0, 4]
    assert any('覆盖' in r for r in report['rounds'][3]['reasons'])
    assert any('中断' in r for r in report['rounds'][4]['reasons'])


@pytest.mark.parametrize("field", ["scene", "hardware", "application_version", "conditions"])
def test_missing_declarations_do_not_silently_merge(study, field):
    study.declarations[study.selected[0]][field] = "  "
    row = study.report()['rounds'][0]
    assert not row['included'] and any('缺少' in r for r in row['reasons'])


@pytest.mark.parametrize("change", ["hardware", "conditions", "policy", "actual", "disk", "origin", "metric_basis", "duration"])
def test_incompatible_conditions_get_separate_groups(study, change):
    key = study.selected[0]
    s = study.records[key]['session']
    if change in ('hardware', 'conditions'):
        study.declarations[key][change] += ' changed'
    elif change == 'policy':
        s['context']['requested_policy']['priority'] = 'BelowNormal'
        next(e for e in s['events'] if e['kind'] == 'apply')['policy']['priority'] = 'BelowNormal'
    elif change == 'actual':
        next(e for e in s['events'] if e['kind'] == 'apply')['after']['affinity'] = [0]
    elif change == 'disk':
        s['disk_selection']['device']['fingerprint'] = 'other-disk'
    elif change == 'origin':
        s['context']['synthetic'] = False
    elif change == 'metric_basis':
        s['frame_attachment']['long_frame_threshold_ms'] = 50
    elif change == 'duration':
        s['baseline_end'] += .1
    assert len(study.report()['groups']) == 3


def test_duplicate_analysis_and_overlapping_rounds_excluded_order_independently(study):
    original = study.selected[0]
    data = copy.deepcopy(study.records[original])
    new_id = 'f' * 32
    data['session']['id'] = new_id
    study.records[new_id] = data
    study.declarations[new_id] = copy.deepcopy(study.declarations[original])
    study.selected.append(new_id)
    for order in (study.selected[:], list(reversed(study.selected))):
        study.selected = order
        rows = {r['session_id']: r for r in study.report()['rounds']}
        for key in (original, new_id):
            assert not rows[key]['included']
            assert any('重复或重叠' in r for r in rows[key]['reasons'])
    study.selected.remove(new_id)
    assert next(r for r in study.report()['rounds'] if r['session_id'] == original)['included']


def test_zero_baseline_has_absolute_difference_without_infinite_percent(study):
    study.metric = 'process.read'
    data = study.records[study.selected[0]]
    data['statistics']['before']['read_mbps']['mean'] = 0
    row = study.report()['rounds'][0]
    assert row['included'] and row['delta'] == row['after']
    assert row['relative_percent'] is None
    assert primary(study.report())['relative_valid_rounds'] == 2


def test_tiny_baseline_does_not_export_infinite_relative_change(study):
    study.metric = 'process.read'
    study.records[study.selected[0]]['statistics']['before']['read_mbps']['mean'] = 1e-310
    report = study.report()
    assert report['rounds'][0]['included']
    assert report['rounds'][0]['relative_percent'] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('metric', list(METRICS))
def test_process_disk_and_frame_metrics_remain_separate(study, metric):
    study.metric = metric
    report = study.report()
    assert report['metric'] == metric and primary(report)['valid_rounds'] == 3
    assert report['rounds'][0]['included']


def test_thresholds_visible_and_attachment_gate_cannot_be_weakened(study):
    study.minimum_coverage = .5
    assert not study.report()['rounds'][3]['included']  # Frame attachment still requires 95%.
    assert study.report()['rounds'][3]['effective_minimum_coverage'] == .95
    study.metric = 'process.cpu'
    assert study.report()['rounds'][3]['included']
    study.minimum_pairs = 5
    assert not primary(study.report())['enough_rounds']
    assert primary(study.report())['valid_rounds'] == 4


@pytest.mark.parametrize('failure', ['log', 'incomplete_log', 'legacy_log', 'manual', 'clock', 'apply', 'baseline', 'missing_after'])
def test_quality_failures_excluded_with_reasons(study, failure):
    data = study.records[study.selected[0]]
    s = data['session']
    if failure == 'log':
        s['frame_attachment']['source']['log']['events_lost_reported'] = 19
    elif failure == 'incomplete_log':
        s['frame_attachment']['source']['log']['capture_complete'] = False
    elif failure == 'legacy_log':
        del s['frame_attachment']['source']['log']['capture_complete']
    elif failure == 'manual':
        s['frame_attachment']['alignment']['mode'] = 'manual'
    elif failure == 'clock':
        s['clock']['breaks'] = [{'timestamp': s['started'] + 1, 'reasons': ['sleep']}]
    elif failure == 'apply':
        next(e for e in s['events'] if e['kind'] == 'apply')['result']['operations'][0]['verified'] = False
    elif failure == 'baseline':
        s['baseline_verified'] = False
    elif failure == 'missing_after':
        s['after_start'] = s['after_end'] = None
    row = study.report()['rounds'][0]
    assert not row['included'] and row['delta'] is None and row['reasons']


def test_portable_study_recalculates_tampered_derived_reports_and_exports_all_rounds(study, tmp_path):
    path = tmp_path / 'study.json'
    expected = study.report()
    study.save(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['report']['groups'][0]['median_delta'] = -999
    data['sessions'][0]['frame_recording']['statistics']['before']['cpu_frame_interval_ms']['p95_ms'] = 999
    path.write_text(json.dumps(data), encoding='utf-8')
    loaded = RepeatedStudy.load(path)
    assert loaded.report() == expected
    loaded.export_csv(tmp_path / 'summary.csv')
    rows = list(csv.DictReader((tmp_path / 'summary.csv').open(encoding='utf-8-sig')))
    assert sum(r['row_type'] == 'round' for r in rows) == 6
    assert sum(r['row_type'] == 'group' for r in rows) == 2
    assert sum(r['included'] == 'False' for r in rows) == 2


def test_bad_project_and_failed_save_preserve_existing_file(study, tmp_path, monkeypatch):
    from ace_scheduler.core import repeated_experiments as module
    path = tmp_path / 'study.json'
    study.save(path)
    original = path.read_bytes()
    monkeypatch.setattr(module, 'MAX_BUNDLE_BYTES', 5)
    with pytest.raises(ValueError, match='64 MiB'):
        study.save(path)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='64 MiB'):
        RepeatedStudy.load(path)


def test_empty_selection_and_malformed_settings_are_explicit(study):
    study.selected = []
    assert study.report()['groups'] == []
    study.minimum_coverage = float('nan')
    with pytest.raises(ValueError, match='覆盖率'):
        study.report()


def test_real_qt_demo_selection_and_declaration_edit_preserve_source(app, tmp_path):
    dialog = RepeatedExperimentsDialog(RepeatedStudy())
    dialog.show()
    QTest.mouseClick(dialog.demo_button, Qt.MouseButton.LeftButton)
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    dialog.loader.finished.connect(loop.quit)
    timeout.start(10000)
    if dialog.loader.isRunning():
        loop.exec()
    timeout.stop()
    app.processEvents()
    assert not dialog.loader.isRunning(), dialog.details.toPlainText()
    assert dialog.rounds.rowCount() == 6 and '测试数据' in dialog.banner.text()
    assert primary(dialog.last_report)['median_delta'] == 0
    dialog.rounds.setCurrentCell(0, 0)
    dialog.rounds.setFocus()
    QTest.keyClick(dialog.rounds, Qt.Key.Key_Space)
    assert len(dialog.last_report['rounds']) == 5
    assert primary(dialog.last_report)['valid_rounds'] == 2
    dialog.rounds.setCurrentCell(0, 0)
    QTest.keyClick(dialog.rounds, Qt.Key.Key_Space)
    assert len(dialog.last_report['rounds']) == 6
    original = copy.deepcopy(dialog.study.records)
    dialog.rounds.setCurrentCell(0, 1)
    dialog.fields['hardware'].setFocus()
    QTest.keyClick(dialog.fields['hardware'], Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(dialog.fields['hardware'], 'Other synthetic hardware')
    QTest.mouseClick(dialog.apply_declaration, Qt.MouseButton.LeftButton)
    assert len(dialog.last_report['groups']) == 3
    assert dialog.study.records == original
    dialog.reject()
    dialog.deleteLater()
