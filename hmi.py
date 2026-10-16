import html
import time
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QGroupBox, QTextEdit, QToolBar, QFrame, QSizePolicy, QScrollArea, QToolButton
)
from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRectF, QPointF
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QPixmap, QPainter, QColor, QPolygonF
from colors import color as theme_color, stylesheet, status_style, THEME_NAMES
from eload import LOAD_MODES, MODE_UNITS

# Límite de líneas en consolas: la telemetría puede registrar errores cada 400 ms
LOG_MAX_LINES = 2000


def make_toolbar_icon(shape: str, color_key: str = "icon") -> QIcon:
    """Iconos vectoriales propios (record/pause/stop/export) con los colores del tema activo."""
    icon = QIcon()
    for mode, col in ((QIcon.Mode.Normal, theme_color(color_key)), (QIcon.Mode.Disabled, theme_color("icon_disabled"))):
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        if shape == "record":
            p.drawEllipse(QRectF(12, 12, 40, 40))
        elif shape == "pause":
            p.drawRoundedRect(QRectF(14, 12, 13, 40), 3, 3)
            p.drawRoundedRect(QRectF(37, 12, 13, 40), 3, 3)
        elif shape == "stop":
            p.drawRoundedRect(QRectF(14, 14, 36, 36), 4, 4)
        elif shape == "export":
            p.drawPolygon(QPolygonF([QPointF(32, 44), QPointF(14, 24), QPointF(25, 24), QPointF(25, 8),
                                     QPointF(39, 8), QPointF(39, 24), QPointF(50, 24)]))
            p.drawRoundedRect(QRectF(10, 49, 44, 7), 2, 2)
        p.end()
        icon.addPixmap(pix, mode)
    return icon


class SinglePSUWidget(QFrame):
    """
    Widget totalmente escalable y responsivo para una sola fuente.
    Soporta traducción dinámica de la interfaz.
    """

    # Claves de traducción del botón de salida (OFF, ON)
    OUTPUT_KEYS = ("btn_enable_out", "btn_disable_out")

    def __init__(self, psu_index: int):
        super().__init__()
        self.psu_index = psu_index

        # Control de estado interno
        self.is_connected = False
        self.is_error_state = False
        self.output_is_on = False
        self.lang_mgr = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)
        self._main_layout = main_layout
        self._layout_level = self.LAYOUT_WIDE
        self._level_min_widths = {}

        # ======================================================================
        # COLUMNA IZQUIERDA: Conexión, Ajustes y Consola
        # ======================================================================
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        self._left_col = left_col

        # 1. Conexión
        self.group_conn = QGroupBox("Conexión")
        layout_conn = QGridLayout(self.group_conn)
        self._layout_conn = layout_conn
        layout_conn.setContentsMargins(6, 6, 6, 6)
        layout_conn.setSpacing(4)

        self.lbl_profile_tag = QLabel("Perfil:")
        layout_conn.addWidget(self.lbl_profile_tag, 0, 0)
        self.combo_profile = QComboBox()
        # No estirar el ancho mínimo al nombre de perfil más largo (el texto se recorta, la lista se ve completa)
        self.combo_profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_profile.setMinimumContentsLength(10)
        self.combo_profile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_conn.addWidget(self.combo_profile, 0, 1)

        self.lbl_channel_tag = QLabel("Canal:")
        layout_conn.addWidget(self.lbl_channel_tag, 0, 2)
        self.combo_channel = QComboBox()
        self.combo_channel.setEnabled(False)
        layout_conn.addWidget(self.combo_channel, 0, 3)

        self.lbl_ip_tag = QLabel("IP:")
        layout_conn.addWidget(self.lbl_ip_tag, 1, 0)
        self.txt_ip = QLineEdit()
        self.txt_ip.setPlaceholderText("192.168.1.100")
        self.txt_ip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_conn.addWidget(self.txt_ip, 1, 1)

        self.lbl_port_tag = QLabel("Puerto:")
        layout_conn.addWidget(self.lbl_port_tag, 1, 2)
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(5025)
        self.spin_port.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout_conn.addWidget(self.spin_port, 1, 3)

        self.lbl_mode_tag = QLabel("Modo:")
        layout_conn.addWidget(self.lbl_mode_tag, 2, 0)
        self.combo_conn_type = QComboBox()
        self.combo_conn_type.addItems(["INSTR", "SOCKET"])
        layout_conn.addWidget(self.combo_conn_type, 2, 1)

        self.btn_connect = QPushButton("Conectar")
        layout_conn.addWidget(self.btn_connect, 2, 2)

        self.lbl_status = QLabel("DESCONECTADO")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(status_style("disconnected"))
        layout_conn.addWidget(self.lbl_status, 2, 3)

        left_col.addWidget(self.group_conn)

        # 2. Ajustes / Setpoints (específico de cada tipo de instrumento).
        # Absorbe el alto libre: la columna izquierda termina a la altura de Telemetría; los controles
        # quedan arriba y el espacio sobrante queda dentro del marco. (La consola SCPI es una ventana aparte.)
        left_col.addWidget(self._build_ctrl_group(), stretch=1)
        self._layout_ctrl.setRowStretch(self._layout_ctrl.rowCount(), 1)

        main_layout.addLayout(left_col, stretch=1)

        # ======================================================================
        # COLUMNA DERECHA: Telemetría y Gráfica Responsiva
        # ======================================================================
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self._right_col = right_col

        self.group_readout = QGroupBox("Telemetría en Tiempo Real")
        layout_readout = QGridLayout(self.group_readout)
        self._layout_readout = layout_readout
        layout_readout.setContentsMargins(6, 6, 6, 6)
        layout_readout.setSpacing(4)

        self.lbl_v_head = QLabel("VOLTAJE")
        self.lbl_v_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_v_val = QLabel("0.000 V")
        self.lbl_v_val.setObjectName("lcd_voltage")
        self.lbl_v_val.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_i_head = QLabel("CORRIENTE")
        self.lbl_i_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_i_val = QLabel("0.000 A")
        self.lbl_i_val.setObjectName("lcd_current")
        self.lbl_i_val.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_p_head = QLabel("POTENCIA")
        self.lbl_p_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_p_val = QLabel("0.000 W")
        self.lbl_p_val.setObjectName("lcd_power")
        self.lbl_p_val.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout_readout.addWidget(self.lbl_v_head, 0, 0)
        layout_readout.addWidget(self.lbl_v_val, 1, 0)
        layout_readout.addWidget(self.lbl_i_head, 0, 1)
        layout_readout.addWidget(self.lbl_i_val, 1, 1)
        layout_readout.addWidget(self.lbl_p_head, 0, 2)
        layout_readout.addWidget(self.lbl_p_val, 1, 2)

        # Gráfica escalable
        self.plot_widget = pg.PlotWidget(title=f"Histórico V / I (#{self.psu_index})")
        self.plot_widget.setBackground(theme_color("plot_bg"))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.plot_widget.setMinimumSize(200, 120)

        self.curve_v = self.plot_widget.plot(pen=pg.mkPen(color=theme_color("plot_voltage"), width=1.5))

        self.p_i = pg.ViewBox()
        self.plot_widget.scene().addItem(self.p_i)
        self.plot_widget.getAxis('right').linkToView(self.p_i)
        self.p_i.setXLink(self.plot_widget.plotItem)
        self.plot_widget.showAxis('right')

        self.curve_i = pg.PlotCurveItem(pen=pg.mkPen(color=theme_color("plot_current"), width=1.5))
        self.p_i.addItem(self.curve_i)

        self.plot_widget.getViewBox().sigResized.connect(self._update_plot_views)
        layout_readout.addWidget(self.plot_widget, 2, 0, 1, 3)
        layout_readout.setRowStretch(2, 1)

        right_col.addWidget(self.group_readout)
        main_layout.addLayout(right_col, stretch=2)

    def _build_ctrl_group(self) -> QGroupBox:
        """Marco de ajustes de una fuente: setpoints V / I y botón de salida."""
        self.group_ctrl = QGroupBox("Ajustes de Salida")
        layout_ctrl = QGridLayout(self.group_ctrl)
        self._layout_ctrl = layout_ctrl
        layout_ctrl.setContentsMargins(6, 6, 6, 6)
        layout_ctrl.setSpacing(4)

        self.lbl_v_tag = QLabel("Voltaje (V):")
        layout_ctrl.addWidget(self.lbl_v_tag, 0, 0)
        self.spin_v = QDoubleSpinBox()
        self.spin_v.setDecimals(3)
        self.spin_v.setSingleStep(0.1)
        self.spin_v.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_ctrl.addWidget(self.spin_v, 0, 1)

        self.btn_apply_v = QPushButton("Set V")
        layout_ctrl.addWidget(self.btn_apply_v, 0, 2)

        self.lbl_i_tag = QLabel("Límite I (A):")
        layout_ctrl.addWidget(self.lbl_i_tag, 1, 0)
        self.spin_i = QDoubleSpinBox()
        self.spin_i.setDecimals(3)
        self.spin_i.setSingleStep(0.05)
        self.spin_i.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_ctrl.addWidget(self.spin_i, 1, 1)

        self.btn_apply_i = QPushButton("Set I")
        layout_ctrl.addWidget(self.btn_apply_i, 1, 2)

        self.btn_output = QPushButton("HABILITAR SALIDA (OFF)")
        self.btn_output.setObjectName("btn_output_off")
        self.btn_output.setMinimumHeight(32)
        layout_ctrl.addWidget(self.btn_output, 2, 0, 1, 3)

        return self.group_ctrl

    # ------------------------------------------------------------------
    # LAYOUT RESPONSIVO (3 niveles según el ancho visible del dock):
    #   WIDE    columnas lado a lado
    #   COMPACT telemetría debajo de conexión/ajustes
    #   NARROW  además, conexión en 2 columnas (etiqueta | campo) y lecturas V/I/P apiladas
    # Nunca se comprime por debajo del mínimo del layout: si ni NARROW cabe, aparece el scroll.
    # ------------------------------------------------------------------
    LAYOUT_WIDE, LAYOUT_COMPACT, LAYOUT_NARROW = 0, 1, 2
    RESPONSIVE_HYSTERESIS = 16

    def _content_min_width(self) -> int:
        m = self._main_layout.contentsMargins()
        left, right = self._left_col.minimumSize().width(), self._right_col.minimumSize().width()
        if self._layout_level == self.LAYOUT_WIDE:
            return left + right + self._main_layout.spacing() + m.left() + m.right()
        return max(left, right) + m.left() + m.right()

    def wide_layout_min_width(self) -> int:
        return self._level_min_widths.get(self.LAYOUT_WIDE) or self._content_min_width()

    def _set_layout_level(self, level: int):
        if level == self._layout_level:
            return
        # Recordar el ancho mínimo del nivel que se abandona para saber cuándo volver a él
        self._level_min_widths[self._layout_level] = self._content_min_width()
        self._layout_level = level
        self._main_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if level == self.LAYOUT_WIDE else QBoxLayout.Direction.TopToBottom)
        narrow = level == self.LAYOUT_NARROW
        self._arrange_connection_grid(narrow)
        self._arrange_readout_grid(narrow)
        # Qt cachea el mínimo del layout: forzar recálculo para que el scroll area use el nuevo
        for lay in (self._layout_conn, self._layout_readout, self._left_col, self._right_col, self._main_layout):
            lay.invalidate()
        self._main_layout.activate()
        self.updateGeometry()

    def apply_responsive_layout(self, available_width: int):
        for _ in range(3):  # puede bajar/subir más de un nivel en un mismo resize
            level = self._layout_level
            if level > self.LAYOUT_WIDE:
                upper_min = self._level_min_widths.get(level - 1)
                if upper_min is not None and available_width >= upper_min + self.RESPONSIVE_HYSTERESIS:
                    self._set_layout_level(level - 1)
                    continue
            if level < self.LAYOUT_NARROW and available_width < self._content_min_width():
                self._set_layout_level(level + 1)
                continue
            break

    def layout_level(self) -> int:
        return self._layout_level

    def is_compact(self) -> bool:
        return self._layout_level != self.LAYOUT_WIDE

    def _arrange_connection_grid(self, narrow: bool):
        g = self._layout_conn
        if narrow:
            rows = [(self.lbl_profile_tag, self.combo_profile), (self.lbl_channel_tag, self.combo_channel),
                    (self.lbl_ip_tag, self.txt_ip), (self.lbl_port_tag, self.spin_port),
                    (self.lbl_mode_tag, self.combo_conn_type), (self.btn_connect, self.lbl_status)]
            for r, (a, b) in enumerate(rows):
                g.addWidget(a, r, 0)
                g.addWidget(b, r, 1)
        else:
            cells = [(self.lbl_profile_tag, 0, 0), (self.combo_profile, 0, 1), (self.lbl_channel_tag, 0, 2),
                     (self.combo_channel, 0, 3), (self.lbl_ip_tag, 1, 0), (self.txt_ip, 1, 1),
                     (self.lbl_port_tag, 1, 2), (self.spin_port, 1, 3), (self.lbl_mode_tag, 2, 0),
                     (self.combo_conn_type, 2, 1), (self.btn_connect, 2, 2), (self.lbl_status, 2, 3)]
            for w, r, c in cells:
                g.addWidget(w, r, c)

    def _arrange_readout_grid(self, narrow: bool):
        g = self._layout_readout
        pairs = [(self.lbl_v_head, self.lbl_v_val), (self.lbl_i_head, self.lbl_i_val),
                 (self.lbl_p_head, self.lbl_p_val)]
        for r in range(7):
            g.setRowStretch(r, 0)
        if narrow:
            for i, (head, val) in enumerate(pairs):
                g.addWidget(head, i * 2, 0)
                g.addWidget(val, i * 2 + 1, 0)
            g.addWidget(self.plot_widget, 6, 0, 1, 1)
            g.setRowStretch(6, 1)
        else:
            for i, (head, val) in enumerate(pairs):
                g.addWidget(head, 0, i)
                g.addWidget(val, 1, i)
            g.addWidget(self.plot_widget, 2, 0, 1, 3)
            g.setRowStretch(2, 1)

    def _update_plot_views(self, *_signal_args):
        self.p_i.setGeometry(self.plot_widget.getViewBox().sceneBoundingRect())
        self.p_i.linkedViewChanged(self.plot_widget.getViewBox(), self.p_i.XAxis)

    def apply_theme(self):
        """Reaplica los colores que no vienen del QSS (gráfica y estado de conexión)."""
        self.plot_widget.setBackground(theme_color("plot_bg"))
        self.curve_v.setPen(pg.mkPen(color=theme_color("plot_voltage"), width=1.5))
        self.curve_i.setPen(pg.mkPen(color=theme_color("plot_current"), width=1.5))
        for axis in ("left", "bottom", "right"):
            ax = self.plot_widget.getAxis(axis)
            ax.setPen(pg.mkPen(theme_color("plot_axis")))
            ax.setTextPen(pg.mkPen(theme_color("plot_axis")))
        if getattr(self, "_plot_title", None):
            self.plot_widget.setTitle(self._plot_title, color=theme_color("plot_axis"))
        self.set_connection_status(self.is_connected, self.is_error_state)

    def _retranslate_ctrl(self, lang_mgr):
        self.group_ctrl.setTitle(lang_mgr.get("group_ctrl"))
        self.lbl_v_tag.setText(lang_mgr.get("lbl_v"))
        self.lbl_i_tag.setText(lang_mgr.get("lbl_i"))
        self.btn_apply_v.setText(lang_mgr.get("btn_set_v"))
        self.btn_apply_i.setText(lang_mgr.get("btn_set_i"))

    def log(self, message: str):
        """El panel no tiene consola propia: el controlador reenvía a la ventana Consola SCPI y al Log Event."""

    def set_connection_status(self, connected: bool, is_error: bool = False):
        self.is_connected = connected
        self.is_error_state = is_error

        status_error = self.lang_mgr.get("status_error") if self.lang_mgr else "ERROR"
        status_conn = self.lang_mgr.get("status_connected") if self.lang_mgr else "CONECTADO"
        status_disconn = self.lang_mgr.get("status_disconnected") if self.lang_mgr else "DESCONECTADO"
        btn_conn = self.lang_mgr.get("btn_connect") if self.lang_mgr else "Conectar"
        btn_disconn = self.lang_mgr.get("btn_disconnect") if self.lang_mgr else "Desconectar"

        if is_error:
            self.lbl_status.setText(status_error)
            self.lbl_status.setStyleSheet(status_style("error"))
            self.btn_connect.setText(btn_conn)
        elif connected:
            self.lbl_status.setText(status_conn)
            self.lbl_status.setStyleSheet(status_style("connected"))
            self.btn_connect.setText(btn_disconn)
        else:
            self.lbl_status.setText(status_disconn)
            self.lbl_status.setStyleSheet(status_style("disconnected"))
            self.btn_connect.setText(btn_conn)

    def set_transition_status(self, text: str):
        """Estado intermedio (conectando / desconectando)."""
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(status_style("error"))

    def update_ui_telemetry(self, data: dict, v_history: list, i_history: list):
        read_v = data.get('ReadV', 0.0)
        read_i = data.get('ReadI', 0.0)
        power = data.get('Power', read_v * read_i)

        # Actualiza las lecturas numéricas
        self.lbl_v_val.setText(f"{read_v:.2f} V")
        self.lbl_i_val.setText(f"{read_i:.3f} A")
        self.lbl_p_val.setText(f"{power:.2f} W")

        # Actualiza el histórico en los gráficos pyqtgraph
        if v_history:
            self.curve_v.setData(v_history)
        if i_history:
            self.curve_i.setData(i_history)

    def retranslate_ui(self, lang_mgr):
        """Actualiza todos los textos de la interfaz individual según el idioma."""
        self.lang_mgr = lang_mgr
        if not lang_mgr:
            return

        self.group_conn.setTitle(lang_mgr.get("group_conn"))
        self.lbl_profile_tag.setText(lang_mgr.get("lbl_profile"))
        self.lbl_channel_tag.setText(lang_mgr.get("lbl_channel"))
        self.lbl_ip_tag.setText(lang_mgr.get("lbl_ip"))
        self.lbl_port_tag.setText(lang_mgr.get("lbl_port"))
        self.lbl_mode_tag.setText(lang_mgr.get("lbl_mode"))

        self.set_connection_status(self.is_connected, self.is_error_state)

        self._retranslate_ctrl(lang_mgr)
        self.btn_output.setText(lang_mgr.get(self.OUTPUT_KEYS[1] if self.output_is_on else self.OUTPUT_KEYS[0]))

        self.group_readout.setTitle(lang_mgr.get("group_readout"))

        self.lbl_v_head.setText(lang_mgr.get("lbl_v_head"))
        self.lbl_i_head.setText(lang_mgr.get("lbl_i_head"))
        self.lbl_p_head.setText(lang_mgr.get("lbl_p_head"))

        self._plot_title = lang_mgr.get("plot_title", index=self.psu_index)
        self.plot_widget.setTitle(self._plot_title, color=theme_color("plot_axis"))


class SingleELoadWidget(SinglePSUWidget):
    """
    Panel de una carga electrónica. Comparte conexión, consola, telemetría y layout responsivo con la fuente;
    el marco de ajustes ofrece modo de carga (CC/CV/CR/CP) y nivel.
    """

    OUTPUT_KEYS = ("btn_enable_in", "btn_disable_in")

    def _build_ctrl_group(self) -> QGroupBox:
        self.group_ctrl = QGroupBox("Ajustes de Carga")
        layout_ctrl = QGridLayout(self.group_ctrl)
        self._layout_ctrl = layout_ctrl
        layout_ctrl.setContentsMargins(6, 6, 6, 6)
        layout_ctrl.setSpacing(4)

        self.lbl_load_mode_tag = QLabel("Modo:")
        layout_ctrl.addWidget(self.lbl_load_mode_tag, 0, 0)
        self.combo_load_mode = QComboBox()
        for mode in LOAD_MODES:
            self.combo_load_mode.addItem(mode, userData=mode)
        self.combo_load_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_ctrl.addWidget(self.combo_load_mode, 0, 1, 1, 2)

        self.lbl_level_tag = QLabel("Nivel (A):")
        layout_ctrl.addWidget(self.lbl_level_tag, 1, 0)
        self.spin_level = QDoubleSpinBox()
        self.spin_level.setDecimals(4)
        self.spin_level.setSingleStep(0.1)
        self.spin_level.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout_ctrl.addWidget(self.spin_level, 1, 1)

        self.btn_apply_level = QPushButton("Set")
        layout_ctrl.addWidget(self.btn_apply_level, 1, 2)

        self.btn_output = QPushButton("HABILITAR ENTRADA (OFF)")
        self.btn_output.setObjectName("btn_output_off")
        self.btn_output.setMinimumHeight(32)
        layout_ctrl.addWidget(self.btn_output, 2, 0, 1, 3)
        return self.group_ctrl

    def current_load_mode(self) -> str:
        return self.combo_load_mode.currentData() or "CC"

    def update_level_label(self):
        mode = self.current_load_mode()
        base = self.lang_mgr.get("lbl_level") if self.lang_mgr else "Nivel"
        self.lbl_level_tag.setText(f"{base} ({MODE_UNITS[mode]}):")

    def _retranslate_ctrl(self, lang_mgr):
        self.group_ctrl.setTitle(lang_mgr.get("group_ctrl_load"))
        self.lbl_load_mode_tag.setText(lang_mgr.get("lbl_load_mode"))
        for i in range(self.combo_load_mode.count()):
            mode = self.combo_load_mode.itemData(i)
            self.combo_load_mode.setItemText(i, lang_mgr.get(f"mode_{mode}"))
        self.btn_apply_level.setText(lang_mgr.get("btn_set_level"))
        self.update_level_label()


class ResponsiveScrollArea(QScrollArea):
    """
    Contenedor del panel de cada instrumento: informa el ancho visible para que el panel elija su layout
    (lado a lado / apilado) y solo muestra barras de desplazamiento si ni el layout apilado cabe.
    """

    def __init__(self, content: QWidget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidget(content)

    def resizeEvent(self, event):
        content = self.widget()
        if hasattr(content, "apply_responsive_layout"):
            content.apply_responsive_layout(event.size().width())
        super().resizeEvent(event)


class SCPIConsoleWindow(QMainWindow):
    """
    Consola SCPI en ventana independiente: mensajes de comunicación de todos los instrumentos,
    con filtro por instrumento. Mismo estilo que el resto de la aplicación.
    """

    closed = pyqtSignal()
    MAX_ENTRIES = 5000
    ALL = "__all__"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SCPIConsoleWindow")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowTitle("Consola SCPI")
        self.resize(760, 420)
        self.setStyleSheet(stylesheet())
        self._entries = deque(maxlen=self.MAX_ENTRIES)  # (clave_instrumento, línea_html)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        self.group_console = QGroupBox("Consola SCPI")
        inner = QVBoxLayout(self.group_console)
        inner.setContentsMargins(4, 4, 4, 4)
        inner.setSpacing(4)

        top = QHBoxLayout()
        self.lbl_filter = QLabel("Instrumento:")
        top.addWidget(self.lbl_filter)
        self.combo_filter = QComboBox()
        self.combo_filter.addItem("Todos", self.ALL)
        self.combo_filter.currentIndexChanged.connect(self._render)
        top.addWidget(self.combo_filter)
        top.addStretch(1)
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.clicked.connect(lambda: self.clear())
        top.addWidget(self.btn_clear)
        inner.addLayout(top)

        self.txt_console = QTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.document().setMaximumBlockCount(LOG_MAX_LINES)
        inner.addWidget(self.txt_console)

        layout.addWidget(self.group_console)
        self.setCentralWidget(central)

    def set_sources(self, sources: list):
        """sources: [(clave, nombre_visible)] de los instrumentos abiertos."""
        current = self.combo_filter.currentData()
        self.combo_filter.blockSignals(True)
        while self.combo_filter.count() > 1:
            self.combo_filter.removeItem(1)
        for key, name in sources:
            self.combo_filter.addItem(name, key)
        idx = self.combo_filter.findData(current)
        self.combo_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_filter.blockSignals(False)
        if idx < 0:
            self._render()

    def add_entry(self, message: str, key: str, name: str, level: str = "info"):
        ts = time.strftime("[%H:%M:%S]")
        color = theme_color(f"log_{level}") if level in ("info", "warning", "error") else theme_color("log_info")
        line = f'<span style="color:{color}">{html.escape(f"{ts} [{name}] {message}")}</span>'
        self._entries.append((key, line))
        if self.combo_filter.currentData() in (self.ALL, key):
            self.txt_console.append(line)

    def _render(self, *_signal_args):
        selected = self.combo_filter.currentData()
        self.txt_console.clear()
        lines = [line for key, line in self._entries if selected in (self.ALL, key)]
        if lines:
            self.txt_console.append("<br>".join(lines))

    def clear(self):
        self._entries.clear()
        self.txt_console.clear()

    def retranslate_ui(self, lang_mgr):
        title = lang_mgr.get("group_log")
        self.setWindowTitle(title)
        self.group_console.setTitle(title)
        self.lbl_filter.setText(lang_mgr.get("lbl_filter"))
        self.combo_filter.setItemText(0, lang_mgr.get("filter_all"))
        self.btn_clear.setText(lang_mgr.get("btn_clear_log"))

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()


class LogEventWidget(QFrame):
    """
    Panel inferior "Log Event": registro centralizado de eventos de la aplicación y de todas las fuentes.
    Mismo estilo que la consola SCPI de cada fuente.
    """


    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.group_events = QGroupBox("Registro de Eventos")
        layout_events = QVBoxLayout(self.group_events)
        layout_events.setContentsMargins(4, 4, 4, 4)
        layout_events.setSpacing(4)

        self.txt_events = QTextEdit()
        self.txt_events.setReadOnly(True)
        self.txt_events.document().setMaximumBlockCount(LOG_MAX_LINES)
        self.txt_events.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout_events.addWidget(self.txt_events)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.clicked.connect(lambda: self.txt_events.clear())
        bar.addWidget(self.btn_clear)
        layout_events.addLayout(bar)

        layout.addWidget(self.group_events)

    def add_event(self, message: str, level: str = "info", source: str = ""):
        ts = time.strftime("[%H:%M:%S]")
        prefix = f"[{source}] " if source else ""
        color = theme_color(f"log_{level}") if level in ("info", "warning", "error") else theme_color("log_info")
        text = html.escape(f"{ts} {prefix}{message}")
        self.txt_events.append(f'<span style="color:{color}">{text}</span>')

    def retranslate_ui(self, lang_mgr):
        self.group_events.setTitle(lang_mgr.get("group_events"))
        self.btn_clear.setText(lang_mgr.get("btn_clear_log"))


class MultiPSUWindowUI(QMainWindow):
    """
    Ventana principal con soporte completo para Drag & Drop y escalado libre.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema Multi-Control de Fuentes de Alimentación")
        self.setGeometry(100, 80, 1000, 600)

        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.AnimatedDocks
        )

        self._create_menus()
        self._create_toolbar()
        self.setStyleSheet(stylesheet())

    def _make_tool_button(self, shape: str, color_key: str, name: str, checkable: bool = False,
                          text: str = "") -> QToolButton:
        btn = QToolButton()
        btn.setObjectName(name)
        btn.setProperty("icon_shape", shape)
        btn.setProperty("icon_color", color_key)
        btn.setIcon(make_toolbar_icon(shape, color_key))
        btn.setCheckable(checkable)
        if text:
            btn.setText(text)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        return btn

    def refresh_toolbar_icons(self):
        for btn in (self.btn_record, self.btn_pause, self.btn_stop, self.btn_export):
            btn.setIcon(make_toolbar_icon(btn.property("icon_shape"), btn.property("icon_color")))

    def _create_menus(self):
        bar = self.menuBar()

        # Config
        self.menu_config = bar.addMenu("Config")
        self.menu_language = self.menu_config.addMenu("Idioma")
        self.lang_action_group = QActionGroup(self)
        self.lang_action_group.setExclusive(True)
        self.lang_actions = {}
        for code, name in (("es", "Español"), ("en", "English"), ("fr", "Français")):
            act = QAction(name, self, checkable=True)
            act.setData(code)
            self.lang_action_group.addAction(act)
            self.menu_language.addAction(act)
            self.lang_actions[code] = act
        self.menu_config.addSeparator()
        self.act_open_config = QAction("Abrir carpeta de configuración", self)
        self.menu_config.addAction(self.act_open_config)

        # View
        self.menu_view = bar.addMenu("View")
        self.act_view_console = QAction("Consola SCPI", self, checkable=True)
        self.act_view_log = QAction("Event Log", self, checkable=True)
        self.menu_view.addAction(self.act_view_console)
        self.menu_view.addAction(self.act_view_log)
        self.menu_view.addSeparator()
        self.menu_theme = self.menu_view.addMenu("Temas")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.theme_actions = {}
        for name in THEME_NAMES:
            act = QAction(name, self, checkable=True)
            act.setData(name)
            self.theme_action_group.addAction(act)
            self.menu_theme.addAction(act)
            self.theme_actions[name] = act
        self.menu_view.addSeparator()
        self.act_equalize = QAction("Igualar paneles", self)
        self.menu_view.addAction(self.act_equalize)

        # Help
        self.menu_help = bar.addMenu("Help")
        self.act_about = QAction("About", self)
        self.act_about_qt = QAction("About Qt", self)
        self.menu_help.addAction(self.act_about)
        self.menu_help.addAction(self.act_about_qt)

    def _create_toolbar(self):
        self.toolbar = QToolBar("Barra de herramientas")
        self.toolbar.setObjectName("MainToolBar")
        self.addToolBar(self.toolbar)

        # Grabación de telemetría: a la izquierda de "Agregar Fuente"
        self.toolbar.setIconSize(QSize(18, 18))
        self.btn_record = self._make_tool_button("record", "rec", "btn_record")
        self.btn_pause = self._make_tool_button("pause", "icon", "btn_pause", checkable=True)
        self.btn_stop = self._make_tool_button("stop", "icon", "btn_stop")
        self.btn_export = self._make_tool_button("export", "icon", "btn_export", text="Exportar")
        for btn in (self.btn_record, self.btn_pause, self.btn_stop, self.btn_export):
            self.toolbar.addWidget(btn)
        self.lbl_rec_status = QLabel("")
        self.lbl_rec_status.setObjectName("lbl_rec_status")
        self.toolbar.addWidget(self.lbl_rec_status)
        self.toolbar.addSeparator()

        self.btn_add_psu = QPushButton("➕ Agregar Fuente")
        self.toolbar.addWidget(self.btn_add_psu)

        self.btn_add_eload = QPushButton("➕ Agregar ELoad")
        self.toolbar.addWidget(self.btn_add_eload)