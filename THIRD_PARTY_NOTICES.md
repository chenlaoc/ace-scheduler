# Third-party notices

ACE Scheduler original code and branding follow the root LICENSE. Third-party
components keep their own licenses and rights, which are not restricted by that notice.

| Component | Use | License / upstream |
| --- | --- | --- |
| Python | Interpreter bundled in Windows builds | [PSF and incorporated licenses](https://docs.python.org/3/license.html) |
| PySide6-Essentials / Shiboken6 | Qt for Python runtime | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, as declared by installed distribution metadata; [Qt for Python](https://doc.qt.io/qtforpython-6/) |
| Qt libraries and plugins | Native UI, fonts, image formats, platform integration | Component-specific Qt and third-party licenses; [Qt licensing](https://www.qt.io/licensing/) |
| psutil | Process discovery and counters | BSD-3-Clause; [upstream](https://github.com/giampaolo/psutil) |
| PyInstaller | Build tool and embedded bootloader | GPL with bootloader exception; [license](https://pyinstaller.org/en/stable/license.html) |
| Pillow | Build-time PNG-to-ICO conversion | HPND and component notices; [license](https://pillow.readthedocs.io/en/stable/about.html#license) |
| pytest | Development and CI | MIT; [upstream](https://github.com/pytest-dev/pytest) |

Exact validated dependency versions are recorded in `requirements-lock.txt`. Windows
packages include Python's license and runtime distribution metadata with the license
files supplied by those distributions. Standard LGPL/GPL texts are included under
`licenses/`. Build-time tools are not application features.

Qt/PySide libraries remain dynamically loaded in the `_internal` directory. This
project does not restrict replacement of LGPL components or debugging modifications
where permitted by their licenses. Qt source and release information is available at
[Qt for Python source](https://code.qt.io/pyside/pyside-setup.git/) and
[Qt releases](https://download.qt.io/official_releases/qt/).

The reference project `adorablenew/ace-killer` was consulted for design analysis;
its modules and provided binary archive are not included. See `docs/ARCHITECTURE.md`
for the precise reference revision and independently implemented behavior.

ACE Scheduler is not affiliated with Tencent, ACE, Microsoft, Apple, or the Qt
Company. Product names mentioned in documentation identify compatibility or technical
references, not endorsements.
