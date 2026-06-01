from __future__ import annotations

import sys

from PySide6 import QtWidgets

from mods_updater.app_meta import APP_NAME, APP_ORGANIZATION
from mods_updater.ui import MainWindow


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORGANIZATION)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
