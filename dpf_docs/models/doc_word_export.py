# -*- coding: utf-8 -*-
"""Word (.docx) export for documented modules, user-manual style.

Generates a complete Russian-language user manual in Word format:

* Tittle page (system name, "Руководство пользователя", version, developer, city/year)
* Real Word TOC field ("ОГЛАВЛЕНИЕ")
* 1. Введение (1.1 Категории, 1.2 Область, 1.3 Назначение, 1.4 Соглашения)
* 2. Содержание документа (2.1 Назначение, 2.2 Материалы, 2.3 Подготовка)
* 3. Список функций — one block per function:
    Функция N: <title>  →  Описание / Требования / Порядок выполнения / screenshot or placeholder / Результат
* 4. Литература
* 5. Словарь терминов

Screenshots are optional: if a function has no screenshot a greyed
italic placeholder line is inserted instead.

``python-docx`` is loaded lazily so the addon still installs without it;
the Word button then raises a clear, actionable error.
"""
import base64
import io
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import docx  # python-docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    HAS_DOCX = True
except ImportError:  # pragma: no cover
    HAS_DOCX = False

BODY_FONT = "Times New Roman"
BODY_SIZE = 12
_GREY = None  # lazy RGBColor below


def _grey():
    return RGBColor(0x80, 0x80, 0x80)


def _red():
    return RGBColor(0xC0, 0x00, 0x00)


class DocWordExport(models.AbstractModel):
    _name = "doc.word.export"
    _description = "DPF Docs - Word Export Service"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    @api.model
    def build_docx(self, doc_modules):
        """Return raw .docx bytes for all given doc.module records."""
        if not HAS_DOCX:
            raise UserError(_(
                "Пакет 'python-docx' не установлен на сервере.\n"
                "Попросите администратора выполнить:  pip install python-docx"
            ))

        document = docx.Document()
        self._apply_base_styles(document)

        primary = doc_modules[:1]
        self._add_cover(document, primary)
        self._add_toc(document)
        self._add_intro_section(document, primary)
        self._add_content_section(document, primary)
        self._add_functions_section(document, doc_modules)
        self._add_bibliography_section(document, primary)
        self._add_glossary_section(document, primary)

        buf = io.BytesIO()
        document.save(buf)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _apply_base_styles(self, document):
        style = document.styles["Normal"]
        style.font.name = BODY_FONT
        style.font.size = Pt(BODY_SIZE)
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:cs"), BODY_FONT)
        rfonts.set(qn("w:eastAsia"), BODY_FONT)

    # ------------------------------------------------------------------
    # Cover page
    # ------------------------------------------------------------------
    def _add_cover(self, document, modules):
        m = modules[:1]
        system_name = (m.system_name if m else None) or (
            '\u0421\u0438\u0441\u0442\u0435\u043c\u0430 "%s"' % (m.name if m else "Odoo")
        )
        platform = (m.platform_version if m else None) or "Odoo 19"
        version = (m.manual_version if m else None) or "1.0"
        developer = (m.developer if m else None) or ""
        city_year = (m.city_year if m else None) or ""

        for _ in range(7):
            document.add_paragraph()

        self._centered(document, system_name, size=18)
        self._centered(document, '\u043d\u0430 \u0431\u0430\u0437\u0435 \u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u044b "%s"' % platform, size=16)
        document.add_paragraph()
        self._centered(document, "\u0420\u0443\u043a\u043e\u0432\u043e\u0434\u0441\u0442\u0432\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f", bold=True, size=18)
        self._centered(document, "\u0412\u0435\u0440\u0441\u0438\u044f %s" % version, size=14)

        for _ in range(4):
            document.add_paragraph()

        if developer:
            self._centered(document, "\u0420\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0447\u0438\u043a: %s" % developer, size=12)

        for _ in range(6):
            document.add_paragraph()

        if city_year:
            self._centered(document, city_year, size=12)

        document.add_page_break()

    def _centered(self, document, text, bold=False, size=12):
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(size)
        r.font.name = BODY_FONT
        return p

    # ------------------------------------------------------------------
    # Table of contents
    # ------------------------------------------------------------------
    def _add_toc(self, document):
        h = document.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = h.add_run("\u041e\u0413\u041b\u0410\u0412\u041b\u0415\u041d\u0418\u0415")
        run.bold = True
        run.font.size = Pt(14)
        document.add_paragraph()

        p = document.add_paragraph()
        r = p.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = r'TOC \o "1-3" \h \z \u'
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        hint = OxmlElement("w:t")
        hint.text = (
            "\u041e\u0433\u043b\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438: "
            "\u043f\u0440\u0430\u0432\u044b\u0439 \u043a\u043b\u0438\u043a \u2192 \u00ab\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u0435\u00bb."
        )
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        r._r.extend([fld_begin, instr, fld_sep, hint, fld_end])
        # NOTE: page_break intentionally removed here.
        # Heading 1 style in Word already forces a page break before the next
        # section, so an explicit add_page_break() was creating a blank page
        # between the TOC and section 1. Removing it also eliminates the large
        # empty space at the bottom of the TOC page when there are few entries.

    # ------------------------------------------------------------------
    # Heading helpers
    # ------------------------------------------------------------------
    def _heading(self, document, text, level):
        p = document.add_heading("", level=level)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(text)
        r.bold = True
        r.font.color.rgb = RGBColor(0, 0, 0)
        r.font.name = BODY_FONT
        r.font.size = Pt(14 if level == 1 else 12)
        return p

    def _paragraph(self, document, text):
        if text and text.strip():
            document.add_paragraph(text.strip())

    def _bullets(self, document, text):
        """Each non-empty line becomes a bullet."""
        for line in (text or "").splitlines():
            line = line.strip()
            if line:
                document.add_paragraph(line, style="List Bullet")

    def _numbered_lines(self, document, text):
        """Each non-empty line becomes a numbered paragraph starting at 1."""
        for i, line in enumerate(
            [l.strip() for l in (text or "").splitlines() if l.strip()], 1
        ):
            p = document.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            p.add_run("%d. %s" % (i, line))

    # ------------------------------------------------------------------
    # 1. Введение
    # ------------------------------------------------------------------
    def _add_intro_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "1. \u0412\u0432\u0435\u0434\u0435\u043d\u0438\u0435", 1)

        self._heading(document, "1.1. \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0439 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439", 2)
        self._bullets(document, m.intro_user_categories if m else "")

        self._heading(document, "1.2. \u041e\u0431\u043b\u0430\u0441\u0442\u044c \u043f\u0440\u0438\u043c\u0435\u043d\u0435\u043d\u0438\u044f", 2)
        self._bullets(document, m.intro_scope if m else "")

        self._heading(document, "1.3. \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430", 2)
        self._paragraph(document, m.intro_purpose if m else "")

        self._heading(document, "1.4. \u0421\u043e\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u044f", 2)
        self._bullets(document, m.intro_conventions if m else "")

    # ------------------------------------------------------------------
    # 2. Содержание документа
    # ------------------------------------------------------------------
    def _add_content_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "2. \u0421\u043e\u0434\u0435\u0440\u0436\u0430\u043d\u0438\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430", 1)

        self._heading(document, "2.1. \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435", 2)
        self._paragraph(document, m.content_purpose if m else "")

        self._heading(document, "2.2. \u041d\u0435\u043e\u0431\u0445\u043e\u0434\u0438\u043c\u044b\u0435 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b", 2)
        self._bullets(document, m.content_materials if m else "")

        self._heading(document, "2.3. \u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043a\u0430 \u043a \u0440\u0430\u0431\u043e\u0442\u0435", 2)
        self._numbered_lines(document, m.content_preparation if m else "")

    # ------------------------------------------------------------------
    # 3. Список функций
    # ------------------------------------------------------------------
    def _add_functions_section(self, document, doc_modules):
        # NOTE: page_break removed here — the Heading 1 style already forces
        # a new page in Word, and an explicit break was creating a blank page.
        self._heading(document, "3. \u0421\u043f\u0438\u0441\u043e\u043a \u0444\u0443\u043d\u043a\u0446\u0438\u0439", 1)

        figure = 0
        sub = 0
        for doc_module in doc_modules:
            sub += 1
            mod_name = doc_module.name or doc_module.technical_name
            self._heading(document, "3.%d. %s" % (sub, mod_name), 2)

            funcs = doc_module.function_ids.sorted(
                key=lambda f: ((f.number or 999999), (f.sequence or 999999), f.id)
            )
            if not funcs:
                p = document.add_paragraph()
                r = p.add_run("\u0424\u0443\u043d\u043a\u0446\u0438\u0438 \u0434\u043b\u044f \u0434\u0430\u043d\u043d\u043e\u0433\u043e \u043c\u043e\u0434\u0443\u043b\u044f \u043d\u0435 \u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u043d\u044b.")
                r.font.color.rgb = _grey()
                r.italic = True
                continue

            for func in funcs:
                figure = self._add_function(document, func, figure)

    def _add_function(self, document, func, figure):
        """Render one function block. Returns updated figure counter."""
        # Функция N: Title
        title_p = document.add_paragraph()
        title_p.paragraph_format.space_before = Pt(10)
        r = title_p.add_run("\u0424\u0443\u043d\u043a\u0446\u0438\u044f %d: %s." % (func.number or 0, func.name or ""))
        r.bold = True
        r.font.size = Pt(12)

        # Описание
        self._labelled(document, "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435:", func.description)

        # Требования (red text)
        if func.requirements and func.requirements.strip():
            p = document.add_paragraph()
            lbl = p.add_run("\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f: ")
            lbl.bold = True
            val = p.add_run(func.requirements.strip())
            val.font.color.rgb = _red()

        # Порядок выполнения
        steps = func.step_lines() if hasattr(func, "step_lines") else []
        if steps:
            p = document.add_paragraph()
            p.add_run("\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f:").bold = True
            for idx, step in enumerate(steps, 1):
                sp = document.add_paragraph()
                sp.paragraph_format.left_indent = Pt(18)
                sp.add_run("%d. %s" % (idx, step))

        # Screenshot or placeholder
        if func.screenshot:
            figure = self._embed_screenshot(document, func, figure)
        else:
            figure = self._add_screenshot_placeholder(document, func, figure)

        # Результат
        self._labelled(document, "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442:", func.result)

        # Separator
        sep = document.add_paragraph()
        sep.paragraph_format.space_after = Pt(6)

        return figure

    def _labelled(self, document, label, value):
        """Bold label + normal text in the same paragraph."""
        if not value or not value.strip():
            return
        p = document.add_paragraph()
        r = p.add_run(label + " ")
        r.bold = True
        p.add_run(value.strip())

    def _embed_screenshot(self, document, func, figure):
        """Embed a real screenshot image with a figure caption."""
        try:
            stream = io.BytesIO(base64.b64decode(func.screenshot))
            document.add_picture(stream, width=Inches(5.5))
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            figure += 1
            cap_p = document.add_paragraph()
            cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption_text = func.screenshot_caption or func.name or ""
            cap_r = cap_p.add_run("\u0420\u0438\u0441.%d %s" % (figure, caption_text))
            cap_r.font.size = Pt(10)
            cap_r.italic = True
        except Exception as exc:  # pragma: no cover
            _logger.warning(
                "Could not embed screenshot for function %s: %s", func.id, exc
            )
        return figure

    def _add_screenshot_placeholder(self, document, func, figure):
        """\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u0443\u044e \u0437\u0430\u0433\u043b\u0443\u0448\u043a\u0443 \u0432\u043c\u0435\u0441\u0442\u043e \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u0430."""
        figure += 1
        name = func.name or "\u044d\u043a\u0440\u0430\u043d\u0430"
        caption_text = func.screenshot_caption or name

        # Grey bordered placeholder block
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(
            "\ud83d\udccc [\u0417\u0434\u0435\u0441\u044c \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442 \u044d\u043a\u0440\u0430\u043d\u0430 \u00ab%s\u00bb]"
            % name
        )
        r.font.color.rgb = _grey()
        r.italic = True
        r.font.size = Pt(11)

        # Caption line below placeholder
        cap_p = document.add_paragraph()
        cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap_r = cap_p.add_run("\u0420\u0438\u0441.%d %s" % (figure, caption_text))
        cap_r.font.size = Pt(10)
        cap_r.italic = True
        cap_r.font.color.rgb = _grey()

        return figure

    # ------------------------------------------------------------------
    # 4. Литература / 5. Словарь
    # ------------------------------------------------------------------
    def _add_bibliography_section(self, document, modules):
        """Render section 4 (Bibliography).

        NOTE: document.add_page_break() was removed from here.
        Heading 1 style in Word already inserts a page break before
        the heading, so an explicit break was creating a blank page
        between section 3 and section 4.
        """
        m = modules[:1]
        self._heading(document, "4. \u041b\u0438\u0442\u0435\u0440\u0430\u0442\u0443\u0440\u0430", 1)
        self._bullets(document, m.bibliography if m else "")

    def _add_glossary_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "5. \u0421\u043b\u043e\u0432\u0430\u0440\u044c \u0442\u0435\u0440\u043c\u0438\u043d\u043e\u0432", 1)
        self._bullets(document, m.glossary if m else "")
