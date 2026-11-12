import io
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

from services.logger_service import get_logger

logger = get_logger("PrintService")

FONT_NAME = "Helvetica"
BOLD_FONT_NAME = "Helvetica-Bold"

def _setup_fonts():
    global FONT_NAME, BOLD_FONT_NAME
    font_candidates = [
        # Windows
        ("Arial", "C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
        ("Tahoma", "C:\\Windows\\Fonts\\tahoma.ttf", "C:\\Windows\\Fonts\\tahomabd.ttf"),
        # Linux (Ubuntu)
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("LiberationSans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    
    for name, regular, bold in font_candidates:
        if os.path.exists(regular):
            try:
                pdfmetrics.registerFont(TTFont(name, regular))
                if os.path.exists(bold):
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
                    BOLD_FONT_NAME = f"{name}-Bold"
                else:
                    BOLD_FONT_NAME = name
                FONT_NAME = name
                logger.info(f"Зарегистрирован кириллический шрифт для PDF: {FONT_NAME}")
                return
            except Exception as e:
                logger.warning(f"Не удалось загрузить шрифт {name}: {e}")

_setup_fonts()


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont(FONT_NAME, 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(15 * mm, 12 * mm, 195 * mm, 12 * mm)
        self.drawString(15 * mm, 8 * mm, "АО «KDB Bank» · Управление Информационных Технологий (IT Department)")
        self.drawRightString(195 * mm, 8 * mm, f"Стр. {self._pageNumber} из {page_count}")
        self.restoreState()


def generate_equipment_act(user_details: dict, pc_details: dict | None = None) -> io.BytesIO:
    """Генерирует официальный Акт приема-передачи оборудования в формате PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    
    style_header_bank = ParagraphStyle(
        'BankHeader',
        fontName=BOLD_FONT_NAME,
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0f172a"),
    )
    style_sub_bank = ParagraphStyle(
        'BankSubHeader',
        fontName=FONT_NAME,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )
    style_title = ParagraphStyle(
        'DocTitle',
        fontName=BOLD_FONT_NAME,
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#0f172a"),
        alignment=1,
        spaceAfter=4,
    )
    style_subtitle = ParagraphStyle(
        'DocSubTitle',
        fontName=FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        alignment=1,
        spaceAfter=12,
    )
    style_body = ParagraphStyle(
        'DocBody',
        fontName=FONT_NAME,
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=8,
    )
    style_table_cell = ParagraphStyle(
        'TableCell',
        fontName=FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1e293b"),
    )
    style_table_header = ParagraphStyle(
        'TableHeader',
        fontName=BOLD_FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0f172a"),
    )
    style_sign_title = ParagraphStyle(
        'SignTitle',
        fontName=BOLD_FONT_NAME,
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#0f172a"),
    )
    style_sign_body = ParagraphStyle(
        'SignBody',
        fontName=FONT_NAME,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )

    story = []

    # 1. Шапка Банка
    header_data = [
        [
            Paragraph("<b>АО «KDB Bank»</b><br/>Управление Информационных Технологий", style_header_bank),
            Paragraph("<b>УТВЕРЖДЕНО</b><br/>Регламент выдачи ИТ-оборудования<br/>АО «KDB Bank» v2.4", style_sub_bank)
        ]
    ]
    t_header = Table(header_data, colWidths=[110 * mm, 70 * mm])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceAfter=12))

    # 2. Название документа
    current_date = datetime.datetime.now().strftime("%d.%m.%Y")
    act_num = datetime.datetime.now().strftime("%Y%m%d") + "-" + str(user_details.get("username", "001"))[:6].upper()
    
    story.append(Paragraph(f"АКТ ПРИЕМА-ПЕРЕДАЧИ ОБОРУДОВАНИЯ № {act_num}", style_title))
    story.append(Paragraph(f"г. Алматы &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Дата: <b>{current_date} г.</b>", style_subtitle))
    
    # 3. Вводный текст
    fio = user_details.get("display_name", "Сотрудник Банка")
    dept = user_details.get("department", "Структурное подразделение")
    title = user_details.get("title", "Должность")
    login = user_details.get("username", "—")
    
    preamble = (
        f"Настоящий Акт составлен о том, что Управление Информационных Технологий АО «KDB Bank» передает, "
        f"а сотрудник <b>{fio}</b> (должность: <i>{title}</i>, подразделение: <i>{dept}</i>, учетная запись: <b>{login}</b>) "
        f"принимает нижеперечисленное компьютерное оборудование и сопутствующие материальные ценности во временное служебное пользование "
        f"для выполнения трудовых обязанностей:"
    )
    story.append(Paragraph(preamble, style_body))
    story.append(Spacer(1, 3 * mm))

    # 4. Таблица передаваемого оборудования
    pc_name = pc_details.get("name", "Ноутбук / Рабочая станция") if pc_details else "Ноутбук Lenovo ThinkPad"
    pc_os = pc_details.get("os", "Microsoft Windows 10/11 Enterprise") if pc_details else "Windows Enterprise"
    pc_desc = pc_details.get("description", "Рабочая станция сотрудника") if pc_details else "Корпоративный ноутбук"
    pc_dns = pc_details.get("dns_name", f"{pc_name}.kdb.kz") if pc_details else "kdb.kz"

    items_data = [
        [
            Paragraph("№", style_table_header),
            Paragraph("Наименование оборудования", style_table_header),
            Paragraph("Сетевое имя / Идентификатор", style_table_header),
            Paragraph("Конфигурация / ОС", style_table_header),
            Paragraph("Кол-во", style_table_header),
        ],
        [
            Paragraph("1", style_table_cell),
            Paragraph(f"<b>Рабочая станция / Ноутбук</b><br/><font size=7.5 color='#64748b'>{pc_desc}</font>", style_table_cell),
            Paragraph(f"<b>{pc_name}</b><br/><font size=7.5 color='#64748b'>{pc_dns}</font>", style_table_cell),
            Paragraph(pc_os, style_table_cell),
            Paragraph("1 шт.", style_table_cell),
        ],
        [
            Paragraph("2", style_table_cell),
            Paragraph("<b>Адаптер питания (Зарядное устройство)</b>", style_table_cell),
            Paragraph("Совместимый Type-C / Slim Tip", style_table_cell),
            Paragraph("Оригинал 65W / 90W", style_table_cell),
            Paragraph("1 шт.", style_table_cell),
        ],
        [
            Paragraph("3", style_table_cell),
            Paragraph("<b>Корпоративный защищенный профиль AD</b>", style_table_cell),
            Paragraph(f"Логин: <b>{login}</b>", style_table_cell),
            Paragraph("Домен: kdb.kz (StartTLS PKI)", style_table_cell),
            Paragraph("1 лиц.", style_table_cell),
        ],
    ]

    t_items = Table(items_data, colWidths=[10 * mm, 62 * mm, 46 * mm, 48 * mm, 14 * mm])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_items)
    story.append(Spacer(1, 5 * mm))

    # 5. Обязательства сотрудника
    rules_text = (
        "<b>Условия и ответственность:</b><br/>"
        "1. Сотрудник подтверждает, что переданное оборудование находится в исправном техническом состоянии, внешних механических повреждений не имеет.<br/>"
        "2. Сотрудник обязуется использовать технику исключительно в служебных целях, соблюдать требования Политики Информационной Безопасности Банка, не передавать пароль и учетную запись третьим лицам.<br/>"
        "3. При увольнении, переводе или замене оборудования сотрудник обязан вернуть технику в Управление Информационных Технологий в полной комплектности."
    )
    story.append(Paragraph(rules_text, ParagraphStyle('Rules', fontName=FONT_NAME, fontSize=8, leading=11.5, textColor=colors.HexColor("#334155"))))
    story.append(Spacer(1, 8 * mm))

    # 6. Блок подписей
    signs_data = [
        [
            Paragraph("<b>Оборудование передал:</b>", style_sign_title),
            Paragraph("<b>Оборудование принял:</b>", style_sign_title),
        ],
        [
            Paragraph("Специалист УИТ АО «KDB Bank»<br/><br/><br/>_________________ / _________________ /<br/><font size=7 color='#64748b'>(подпись / расшифровка)</font>", style_sign_body),
            Paragraph(f"Сотрудник:<br/><b>{fio}</b><br/><br/>_________________ / {fio} /<br/><font size=7 color='#64748b'>(подпись / расшифровка)</font>", style_sign_body),
        ],
        [
            Paragraph(f"М.П. &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; «{current_date[:2]}» {datetime.datetime.now().strftime('%m')} 2026 г.", style_sign_body),
            Paragraph(f"«{current_date[:2]}» {datetime.datetime.now().strftime('%m')} 2026 г.", style_sign_body),
        ]
    ]

    t_signs = Table(signs_data, colWidths=[90 * mm, 90 * mm])
    t_signs.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(t_signs)

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    logger.info(f"Акт приема-передачи успешно сформирован в PDF для {login}")
    return buffer
