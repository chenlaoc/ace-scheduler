"""Real Qt input acceptance using synthetic data only, with no monitoring worker."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.core.repeated_experiments import RepeatedStudy
from ace_scheduler.ui.main_window import MainWindow


def main():
    folder = Path('artifacts/stage-four/repeated-validation')
    folder.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppConfig(), ConfigManager(folder / 'config.json'), read_only=True, start_worker=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show_page(2)
    window.show()
    app.processEvents()
    stage, errors, results = [0], [], {}
    def drive():
        dialog = app.activeModalWidget()
        if not dialog:
            return
        try:
            if stage[0] == 0:
                stage[0] = 1
                QTest.mouseClick(dialog.demo_button, Qt.MouseButton.LeftButton)
            elif stage[0] == 1 and dialog.loader and not dialog.loader.isRunning():
                stage[0] = 2
                assert len(dialog.last_report['rounds']) == 6
                expected = dialog.study.report()
                primary = next(g for g in expected['groups'] if g['basis']['application_version'] == '1.0')
                assert primary['valid_rounds'] == 3 and primary['median_delta'] == 0
                assert primary['median_absolute_deviation'] == 4
                for theme in ('light', 'dark'):
                    window.theme_controller.set_mode(theme)
                    for width, height in ((1280, 860), (1040, 700)):
                        window.resize(width, height)
                        dialog.resize(960, 670)
                        app.processEvents()
                        dialog.grab().save(str(folder / f'{theme}-{width}-dialog.png'))
                path = folder / 'synthetic-study.json'
                dialog.study.save(path)
                dialog.study.export_csv(folder / 'synthetic-summary.csv')
                assert RepeatedStudy.load(path).report() == expected
                # Reload through the actual asynchronous dialog path as well.
                dialog.load(str(path))
                results.update({'report': expected, 'roundtrip_equal': True, 'input_flow': 'multi-round button -> load synthetic example -> background reload'})
                stage[0] = 3
            elif stage[0] == 3 and not dialog.loader.isRunning():
                stage[0] = 4
                assert dialog.study.report() == results['report']
                assert not window.armed and not window.history.sessions and window.thread is None
                results.update({'live_history_unchanged': True, 'monitor_started': False, 'armed_rules': 0})
                dialog.reject()
        except Exception as exc:
            errors.append(repr(exc))
            dialog.reject()
    timer = QTimer()
    timer.timeout.connect(drive)
    timer.start(30)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: app.activeModalWidget().reject() if app.activeModalWidget() else None)
    timeout.start(30000)
    try:
        QTest.mouseClick(window.experiment_page.repeated_button, Qt.MouseButton.LeftButton)
        assert not errors, errors
        assert stage[0] == 4, stage
        (folder / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'success': True, 'result': str(folder / 'result.json')}, ensure_ascii=False))
    finally:
        timer.stop()
        timeout.stop()
        window.close()


if __name__ == '__main__':
    main()
