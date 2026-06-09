# -*- coding: utf-8 -*-
"""Word (.docx) export for documented modules, user-manual style.

Implemented as a separate model (``doc.word.export``, an AbstractModel service)
so the export logic stays in its own file, per the project's one-class-per-file
architecture.

The generated document mirrors the reference user manual:

* a centered title page (system name, "Руководство пользователя", version,
  developer, city / year);
* a real Word table of contents ("ОГЛАВЛЕНИЕ") built from a TOC field so it
  paginates correctly once the reader updates fields;
* numbered, bold section headings (1. Введение, 2. Содержание документа,
  3. Список функций, 4. Литература, 5. Словарь);
* per-function blocks: bold "Функция N:", bold "Описание:" / "Требования:"
  labels (requirements text in red), a numbered "Порядок выполнения:" list, a
  centered screenshot with a "Рис.N" caption, and a bold "Результат:".

``python-docx`` is required for this feature. It is imported lazily so the
addon still installs and runs (Markdown + PDF) on systems where it is missing;
the Word button then raises a clear, actionable error instead of failing at
import time.
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
except ImportError:  # pragma: no cover - optional dependency
    HAS_DOCX = False


# Visual constants (kept close to the reference manual look).
BODY_FONT = "Times New Roman"
BODY_SIZE = 12  # points


class DocWordExport(models.AbstractModel):
    _name = "doc.word.export"
    _description = "DPF Docs - Word Export Service"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    @api.model
    def build_docx(self, doc_modules):
        """Return raw .docx bytes documenting the given ``doc.module`` records."""
        if not HAS_DOCX:
            raise UserError(_(
                "The Python package 'python-docx' is required for Word export.\n"
                "Install it on the Odoo server with:  pip install python-docx"
            ))

        document = docx.Document()
        self._apply_base_styles(document)

        # One manual covers one product; if several modules were collected we
        # still produce a single document, using the first module's metadata
        # for the cover and emitting a function chapter per module.
        primary = doc_modules[:1]
        self._add_cover(document, primary)
        self._add_toc(document)

        self._add_intro_section(document, primary)
        self._add_content_section(document, primary)
        self._add_functions_section(document, doc_modules)
        self._add_bibliography_section(document, primary)
        self._add_glossary_section(document, primary)

        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Styling helpers
    # ------------------------------------------------------------------
    def _apply_base_styles(self, document):
        """Set a clean, manual-like default font for the whole document."""
        style = document.styles["Normal"]
        style.font.name = BODY_FONT
        style.font.size = Pt(BODY_SIZE)
        # Ensure Cyrillic glyphs use the same face.
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:cs"), BODY_FONT)
        rfonts.set(qn("w:eastAsia"), BODY_FONT)

    @staticmethod
    def _red():
        return RGBColor(0xC0, 0x00, 0x00)

    # ------------------------------------------------------------------
    # Cover page
    # ------------------------------------------------------------------
    def _add_cover(self, document, modules):
        """Centered title page matching the reference manual."""
        module = modules[:1]
        system_name = (module.system_name if module else None) or (
            'Система "%s"' % (module.name if module else "Odoo")
        )
        platform = (module.platform_version if module else None) or "Odoo 19"
        version = (module.manual_version if module else None) or "1.0"
        developer = (module.developer if module else None) or ""
        city_year = (module.city_year if module else None) or ""

        # Push the title block toward vertical center.
        for _idx in range(7):
            document.add_paragraph()

        self._centered_line(document, system_name, bold=False, size=18)
        self._centered_line(
            document, 'на базе платформы "%s"' % platform, bold=False, size=18
        )
        document.add_paragraph()
        self._centered_line(
            document, "Руководство пользователя", bold=False, size=16
        )
        self._centered_line(document, "Версия %s" % version, bold=False, size=16)

        for _idx in range(3):
            document.add_paragraph()

        if developer:
            self._centered_line(
                document, "Разработчик: %s" % developer, bold=False, size=12
            )

        # Footer city/year sits near the bottom of the page.
        for _idx in range(7):
            document.add_paragraph()
        if city_year:
            self._centered_line(document, city_year, bold=False, size=12)

        document.add_page_break()

    def _centered_line(self, document, text, bold=False, size=12):
        para = document.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        return para

    # ------------------------------------------------------------------
    # Table of contents (real Word TOC field)
    # ------------------------------------------------------------------
    def _add_toc(self, document):
        """Insert an "ОГЛАВЛЕНИЕ" header and a TOC field (levels 1-3)."""
        header = document.add_paragraph()
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = header.add_run("ОГЛАВЛЕНИЕ")
        run.bold = True
        run.font.size = Pt(14)
        document.add_paragraph()

        para = document.add_paragraph()
        run = para.add_run()
        # Build: <w:fldChar begin> <w:instrText> TOC ... </w:instrText>
        #        <w:fldChar separate> "update fields" hint <w:fldChar end>
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = r'TOC \o "1-3" \h \z \u'
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = (
            "Оглавление обновляется автоматически: "
            "правый клик \u2192 «Обновить поле»."
        )
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")

        run._r.append(fld_begin)
        run._r.append(instr)
        run._r.append(fld_sep)
        run._r.append(placeholder)
        run._r.append(fld_end)

        document.add_page_break()

    # ------------------------------------------------------------------
    # Heading helpers (numbered, bold, registered in the TOC)
    # ------------------------------------------------------------------
    def _heading(self, document, text, level):
        """Add a bold numbered heading that the TOC field will pick up.

        We use python-docx's built-in Heading styles (level 1-3) so they are
        included in the TOC, but override the look to match the manual: bold,
        black, body font, modest size.
        """
        para = document.add_heading("", level=level)
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = para.add_run(text)
        run.bold = True
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        run.font.name = BODY_FONT
        run.font.size = Pt(14 if level == 1 else 12)
        return para

    def _bullets(self, document, text):
        """Render each non-empty line of ``text`` as a bullet paragraph."""
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            document.add_paragraph(line, style="List Bullet")

    def _paragraph(self, document, text):
        if text:
            document.add_paragraph(text.strip())

    # ------------------------------------------------------------------
    # 1. Введение
    # ------------------------------------------------------------------
    def _add_intro_section(self, document, modules):
        module = modules[:1]
        self._heading(document, "1. Введение", 1)

        self._heading(document, "1.1. Описание категории пользователей", 2)
        self._bullets(document, module.intro_user_categories if module else "")

        self._heading(document, "1.2. Область применения", 2)
        self._bullets(document, module.intro_scope if module else "")

        self._heading(document, "1.3. Назначение документа", 2)
        self._paragraph(document, module.intro_purpose if module else "")

        self._heading(document, "1.4. Соглашения", 2)
        self._heading(document, "1.4.1. Стилистические соглашения", 3)
        self._bullets(document, module.intro_conventions if module else "")

    # ------------------------------------------------------------------
    # 2. Содержание документа
    # ------------------------------------------------------------------
    def _add_content_section(self, document, modules):
        module = modules[:1]
        self._heading(document, "2. Содержание документа", 1)

        self._heading(document, "2.1. Назначение", 2)
        self._paragraph(document, module.content_purpose if module else "")

        self._heading(document, "2.2. Материалы", 2)
        self._bullets(document, module.content_materials if module else "")

        self._heading(document, "2.3. Подготовка", 2)
        self._bullets(document, module.content_preparation if module else "")

    # ------------------------------------------------------------------
    # 3. Список функций (core)
    # ------------------------------------------------------------------
    def _add_functions_section(self, document, doc_modules):
        document.add_page_break()
        self._heading(document, "3. Список функций", 1)

        sub = 0
        figure = 0
        for doc_module in doc_modules:
            sub += 1
            self._heading(
                document,
                "3.%d. %s" % (sub, doc_module.name or doc_module.technical_name),
                2,
            )
            functions = doc_module.function_ids
            if not functions:
                self._paragraph(
                    document, "Функции для данного модуля не сформированы."
                )
                continue
            for func in functions:
                figure = self._add_function(document, func, figure)

    def _add_function(self, document, func, figure):
        """Render one doc.function block; return the updated figure counter."""
        # Bold "Функция N: Title."
        title_para = document.add_paragraph()
        title_para.paragraph_format.space_before = Pt(10)
        run = title_para.add_run(
            "Функция %d: %s." % (func.number or 0, func.name or "")
        )
        run.bold = True

        # Описание:
        self._labelled(document, "Описание:", func.description)

        # Требования: (label bold, value red)
        if func.requirements:
            req = document.add_paragraph()
            label = req.add_run("Требования: ")
            label.bold = True
            value = req.add_run(func.requirements.strip())
            value.font.color.rgb = self._red()

        # Порядок выполнения: numbered steps.
        # Numbers are written as plain text ("1. ...") so each function restarts
        # at 1, exactly like the reference manual (Word's List Number style
        # would otherwise continue numbering across the whole document).
        steps = func.step_lines()
        if steps:
            head = document.add_paragraph()
            head.add_run("Порядок выполнения:").bold = True
            for index, line in enumerate(steps, start=1):
                para = document.add_paragraph()
                para.paragraph_format.left_indent = Pt(18)
                para.add_run("%d. %s" % (index, line))

        # Screenshot + figure caption.
        if func.screenshot:
            figure += 1
            self._add_screenshot(document, func, figure)

        # Результат:
        self._labelled(document, "Результат:", func.result)
        return figure

    def _labelled(self, document, label, value):
        """Bold inline label followed by normal text in the same paragraph."""
        if not value:
            return
        para = document.add_paragraph()
        run = para.add_run(label + " ")
        run.bold = True
        para.add_run(value.strip())

    def _add_screenshot(self, document, func, figure):
        """Embed a centered screenshot followed by a 'Рис.N' caption."""
        try:
            image_bytes = base64.b64decode(func.screenshot)
            stream = io.BytesIO(image_bytes)
            document.add_picture(stream, width=Inches(5.5))
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

            caption = document.add_paragraph()
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap_text = func.screenshot_caption or func.name or ""
            cap_run = caption.add_run("Рис.%d %s" % (figure, cap_text))
            cap_run.font.size = Pt(10)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "Could not embed screenshot for function %s: %s", func.id, exc
            )

    # ------------------------------------------------------------------
    # 4. Литература / 5. Словарь
    # ------------------------------------------------------------------
    def _add_bibliography_section(self, document, modules):
        module = modules[:1]
        document.add_page_break()
        self._heading(document, "4. Литература", 1)
        self._bullets(document, module.bibliography if module else "")

    def _add_glossary_section(self, document, modules):
        module = modules[:1]
        self._heading(document, "5. Словарь", 1)
        self._bullets(document, module.glossary if module else "")
